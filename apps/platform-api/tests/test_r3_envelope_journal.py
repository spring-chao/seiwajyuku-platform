import json
import multiprocessing
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import r3_envelope_journal as j
from r3_execution_envelope import GateError
from test_r3_execution_envelope import BASE, ENABLED, FLOW, GATES, TARGET

NOW = 1000


def ev(evidence_type, subject, **data):
    return evidence_type.fixture(subject, data, now=NOW)


def approval(purpose="ENVELOPE", approval_id="approval-1"):
    return ev(
        j.ApprovalEvidence,
        "one",
        purpose=purpose,
        approval_id=approval_id,
        actor_user_id=1,
    )


def traffic(target=False):
    from dataclasses import asdict

    return ev(j.TrafficEvidence, "service", **asdict(TARGET if target else FLOW))


def prepare():
    return (
        approval(),
        traffic(),
        ev(j.ServiceEvidence, "service", status="normal"),
        ev(
            j.RuntimeEvidence,
            BASE.stable,
            gates=GATES,
            runtime_commit=BASE.runtime_commit,
        ),
        ev(j.ReleaseEvidence, "service", closed=True),
        ev(j.ManageTaskEvidence, "service", inactive=True),
        ev(j.BackupEvidence, "one", ready=True),
        ev(j.ProvenanceEvidence, "one", verified=True),
    )


def candidate():
    return (
        ev(
            j.RevisionEvidence,
            "one",
            revision="api-259",
            runtime_commit=BASE.runtime_commit,
            image_digest=BASE.image_digest,
            vpc=BASE.vpc,
            gates=ENABLED,
            other_config_equal=True,
        ),
    )


def verified():
    return (
        traffic(True),
        ev(
            j.RuntimeEvidence,
            "api-259",
            routed_revision="api-259",
            token_matches=True,
            pod_ready=True,
            runtime_commit=BASE.runtime_commit,
            build_200=2,
            live_200=1,
            db_200=20,
            cls_candidate=23,
            cls_stable=0,
            cls_unknown=0,
        ),
    )


def apply_evidence():
    return (
        approval("APPLY", "apply-1"),
        traffic(True),
        ev(
            j.RuntimeEvidence,
            "api-259",
            gates=ENABLED,
            route_verified=True,
            runtime_commit=BASE.runtime_commit,
        ),
        ev(j.ActorEvidence, "1", active=True, system_admin=True, permission=True),
        ev(
            j.DatabaseBaselineEvidence,
            "one",
            invariants_verified=True,
            production_fingerprint=BASE.production_fingerprint,
            canonical_fingerprint=BASE.canonical_fingerprint,
        ),
    )


def close_evidence(**changes):
    data = {
        "candidate_revision": "api-259",
        "release_closed": True,
        "task_inactive": True,
        "candidate_instances": 0,
        "candidate_unroutable": True,
        "retained_true_config": True,
        "stable_false_gates_verified": True,
    }
    data.update(changes)
    return (
        traffic(),
        ev(j.RuntimeEvidence, BASE.stable, gates=GATES),
        ev(j.CloseEvidence, "one", **data),
    )


@pytest.fixture
def controller(tmp_path):
    journal = j.Journal(tmp_path / "journal.sqlite")
    journal.create("one", "service", "approval-1", BASE)
    return j.OfflineController(journal, "one", "approval-1", clock=lambda: NOW)


def to_ready(c):
    c.advance("prepare", prepare())
    c.advance("candidate", candidate())
    c.advance("target", (approval(),))
    c.advance("verify_target", verified())


def restarted(c):
    return j.OfflineController(
        j.Journal(c.journal.path), "one", "approval-1", clock=lambda: NOW
    )


@pytest.mark.parametrize(
    "action,event,evidence",
    [
        ("create", "prepare", prepare),
        ("target", "target", lambda: (approval(),)),
        ("apply", "apply", apply_evidence),
        ("restore", "restore", tuple),
    ],
)
def test_every_pending_survives_restart_no_replay(controller, action, event, evidence):
    c = controller
    if action != "create":
        c.advance("prepare", prepare())
        c.advance("candidate", candidate())
    if action in {"apply", "restore"}:
        c.advance("target", (approval(),))
        c.advance("verify_target", verified())
    c.advance(event, evidence())
    resumed = restarted(c)
    assert resumed.journal.show("one", "approval-1")["attempts"][action] == 1
    with pytest.raises(GateError):
        resumed.advance(event, evidence())
    with pytest.raises(GateError):
        j.FakeWriteAdapter().dispatch(resumed.journal, "one", "approval-1", action)


