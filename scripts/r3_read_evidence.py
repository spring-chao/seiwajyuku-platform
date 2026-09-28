"""Phase 3 observation contracts. No controller, journal or action reservation.

HMAC binds exported observations to an externally trusted collector key. It is
not a Tencent signature or protection against arbitrary code on the collector
host. Fixture issuers use a distinct realm and cannot validate as live evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass


class ReadFailure(RuntimeError):
    """Only fixed error codes and safe trace metadata; never raw exceptions."""

    def __init__(self, code, *, request_id=None, error_code=None):
        super().__init__(code)
        self.code = code
        self.request_id = safe_request_id(request_id, required=False)
        self.attempt_count = 1
        self.error_code = (
            error_code
            if isinstance(error_code, str)
            and re.fullmatch(r"[A-Za-z0-9_.]{1,100}", error_code)
            else None
        )


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_request_id(value, *, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9-]{8,80}", value):
        raise ValueError("REQUEST_ID_INVALID")
    return value


SECRET = re.compile(
    r"secret|token|password|credential|cookie|authorization|jwt|database_url|webshell",
    re.IGNORECASE,
)


def redact(value):
    """Redact before hashing; unknown strings omitted, not logged verbatim."""
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if SECRET.search(str(k))
            or k in {"EnvParams", "TrafficTypeValues", "UrlParam", "FlowParams"}
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, str):
        if "://" in value or "Bearer " in value or "=" in value:
            return "[REDACTED_STRING]"
        return value
    if value is None or type(value) in (bool, int, float):
        return value
    raise ReadFailure("RESPONSE_TYPE_INVALID")


@dataclass(frozen=True)
class Observation:
    kind: str
    source: str
    subject: str
    observed_at: float
    expires_at: float
    raw_fingerprint: str
    request_id: str | None
    attempt_count: int
    payload_json: str
    realm: str
    seal: str

    def payload(self):
        return json.loads(self.payload_json)


KINDS = frozenset(
    {
        "ServiceEvidence",
        "RevisionEvidence",
        "TrafficEvidence",
        "ReleaseEvidence",
        "ManageTaskEvidence",
        "RuntimeEvidence",
        "DatabaseBaselineEvidence",
        "PodEvidence",
    }
)


class ServiceEvidence(Observation):
    pass


class RevisionEvidence(Observation):
    pass


class TrafficEvidence(Observation):
    pass


class ReleaseEvidence(Observation):
    pass


class ManageTaskEvidence(Observation):
    pass


class RuntimeEvidence(Observation):
    pass


class DatabaseBaselineEvidence(Observation):
    pass


class PodEvidence(Observation):
    pass


TYPES = {
    t.__name__: t
    for t in (
        ServiceEvidence,
        RevisionEvidence,
        TrafficEvidence,
        ReleaseEvidence,
        ManageTaskEvidence,
        RuntimeEvidence,
        DatabaseBaselineEvidence,
        PodEvidence,
    )
}

SOURCES = {
    "ServiceEvidence": {"tencent:DescribeCloudRunServerDetail"},
    "RevisionEvidence": {"tencent:DescribeVersionDetail"},
    "TrafficEvidence": {"tencent:DescribeCloudRunServerDetail+DescribeReleaseOrder"},
    "ReleaseEvidence": {"tencent:DescribeReleaseOrder"},
    "ManageTaskEvidence": {"tencent:DescribeServerManageTask"},
    "PodEvidence": {"tencent:DescribeCloudRunPodList"},
    "RuntimeEvidence": {
        "runtime-http:/api/v1/system/build-info",
        "runtime-http:/health/live",
        "runtime-http:/api/v1/health",
    },
    "DatabaseBaselineEvidence": {"mysql-readonly:R3_BASELINE"},
}


class _Issuer:
    """Internal adapter capability; not a CLI or public raw-evidence importer."""

    def __init__(self, key, *, realm="live", clock=time.time):
        if (
            not isinstance(key, bytes)
            or len(key) < 32
            or realm not in {"live", "fixture"}
        ):
            raise ReadFailure("COLLECTOR_KEY_INVALID")
        self.__key, self.realm, self.clock = key, realm, clock

    def _mac(self, value):
        return hmac.new(self.__key, canonical(value), hashlib.sha256).hexdigest()

    def _secret_digest(self, value):
        # Keyed comparison prevents dictionary attacks against low-entropy env values.
        return self._mac({"env_comparison": value})

    def _issue(self, kind, source, subject, raw, payload, started, request_id=None):
        ended = self.clock()
        if kind not in KINDS or not 0 <= ended - started <= 30:
            raise ReadFailure("SNAPSHOT_NOT_COHERENT")
        record = {
            "kind": kind,
            "source": source,
            "subject": subject,
            "observed_at": started,
            "expires_at": started + 60,
            "raw_fingerprint": fingerprint(redact(raw)),
            "request_id": request_id,
            "attempt_count": 1,
            "payload_json": canonical(payload).decode(),
            "realm": self.realm,
        }
        return TYPES[kind](**record, seal=self._mac(record))

    def bundle(self, scope, revision, observations, started):
        ended = self.clock()
        validate_window(observations, started, ended)
        result = {
            "schema_version": 1,
            "collection_id": str(uuid.uuid4()),
            "service": scope,
            "baseline_revision": revision,
            "collected_at_start": started,
            "collected_at_end": ended,
            "evidence": [asdict(x) for x in observations],
            "realm": self.realm,
            "production_ready": False,
        }
        result["bundle_fingerprint"] = fingerprint(result)
        result["seal"] = self._mac(result)
        return result

    def _bind_runtime(self, records, flow):
        result = []
        for record in records:
            item = asdict(record)
            item.pop("seal")
            payload = record.payload()
            payload["identity_basis"] = "BRACKETED_FLOW_100"
            payload["route_proof"] = flow.raw_fingerprint
            item["payload_json"] = canonical(payload).decode()
            result.append(RuntimeEvidence(**item, seal=self._mac(item)))
        return result


def validate_window(observations, start, end):
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end)):
        raise ReadFailure("EVIDENCE_TIME_INVALID")
    if not observations or not 0 <= end - start <= 30:
        raise ReadFailure("SNAPSHOT_NOT_COHERENT")
    for x in observations:
        if not start <= x.observed_at <= end < x.expires_at:
            raise ReadFailure("SNAPSHOT_NOT_COHERENT")


def verify_bundle(
    bundle, *, key, expected_scope, expected_revision, now=None, expected_realm="live"
):
    """Verify offline only. Does not convert to fixture evidence or advance R3."""
    now = time.time() if now is None else now
    data = dict(bundle)
    seal = data.pop("seal", "")
    issuer = _Issuer(key, realm=expected_realm)
    if not hmac.compare_digest(seal, issuer._mac(data)):
        raise ReadFailure("BUNDLE_AUTHENTICITY_FAILED")
    claimed = data.pop("bundle_fingerprint", None)
    if claimed != fingerprint(data):
        raise ReadFailure("BUNDLE_FINGERPRINT_MISMATCH")
    if (
        data["realm"] != expected_realm
        or data["service"] != expected_scope
        or data["baseline_revision"] != expected_revision
        or data["schema_version"] != 1
        or data["production_ready"] is not False
    ):
        raise ReadFailure("BUNDLE_IDENTITY_MISMATCH")
    records = []
    for item in data["evidence"]:
        r = dict(item)
        signature = r.pop("seal")
        if not hmac.compare_digest(signature, issuer._mac(r)):
            raise ReadFailure("EVIDENCE_TAMPERED")
        if (
            r["realm"] != expected_realm
            or r["kind"] not in KINDS
            or r["source"] not in SOURCES[r["kind"]]
            or not r["observed_at"] <= now < r["expires_at"]
            or r["expires_at"] - r["observed_at"] != 60
            or r["attempt_count"] != 1
        ):
            raise ReadFailure("EVIDENCE_INVALID_OR_EXPIRED")
        records.append(TYPES[r["kind"]](**item))
    validate_window(records, data["collected_at_start"], data["collected_at_end"])
    for record in records:
        payload = record.payload()
        if record.kind in {"ServiceEvidence", "TrafficEvidence"}:
            subject = expected_scope
        elif record.kind in {"RevisionEvidence", "PodEvidence", "RuntimeEvidence"}:
            subject = f"{expected_scope}/{payload.get('revision')}"
        elif record.kind == "ReleaseEvidence":
            subject = f"{expected_scope}/release/{payload.get('order_id')}"
        elif record.kind == "ManageTaskEvidence":
            subject = f"{expected_scope}/task/{payload.get('task_id')}"
        else:
            subject = expected_scope
        if record.subject != subject:
            raise ReadFailure("EVIDENCE_SUBJECT_MISMATCH")
        if record.kind == "RuntimeEvidence":
            proofs = [
                r
                for r in records
                if r.kind == "TrafficEvidence"
                and r.raw_fingerprint == payload.get("route_proof")
            ]
            if (
                payload.get("revision") != expected_revision
                or payload.get("identity_basis") != "BRACKETED_FLOW_100"
                or len(proofs) != 1
                or proofs[0].payload().get("stable_revision") != expected_revision
            ):
                raise ReadFailure("RUNTIME_IDENTITY_NOT_PROVEN")
    return tuple(records)
