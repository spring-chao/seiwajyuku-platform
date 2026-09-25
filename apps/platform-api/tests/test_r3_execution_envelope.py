import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from r3_execution_envelope import Baseline, Envelope, GateError, State, Traffic


BASE = Baseline("api-258", "a" * 40, "sha256:" + "b" * 64,
                ("vpc", "cidr", "subnet", "subnet-cidr"), "c" * 64, "d" * 64)
FLOW = Traffic(BASE.stable, 100, 0, "FLOW", True)
TARGET = Traffic(BASE.stable, 100, 0, "URL_PARAMS", False)
GATES = {"APP_ENV": "production", "DEPLOYMENT_READ_ONLY": "false",
         "ALLOW_PRODUCTION_MUTATIONS": "true",
         "G5_4_PRODUCTION_RULE_APPLY_ENABLED": "false",
         "LEARNING_CREDIT_SETTLEMENT_ENABLED": "false",
         "RUN_BOOTSTRAP_ON_STARTUP": "false"}
ENABLED = {**GATES, "G5_4_PRODUCTION_RULE_APPLY_ENABLED": "true"}


def prepared():
    e = Envelope(BASE)
    e.prepare(FLOW, GATES, release_closed=True, task_inactive=True,
              backup_ready=True, provenance_verified=True)
    return e


def candidate(e, **overrides):
    args = dict(runtime_commit=BASE.runtime_commit, image_digest=BASE.image_digest,
                vpc=BASE.vpc, gates=ENABLED, other_config_equal=True)
    args.update(overrides)
    e.candidate_ready("api-259", **args)


def targeted(**overrides):
    e = prepared()
    candidate(e)
    e.target()
    args = dict(routed_revision="api-259", token_matches=True, pod_ready=True,
                runtime_commit=BASE.runtime_commit, build_200=2, live_200=1,
                db_200=20, cls_candidate=23, cls_stable=0, cls_unknown=0)
    args.update(overrides)
    return e, args


def ready():
    e, args = targeted()
    e.verify_target(TARGET, **args)
    return e


def apply(e, **overrides):
    args = dict(approved=True, route_still_verified=True, actor_active=True,
                system_admin=True, permission=True, runtime_commit=BASE.runtime_commit,
                production_fingerprint=BASE.production_fingerprint,
                canonical_fingerprint=BASE.canonical_fingerprint)
    args.update(overrides)
    return e.reserve_apply(TARGET, ENABLED, **args)


def close(e, **overrides):
    args = dict(release_closed=True, task_inactive=True, candidate_instances=0,
                candidate_unroutable=True, retained_true_config=True)
    args.update(overrides)
    return e.verify_closed(FLOW, GATES, **args)


def test_success_stops_before_apply_and_keeps_stable():
    e = ready()
    assert e.state == State.READY
    assert e.attempts["apply"] == 0
    assert apply(e)["stable"] == "api-258"
    e.record_apply(audit_count=1, canonical_exact=25, invariants_unchanged=True)
    assert e.restore()["stable"] == "api-258"
    e.verify_restored(FLOW, GATES)
    result = close(e)
    assert result["apply_outcome"] == "VERIFIED"
    assert not result["all_stored_flags_false"]
    assert not result["production_ready"]
    assert e.attempts == dict(create=1, target=1, apply=1, restore=1)


@pytest.mark.parametrize("key", list(GATES))
def test_all_six_gates_required(key):
    e = Envelope(BASE)
    gates = dict(GATES)
    del gates[key]
    with pytest.raises(GateError):
        e.prepare(FLOW, gates, release_closed=True, task_inactive=True,
                  backup_ready=True, provenance_verified=True)
    assert not any(e.attempts.values())


@pytest.mark.parametrize("change", [dict(image_digest="sha256:" + "e" * 64),
                                    dict(runtime_commit="f" * 40),
                                    dict(vpc=("wrong", "c", "s", "c")),
                                    dict(other_config_equal=False)])
def test_config_mismatch_never_routes(change):
    e = prepared()
    with pytest.raises(GateError):
        candidate(e, **change)
    assert e.attempts["target"] == 0
    e.request_manual_cleanup()


@pytest.mark.parametrize("change", [dict(pod_ready=False), dict(token_matches=False),
                                    dict(routed_revision="api-258"), dict(db_200=19),
                                    dict(cls_candidate=22), dict(cls_stable=1),
                                    dict(cls_unknown=1)])
def test_probe_evidence_fails_closed(change):
    e, args = targeted(**change)
    with pytest.raises(GateError):
        e.verify_target(TARGET, **args)
    assert e.attempts["apply"] == 0
    e.restore()


@pytest.mark.parametrize("key", ["approved", "route_still_verified", "actor_active",
                                "system_admin", "permission"])
def test_apply_authority_and_freshness(key):
    e = ready()
    with pytest.raises(GateError):
        apply(e, **{key: False})
    assert e.attempts["apply"] == 0


@pytest.mark.parametrize("key", ["runtime_commit", "production_fingerprint",
                                "canonical_fingerprint"])
def test_apply_drift(key):
    e = ready()
    with pytest.raises(GateError):
        apply(e, **{key: "changed"})


def test_unknown_apply_never_retries_but_cleanup_available():
    e = ready()
    apply(e)  # timeout after dispatch: no result recorded
    with pytest.raises(GateError):
        apply(e)
    e.restore()
    e.verify_restored(FLOW, GATES)
    assert close(e)["apply_outcome"] == "UNKNOWN"


def test_unknown_create_no_second_candidate():
    e = prepared()
    with pytest.raises(GateError):
        e.prepare(FLOW, GATES, release_closed=True, task_inactive=True,
                  backup_ready=True, provenance_verified=True)
    e.request_manual_cleanup()
    with pytest.raises(GateError):
        close(e, candidate_instances=None)


def test_restore_never_retries_or_falls_back_to_true_candidate():
    e = ready()
    assert e.restore()["stable"] == BASE.stable
    with pytest.raises(GateError):
        e.restore()
    with pytest.raises(GateError):
        e.verify_restored(Traffic("api-259", 100, 0, "FLOW", True), ENABLED)
    assert e.state == State.RESTORE_PENDING


@pytest.mark.parametrize("change", [dict(release_closed=False), dict(task_inactive=False),
                                    dict(candidate_instances=1),
                                    dict(candidate_unroutable=False)])
def test_manual_close_is_independent(change):
    e = ready()
    e.restore()
    e.verify_restored(FLOW, GATES)
    with pytest.raises(GateError):
        close(e, **change)
    assert e.state == State.MANUAL_CLOSE


def test_no_sdk_or_http_imports():
    import ast
    import r3_execution_envelope

    tree = ast.parse(Path(r3_execution_envelope.__file__).read_text())
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert set(modules) <= {"__future__", "dataclasses", "enum", "typing", "re"}
