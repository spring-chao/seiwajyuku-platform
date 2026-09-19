#!/usr/bin/env python3
"""Controlled CloudBase Run release for ``seiwajyuku-platform-api``.

The release controller deliberately has one production target and one network
policy: every ordinary code/config release inherits the complete VpcConf from
the unique 100%-traffic stable revision.  The candidate is queried and checked
before any candidate routing is installed.

Dry-run performs only read operations.  Live execution uses Tencent Cloud's
typed Python SDK; it never builds API JSON in a shell command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ENV_ID = "shengheshu-d2g2zyyl99f6c6fc2"
SERVICE_NAME = "seiwajyuku-platform-api"
REGION = "ap-shanghai"
CANARY_QUERY_KEY = "sj_canary"
SDK_API_PATH = "UpdateCloudRunServer"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

SAFE_BOOLEAN_ENV_KEYS = frozenset(
    {
        "ALLOW_PRODUCTION_MUTATIONS",
        "DEPLOYMENT_READ_ONLY",
        "G5_4_PRODUCTION_RULE_APPLY_ENABLED",
        "IDENTITY_ADMIN_WRITES_ENABLED",
        "LEARNING_CREDIT_SETTLEMENT_ENABLED",
        "RUN_BOOTSTRAP_ON_STARTUP",
    }
)
SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "CREDENTIAL",
    "DATABASE_URL",
    "ENCRYPTION_KEY",
    "JWT",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)


class ReleaseState(str, Enum):
    DISCOVER_STABLE = "DISCOVER_STABLE"
    READ_STABLE_VERSION = "READ_STABLE_VERSION"
    BUILD_PLAN = "BUILD_PLAN"
    CREATE_CANDIDATE = "CREATE_CANDIDATE"
    VERIFY_CANDIDATE_CONFIG = "VERIFY_CANDIDATE_CONFIG"
    TARGETED_HEALTH = "TARGETED_HEALTH"
    READY_FOR_RELEASE = "READY_FOR_RELEASE"
    GRAY = "GRAY"
    FULL = "FULL"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class ReleaseFailure(RuntimeError):
    """A fail-closed release outcome whose message never contains secrets."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        terminal_state: ReleaseState = ReleaseState.BLOCKED,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.terminal_state = terminal_state


@dataclass(frozen=True)
class VpcConfiguration:
    vpc_id: str
    vpc_cidr: str
    subnet_id: str
    subnet_cidr: str

    @classmethod
    def from_payload(cls, payload: Any) -> VpcConfiguration:
        return cls(
            vpc_id=str(_value(payload, "VpcId") or "").strip(),
            vpc_cidr=str(_value(payload, "VpcCIDR") or "").strip(),
            subnet_id=str(_value(payload, "SubnetId") or "").strip(),
            subnet_cidr=str(_value(payload, "SubnetCIDR") or "").strip(),
        )

    def require_baseline(self) -> None:
        if not self.vpc_id or not self.subnet_id:
            raise ReleaseFailure(
                "VPC_BASELINE_MISSING",
                "stable revision is missing VpcId or SubnetId",
            )

    def as_api_dict(self) -> dict[str, str]:
        return {
            "VpcId": self.vpc_id,
            "VpcCIDR": self.vpc_cidr,
            "SubnetId": self.subnet_id,
            "SubnetCIDR": self.subnet_cidr,
        }

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.as_api_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class DeployArtifact:
    deploy_type: str
    image_url: str | None = None
    package_name: str | None = None
    package_version: str | None = None

    def validate_for_update(self) -> None:
        if self.deploy_type == "image" and self.image_url:
            return
        if self.deploy_type == "package" and self.package_name and self.package_version:
            return
        raise ReleaseFailure(
            "DEPLOY_ARTIFACT_INVALID",
            "deploy artifact must be a complete image or package",
        )


@dataclass(frozen=True)
class ReleaseInput:
    desired_runtime_commit: str
    artifact_mode: str
    requested_env_changes: Mapping[str, str | None]
    approval_ref: str | None = None
    image_url: str | None = None
    expected_database_vpc_id: str | None = None
    build_id: str | None = None
    ci_provenance_verified: bool = False

    def __post_init__(self) -> None:
        if not SHA_RE.fullmatch(self.desired_runtime_commit.lower()):
            raise ReleaseFailure(
                "DESIRED_COMMIT_INVALID",
                "desired runtime commit must be a 40-character Git SHA",
            )
        if self.artifact_mode not in {"source", "image", "stable-image"}:
            raise ReleaseFailure("DEPLOY_ARTIFACT_INVALID", "unsupported artifact mode")
        if self.artifact_mode == "image" and not self.image_url:
            raise ReleaseFailure(
                "DEPLOY_ARTIFACT_INVALID", "image mode requires an image URL"
            )
        if self.image_url:
            parsed = urlsplit(self.image_url)
            if parsed.username or parsed.password:
                raise ReleaseFailure(
                    "DEPLOY_ARTIFACT_INVALID",
                    "image URL must not contain embedded credentials",
                )
        if self.approval_ref and not re.fullmatch(
            r"[A-Za-z0-9._/-]{1,120}", self.approval_ref
        ):
            raise ReleaseFailure(
                "APPROVAL_REFERENCE_INVALID",
                "approval reference contains unsupported characters",
            )
        if self.build_id and not re.fullmatch(r"[A-Za-z0-9._/-]{1,120}", self.build_id):
            raise ReleaseFailure(
                "BUILD_ID_INVALID", "build id contains unsupported characters"
            )


