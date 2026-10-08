"""Narrow, independently approved CloudRun write transport for R3.

This is a transport building block, not the live lifecycle controller. There is
no CLI, source upload, full rollout, APPLY, stop/delete, or automatic cleanup.
The future controller must verify fresh evidence and reserve its lifecycle
transition before calling this separately budgeted transport. A response never
proves candidate readiness, routing, or closure.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from r3_authorization import verify_authorization
from r3_cloud_read import ENV_ID, REGION, SCOPE, SERVICE, VPC_KEYS, revision_name
from r3_read_evidence import ReadFailure, canonical, fingerprint, safe_request_id


CREATE = "CREATE_ENABLE_CANDIDATE"
TARGET = "TARGET_CANDIDATE"
RESTORE = "RESTORE_STABLE_FLOW"
G5 = "G5_4_PRODUCTION_RULE_APPLY_ENABLED"
FIELDS = frozenset({
    "schema_version", "purpose", "scope", "controller_commit", "approval_ref",
    "approved_by", "realm", "not_before", "expires_at", "operation_id", "action",
    "request_fingerprint", "journal_fingerprint", "max_attempts",
})
BEFORE_GATES = {
    "APP_ENV": "production", "DEPLOYMENT_READ_ONLY": "false",
    "ALLOW_PRODUCTION_MUTATIONS": "true", G5: "false",
    "LEARNING_CREDIT_SETTLEMENT_ENABLED": "false", "RUN_BOOTSTRAP_ON_STARTUP": "false",
}


@dataclass(frozen=True)
class CloudWritePlan:
    action: str
    stable_revision: str
    candidate_revision: str | None
    # Never let repr, a summary or a journal expose environment/route secrets.
    request_json: str = field(repr=False)
    before_env_json: str | None = field(default=None, repr=False)

    def binding(self, key):
        if not isinstance(key, bytes) or len(key) < 32:
            raise ReadFailure("R3_REQUEST_BINDING_KEY_REQUIRED")
        return hmac.new(key, canonical({
            "action": self.action, "stable": self.stable_revision,
            "candidate": self.candidate_revision, "request": json.loads(self.request_json),
            "before_env": json.loads(self.before_env_json) if self.before_env_json else None,
        }), hashlib.sha256).hexdigest()

    def summary(self):
        return {"action": self.action, "stable_revision": self.stable_revision,
                "candidate_revision": self.candidate_revision,
                "max_write_attempts": 1, "lifecycle_verified": False,
                "production_ready": False}


def _vpc(value):
    if not isinstance(value, dict) or set(value) != set(VPC_KEYS):
        raise ReadFailure("R3_VERSION_VPC_REQUIRED")
    if not re.fullmatch(r"vpc-[a-z0-9]+", str(value["VpcId"])) or not re.fullmatch(r"subnet-[a-z0-9]+", str(value["SubnetId"])):
        raise ReadFailure("R3_VERSION_VPC_REQUIRED")
    try:
        network = ipaddress.ip_network(value["VpcCIDR"], strict=True)
        subnet = ipaddress.ip_network(value["SubnetCIDR"], strict=True)
        if network.version != 4 or subnet.version != 4 or not subnet.subnet_of(network):
            raise ValueError()
    except (ValueError, TypeError):
        raise ReadFailure("R3_VERSION_VPC_REQUIRED") from None


def enable_candidate_plan(stable_revision, *, image_url, stable_env, version_vpc):
    """Plan only. Inputs must later be bound to the real stable observation."""
    from deploy_cloudrun_api import DeployArtifact, UpdateRequestSpec, VpcConfiguration, build_update_request

    revision_name(stable_revision)
    if not isinstance(image_url, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]+)?/[a-zA-Z0-9/_.-]+@sha256:[a-f0-9]{64}", image_url):
        raise ReadFailure("R3_IMMUTABLE_IMAGE_REQUIRED")
    if not isinstance(stable_env, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or not isinstance(v, str) for k, v in stable_env.items()):
        raise ReadFailure("R3_ENVIRONMENT_INVALID")
    if any(stable_env.get(k) != v for k, v in BEFORE_GATES.items()):
        raise ReadFailure("R3_STABLE_GATES_NOT_VERIFIED")
    _vpc(version_vpc)
    after = {**stable_env, G5: "true"}
    request = build_update_request(UpdateRequestSpec(
        artifact=DeployArtifact("image", image_url=image_url),
        vpc_conf=VpcConfiguration(version_vpc["VpcId"], version_vpc["VpcCIDR"], version_vpc["SubnetId"], version_vpc["SubnetCIDR"]),
        env_params_json=json.dumps(after, ensure_ascii=False, separators=(",", ":")),
        deploy_remark="R3 independently approved temporary candidate",
    ))
    return CloudWritePlan(CREATE, stable_revision, None, request.to_json_string(), json.dumps(stable_env, ensure_ascii=False))


def target_candidate_plan(stable_revision, candidate_revision, token):
    from deploy_cloudrun_api import build_targeted_release_request

    revision_name(stable_revision)
    revision_name(candidate_revision)
    if candidate_revision == stable_revision or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ReadFailure("R3_TARGET_IDENTITY_INVALID")
    request = build_targeted_release_request(stable_revision, candidate_revision, token)
    return CloudWritePlan(TARGET, stable_revision, candidate_revision, request.to_json_string())


def restore_stable_plan(stable_revision, candidate_revision):
    from deploy_cloudrun_api import build_flow_release_request

    revision_name(stable_revision)
    revision_name(candidate_revision)
    if candidate_revision == stable_revision:
        raise ReadFailure("R3_TARGET_IDENTITY_INVALID")
    return CloudWritePlan(RESTORE, stable_revision, candidate_revision,
                          build_flow_release_request(stable_revision, candidate_revision, 0).to_json_string())


def validate_plan(plan):
    """Reconstruct the exact fixed shape, rejecting forged or widened plans."""
    if type(plan) is not CloudWritePlan:
        raise ReadFailure("R3_FIXED_WRITE_PLAN_REQUIRED")
    try:
        body = json.loads(plan.request_json)
        if plan.action == CREATE:
            items = body["Items"]
            if len(items) != 2 or [i["Key"] for i in items] != ["VpcConf", "EnvParam"]:
                raise ValueError()
            expected = enable_candidate_plan(plan.stable_revision,
                image_url=body["DeployInfo"]["ImageUrl"],
                stable_env=json.loads(plan.before_env_json), version_vpc=items[0]["VpcConf"])
        elif plan.action == TARGET:
            expected = target_candidate_plan(plan.stable_revision, plan.candidate_revision,
                                              body["VersionFlowItems"][1]["UrlParam"]["Value"])
        elif plan.action == RESTORE:
            expected = restore_stable_plan(plan.stable_revision, plan.candidate_revision)
        else:
            raise ValueError()
        if (json.loads(expected.request_json) != body or expected.candidate_revision != plan.candidate_revision
                or expected.before_env_json != plan.before_env_json):
            raise ValueError()
        return body
    except ReadFailure:
        raise
    except (ValueError, TypeError, KeyError, IndexError):
        raise ReadFailure("R3_WRITE_PLAN_WIDENED") from None


class WriteAttemptJournal:
    """Independent durable per-operation/action budget, spent before dispatch."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.is_symlink():
            raise ReadFailure("R3_WRITE_JOURNAL_LINK_DISALLOWED")
        existed = self.path.exists()
        try:
            with self._db() as db:
                if existed:
                    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if db.execute("PRAGMA user_version").fetchone()[0] != 1 or tables != {"write_attempts"}:
                        raise ReadFailure("R3_WRITE_JOURNAL_CORRUPT")
                else:
                    db.execute("PRAGMA user_version=1")
                    db.execute("CREATE TABLE write_attempts(operation_id TEXT NOT NULL,action TEXT NOT NULL,approval_ref TEXT UNIQUE NOT NULL,grant_fingerprint TEXT NOT NULL,started_at REAL NOT NULL,outcome TEXT NOT NULL,request_id TEXT,task_id INTEGER,PRIMARY KEY(operation_id,action))")
        except sqlite3.Error:
            raise ReadFailure("R3_WRITE_JOURNAL_UNAVAILABLE") from None

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ReadFailure("R3_WRITE_JOURNAL_CORRUPT")
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, grant, now):
        if grant["journal_fingerprint"] != fingerprint(self.path.resolve().as_posix()):
            raise ReadFailure("R3_WRITE_JOURNAL_TARGET_MISMATCH")
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO write_attempts VALUES(?,?,?,?,?,'UNKNOWN',NULL,NULL)",
                    (grant["operation_id"], grant["action"], grant["approval_ref"], fingerprint(grant), now))
        except sqlite3.IntegrityError:
            raise ReadFailure("R3_WRITE_REPLAY_DISALLOWED") from None
        except sqlite3.Error:
            raise ReadFailure("R3_WRITE_JOURNAL_UNAVAILABLE") from None

    def response_received(self, grant, request_id, task_id):
        try:
            with self._db() as db:
                changed = db.execute("UPDATE write_attempts SET outcome='RESPONSE_RECEIVED',request_id=?,task_id=? WHERE operation_id=? AND action=? AND outcome='UNKNOWN'",
                    (request_id, task_id, grant["operation_id"], grant["action"])).rowcount
                if changed != 1:
                    raise ReadFailure("R3_WRITE_JOURNAL_CORRUPT")
        except sqlite3.Error:
            raise ReadFailure("R3_WRITE_JOURNAL_UNAVAILABLE") from None


