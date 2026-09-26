"""Fixed CloudRun read capabilities and GET-only production runtime probes.

No import of the deployment controller, no action-name dispatch interface.
SDK clients remain inside narrow closures, never exposed by the adapter.
"""

from __future__ import annotations

import http.client
import json
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from r3_read_evidence import ReadFailure, safe_request_id

ENV_ID = "shengheshu-d2g2zyyl99f6c6fc2"
SERVICE = "seiwajyuku-platform-api"
REGION = "ap-shanghai"
SCOPE = f"{REGION}/{ENV_ID}/{SERVICE}"
GATE_KEYS = (
    "G5_4_PRODUCTION_RULE_APPLY_ENABLED",
    "LEARNING_CREDIT_SETTLEMENT_ENABLED",
    "RUN_BOOTSTRAP_ON_STARTUP",
)
CONFIG_KEYS = (
    "Port",
    "Cpu",
    "Mem",
    "MinNum",
    "MaxNum",
    "PolicyDetails",
    "EntryPoint",
    "Cmd",
    "VolumesConf",
    "Dockerfile",
    "BuildDir",
    "LogPath",
)
VPC_KEYS = ("VpcId", "VpcCIDR", "SubnetId", "SubnetCIDR")


@dataclass(frozen=True)
class _ReadPorts:
    service: object
    revision: object
    release: object
    task: object
    pods: object
    realm: str = "fixture"


def _sdk_ports(secret_id, secret_key, token=None):
    from tencentcloud.common.credential import Credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.common.retry import NoopRetryer
    from tencentcloud.tcbr.v20220217 import models
    from tencentcloud.tcbr.v20220217.tcbr_client import TcbrClient

    http = HttpProfile(endpoint="tcbr.tencentcloudapi.com", reqTimeout=10)
    client = TcbrClient(
        Credential(secret_id, secret_key, token),
        REGION,
        ClientProfile(httpProfile=http, retryer=NoopRetryer()),
    )

    def service():
        r = models.DescribeCloudRunServerDetailRequest()
        r.EnvId, r.ServerName = ENV_ID, SERVICE
        return client.DescribeCloudRunServerDetail(r)

    def revision(name):
        r = models.DescribeVersionDetailRequest()
        r.EnvId, r.ServerName, r.VersionName = ENV_ID, SERVICE, name
        return client.DescribeVersionDetail(r)

    def release():
        r = models.DescribeReleaseOrderRequest()
        r.EnvId, r.ServerName = ENV_ID, SERVICE
        return client.DescribeReleaseOrder(r)

    def task(task_id):
        r = models.DescribeServerManageTaskRequest()
        r.EnvId, r.ServerName, r.TaskId = ENV_ID, SERVICE, task_id
        return client.DescribeServerManageTask(r)

    def pods(name):
        r = models.DescribeCloudRunPodListRequest()
        r.EnvId, r.ServerName, r.VersionName = ENV_ID, SERVICE, name
        r.PageSize, r.PageNum = 50, 1
        return client.DescribeCloudRunPodList(r)

    return _ReadPorts(service, revision, release, task, pods, realm="live")


def revision_name(name):
    if not isinstance(name, str) or not re.fullmatch(SERVICE + r"-\d+", name):
        raise ReadFailure("REVISION_ID_INVALID")
    return name


def _required(raw, keys):
    if not isinstance(raw, dict) or any(k not in raw or raw[k] is None for k in keys):
        raise ReadFailure("RESPONSE_FIELDS_MISSING")
    return raw