@dataclass(frozen=True)
class ReleasePlan:
    env_id: str
    service_name: str
    stable_revision: str
    stable_runtime_commit: str
    desired_runtime_commit: str
    stable_vpc_conf: VpcConfiguration
    desired_vpc_conf: VpcConfiguration
    stable_image_url: str | None
    base_url: str
    artifact_mode: str
    release_type: str
    artifact: DeployArtifact | None
    env_change_summary: tuple[dict[str, str], ...]
    env_params_json: str | None = field(default=None, repr=False)
    approval_ref: str | None = None
    build_id: str | None = None
    ci_provenance_verified: bool = False

    def safe_dict(self) -> dict[str, Any]:
        deploy_type = (
            self.artifact.deploy_type
            if self.artifact is not None
            else "package (source upload pending)"
        )
        return {
            "state": ReleaseState.BUILD_PLAN.value,
            "env_id": self.env_id,
            "service_name": self.service_name,
            "stable_revision": self.stable_revision,
            "stable_runtime_commit": self.stable_runtime_commit,
            "desired_runtime_commit": self.desired_runtime_commit,
            "ci_provenance_verified": self.ci_provenance_verified,
            "deploy_type": deploy_type,
            "release_type": self.release_type,
            "stable_vpc_conf": self.stable_vpc_conf.as_api_dict(),
            "stable_vpc_fingerprint": self.stable_vpc_conf.fingerprint,
            "candidate_request_vpc_conf": self.desired_vpc_conf.as_api_dict(),
            "candidate_request_items": [
                "VpcConf",
                *(["EnvParam"] if self.env_params_json is not None else []),
            ],
            "desired_env_changes": list(self.env_change_summary),
            "release_strategy": {
                "create": "UpdateCloudRunServer with ReleaseType=GRAY",
                "candidate_route": "URL_PARAMS one-time token",
                "stable_default": True,
                "database_health_samples": 20,
                "network_change": "DISALLOWED; inherit stable revision",
            },
            "dry_run_effects": {
                "revision_writes": 0,
                "network_writes": 0,
                "database_writes": 0,
            },
        }


@dataclass(frozen=True)
class UpdateRequestSpec:
    artifact: DeployArtifact
    vpc_conf: VpcConfiguration
    env_params_json: str | None
    deploy_remark: str


class CloudRunApi(Protocol):
    def describe_service(self) -> Mapping[str, Any]: ...

    def describe_version(self, version_name: str) -> Mapping[str, Any]: ...

    def describe_deploy_records(self) -> Sequence[Mapping[str, Any]]: ...

    def describe_manage_task(self, task_id: int) -> Mapping[str, Any] | None: ...

    def create_candidate(self, spec: UpdateRequestSpec) -> int: ...

    def release_targeted(
        self, stable_revision: str, candidate_revision: str, token: str
    ) -> None: ...

    def release_flow(
        self, stable_revision: str, candidate_revision: str, candidate_percent: int
    ) -> None: ...


class SourceArtifactProvider(Protocol):
    def prepare(self, plan: ReleasePlan) -> DeployArtifact: ...


@dataclass(frozen=True)
class HttpResult:
    status: int
    payload: Mapping[str, Any]


class CandidateProbe(Protocol):
    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> HttpResult: ...


def _value(payload: Any, name: str, default: Any = None) -> Any:
    if payload is None:
        return default
    if isinstance(payload, Mapping):
        return payload.get(name, default)
    return getattr(payload, name, default)


def _ratio(value: Any) -> int:
    text = str(value if value is not None else "").strip().removesuffix("%")
    try:
        number = float(text)
    except ValueError as exc:
        raise ReleaseFailure(
            "STABLE_REVISION_NOT_UNIQUE", "online traffic ratio is invalid"
        ) from exc
    if not number.is_integer():
        raise ReleaseFailure(
            "STABLE_REVISION_NOT_UNIQUE", "online traffic ratio is not integral"
        )
    return int(number)


def _normalise_base_url(domain: str) -> str:
    value = domain.strip().rstrip("/")
    if not value:
        raise ReleaseFailure(
            "STABLE_ENDPOINT_MISSING", "stable service domain is unavailable"
        )
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ReleaseFailure(
            "STABLE_ENDPOINT_MISSING", "stable service domain is invalid"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _parse_env_params(raw: Any) -> dict[str, str]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise ReleaseFailure(
                "ENV_BASELINE_INVALID",
                "stable revision EnvParams is not a JSON object",
            ) from exc
    if not isinstance(parsed, dict):
        raise ReleaseFailure(
            "ENV_BASELINE_INVALID", "stable revision EnvParams is not an object"
        )
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, (str, int, float, bool)):
            raise ReleaseFailure(
                "ENV_BASELINE_INVALID", "stable revision EnvParams has invalid values"
            )
        result[key] = str(value)
    return result


def _is_sensitive_env_key(key: str) -> bool:
    upper = key.upper()
    return any(marker in upper for marker in SENSITIVE_ENV_MARKERS)


def safe_env_change_summary(
    stable: Mapping[str, str], changes: Mapping[str, str | None]
) -> tuple[dict[str, str], ...]:
    summary: list[dict[str, str]] = []
    for key in sorted(changes):
        desired = changes[key]
        if desired is None:
            status = "changed" if key in stable else "unchanged"
        else:
            status = "unchanged" if stable.get(key) == desired else "changed"
        item = {"key": key, "status": status}
        normalized = str(desired).lower() if desired is not None else ""
        if (
            key in SAFE_BOOLEAN_ENV_KEYS
            and not _is_sensitive_env_key(key)
            and normalized in {"true", "false"}
        ):
            item["value"] = normalized
        summary.append(item)
    return tuple(summary)


