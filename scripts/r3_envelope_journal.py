"""Local-only persistence and fixture evidence for the R3 rehearsal controller.

No cloud SDK, network client, production transport or production CLI exists.
Fingerprints detect accidental corruption, not malicious journal modification.
Use one canonical, access-controlled journal path for a service/operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from r3_execution_envelope import Baseline, Envelope, GateError, State, Traffic


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).timestamp()


@dataclass(frozen=True)
class Evidence:
    source: str
    observed_at: float
    expires_at: float
    subject: str
    raw_fingerprint: str
    payload_json: str

    @classmethod
    def fixture(cls, subject, payload, *, now=None, ttl=60):
        now = utcnow() if now is None else now
        return cls(
            "fixture:r3-phase2",
            now,
            now + ttl,
            subject,
            digest(payload),
            json.dumps(payload, sort_keys=True),
        )

    def read(self, subject, now):
        # Only fixture sources in Phase 2. Real evidence must have a separate,
        # reviewed authenticated adapter; callers cannot label it production.
        if self.source != "fixture:r3-phase2" or self.subject != subject:
            raise GateError("EVIDENCE_SOURCE_OR_SUBJECT_MISMATCH")
        if not all(
            type(x) in (int, float) and math.isfinite(x)
            for x in (self.observed_at, self.expires_at, now)
        ):
            raise GateError("EVIDENCE_TIME_INVALID")
        if not self.observed_at <= now < self.expires_at:
            raise GateError("EVIDENCE_EXPIRED_OR_FUTURE")
        if not 0 < self.expires_at - self.observed_at <= 300:
            raise GateError("EVIDENCE_TTL_INVALID")
        value = json.loads(self.payload_json)
        if digest(value) != self.raw_fingerprint:
            raise GateError("EVIDENCE_CORRUPT")
        if not isinstance(value, dict):
            raise GateError("EVIDENCE_PAYLOAD_INVALID")
        boolean_fields = {
            "closed",
            "inactive",
            "ready",
            "verified",
            "other_config_equal",
            "params_empty",
            "token_matches",
            "pod_ready",
            "route_verified",
            "active",
            "system_admin",
            "permission",
            "invariants_verified",
            "invariants_unchanged",
            "release_closed",
            "task_inactive",
            "candidate_unroutable",
            "retained_true_config",
            "stable_false_gates_verified",
        }
        count_fields = {
            "stable_percent",
            "candidate_percent",
            "build_200",
            "live_200",
            "db_200",
            "cls_candidate",
            "cls_stable",
            "cls_unknown",
            "audit_count",
            "canonical_exact",
            "candidate_instances",
        }
        for key, item in value.items():
            if key in boolean_fields and type(item) is not bool:
                raise GateError("EVIDENCE_BOOLEAN_REQUIRED")
            if key in count_fields and (type(item) is not int or item < 0):
                raise GateError("EVIDENCE_COUNT_REQUIRED")
        return value


class ServiceEvidence(Evidence):
    pass


class RevisionEvidence(Evidence):
    pass


class TrafficEvidence(Evidence):
    pass


class ReleaseEvidence(Evidence):
    pass


class ManageTaskEvidence(Evidence):
    pass


class RuntimeEvidence(Evidence):
    pass


class DatabaseBaselineEvidence(Evidence):
    pass


class ApplyOutcomeEvidence(Evidence):
    pass


class PendingOutcomeEvidence(Evidence):
    pass


class CloseEvidence(Evidence):
    pass


class ApprovalEvidence(Evidence):
    pass


class ActorEvidence(Evidence):
    pass


class BackupEvidence(Evidence):
    pass


class ProvenanceEvidence(Evidence):
    pass


def serialize(e):
    return {
        "baseline": asdict(e.baseline),
        "state": e.state.value,
        "vpc_fingerprint": digest(e.baseline.vpc),
        "candidate": e.candidate,
        "attempts": e.attempts,
        "route_may_be_active": e.route_may_be_active,
        "apply_outcome": e.apply_outcome,
    }


def deserialize(value):
    b = dict(value["baseline"])
    b["vpc"] = tuple(b["vpc"])
    e = Envelope(Baseline(**b))
    if value["vpc_fingerprint"] != digest(e.baseline.vpc):
        raise GateError("JOURNAL_VPC_CORRUPT")
    e.state = State(value["state"])
    e.candidate = value["candidate"]
    e.attempts = value["attempts"]
    if set(e.attempts) != {"create", "target", "apply", "restore"}:
        raise GateError("JOURNAL_INVALID_ATTEMPTS")
    if any(type(n) is not int or n not in (0, 1) for n in e.attempts.values()):
        raise GateError("JOURNAL_INVALID_ATTEMPTS")
    e.route_may_be_active = value["route_may_be_active"]
    e.apply_outcome = value["apply_outcome"]
    return e


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._dispatch_permits = set()
        existed = self.path.exists()
        try:
            with self._db() as c:
                if existed:
                    tables = {
                        r[0]
                        for r in c.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if (
                        c.execute("PRAGMA user_version").fetchone()[0] != 1
                        or not {"envelopes", "reservations", "observations"} <= tables
                    ):
                        raise GateError("JOURNAL_SCHEMA_CORRUPT")
                    return
                c.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS envelopes(
                  id TEXT PRIMARY KEY, service TEXT NOT NULL, operation TEXT NOT NULL,
                  approval_id TEXT NOT NULL, apply_approval_id TEXT,
                  state TEXT NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL,
                  created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active
                  ON envelopes(service,operation) WHERE state != 'CLOSED';
                CREATE TABLE IF NOT EXISTS reservations(
                  envelope_id TEXT NOT NULL REFERENCES envelopes(id), action TEXT NOT NULL,
                  dispatched INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(envelope_id,action));
                CREATE TABLE IF NOT EXISTS observations(
                  envelope_id TEXT NOT NULL, event TEXT NOT NULL, recorded_at REAL NOT NULL,
                  evidence_fingerprints TEXT NOT NULL);
                PRAGMA user_version=1;
                COMMIT;
                """)
        except sqlite3.Error as exc:
            raise GateError("JOURNAL_UNAVAILABLE") from exc

    @contextmanager
    def _db(self):
        c = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=FULL")
            if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise GateError("JOURNAL_CORRUPT")
            yield c
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        try:
            with self._db() as c:
                c.execute("BEGIN IMMEDIATE")
                try:
                    yield c
                    c.commit()
                except BaseException:
                    c.rollback()
                    raise
        except sqlite3.Error as exc:
            raise GateError("JOURNAL_LOCKED_OR_INVALID") from exc

    def create(self, envelope_id, service, approval_id, baseline):
        if not all(
            isinstance(x, str) and x.strip()
            for x in (envelope_id, service, approval_id)
        ):
            raise GateError("IDENTITY_REQUIRED")
        value = serialize(Envelope(baseline))
        with self.transaction() as c:
            c.execute(
                "INSERT INTO envelopes VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    envelope_id,
                    service,
                    "R3",
                    approval_id,
                    None,
                    "NEW",
                    json.dumps(value),
                    digest(value),
                    utcnow(),
                    utcnow(),
                ),
            )

    def _load(self, c, envelope_id, approval_id):
        row = c.execute("SELECT * FROM envelopes WHERE id=?", (envelope_id,)).fetchone()
        if row is None or row["approval_id"] != approval_id:
            raise GateError("APPROVAL_ID_MISMATCH")
        try:
            value = json.loads(row["payload"])
            if digest(value) != row["checksum"] or value["state"] != row["state"]:
                raise GateError("JOURNAL_CORRUPT")
            e = deserialize(value)
            reserved = {
                r[0]
                for r in c.execute(
                    "SELECT action FROM reservations WHERE envelope_id=?",
                    (envelope_id,),
                )
            }
            if reserved != {k for k, v in e.attempts.items() if v}:
                raise GateError("JOURNAL_RESERVATION_MISMATCH")
            return row, e
        except (ValueError, KeyError, TypeError) as exc:
            raise GateError("JOURNAL_CORRUPT") from exc

    def show(self, envelope_id, approval_id):
        with self.transaction() as c:
            row, e = self._load(c, envelope_id, approval_id)
            return {
                **serialize(e),
                "envelope_id": envelope_id,
                "approval_id": approval_id,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "production_ready": False,
            }