def test_dispatch_after_commit_and_timeout_durable(controller):
    controller.advance("prepare", prepare())
    assert (
        j.Journal(controller.journal.path).show("one", "approval-1")["state"]
        == "CREATE_PENDING"
    )
    with pytest.raises(TimeoutError):
        j.FakeWriteAdapter().dispatch(
            controller.journal, "one", "approval-1", "create", "timeout"
        )
    with pytest.raises(GateError):
        j.FakeWriteAdapter().dispatch(controller.journal, "one", "approval-1", "create")


def test_unknown_apply_can_restore_and_close(controller):
    to_ready(controller)
    controller.advance("apply", apply_evidence())
    c = restarted(controller)
    c.advance("restore", ())
    c.advance(
        "verify_restored", (traffic(), ev(j.RuntimeEvidence, BASE.stable, gates=GATES))
    )
    result = c.advance("close", close_evidence())
    assert result["apply_outcome"] == "UNKNOWN"
    assert result["retained_true_config"] and not result["reactivation_authorized"]
    assert not result["production_ready"]


@pytest.mark.parametrize(
    "change",
    [
        {"release_closed": False},
        {"task_inactive": False},
        {"candidate_instances": 1},
        {"candidate_unroutable": False},
        {"candidate_revision": "wrong"},
    ],
)
def test_close_incomplete(controller, change):
    to_ready(controller)
    controller.advance("restore", ())
    controller.advance(
        "verify_restored", (traffic(), ev(j.RuntimeEvidence, BASE.stable, gates=GATES))
    )
    with pytest.raises(GateError):
        controller.advance("close", close_evidence(**change))


@pytest.mark.parametrize(
    "changed",
    [
        {"expires_at": NOW},
        {"observed_at": NOW + 1},
        {"subject": "other"},
        {"source": "production"},
        {"raw_fingerprint": "bad"},
    ],
)
def test_bad_evidence_fails_before_reservation(controller, changed):
    evidence = list(prepare())
    evidence[0] = replace(evidence[0], **changed)
    with pytest.raises(GateError):
        controller.advance("prepare", tuple(evidence))
    assert controller.journal.show("one", "approval-1")["attempts"]["create"] == 0


@pytest.mark.parametrize(
    "index,change",
    [
        (0, {"approval_id": "approval-1"}),
        (0, {"approval_id": ""}),
        (2, {"runtime_commit": "f" * 40}),
        (3, {"permission": False}),
        (4, {"production_fingerprint": "f" * 64}),
        (4, {"canonical_fingerprint": "f" * 64}),
    ],
)
def test_apply_guards(controller, index, change):
    to_ready(controller)
    evidence = list(apply_evidence())
    old = evidence[index]
    data = json.loads(old.payload_json)
    data.update(change)
    evidence[index] = type(old).fixture(old.subject, data, now=NOW)
    with pytest.raises(GateError):
        controller.advance("apply", tuple(evidence))
    assert (
        controller.journal.show("one", "approval-1")["state"]
        == "READY_FOR_SEPARATE_APPLY_APPROVAL"
    )


@pytest.mark.parametrize("index", [0, 1])
def test_expired_apply_approval_or_route(controller, index):
    to_ready(controller)
    evidence = list(apply_evidence())
    evidence[index] = replace(evidence[index], expires_at=NOW)
    with pytest.raises(GateError):
        controller.advance("apply", tuple(evidence))


def test_changed_envelope_approval(controller):
    with pytest.raises(GateError):
        controller.journal.show("one", "changed")


def test_corrupt_payload_fail_closed(controller):
    with sqlite3.connect(controller.journal.path) as c:
        c.execute("UPDATE envelopes SET payload='{}'")
    with pytest.raises(GateError):
        restarted(controller).advance("prepare", prepare())


def test_corrupt_sqlite_file(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"not sqlite")
    with pytest.raises(GateError):
        j.Journal(path)


def compete(path, envelope_id, queue):
    try:
        journal = j.Journal(Path(path))
        journal.create(envelope_id, "same-service", "a", BASE)
        queue.put("created")
    except GateError:
        queue.put("closed")