def _merge_env_params(
    stable: Mapping[str, str], changes: Mapping[str, str | None]
) -> tuple[str | None, tuple[dict[str, str], ...]]:
    merged = dict(stable)
    changed = False
    for key, desired in changes.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ReleaseFailure("ENV_CHANGE_INVALID", "environment key is invalid")
        if desired is None:
            changed = key in merged or changed
            merged.pop(key, None)
        else:
            if not isinstance(desired, str):
                raise ReleaseFailure(
                    "ENV_CHANGE_INVALID", "environment values must be strings or null"
                )
            changed = merged.get(key) != desired or changed
            merged[key] = desired
    summary = safe_env_change_summary(stable, changes)
    if not changed:
        return None, summary
    return json.dumps(merged, ensure_ascii=False, sort_keys=True), summary


def _sdk_response_dict(response: Any) -> dict[str, Any]:
    return json.loads(response.to_json_string())


def build_update_request(spec: UpdateRequestSpec) -> Any:
    """Build a typed ``UpdateCloudRunServerRequest`` from a guarded spec."""

    from tencentcloud.tcbr.v20220217 import models

    spec.artifact.validate_for_update()
    vpc = models.VpcConf()
    vpc.VpcId = spec.vpc_conf.vpc_id
    vpc.VpcCIDR = spec.vpc_conf.vpc_cidr
    vpc.SubnetId = spec.vpc_conf.subnet_id
    vpc.SubnetCIDR = spec.vpc_conf.subnet_cidr

    vpc_item = models.DiffConfigItem()
    vpc_item.Key = "VpcConf"
    vpc_item.VpcConf = vpc
    items = [vpc_item]
    if spec.env_params_json is not None:
        env_item = models.DiffConfigItem()
        env_item.Key = "EnvParam"
        env_item.Value = spec.env_params_json
        items.append(env_item)

    deploy = models.DeployParam()
    deploy.DeployType = spec.artifact.deploy_type
    deploy.ImageUrl = spec.artifact.image_url
    deploy.PackageName = spec.artifact.package_name
    deploy.PackageVersion = spec.artifact.package_version
    deploy.DeployRemark = spec.deploy_remark
    # A candidate must exist before version-level network and health gates can
    # run.  FULL here would bypass those gates.
    deploy.ReleaseType = "GRAY"

    request = models.UpdateCloudRunServerRequest()
    request.EnvId = ENV_ID
    request.ServerName = SERVICE_NAME
    request.DeployInfo = deploy
    request.Items = items
    return request


def build_targeted_release_request(
    stable_revision: str, candidate_revision: str, token: str
) -> Any:
    from tencentcloud.tcbr.v20220217 import models

    stable = models.VersionFlowInfo()
    stable.VersionName = stable_revision
    stable.IsDefaultPriority = True
    stable.FlowRatio = 100
    stable.Priority = 1

    route = models.ObjectKV()
    route.Key = CANARY_QUERY_KEY
    route.Value = token
    candidate = models.VersionFlowInfo()
    candidate.VersionName = candidate_revision
    candidate.IsDefaultPriority = False
    candidate.FlowRatio = 0
    candidate.UrlParam = route
    candidate.Priority = 2

    request = models.ReleaseGrayRequest()
    request.EnvId = ENV_ID
    request.ServerName = SERVICE_NAME
    request.GrayType = "gray"
    request.TrafficType = "URL_PARAMS"
    request.GrayFlowRatio = 0
    request.OperatorRemark = "controlled candidate health route"
    request.VersionFlowItems = [stable, candidate]
    return request


def build_flow_release_request(
    stable_revision: str, candidate_revision: str, candidate_percent: int
) -> Any:
    from tencentcloud.tcbr.v20220217 import models

    if not 0 <= candidate_percent <= 100:
        raise ReleaseFailure("GRAY_RATIO_INVALID", "candidate ratio must be 0..100")
    items = []
    if candidate_percent < 100:
        stable = models.VersionFlowInfo()
        stable.VersionName = stable_revision
        stable.IsDefaultPriority = True
        stable.FlowRatio = 100 - candidate_percent
        stable.Priority = 1
        items.append(stable)
    candidate = models.VersionFlowInfo()
    candidate.VersionName = candidate_revision
    candidate.IsDefaultPriority = candidate_percent == 100
    candidate.FlowRatio = candidate_percent
    candidate.Priority = 0 if candidate_percent == 100 else 2
    items.append(candidate)

    request = models.ReleaseGrayRequest()
    request.EnvId = ENV_ID
    request.ServerName = SERVICE_NAME
    request.GrayType = "gray"
    request.TrafficType = "FLOW"
    request.GrayFlowRatio = candidate_percent
    request.OperatorRemark = "controlled post-gate flow"
    request.VersionFlowItems = items
    return request