@dataclass(frozen=True)
class _WritePorts:
    create: object
    target: object
    restore: object


def _sdk_write_ports(secret_id, secret_key, token=None):
    from tencentcloud.common.credential import Credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.common.retry import NoopRetryer
    from tencentcloud.tcbr.v20220217 import models
    from tencentcloud.tcbr.v20220217.tcbr_client import TcbrClient

    if not all(isinstance(x, str) and x.strip() for x in (secret_id, secret_key)):
        raise ReadFailure("R3_WRITE_CREDENTIALS_REQUIRED")
    client = TcbrClient(Credential(secret_id, secret_key, token), REGION,
        ClientProfile(httpProfile=HttpProfile(endpoint="tcbr.tencentcloudapi.com", reqTimeout=10), retryer=NoopRetryer()))

    def create(plan):
        request = models.UpdateCloudRunServerRequest()
        request.from_json_string(plan.request_json)
        return client.UpdateCloudRunServer(request)

    def target(plan):
        request = models.ReleaseGrayRequest()
        request.from_json_string(plan.request_json)
        return client.ReleaseGray(request)

    def restore(plan):
        request = models.ReleaseGrayRequest()
        request.from_json_string(plan.request_json)
        return client.ReleaseGray(request)

    return _WritePorts(create, target, restore)