def test_two_processes_only_one_active(tmp_path):
    path = tmp_path / "race.sqlite"
    j.Journal(path)
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    ps = [ctx.Process(target=compete, args=(str(path), str(n), q)) for n in range(2)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(15)
        assert p.exitcode == 0
    assert sorted([q.get(timeout=2), q.get(timeout=2)]) == ["closed", "created"]


def test_url_params_remain_blocks_restore(controller):
    to_ready(controller)
    controller.advance("restore", ())
    t = ev(
        j.TrafficEvidence,
        "service",
        stable=BASE.stable,
        stable_percent=100,
        candidate_percent=0,
        kind="FLOW",
        params_empty=False,
    )
    with pytest.raises(GateError):
        controller.advance(
            "verify_restored", (t, ev(j.RuntimeEvidence, BASE.stable, gates=GATES))
        )
    with pytest.raises(GateError):
        restarted(controller).advance("restore", ())


def test_boolean_string_rejected(controller):
    data = list(prepare())
    data[2] = ev(j.ServiceEvidence, "service", status="normal", ready="false")
    with pytest.raises(GateError):
        controller.advance("prepare", tuple(data))


def test_closed_allows_next_envelope_but_old_id_cannot_recreate(controller):
    to_ready(controller)
    controller.advance("restore", ())
    controller.advance(
        "verify_restored", (traffic(), ev(j.RuntimeEvidence, BASE.stable, gates=GATES))
    )
    controller.advance("close", close_evidence())
    controller.journal.create("two", "service", "new-approval", BASE)
    with pytest.raises(GateError):
        controller.journal.create("one", "other-service", "new-approval", BASE)


def test_resume_cli_is_observation_only(controller, capsys):
    controller.advance("prepare", prepare())
    j.main(
        [
            "--resume-offline",
            "--journal",
            str(controller.journal.path),
            "--envelope-id",
            "one",
            "--approval-id",
            "approval-1",
        ]
    )
    assert json.loads(capsys.readouterr().out)["state"] == "CREATE_PENDING"


def test_no_production_cli_flags():
    with pytest.raises(SystemExit) as exc:
        j.main(["--execute-production"])
    assert exc.value.code != 0


def test_fake_adapter_success_and_apply_result(controller):
    controller.advance("prepare", prepare())
    assert j.FakeWriteAdapter().dispatch(
        controller.journal, "one", "approval-1", "create"
    )["offline_only"]
    controller.advance("candidate", candidate())
    controller.advance("target", (approval(),))
    controller.advance("verify_target", verified())
    controller.advance("apply", apply_evidence())
    outcome = ev(
        j.ApplyOutcomeEvidence,
        "one",
        audit_count=1,
        canonical_exact=25,
        invariants_unchanged=True,
    )
    reader = j.FakeReadAdapter({"outcome": (outcome,)})
    restarted(controller).advance("apply_result", reader.read("outcome"))
    assert controller.journal.show("one", "approval-1")["state"] == "APPLIED"


def crash_worker(path):
    import os

    journal = j.Journal(Path(path))
    c = j.OfflineController(journal, "one", "approval-1", clock=lambda: NOW)
    c.advance("prepare", prepare())
    j.FakeWriteAdapter().dispatch(journal, "one", "approval-1", "create", "unknown")
    os._exit(0)


def test_real_process_exit_after_fake_dispatch(controller):
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=crash_worker, args=(str(controller.journal.path),))
    p.start()
    p.join(15)
    assert p.exitcode == 0
    assert (
        restarted(controller).journal.show("one", "approval-1")["state"]
        == "CREATE_PENDING"
    )
    with pytest.raises(GateError):
        j.FakeWriteAdapter().dispatch(controller.journal, "one", "approval-1", "create")


@pytest.mark.parametrize("outcome", ["NOT_OCCURRED", "UNKNOWN"])
def test_pending_outcome_never_refunds_budget(controller, outcome):
    to_ready(controller)
    controller.advance("apply", apply_evidence())
    c = restarted(controller)
    result = c.advance(
        "observe_pending",
        (ev(j.PendingOutcomeEvidence, "one", action="apply", outcome=outcome),),
    )
    assert not result["replay_allowed"]
    with pytest.raises(GateError):
        c.advance("apply", apply_evidence())
    c.advance("restore", ())


def test_missing_schema_and_empty_file_not_reinitialized(controller, tmp_path):
    with sqlite3.connect(controller.journal.path) as conn:
        conn.execute("DROP TABLE observations")
    with pytest.raises(GateError):
        restarted(controller)
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    with pytest.raises(GateError):
        j.Journal(empty)


def test_missing_close_false_proof(controller):
    to_ready(controller)
    controller.advance("restore", ())
    controller.advance(
        "verify_restored", (traffic(), ev(j.RuntimeEvidence, BASE.stable, gates=GATES))
    )
    with pytest.raises(GateError):
        controller.advance("close", close_evidence(stable_false_gates_verified=False))