class TencentCloudSdkApi:
    """Narrow official-SDK adapter; it cannot target another service/env."""

    def __init__(self) -> None:
        from tencentcloud.common.credential import Credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.tcbr.v20220217.tcbr_client import TcbrClient

        secret_id = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
        secret_key = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
        token = os.getenv("TENCENTCLOUD_TOKEN", "").strip() or None
        if not secret_id or not secret_key:
            raise ReleaseFailure(
                "TENCENT_CREDENTIALS_MISSING",
                "Tencent Cloud SDK credentials are not configured",
            )
        credential = Credential(secret_id, secret_key, token)
        http_profile = HttpProfile()
        http_profile.endpoint = "tcbr.tencentcloudapi.com"
        client_profile = ClientProfile(httpProfile=http_profile)
        self._credential = credential
        self._tcbr = TcbrClient(credential, REGION, client_profile)
        self._tcb = None

    def _tcbr_request(self, name: str, request: Any) -> dict[str, Any]:
        return _sdk_response_dict(getattr(self._tcbr, name)(request))

    def describe_service(self) -> Mapping[str, Any]:
        from tencentcloud.tcbr.v20220217 import models

        request = models.DescribeCloudRunServerDetailRequest()
        request.EnvId = ENV_ID
        request.ServerName = SERVICE_NAME
        return self._tcbr_request("DescribeCloudRunServerDetail", request)

    def describe_version(self, version_name: str) -> Mapping[str, Any]:
        from tencentcloud.tcbr.v20220217 import models

        request = models.DescribeVersionDetailRequest()
        request.EnvId = ENV_ID
        request.ServerName = SERVICE_NAME
        request.VersionName = version_name
        return self._tcbr_request("DescribeVersionDetail", request)

    def describe_deploy_records(self) -> Sequence[Mapping[str, Any]]:
        from tencentcloud.tcbr.v20220217 import models

        request = models.DescribeCloudRunDeployRecordRequest()
        request.EnvId = ENV_ID
        request.ServerName = SERVICE_NAME
        response = self._tcbr_request("DescribeCloudRunDeployRecord", request)
        return list(response.get("DeployRecords") or [])

    def describe_manage_task(self, task_id: int) -> Mapping[str, Any] | None:
        from tencentcloud.tcbr.v20220217 import models

        request = models.DescribeServerManageTaskRequest()
        request.EnvId = ENV_ID
        request.ServerName = SERVICE_NAME
        request.TaskId = task_id
        response = self._tcbr_request("DescribeServerManageTask", request)
        if not response.get("IsExist"):
            return None
        task = response.get("Task")
        return task if isinstance(task, Mapping) else None

    def create_candidate(self, spec: UpdateRequestSpec) -> int:
        response = self._tcbr_request(
            "UpdateCloudRunServer", build_update_request(spec)
        )
        return int(response.get("TaskId") or 0)

    def release_targeted(
        self, stable_revision: str, candidate_revision: str, token: str
    ) -> None:
        self._tcbr_request(
            "ReleaseGray",
            build_targeted_release_request(stable_revision, candidate_revision, token),
        )

    def release_flow(
        self, stable_revision: str, candidate_revision: str, candidate_percent: int
    ) -> None:
        self._tcbr_request(
            "ReleaseGray",
            build_flow_release_request(
                stable_revision, candidate_revision, candidate_percent
            ),
        )

    def reserve_source_package(self) -> Mapping[str, Any]:
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.tcb.v20180608 import models
        from tencentcloud.tcb.v20180608.tcb_client import TcbClient

        if self._tcb is None:
            http_profile = HttpProfile()
            http_profile.endpoint = "tcb.tencentcloudapi.com"
            self._tcb = TcbClient(
                self._credential,
                REGION,
                ClientProfile(httpProfile=http_profile),
            )
        request = models.DescribeCloudBaseBuildServiceRequest()
        request.EnvId = ENV_ID
        request.ServiceName = SERVICE_NAME
        request.CIBusiness = "cloudbaserun"
        request.Suffix = "zip"
        return _sdk_response_dict(self._tcb.DescribeCloudBaseBuildService(request))


class UrlLibProbe:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> HttpResult:
        url = base_url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + urlencode(query)
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
            payload = json.loads(body)
            if not isinstance(payload, Mapping):
                raise TypeError("HTTP JSON response is not an object")
            return HttpResult(status=response.status, payload=payload)


class GitSourcePackageProvider:
    def __init__(self, api: TencentCloudSdkApi, repo_root: Path) -> None:
        self.api = api
        self.repo_root = repo_root.resolve()

    def _verify_checkout(self, commit: str) -> None:
        from build_provenance import verify_checkout

        verify_checkout(
            self.repo_root,
            expected_commit=commit,
            require_origin_main=True,
            migration_manifest=self.repo_root
            / "docs/V1.2-R1.1-migration-manifest-20260828.json",
        )

    def _archive(self, destination: Path, commit: str, build_id: str) -> None:
        result = subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                f"--output={destination}",
                commit,
            ],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise ReleaseFailure(
                "SOURCE_ARCHIVE_FAILED", "unable to create verified source archive"
            )
        stamp = {
            "commit_sha": commit,
            "version": commit[:12],
            "build_time_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "build_id": build_id,
        }
        with zipfile.ZipFile(
            destination, "a", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "apps/platform-api/app/build-info.json",
                json.dumps(stamp, ensure_ascii=False, indent=2) + "\n",
            )

    def prepare(self, plan: ReleasePlan) -> DeployArtifact:
        if not plan.build_id:
            raise ReleaseFailure(
                "BUILD_ID_MISSING", "source release requires an immutable build id"
            )
        self._verify_checkout(plan.desired_runtime_commit)
        reservation = self.api.reserve_source_package()
        upload_url = str(reservation.get("UploadUrl") or "")
        package_name = str(reservation.get("PackageName") or "")
        package_version = str(reservation.get("PackageVersion") or "")
        headers = {
            str(item.get("Key")): str(item.get("Value"))
            for item in reservation.get("UploadHeaders") or []
            if isinstance(item, Mapping) and item.get("Key") is not None
        }
        if not upload_url or not package_name or not package_version:
            raise ReleaseFailure(
                "SOURCE_UPLOAD_RESERVATION_FAILED",
                "CloudBase did not return a complete package upload reservation",
            )
        with tempfile.TemporaryDirectory(prefix="sj-cloudrun-release-") as temp_dir:
            archive_path = Path(temp_dir) / "source.zip"
            self._archive(archive_path, plan.desired_runtime_commit, plan.build_id)
            # requests is a dependency of the official Tencent SDK. Streaming
            # avoids loading the complete source package into memory.
            import requests

            with archive_path.open("rb") as stream:
                response = requests.put(
                    upload_url,
                    data=stream,
                    headers=headers,
                    timeout=(15, 300),
                )
            if not 200 <= response.status_code < 300:
                raise ReleaseFailure(
                    "SOURCE_UPLOAD_FAILED", "source package upload was rejected"
                )
        return DeployArtifact(
            deploy_type="package",
            package_name=package_name,
            package_version=package_version,
        )