class CloudWriteTransport:
    def __init__(self, ports):
        if type(ports) is not _WritePorts:
            raise ReadFailure("R3_WRITE_CAPABILITIES_REQUIRED")
        self.__ports = ports

    @classmethod
    def authenticated(cls, secret_id, secret_key, token=None):
        return cls(_sdk_write_ports(secret_id, secret_key, token))

    def _send(self, action, plan, document, *, authorization_key, binding_key, commit, journal, clock):
        validate_plan(plan)
        grant = verify_authorization(document, key=authorization_key,
            purpose="R3_CLOUD_WRITE_CAPABILITY", scope=SCOPE, commit=commit, now=clock())
        if (set(grant) != FIELDS or grant["action"] != action or plan.action != action
                or type(grant["max_attempts"]) is not int or grant["max_attempts"] != 1
                or not isinstance(grant["operation_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", grant["operation_id"])
                or any(not isinstance(grant[k], str) or not re.fullmatch(r"[a-f0-9]{64}", grant[k]) for k in ("request_fingerprint", "journal_fingerprint"))
                or not hmac.compare_digest(plan.binding(binding_key), grant["request_fingerprint"])):
            raise ReadFailure("R3_WRITE_AUTHORIZATION_SCOPE_INVALID")
        if authorization_key == binding_key or type(journal) is not WriteAttemptJournal:
            raise ReadFailure("R3_WRITE_INDEPENDENT_APPROVAL_REQUIRED")
        journal.reserve(grant, clock())
        try:
            # Reserve is durable before the first byte of this single SDK call.
            if not grant["not_before"] <= clock() < grant["expires_at"]:
                raise ReadFailure("AUTHORIZATION_EXPIRED_OR_FUTURE")
            call = {CREATE: self.__ports.create, TARGET: self.__ports.target, RESTORE: self.__ports.restore}[action]
            response = json.loads(call(plan).to_json_string())
            if not isinstance(response, dict) or set(response) != ({"RequestId", "TaskId"} if action == CREATE else {"RequestId"}):
                raise ValueError()
            request_id = safe_request_id(response.get("RequestId"))
            task_id = response.get("TaskId") if action == CREATE else None
            if action == CREATE and (type(task_id) is not int or task_id <= 0):
                raise ValueError()
            journal.response_received(grant, request_id, task_id)
            return {**plan.summary(), "result": "RESPONSE_RECEIVED", "request_id": request_id,
                    "task_id": task_id, "approval_ref": grant["approval_ref"], "replay_allowed": False}
        except Exception:
            # Even a successful cloud response with a failed local save is unknown.
            raise ReadFailure("R3_CLOUD_WRITE_OUTCOME_UNKNOWN") from None

    def create_enable_candidate(self, plan, document, **kwargs):
        return self._send(CREATE, plan, document, clock=kwargs.pop("clock", time.time), **kwargs)

    def target_candidate(self, plan, document, **kwargs):
        return self._send(TARGET, plan, document, clock=kwargs.pop("clock", time.time), **kwargs)

    def restore_stable_flow(self, plan, document, **kwargs):
        return self._send(RESTORE, plan, document, clock=kwargs.pop("clock", time.time), **kwargs)
