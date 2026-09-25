"""Offline R3 lifecycle planner. Deliberately has no SDK, HTTP or execute mode.

Evidence is supplied by a caller in tests/rehearsals, not authenticated here.
This module is NOT a production executor or a replacement for runtime guards.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class GateError(ValueError):
    pass


class State(str, Enum):
    NEW = "NEW"
    CREATE_PENDING = "CREATE_PENDING"
    CANDIDATE = "CANDIDATE"
    ROUTE_PENDING = "ROUTE_PENDING"
    READY = "READY_FOR_SEPARATE_APPLY_APPROVAL"
    APPLY_PENDING = "APPLY_PENDING"
    APPLIED = "APPLIED"
    RESTORE_PENDING = "RESTORE_PENDING"
    MANUAL_CLOSE = "MANUAL_CLOSE_REQUIRED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Baseline:
    stable: str
    runtime_commit: str
    image_digest: str
    vpc: tuple[str, str, str, str]
    production_fingerprint: str
    canonical_fingerprint: str

    def __post_init__(self):
        import re

        if not self.stable or not all(self.vpc) or len(self.vpc) != 4:
            raise GateError("BASELINE_INCOMPLETE")
        for value, size in ((self.runtime_commit, 40),
                            (self.production_fingerprint, 64),
                            (self.canonical_fingerprint, 64)):
            if not re.fullmatch(rf"[0-9a-f]{{{size}}}", value):
                raise GateError("FINGERPRINT_INVALID")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_digest):
            raise GateError("IMMUTABLE_IMAGE_REQUIRED")


@dataclass(frozen=True)
class Traffic:
    stable: str
    stable_percent: int
    candidate_percent: int
    kind: str
    params_empty: bool


def check_gates(gates: Mapping[str, str], *, enabled: bool) -> None:
    expected = {
        "APP_ENV": "production",
        "DEPLOYMENT_READ_ONLY": "false",
        "ALLOW_PRODUCTION_MUTATIONS": "true",
        "G5_4_PRODUCTION_RULE_APPLY_ENABLED": str(enabled).lower(),
        "LEARNING_CREDIT_SETTLEMENT_ENABLED": "false",
        "RUN_BOOTSTRAP_ON_STARTUP": "false",
    }
    if any(gates.get(key) != value for key, value in expected.items()):
        raise GateError("RUNTIME_GATE_MISMATCH")


class Envelope:
    """One in-memory rehearsal only. Restart/resume and live I/O are unsupported.

    Reserve transitions BEFORE dispatch in a future adapter. A pending state
    represents an uncertain response and must never allow replay of that action.
    No token, environment secrets or HTTP payloads are retained.
    """

    def __init__(self, baseline: Baseline):
        self.baseline = baseline
        self.state = State.NEW
        self.candidate: str | None = None
        self.attempts = {"create": 0, "target": 0, "apply": 0, "restore": 0}
        self.route_may_be_active = False
        self.apply_outcome = "NOT_ATTEMPTED"

    def _require(self, *states: State) -> None:
        if self.state not in states:
            raise GateError("INVALID_TRANSITION")

    def _reserve(self, action: str, state: State) -> dict:
        if self.attempts[action]:
            raise GateError("ACTION_ALREADY_ATTEMPTED")
        self.attempts[action] = 1
        self.state = state
        return {"action": action, "stable": self.baseline.stable,
                "candidate": self.candidate, "offline_only": True}

    def _traffic(self, evidence: Traffic, kind: str) -> None:
        if (evidence.stable != self.baseline.stable
                or evidence.stable_percent != 100
                or evidence.candidate_percent != 0 or evidence.kind != kind):
            raise GateError("STABLE_TRAFFIC_CHANGED")
        if kind == "FLOW" and not evidence.params_empty:
            raise GateError("URL_PARAMS_REMAIN")

    def prepare(self, traffic: Traffic, gates: Mapping[str, str], *,
                release_closed: bool, task_inactive: bool,
                backup_ready: bool, provenance_verified: bool) -> dict:
        self._require(State.NEW)
        self._traffic(traffic, "FLOW")
        check_gates(gates, enabled=False)
        if not all((release_closed, task_inactive, backup_ready, provenance_verified)):
            raise GateError("PREFLIGHT_INCOMPLETE")
        return self._reserve("create", State.CREATE_PENDING)

    def candidate_ready(self, revision: str, *, runtime_commit: str,
                        image_digest: str, vpc: tuple[str, str, str, str],
                        gates: Mapping[str, str], other_config_equal: bool) -> None:
        self._require(State.CREATE_PENDING)
        if not revision or revision == self.baseline.stable:
            raise GateError("CANDIDATE_ID_INVALID")
        # Remember identity even if later checks fail, for manual cleanup.
        self.candidate = revision
        check_gates(gates, enabled=True)
        if (runtime_commit != self.baseline.runtime_commit
                or image_digest != self.baseline.image_digest
                or vpc != self.baseline.vpc or not other_config_equal):
            raise GateError("CANDIDATE_CONFIG_MISMATCH")
        self.state = State.CANDIDATE

    def target(self) -> dict:
        self._require(State.CANDIDATE)
        self.route_may_be_active = True
        return self._reserve("target", State.ROUTE_PENDING)

    def verify_target(self, traffic: Traffic, *, routed_revision: str,
                      token_matches: bool, pod_ready: bool,
                      runtime_commit: str, build_200: int, live_200: int,
                      db_200: int, cls_candidate: int, cls_stable: int,
                      cls_unknown: int) -> None:
        self._require(State.ROUTE_PENDING)
        self._traffic(traffic, "URL_PARAMS")
        if (traffic.params_empty or routed_revision != self.candidate
                or not token_matches or not pod_ready
                or runtime_commit != self.baseline.runtime_commit
                or (build_200, live_200, db_200) != (2, 1, 20)
                or (cls_candidate, cls_stable, cls_unknown) != (23, 0, 0)):
            raise GateError("TARGET_IDENTITY_NOT_PROVEN")
        self.state = State.READY

    def reserve_apply(self, traffic: Traffic, gates: Mapping[str, str], *,
                      approved: bool, route_still_verified: bool,
                      actor_active: bool, system_admin: bool, permission: bool,
                      runtime_commit: str, production_fingerprint: str,
                      canonical_fingerprint: str) -> dict:
        self._require(State.READY)
        self._traffic(traffic, "URL_PARAMS")
        check_gates(gates, enabled=True)
        if (traffic.params_empty or not all((approved, route_still_verified,
                                            actor_active, system_admin, permission))
                or runtime_commit != self.baseline.runtime_commit
                or production_fingerprint != self.baseline.production_fingerprint
                or canonical_fingerprint != self.baseline.canonical_fingerprint):
            raise GateError("APPLY_PREFLIGHT_FAILED")
        self.apply_outcome = "UNKNOWN"
        return self._reserve("apply", State.APPLY_PENDING)

    def record_apply(self, *, audit_count: int, canonical_exact: int,
                     invariants_unchanged: bool) -> None:
        self._require(State.APPLY_PENDING)
        if (audit_count, canonical_exact) != (1, 25) or not invariants_unchanged:
            raise GateError("APPLY_OUTCOME_NOT_PROVEN")
        self.apply_outcome = "VERIFIED"
        self.state = State.APPLIED

    def restore(self) -> dict:
        self._require(State.ROUTE_PENDING, State.READY, State.APPLY_PENDING,
                      State.APPLIED)
        return self._reserve("restore", State.RESTORE_PENDING)

    def verify_restored(self, traffic: Traffic, gates: Mapping[str, str]) -> None:
        self._require(State.RESTORE_PENDING)
        self._traffic(traffic, "FLOW")
        check_gates(gates, enabled=False)
        self.route_may_be_active = False
        self.state = State.MANUAL_CLOSE

    def request_manual_cleanup(self) -> None:
        self._require(State.CREATE_PENDING, State.CANDIDATE)
        self.state = State.MANUAL_CLOSE

    def verify_closed(self, traffic: Traffic, gates: Mapping[str, str], *,
                      release_closed: bool, task_inactive: bool,
                      candidate_instances: int | None,
                      candidate_unroutable: bool,
                      retained_true_config: bool) -> dict:
        self._require(State.MANUAL_CLOSE)
        self._traffic(traffic, "FLOW")
        check_gates(gates, enabled=False)
        if not all((release_closed, task_inactive, candidate_instances == 0,
                    candidate_unroutable)):
            raise GateError("CLOSE_NOT_PROVEN")
        self.state = State.CLOSED
        return {"state": self.state.value, "apply_outcome": self.apply_outcome,
                "retained_true_config": retained_true_config,
                "reactivation_authorized": False,
                "all_stored_flags_false": not retained_true_config,
                "production_ready": False}