class TencentCloudReadAdapter:
    def __init__(self, issuer, ports):
        if type(ports) is not _ReadPorts or issuer.realm != ports.realm:
            raise ReadFailure("READ_CAPABILITIES_REQUIRED")
        self._issuer, self.__ports = issuer, ports

    @classmethod
    def authenticated(cls, issuer, secret_id, secret_key, token=None):
        return cls(issuer, _sdk_ports(secret_id, secret_key, token))

    def _observe(self, read):
        started = self._issuer.clock()
        try:
            value = read()
            raw = json.loads(value.to_json_string())
        except Exception as exc:  # noqa: BLE001 - sanitize every SDK/transport failure
            # Arbitrary SDK messages may embed credentials/URLs. Do not retain.
            rid = None
            if callable(getattr(exc, "get_request_id", None)):
                candidate = exc.get_request_id()
                try:
                    rid = safe_request_id(candidate)
                except ValueError:
                    pass
            error_code = (
                exc.get_code()
                if callable(getattr(exc, "get_code", None))
                else type(exc).__name__
            )
            raise ReadFailure(
                "CLOUD_READ_FAILED", request_id=rid, error_code=error_code
            ) from None
        try:
            rid = safe_request_id(raw.get("RequestId"))
        except (ValueError, AttributeError):
            raise ReadFailure("REQUEST_ID_INVALID") from None
        return raw, started, rid

    def service(self):
        raw, start, rid = self._observe(self.__ports.service)
        base = _required(raw.get("BaseInfo"), ("ServerName", "Status", "TrafficType"))
        conf = _required(raw.get("ServerConfig"), ("EnvId", "ServerName"))
        if (
            base["ServerName"] != SERVICE
            or conf["ServerName"] != SERVICE
            or conf["EnvId"] != ENV_ID
        ):
            raise ReadFailure("SERVICE_ID_MISMATCH")
        rows = raw.get("OnlineVersionInfos")
        if not isinstance(rows, list) or not rows:
            raise ReadFailure("TRAFFIC_UNKNOWN")
        versions = []
        for row in rows:
            _required(row, ("VersionName", "FlowRatio"))
            name = revision_name(row["VersionName"])
            if not re.fullmatch(r"\d{1,3}", str(row["FlowRatio"])):
                raise ReadFailure("TRAFFIC_UNKNOWN")
            ratio = int(row["FlowRatio"])
            if not 0 <= ratio <= 100:
                raise ReadFailure("TRAFFIC_UNKNOWN")
            image = row.get("ImageUrl")
            if image is not None and (
                not isinstance(image, str)
                or not re.fullmatch(
                    r"[a-zA-Z0-9.-]+(?::[0-9]+)?/[a-zA-Z0-9/_.:-]+(?:@sha256:[a-f0-9]{64})?",
                    image,
                )
            ):
                raise ReadFailure("IMAGE_FORMAT_INVALID")
            versions.append(
                {
                    "revision": name,
                    "percent": ratio,
                    "image_reference": image,
                    "image_digest": image.split("@", 1)[1]
                    if image and re.search(r"@sha256:[a-f0-9]{64}$", image)
                    else "UNKNOWN",
                }
            )
        if (
            len({x["revision"] for x in versions}) != len(versions)
            or sum(x["percent"] for x in versions) != 100
        ):
            raise ReadFailure("TRAFFIC_UNKNOWN")
        payload = {
            "service": SERVICE,
            "environment": ENV_ID,
            "status": base["Status"],
            "traffic_type": base["TrafficType"],
            "versions": versions,
            "default_domain": base.get("DefaultDomainName"),
        }
        return self._issuer._issue(
            "ServiceEvidence",
            "tencent:DescribeCloudRunServerDetail",
            SCOPE,
            raw,
            payload,
            start,
            rid,
        )

    def revision(self, name):
        name = revision_name(name)
        raw, start, rid = self._observe(lambda: self.__ports.revision(name))
        _required(raw, ("Name", "Status", "EnvParams"))
        if raw["Name"] != name:
            raise ReadFailure("REVISION_ID_MISMATCH")
        env = raw["EnvParams"]
        if isinstance(env, str):
            try:
                env = json.loads(env)
            except ValueError:
                raise ReadFailure("ENV_INVALID") from None
        if not isinstance(env, dict) or any(
            not isinstance(k, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
            or not isinstance(v, str)
            for k, v in env.items()
        ):
            raise ReadFailure("ENV_INVALID")
        gates = {k: env.get(k, "UNKNOWN") for k in GATE_KEYS}
        vpc = raw.get("VpcConf") or {}
        payload = {
            "revision": name,
            "status": raw["Status"],
            "gates": gates,
            "stable_false_gates_verified": all(x == "false" for x in gates.values()),
            "env_fingerprints": {
                k: self._issuer._secret_digest(v) for k, v in env.items()
            },
            "vpc": {k: vpc.get(k, "UNKNOWN") for k in VPC_KEYS},
            "config": {
                k: raw[k] if k in raw and raw[k] is not None else "UNKNOWN"
                for k in CONFIG_KEYS
            },
        }
        # EntryPoint/Cmd and other config may contain secrets: compare keyed hashes,
        # never export their raw values.
        payload["config"] = {
            k: "UNKNOWN" if v == "UNKNOWN" else self._issuer._secret_digest(v)
            for k, v in payload["config"].items()
        }
        return self._issuer._issue(
            "RevisionEvidence",
            "tencent:DescribeVersionDetail",
            f"{SCOPE}/{name}",
            raw,
            payload,
            start,
            rid,
        )

    def release(self):
        raw, start, rid = self._observe(self.__ports.release)
        if raw.get("IsExist") is not True:
            raise ReadFailure("RELEASE_IDENTITY_UNKNOWN")
        order = _required(
            raw.get("ReleaseOrderInfo"),
            ("Id", "ServerName", "IsReleasing", "ReleaseStatus", "TrafficType"),
        )
        if (
            order["ServerName"] != SERVICE
            or type(order["Id"]) is not int
            or type(order["IsReleasing"]) is not bool
        ):
            raise ReadFailure("RELEASE_IDENTITY_MISMATCH")
        params = order.get("TrafficTypeValues", "UNKNOWN")
        version_params = [
            order.get(k, {}).get("UrlParam", "UNKNOWN")
            if isinstance(order.get(k), dict)
            else "UNKNOWN"
            for k in ("CurrentVersion", "ReleaseVersion")
        ]
        empty = params == [] and all(
            x in (None, {}, {"Key": "", "Value": ""}) for x in version_params
        )
        payload = {
            "order_id": order["Id"],
            "is_releasing": order["IsReleasing"],
            "status": order["ReleaseStatus"],
            "traffic_type": order["TrafficType"],
            "params_empty": empty,
            "params_fingerprint": self._issuer._secret_digest([params, version_params]),
            "release_closed": order["IsReleasing"] is False
            and order["ReleaseStatus"]
            in {"success", "finished", "stopped", "cancelled", "canceled", "done"},
        }
        return self._issuer._issue(
            "ReleaseEvidence",
            "tencent:DescribeReleaseOrder",
            f"{SCOPE}/release/{order['Id']}",
            raw,
            payload,
            start,
            rid,
        )

    def task(self, task_id):
        if type(task_id) is not int or task_id <= 0:
            raise ReadFailure("TASK_ID_INVALID")
        raw, start, rid = self._observe(lambda: self.__ports.task(task_id))
        if raw.get("IsExist") is not True:
            raise ReadFailure("TASK_IDENTITY_UNKNOWN")
        task = _required(
            raw.get("Task"),
            ("Id", "EnvId", "ServerName", "Status", "ReleaseId", "VersionName"),
        )
        if (
            task["Id"] != task_id
            or task["EnvId"] != ENV_ID
            or task["ServerName"] != SERVICE
        ):
            raise ReadFailure("TASK_IDENTITY_MISMATCH")
        payload = {
            "task_id": task_id,
            "release_id": task["ReleaseId"],
            "revision": revision_name(task["VersionName"]),
            "status": task["Status"],
            "task_inactive": task["Status"]
            in {"stopped", "finished", "success", "failed", "cancelled", "canceled"},
        }
        return self._issuer._issue(
            "ManageTaskEvidence",
            "tencent:DescribeServerManageTask",
            f"{SCOPE}/task/{task_id}",
            raw,
            payload,
            start,
            rid,
        )

    def pods(self, name):
        name = revision_name(name)
        raw, start, rid = self._observe(lambda: self.__ports.pods(name))
        _required(raw, ("PodList", "TotalCount"))
        if (
            type(raw["TotalCount"]) is not int
            or not isinstance(raw["PodList"], list)
            or raw["TotalCount"] != len(raw["PodList"])
        ):
            raise ReadFailure("POD_LIST_INCOMPLETE")
        pods = [
            {k: _required(p, ("PodId", "Status"))[k] for k in ("PodId", "Status")}
            for p in raw["PodList"]
        ]
        payload = {
            "revision": name,
            "count": len(pods),
            "pods": pods,
            "running": any(p["Status"].lower() == "running" for p in pods),
            "readiness": "UNKNOWN",
        }  # SDK reports Status, not a Ready condition.
        return self._issuer._issue(
            "PodEvidence",
            "tencent:DescribeCloudRunPodList",
            f"{SCOPE}/{name}",
            raw,
            payload,
            start,
            rid,
        )


def compare_config(stable, candidate, stable_image, candidate_image):
    """Exact comparison: absent/unprovable values stay UNKNOWN, never equal."""
    a, b = stable.payload(), candidate.payload()
    result = {}
    for group in ("vpc", "config", "env_fingerprints", "gates"):
        for k in sorted(set(a[group]) | set(b[group])):
            x, y = a[group].get(k, "UNKNOWN"), b[group].get(k, "UNKNOWN")
            result[f"{group}.{k}"] = (
                "UNKNOWN"
                if x in (None, "UNKNOWN") or y in (None, "UNKNOWN")
                else "EQUAL"
                if x == y
                else "DIFFERENT"
            )
    for field in ("image_reference", "image_digest"):
        x, y = stable_image.get(field), candidate_image.get(field)
        result[field] = (
            "UNKNOWN"
            if x in (None, "UNKNOWN") or y in (None, "UNKNOWN")
            else "EQUAL"
            if x == y
            else "DIFFERENT"
        )
    result["other_config_equal"] = all(v == "EQUAL" for v in result.values())
    return result


PATHS = ("/api/v1/system/build-info", "/health/live", "/api/v1/health")


class _GetOnly:
    def __init__(self, origin):
        u = urlsplit(origin)
        if (
            u.scheme != "https"
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.port not in (None, 443)
            or u.path not in ("", "/")
            or not u.hostname
        ):
            raise ReadFailure("HTTP_ORIGIN_INVALID")
        self.__host = u.hostname

    def get(self, path):
        if path not in PATHS:
            raise ReadFailure("HTTP_PATH_NOT_ALLOWED")
        connection = http.client.HTTPSConnection(
            self.__host, timeout=10, context=ssl.create_default_context()
        )
        try:
            connection.request(
                "GET",
                path,
                headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            )
            response = connection.getresponse()
            body = response.read(65537)
            if response.status != 200 or len(body) > 65536:
                raise ReadFailure("HTTP_STATUS_OR_SIZE_INVALID")
            if "application/json" not in (response.getheader("Content-Type") or ""):
                raise ReadFailure("HTTP_FORMAT_INVALID")
            data = json.loads(body)
            return data, response.getheader("X-Request-ID")
        except ReadFailure:
            raise
        except Exception:  # noqa: BLE001 - never leak URLs or transport credentials
            raise ReadFailure("HTTP_READ_FAILED") from None
        finally:
            connection.close()


class RuntimeReadAdapter:
    def __init__(self, issuer, origin):
        self._issuer, self.__http = issuer, _GetOnly(origin)

    def collect(self, revision, expected_commit):
        revision_name(revision)
        if not re.fullmatch(r"[a-f0-9]{40}", expected_commit):
            raise ReadFailure("COMMIT_INVALID")
        observations = []
        for path in PATHS:
            start = self._issuer.clock()
            raw, rid = self.__http.get(path)
            if not isinstance(raw, dict):
                raise ReadFailure("HTTP_FORMAT_INVALID")
            try:
                rid = safe_request_id(rid, required=False)
            except ValueError:
                raise ReadFailure("REQUEST_ID_INVALID") from None
            if path == PATHS[0]:
                if (
                    raw.get("commit_sha") != expected_commit
                    or raw.get("environment") != "production"
                ):
                    raise ReadFailure("RUNTIME_COMMIT_OR_ENV_MISMATCH")
                payload = {
                    k: raw.get(k, "UNKNOWN")
                    for k in ("commit_sha", "image_digest", "environment")
                }
            else:
                if raw.get("service") != SERVICE or raw.get("status") != "ok":
                    raise ReadFailure("RUNTIME_SERVICE_MISMATCH")
                payload = {"service": SERVICE, "status": "ok"}
            payload.update(
                http_status=200,
                revision=revision,
                identity_basis="PENDING_BRACKETED_FLOW_PROOF",
            )
            observations.append(
                self._issuer._issue(
                    "RuntimeEvidence",
                    f"runtime-http:{path}",
                    f"{SCOPE}/{revision}",
                    raw,
                    payload,
                    start,
                    rid,
                )
            )
        return observations


def require_stable_flow(service, release, revision):
    s, r = service.payload(), release.payload()
    positive = [x for x in s["versions"] if x["percent"] > 0]
    if (
        s["status"] != "normal"
        or s["traffic_type"] != "FLOW"
        or r["traffic_type"] != "FLOW"
        or not r["params_empty"]
        or not r["release_closed"]
        or len(positive) != 1
        or positive[0]["revision"] != revision
        or positive[0]["percent"] != 100
    ):
        raise ReadFailure("FLOW_IDENTITY_NOT_PROVEN")


def collect_cloud_runtime(cloud, issuer, revision, task_id, expected_commit):
    """Bracket all reads; no retries or lifecycle state changes on any failure."""
    start = issuer.clock()
    before, order = cloud.service(), cloud.release()
    require_stable_flow(before, order, revision)
    rev, task, pods = (
        cloud.revision(revision),
        cloud.task(task_id),
        cloud.pods(revision),
    )
    if (
        task.payload()["release_id"] != order.payload()["order_id"]
        or not task.payload()["task_inactive"]
        or rev.payload()["status"] != "normal"
    ):
        raise ReadFailure("CONTROL_STATE_NOT_READY")
    domain = before.payload()["default_domain"]
    if not isinstance(domain, str) or not domain:
        raise ReadFailure("RUNTIME_ORIGIN_UNKNOWN")
    origin = domain if domain.startswith("https://") else "https://" + domain
    runtime = RuntimeReadAdapter(issuer, origin).collect(revision, expected_commit)
    after, end_order, end_rev, end_task = (
        cloud.service(),
        cloud.release(),
        cloud.revision(revision),
        cloud.task(task_id),
    )
    for a, b in ((before, after), (order, end_order), (rev, end_rev), (task, end_task)):
        if a.payload() != b.payload():
            raise ReadFailure("SNAPSHOT_CHANGED")
    require_stable_flow(after, end_order, revision)
    flow = issuer._issue(
        "TrafficEvidence",
        "tencent:DescribeCloudRunServerDetail+DescribeReleaseOrder",
        SCOPE,
        {
            "before": before.raw_fingerprint,
            "after": after.raw_fingerprint,
            "release_before": order.raw_fingerprint,
            "release_after": end_order.raw_fingerprint,
        },
        {
            "stable_revision": revision,
            "stable_percent": 100,
            "ordinary_others_percent": 0,
            "traffic_type": "FLOW",
            "params_empty": True,
            "runtime_identity_basis": "BRACKETED_FLOW_100",
        },
        start,
    )
    runtime = issuer._bind_runtime(runtime, flow)
    return issuer.bundle(
        SCOPE,
        revision,
        [
            before,
            order,
            rev,
            task,
            pods,
            *runtime,
            after,
            end_order,
            end_rev,
            end_task,
            flow,
        ],
        start,
    )