class CloudRunReleaseController:
    def __init__(
        self,
        api: CloudRunApi,
        probe: CandidateProbe,
        *,
        source_provider: SourceArtifactProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 5.0,
        max_polls: int = 120,
    ) -> None:
        self.api = api
        self.probe = probe
        self.source_provider = source_provider
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.max_polls = max_polls
        self.history: list[ReleaseState] = []
        self._candidate_created = False
        self._routing_changed = False
        self._candidate_revision: str | None = None
        self._stable_revision: str | None = None

    def _transition(self, state: ReleaseState) -> None:
        self.history.append(state)

    def _discover_stable(
        self, service: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
    ) -> tuple[str, str | None, str]:
        base_info = _value(service, "BaseInfo")
        if str(_value(base_info, "Status") or "").lower() != "normal":
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE", "CloudRun service is not normal"
            )
        returned_service = str(_value(base_info, "ServerName") or "").strip()
        if returned_service and returned_service != SERVICE_NAME:
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE",
                "CloudRun service detail does not match the controlled target",
            )
        traffic_type = str(_value(base_info, "TrafficType") or "FLOW").upper()
        if traffic_type != "FLOW":
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE",
                "CloudRun service already has non-default routing",
            )
        for record in records:
            status = str(_value(record, "Status") or "").lower()
            if bool(_value(record, "IsReleasing", False)) or status == "deploying":
                raise ReleaseFailure(
                    "STABLE_REVISION_NOT_UNIQUE",
                    "a CloudRun release is already active",
                )
        online = list(_value(service, "OnlineVersionInfos") or [])
        ratios = [_ratio(_value(item, "FlowRatio")) for item in online]
        if any(ratio < 0 or ratio > 100 for ratio in ratios) or sum(ratios) != 100:
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE",
                "online traffic ratios do not form one complete release",
            )
        positive = [
            item for item, ratio in zip(online, ratios, strict=True) if ratio > 0
        ]
        if len(positive) != 1 or _ratio(_value(positive[0], "FlowRatio")) != 100:
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE",
                "service does not have exactly one 100%-traffic stable revision",
            )
        stable_name = str(_value(positive[0], "VersionName") or "").strip()
        if not stable_name or not stable_name.startswith(SERVICE_NAME + "-"):
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE", "stable revision name is missing"
            )
        image_url = str(_value(positive[0], "ImageUrl") or "").strip() or None
        domain = str(_value(base_info, "DefaultDomainName") or "")
        return stable_name, image_url, _normalise_base_url(domain)

    def _read_stable_commit(self, base_url: str) -> str:
        try:
            result = self.probe.get_json(base_url, "/api/v1/system/build-info")
        except Exception as exc:
            raise ReleaseFailure(
                "STABLE_PROVENANCE_UNAVAILABLE",
                "stable build provenance endpoint is unavailable",
            ) from exc
        commit = str(result.payload.get("commit_sha") or "").lower()
        if result.status != 200 or not SHA_RE.fullmatch(commit):
            raise ReleaseFailure(
                "STABLE_PROVENANCE_UNAVAILABLE",
                "stable build provenance is incomplete",
            )
        return commit

    def build_plan(self, release_input: ReleaseInput) -> ReleasePlan:
        try:
            return self._build_plan(release_input)
        except ReleaseFailure:
            if not self.history or self.history[-1] not in {
                ReleaseState.BLOCKED,
                ReleaseState.FAILED,
            }:
                self._transition(ReleaseState.BLOCKED)
            raise
        except Exception as exc:
            self._transition(ReleaseState.BLOCKED)
            raise ReleaseFailure(
                "UNEXPECTED_RELEASE_ERROR",
                f"release planning stopped after {type(exc).__name__}",
            ) from exc

    def _build_plan(self, release_input: ReleaseInput) -> ReleasePlan:
        self._transition(ReleaseState.DISCOVER_STABLE)
        service = self.api.describe_service()
        records = self.api.describe_deploy_records()
        current_task = self.api.describe_manage_task(0)
        current_task_status = str(_value(current_task, "Status") or "").lower()
        if current_task_status in {
            "deploying",
            "pending",
            "processing",
            "running",
            "todo",
        }:
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE",
                "a CloudRun release task is already active",
            )
        stable_name, stable_image, base_url = self._discover_stable(service, records)
        self._stable_revision = stable_name

        self._transition(ReleaseState.READ_STABLE_VERSION)
        stable = self.api.describe_version(stable_name)
        if str(_value(stable, "Status") or "").lower() != "normal":
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE", "stable revision is not normal"
            )
        stable_vpc = VpcConfiguration.from_payload(_value(stable, "VpcConf"))
        stable_vpc.require_baseline()
        expected_db_vpc = (release_input.expected_database_vpc_id or "").strip()
        if expected_db_vpc and stable_vpc.vpc_id != expected_db_vpc:
            raise ReleaseFailure(
                "VPC_DATABASE_MISMATCH",
                "stable revision VPC does not match the read-only database VPC assertion",
            )
        stable_commit = self._read_stable_commit(base_url)
        stable_env = _parse_env_params(_value(stable, "EnvParams"))
        env_json, env_summary = _merge_env_params(
            stable_env, release_input.requested_env_changes
        )

        artifact: DeployArtifact | None
        if release_input.artifact_mode == "image":
            artifact = DeployArtifact(
                deploy_type="image", image_url=release_input.image_url
            )
        elif release_input.artifact_mode == "stable-image":
            if not stable_image:
                raise ReleaseFailure(
                    "DEPLOY_ARTIFACT_INVALID",
                    "stable image is unavailable for a configuration release",
                )
            if not release_input.requested_env_changes:
                raise ReleaseFailure(
                    "ENV_CHANGE_INVALID",
                    "stable-image mode requires an explicit environment change",
                )
            artifact = DeployArtifact(deploy_type="image", image_url=stable_image)
        else:
            artifact = None

        self._transition(ReleaseState.BUILD_PLAN)
        return ReleasePlan(
            env_id=ENV_ID,
            service_name=SERVICE_NAME,
            stable_revision=stable_name,
            stable_runtime_commit=stable_commit,
            desired_runtime_commit=release_input.desired_runtime_commit.lower(),
            stable_vpc_conf=stable_vpc,
            desired_vpc_conf=stable_vpc,
            stable_image_url=stable_image,
            base_url=base_url,
            artifact_mode=release_input.artifact_mode,
            release_type="CONFIG_RELEASE"
            if release_input.artifact_mode == "stable-image"
            else "CODE_RELEASE",
            artifact=artifact,
            env_change_summary=env_summary,
            env_params_json=env_json,
            approval_ref=release_input.approval_ref,
            build_id=release_input.build_id,
            ci_provenance_verified=release_input.ci_provenance_verified,
        )

    def _candidate_name_from_records(
        self,
        before_ids: set[str],
        records: Sequence[Mapping[str, Any]],
    ) -> str | None:
        new_records = [
            item
            for item in records
            if str(_value(item, "DeployId") or "") not in before_ids
        ]
        if not new_records:
            return None
        if len(new_records) != 1:
            raise ReleaseFailure(
                "CANDIDATE_NOT_UNIQUE",
                "more than one new CloudRun deploy record appeared",
                terminal_state=ReleaseState.FAILED,
            )
        deploy_id = str(_value(new_records[0], "DeployId") or "").strip()
        if not deploy_id:
            return None
        if deploy_id.startswith(SERVICE_NAME + "-"):
            return deploy_id
        return f"{SERVICE_NAME}-{deploy_id}"

    def _is_new_candidate_name(self, version_name: str, before_ids: set[str]) -> bool:
        if not version_name or version_name == self._stable_revision:
            return False
        suffix = version_name
        prefix = SERVICE_NAME + "-"
        suffix = suffix.removeprefix(prefix)
        return version_name not in before_ids and suffix not in before_ids

    def _wait_for_candidate(
        self, task_id: int, before_ids: set[str], before_task_id: int | None
    ) -> str:
        candidate_name: str | None = None
        failed_statuses = {"failed", "deploy_failed", "build_failed", "create_failed"}
        for _ in range(self.max_polls):
            task = self.api.describe_manage_task(task_id)
            if task:
                task_status = str(_value(task, "Status") or "").lower()
                task_version = str(_value(task, "VersionName") or "").strip()
                current_task_id = int(_value(task, "Id") or 0)
                is_new_task = (
                    task_id > 0
                    or before_task_id is None
                    or current_task_id != before_task_id
                    or self._is_new_candidate_name(task_version, before_ids)
                )
                if is_new_task and task_status in failed_statuses:
                    raise ReleaseFailure(
                        "CANDIDATE_CREATE_FAILED",
                        "CloudRun candidate task failed",
                        terminal_state=ReleaseState.FAILED,
                    )
                if is_new_task and self._is_new_candidate_name(
                    task_version, before_ids
                ):
                    candidate_name = task_version
            if not candidate_name:
                candidate_name = self._candidate_name_from_records(
                    before_ids, self.api.describe_deploy_records()
                )
            if candidate_name:
                detail = self.api.describe_version(candidate_name)
                status = str(_value(detail, "Status") or "").lower()
                if status == "normal":
                    return candidate_name
                if status in failed_statuses:
                    raise ReleaseFailure(
                        "CANDIDATE_CREATE_FAILED",
                        "CloudRun candidate version failed",
                        terminal_state=ReleaseState.FAILED,
                    )
            self.sleep(self.poll_seconds)
        raise ReleaseFailure(
            "CANDIDATE_NOT_READY",
            "CloudRun candidate did not become normal before timeout",
            terminal_state=ReleaseState.FAILED,
        )

    def _assert_candidate_vpc(
        self, candidate: Mapping[str, Any], expected: VpcConfiguration
    ) -> None:
        actual = VpcConfiguration.from_payload(_value(candidate, "VpcConf"))
        if actual != expected:
            raise ReleaseFailure(
                "CANDIDATE_VPC_MISMATCH",
                "candidate VpcConf does not exactly match the stable revision",
                terminal_state=ReleaseState.FAILED,
            )

    def _assert_candidate_health(
        self,
        plan: ReleasePlan,
        candidate_revision: str,
        token: str,
    ) -> None:
        query = {CANARY_QUERY_KEY: token}
        try:
            build = self.probe.get_json(
                plan.base_url, "/api/v1/system/build-info", query=query
            )
        except Exception as exc:
            raise ReleaseFailure(
                "CANDIDATE_PROVENANCE_MISMATCH",
                "targeted candidate build provenance is unavailable",
                terminal_state=ReleaseState.FAILED,
            ) from exc
        if (
            build.status != 200
            or str(build.payload.get("commit_sha") or "").lower()
            != plan.desired_runtime_commit
        ):
            raise ReleaseFailure(
                "CANDIDATE_PROVENANCE_MISMATCH",
                "targeted candidate commit does not match the approved commit",
                terminal_state=ReleaseState.FAILED,
            )
        try:
            live = self.probe.get_json(plan.base_url, "/health/live", query=query)
        except Exception as exc:
            raise ReleaseFailure(
                "CANDIDATE_LIVENESS_NOT_READY",
                "targeted candidate liveness check failed",
                terminal_state=ReleaseState.FAILED,
            ) from exc
        if live.status != 200 or live.payload.get("status") != "ok":
            raise ReleaseFailure(
                "CANDIDATE_LIVENESS_NOT_READY",
                "targeted candidate liveness check failed",
                terminal_state=ReleaseState.FAILED,
            )
        for _ in range(20):
            try:
                health = self.probe.get_json(
                    plan.base_url, "/api/v1/health", query=query
                )
            except Exception as exc:
                raise ReleaseFailure(
                    "CANDIDATE_DATABASE_NOT_READY",
                    "targeted candidate database health did not pass 20/20",
                    terminal_state=ReleaseState.FAILED,
                ) from exc
            if health.status != 200 or health.payload.get("status") != "ok":
                raise ReleaseFailure(
                    "CANDIDATE_DATABASE_NOT_READY",
                    "targeted candidate database health did not pass 20/20",
                    terminal_state=ReleaseState.FAILED,
                )
        final_build = self.probe.get_json(
            plan.base_url, "/api/v1/system/build-info", query=query
        )
        if (
            final_build.status != 200
            or str(final_build.payload.get("commit_sha") or "").lower()
            != plan.desired_runtime_commit
        ):
            raise ReleaseFailure(
                "CANDIDATE_PROVENANCE_MISMATCH",
                "candidate identity changed during targeted health",
                terminal_state=ReleaseState.FAILED,
            )

    def _restore_stable_route(self) -> None:
        if self._routing_changed and self._stable_revision and self._candidate_revision:
            self.api.release_flow(self._stable_revision, self._candidate_revision, 0)
            self._routing_changed = False

    def _best_effort_restore_stable_route(self) -> bool:
        try:
            self._restore_stable_route()
        except Exception:  # noqa: BLE001 - never mask the original release failure
            return False
        return True

    def execute(
        self,
        plan: ReleasePlan,
        *,
        promotion: str = "ready",
        gray_percent: int = 5,
    ) -> dict[str, Any]:
        if not plan.approval_ref:
            raise ReleaseFailure(
                "APPROVAL_REFERENCE_MISSING",
                "live release requires a bounded approval reference",
            )
        if not plan.ci_provenance_verified:
            raise ReleaseFailure(
                "CI_PROVENANCE_MISSING",
                "live release requires a successful CI release manifest for the commit",
            )
        if promotion not in {"ready", "gray", "full"}:
            raise ReleaseFailure("PROMOTION_INVALID", "unsupported promotion mode")
        if not 1 <= gray_percent <= 99:
            raise ReleaseFailure("GRAY_RATIO_INVALID", "gray ratio must be 1..99")

        artifact = plan.artifact
        if artifact is None:
            if self.source_provider is None:
                raise ReleaseFailure(
                    "SOURCE_PROVIDER_MISSING", "source package provider is unavailable"
                )
            artifact = self.source_provider.prepare(plan)
        artifact.validate_for_update()

        # Source upload can take long enough for the control-plane baseline to
        # change. Re-read it immediately before the first service write.
        current_service = self.api.describe_service()
        before_records = self.api.describe_deploy_records()
        current_stable, _, _ = self._discover_stable(current_service, before_records)
        if current_stable != plan.stable_revision:
            raise ReleaseFailure(
                "STABLE_REVISION_CHANGED",
                "stable revision changed after the release plan was built",
            )
        current_stable_detail = self.api.describe_version(current_stable)
        current_vpc = VpcConfiguration.from_payload(
            _value(current_stable_detail, "VpcConf")
        )
        current_vpc.require_baseline()
        if current_vpc != plan.stable_vpc_conf:
            raise ReleaseFailure(
                "STABLE_VPC_CHANGED",
                "stable revision VpcConf changed after the release plan was built",
            )
        before_task = self.api.describe_manage_task(0)
        before_task_status = str(_value(before_task, "Status") or "").lower()
        if before_task_status in {
            "deploying",
            "pending",
            "processing",
            "running",
            "todo",
        }:
            raise ReleaseFailure(
                "STABLE_REVISION_NOT_UNIQUE",
                "a CloudRun release task became active before candidate creation",
            )
        before_ids = {
            str(_value(record, "DeployId") or "") for record in before_records
        }
        before_task_id = (
            int(_value(before_task, "Id") or 0) if before_task is not None else None
        )
        remark = (
            f"{plan.approval_ref}; commit={plan.desired_runtime_commit[:12]}; "
            f"stable={plan.stable_revision}"
        )
        spec = UpdateRequestSpec(
            artifact=artifact,
            vpc_conf=plan.desired_vpc_conf,
            env_params_json=plan.env_params_json,
            deploy_remark=remark,
        )

        try:
            self._transition(ReleaseState.CREATE_CANDIDATE)
            self._candidate_created = True
            task_id = self.api.create_candidate(spec)
            candidate_name = self._wait_for_candidate(
                task_id, before_ids, before_task_id
            )
            self._candidate_revision = candidate_name

            self._transition(ReleaseState.VERIFY_CANDIDATE_CONFIG)
            candidate = self.api.describe_version(candidate_name)
            self._assert_candidate_vpc(candidate, plan.desired_vpc_conf)

            self._transition(ReleaseState.TARGETED_HEALTH)
            token = secrets.token_urlsafe(32)
            # Treat an uncertain API response as potentially applied.  A
            # cleanup attempt is safe even when the targeted route was not
            # committed, while omitting cleanup could leave a test route live.
            self._routing_changed = True
            self.api.release_targeted(plan.stable_revision, candidate_name, token)
            self._assert_candidate_health(plan, candidate_name, token)

            self._transition(ReleaseState.READY_FOR_RELEASE)
            if promotion == "ready":
                self._restore_stable_route()
            else:
                self._transition(ReleaseState.GRAY)
                self._routing_changed = True
                self.api.release_flow(
                    plan.stable_revision, candidate_name, gray_percent
                )
                if promotion == "full":
                    self._transition(ReleaseState.FULL)
                    self.api.release_flow(plan.stable_revision, candidate_name, 100)
                    full_build = self.probe.get_json(
                        plan.base_url, "/api/v1/system/build-info"
                    )
                    full_health = self.probe.get_json(plan.base_url, "/api/v1/health")
                    if (
                        full_build.status != 200
                        or str(full_build.payload.get("commit_sha") or "").lower()
                        != plan.desired_runtime_commit
                        or full_health.status != 200
                        or full_health.payload.get("status") != "ok"
                    ):
                        raise ReleaseFailure(
                            "FULL_RELEASE_VERIFICATION_FAILED",
                            "full release verification failed",
                            terminal_state=ReleaseState.FAILED,
                        )
                    self._routing_changed = False
                    self._transition(ReleaseState.VERIFIED)
            return {
                "state": self.history[-1].value,
                "stable_revision": plan.stable_revision,
                "candidate_revision": candidate_name,
                "desired_runtime_commit": plan.desired_runtime_commit,
                "candidate_vpc_fingerprint": plan.desired_vpc_conf.fingerprint,
                "targeted_database_health": "20/20",
                "promotion": promotion,
                "history": [state.value for state in self.history],
            }
        except ReleaseFailure as exc:
            if self._routing_changed:
                self._best_effort_restore_stable_route()
            self._transition(
                exc.terminal_state if self._candidate_created else ReleaseState.BLOCKED
            )
            raise
        except Exception as exc:
            if self._routing_changed:
                self._best_effort_restore_stable_route()
            self._transition(
                ReleaseState.FAILED if self._candidate_created else ReleaseState.BLOCKED
            )
            raise ReleaseFailure(
                "UNEXPECTED_RELEASE_ERROR",
                f"release stopped after an unexpected {type(exc).__name__}",
                terminal_state=self.history[-1],
            ) from exc