class OfflineController:
    """Consumes typed fixture evidence, never raw booleans on the public API."""

    def __init__(self, journal, envelope_id, approval_id, clock=utcnow):
        self.journal, self.id, self.approval_id = journal, envelope_id, approval_id
        self.clock = clock

    def advance(self, event, evidence):
        if not isinstance(evidence, tuple) or any(
            not isinstance(x, Evidence) for x in evidence
        ):
            raise GateError("TYPED_EVIDENCE_REQUIRED")
        with self.journal.transaction() as c:
            row, e = self.journal._load(c, self.id, self.approval_id)
            now = self.clock()

            def read(kind, subject):
                matches = [x for x in evidence if type(x) is kind]
                if len(matches) != 1:
                    raise GateError("EVIDENCE_MISSING_OR_DUPLICATE")
                return matches[0].read(subject, now)

            def traffic():
                return Traffic(**read(TrafficEvidence, row["service"]))

            def runtime(subject):
                return read(RuntimeEvidence, subject)

            result = None
            if event == "prepare":
                a = read(ApprovalEvidence, self.id)
                if a["approval_id"] != self.approval_id or a["purpose"] != "ENVELOPE":
                    raise GateError("APPROVAL_ID_MISMATCH")
                service = read(ServiceEvidence, row["service"])
                if service["status"] != "normal":
                    raise GateError("SERVICE_NOT_NORMAL")
                stable_runtime = runtime(e.baseline.stable)
                if stable_runtime["runtime_commit"] != e.baseline.runtime_commit:
                    raise GateError("STABLE_RUNTIME_MISMATCH")
                result = e.prepare(
                    traffic(),
                    stable_runtime["gates"],
                    release_closed=read(ReleaseEvidence, row["service"])["closed"],
                    task_inactive=read(ManageTaskEvidence, row["service"])["inactive"],
                    backup_ready=read(BackupEvidence, self.id)["ready"],
                    provenance_verified=read(ProvenanceEvidence, self.id)["verified"],
                )
            elif event == "candidate":
                v = read(RevisionEvidence, self.id)
                revision = v.pop("revision")
                v["vpc"] = tuple(v["vpc"])
                e.candidate_ready(revision, **v)
            elif event == "target":
                a = read(ApprovalEvidence, self.id)
                if a["approval_id"] != self.approval_id or a["purpose"] != "ENVELOPE":
                    raise GateError("APPROVAL_ID_MISMATCH")
                result = e.target()
            elif event == "verify_target":
                r = runtime(e.candidate)
                e.verify_target(traffic(), **r)
            elif event == "apply":
                a = read(ApprovalEvidence, self.id)
                if (
                    a["purpose"] != "APPLY"
                    or not a["approval_id"]
                    or a["approval_id"] == self.approval_id
                    or row["apply_approval_id"] not in (None, a["approval_id"])
                ):
                    raise GateError("SEPARATE_APPLY_APPROVAL_REQUIRED")
                actor = read(ActorEvidence, str(a["actor_user_id"]))
                r = runtime(e.candidate)
                db = read(DatabaseBaselineEvidence, self.id)
                if not db["invariants_verified"]:
                    raise GateError("DATABASE_BASELINE_CHANGED")
                result = e.reserve_apply(
                    traffic(),
                    r["gates"],
                    approved=True,
                    route_still_verified=r["route_verified"],
                    actor_active=actor["active"],
                    system_admin=actor["system_admin"],
                    permission=actor["permission"],
                    runtime_commit=r["runtime_commit"],
                    production_fingerprint=db["production_fingerprint"],
                    canonical_fingerprint=db["canonical_fingerprint"],
                )
                c.execute(
                    "UPDATE envelopes SET apply_approval_id=? WHERE id=?",
                    (a["approval_id"], self.id),
                )
            elif event == "apply_result":
                if e.apply_outcome == "NOT_OCCURRED":
                    raise GateError("CONFLICTING_APPLY_EVIDENCE")
                e.record_apply(**read(ApplyOutcomeEvidence, self.id))
            elif event == "observe_pending":
                p = read(PendingOutcomeEvidence, self.id)
                pending = {
                    "create": State.CREATE_PENDING,
                    "target": State.ROUTE_PENDING,
                    "apply": State.APPLY_PENDING,
                    "restore": State.RESTORE_PENDING,
                }
                if pending.get(p["action"]) != e.state or p["outcome"] not in {
                    "UNKNOWN",
                    "NOT_OCCURRED",
                }:
                    raise GateError("PENDING_EVIDENCE_INVALID")
                if p["action"] == "apply":
                    if e.apply_outcome == "NOT_OCCURRED" and p["outcome"] == "UNKNOWN":
                        raise GateError("CONFLICTING_APPLY_EVIDENCE")
                    e.apply_outcome = p["outcome"]
                # State and reservation remain spent, including NOT_OCCURRED.
                result = {"observation": p["outcome"], "replay_allowed": False}
            elif event == "restore":
                # Cleanup never depends on reauthorizing APPLY or an expired
                # enable approval. Its one-shot budget is part of the envelope.
                result = e.restore()
            elif event == "verify_restored":
                e.verify_restored(traffic(), runtime(e.baseline.stable)["gates"])
            elif event == "manual_cleanup":
                e.request_manual_cleanup()
            elif event == "close":
                close = read(CloseEvidence, self.id)
                if not close.pop("stable_false_gates_verified"):
                    raise GateError("STABLE_FALSE_GATES_NOT_PROVEN")
                if not e.candidate or close.pop("candidate_revision") != e.candidate:
                    raise GateError("CLOSE_SUBJECT_MISMATCH")
                result = e.verify_closed(
                    traffic(), runtime(e.baseline.stable)["gates"], **close
                )
            else:
                raise GateError("OFFLINE_EVENT_NOT_ALLOWED")
            if isinstance(result, dict) and "action" in result:
                c.execute(
                    "INSERT INTO reservations(envelope_id,action) VALUES(?,?)",
                    (self.id, result["action"]),
                )
            value = serialize(e)
            c.execute(
                "UPDATE envelopes SET state=?,payload=?,checksum=?,updated_at=? WHERE id=?",
                (e.state.value, json.dumps(value), digest(value), now, self.id),
            )
            c.execute(
                "INSERT INTO observations VALUES(?,?,?,?)",
                (
                    self.id,
                    event,
                    now,
                    json.dumps([x.raw_fingerprint for x in evidence]),
                ),
            )
        # Commit completed before any future dispatch opportunity is returned.
        if isinstance(result, dict) and "action" in result:
            self.journal._dispatch_permits.add((self.id, result["action"]))
        return result