def _load_env_changes(path: Path | None) -> dict[str, str | None]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseFailure(
            "ENV_CHANGE_INVALID", "environment change file is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ReleaseFailure(
            "ENV_CHANGE_INVALID", "environment change file must be a JSON object"
        )
    result: dict[str, str | None] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or (
            value is not None and not isinstance(value, str)
        ):
            raise ReleaseFailure(
                "ENV_CHANGE_INVALID",
                "environment change keys/values must be strings; null removes a key",
            )
        result[key] = value
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="approved 40-character Git SHA")
    artifact = parser.add_mutually_exclusive_group(required=True)
    artifact.add_argument(
        "--source", action="store_true", help="package clean main source"
    )
    artifact.add_argument("--image-url", help="prebuilt immutable image URL")
    artifact.add_argument(
        "--reuse-stable-image",
        action="store_true",
        help="configuration release using the current stable image",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--expected-database-vpc-id")
    parser.add_argument("--approval-ref")
    parser.add_argument("--build-id")
    parser.add_argument(
        "--release-manifest",
        type=Path,
        help="downloaded release-provenance manifest from the 9/9 main CI run",
    )
    parser.add_argument(
        "--promotion", choices=("ready", "gray", "full"), default="ready"
    )
    parser.add_argument("--gray-percent", type=int, default=5)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-polls", type=int, default=120)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.poll_seconds <= 0 or args.max_polls <= 0:
            raise ReleaseFailure("POLL_POLICY_INVALID", "poll timing must be positive")
        changes = _load_env_changes(args.env_file)
        ci_provenance_verified = False
        if args.release_manifest is not None:
            from build_provenance import validate_manifest

            try:
                manifest = validate_manifest(args.release_manifest.resolve())
            except RuntimeError as exc:
                raise ReleaseFailure(
                    "CI_PROVENANCE_INVALID",
                    "release provenance manifest did not pass validation",
                ) from exc
            if str(manifest.get("release_commit") or "").lower() != args.commit.lower():
                raise ReleaseFailure(
                    "CI_PROVENANCE_INVALID",
                    "release provenance commit does not match --commit",
                )
            ci_provenance_verified = True
        if args.execute and not ci_provenance_verified:
            raise ReleaseFailure(
                "CI_PROVENANCE_MISSING",
                "--execute requires --release-manifest from the successful main CI run",
            )
        artifact_mode = (
            "source"
            if args.source
            else "stable-image"
            if args.reuse_stable_image
            else "image"
        )
        release_input = ReleaseInput(
            desired_runtime_commit=args.commit,
            artifact_mode=artifact_mode,
            requested_env_changes=changes,
            approval_ref=args.approval_ref,
            image_url=args.image_url,
            expected_database_vpc_id=args.expected_database_vpc_id,
            build_id=args.build_id,
            ci_provenance_verified=ci_provenance_verified,
        )
        api = TencentCloudSdkApi()
        probe = UrlLibProbe()
        repo_root = Path(__file__).resolve().parents[1]
        source_provider = GitSourcePackageProvider(api, repo_root)
        controller = CloudRunReleaseController(
            api,
            probe,
            source_provider=source_provider,
            poll_seconds=args.poll_seconds,
            max_polls=args.max_polls,
        )
        plan = controller.build_plan(release_input)
        if args.dry_run:
            safe_plan = plan.safe_dict()
            safe_plan["release_strategy"]["requested_promotion"] = args.promotion
            print(json.dumps(safe_plan, ensure_ascii=False, indent=2))
            return 0
        result = controller.execute(
            plan, promotion=args.promotion, gray_percent=args.gray_percent
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ReleaseFailure as exc:
        print(
            json.dumps(
                {
                    "state": exc.terminal_state.value,
                    "error": exc.code,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - sanitized CLI boundary
        # Do not echo SDK request payloads, signed upload URLs, or environment
        # values from an unexpected exception.
        print(
            json.dumps(
                {
                    "state": ReleaseState.BLOCKED.value,
                    "error": "UNEXPECTED_RELEASE_ERROR",
                    "message": f"release stopped after {type(exc).__name__}",
                }
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