class FakeReadAdapter:
    def __init__(self, fixtures):
        self.fixtures = fixtures

    def read(self, key):
        return self.fixtures[key]


class FakeWriteAdapter:
    """Exactly one durable simulated dispatch. No outbound I/O, even on success."""

    def dispatch(self, journal, envelope_id, approval_id, action, outcome="success"):
        if outcome not in {"success", "timeout", "unknown"}:
            raise GateError("INVALID_FAKE_OUTCOME")
        expected = {
            "create": State.CREATE_PENDING,
            "target": State.ROUTE_PENDING,
            "apply": State.APPLY_PENDING,
            "restore": State.RESTORE_PENDING,
        }
        permit = (envelope_id, action)
        if permit not in journal._dispatch_permits:
            raise GateError("RESTART_OR_REPLAY_DISALLOWED")
        journal._dispatch_permits.remove(permit)
        with journal.transaction() as c:
            _, e = journal._load(c, envelope_id, approval_id)
            if expected.get(action) != e.state:
                raise GateError("DISPATCH_STATE_MISMATCH")
            count = c.execute(
                "UPDATE reservations SET dispatched=1 WHERE envelope_id=? "
                "AND action=? AND dispatched=0",
                (envelope_id, action),
            ).rowcount
            if count != 1:
                raise GateError("REPLAY_DISALLOWED")
        if outcome == "timeout":
            raise TimeoutError("SIMULATED_TIMEOUT")
        return {"outcome": outcome, "offline_only": True}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="R3 offline journal; no production execution"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path, help="synthetic baseline JSON fixture")
    mode.add_argument("--show-state", action="store_true")
    mode.add_argument(
        "--resume-offline", action="store_true", help="inspect only, never dispatch"
    )
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--envelope-id", required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--service", default="fixture-service")
    args = parser.parse_args(argv)
    if not args.plan and not args.journal.is_file():
        parser.error("existing journal required; cannot recreate missing state")
    journal = Journal(args.journal)
    if args.plan:
        data = json.loads(args.plan.read_text(encoding="utf-8"))
        data["vpc"] = tuple(data["vpc"])
        journal.create(
            args.envelope_id, args.service, args.approval_id, Baseline(**data)
        )
    print(json.dumps(journal.show(args.envelope_id, args.approval_id), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
