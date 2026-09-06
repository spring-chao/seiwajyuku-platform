from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db import execute, fetch_one, transaction
from app.services.class_meeting_credits import dry_run_class_meeting_settlement
from app.services.credit_rule_mapping import resolve_credit_rule_mapping
from app.services.learning_credits import dry_run_study_meeting_settlement
from app.services.learning_cycles import (
    bind_class_learning_plan,
    restart_class_learning_plan,
    resume_class_learning_plan,
)
from test_class_meeting_credits import _create_class_meeting
from test_study_meeting_evidence import create
from test_learning_credit_ledger import _submit_without_evidence
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def g5_3_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


@pytest.fixture(scope="module", autouse=True)
def cleanup_created_exact_2026_plan():
    """Do not let the production-shaped plan fixture alter later modules."""

    existing = fetch_one(
        "SELECT id FROM learning_plan_versions "
        "WHERE plan_key='standard-3y' AND version_label='2026' LIMIT 1"
    )
    yield
    if existing:
        return
    with transaction() as connection:
        plan = execute(
            connection,
            "SELECT id FROM learning_plan_versions "
            "WHERE plan_key='standard-3y' AND version_label='2026' LIMIT 1",
        ).fetchone()
        if not plan:
            return
        plan_id = int(plan["id"])
        binding_ids = [
            int(row["id"])
            for row in execute(
                connection,
                "SELECT id FROM class_learning_bindings WHERE plan_version_id=?",
                (plan_id,),
            ).fetchall()
        ]
        if binding_ids:
            placeholders = ",".join("?" for _ in binding_ids)
            cycle_ids = [
                int(row["id"])
                for row in execute(
                    connection,
                    "SELECT id FROM class_learning_cycles WHERE binding_id IN ("
                    + placeholders
                    + ")",
                    tuple(binding_ids),
                ).fetchall()
            ]
            if cycle_ids:
                cycle_placeholders = ",".join("?" for _ in cycle_ids)
                session_ids = [
                    int(row["id"])
                    for row in execute(
                        connection,
                        "SELECT id FROM study_meeting_sessions WHERE learning_cycle_id IN ("
                        + cycle_placeholders
                        + ")",
                        tuple(cycle_ids),
                    ).fetchall()
                ]
                if session_ids:
                    session_placeholders = ",".join("?" for _ in session_ids)
                    execute(
                        connection,
                        "DELETE FROM study_meeting_evidence WHERE study_meeting_session_id IN ("
                        + session_placeholders
                        + ")",
                        tuple(session_ids),
                    )
                    execute(
                        connection,
                        "DELETE FROM study_meeting_sessions WHERE id IN ("
                        + session_placeholders
                        + ")",
                        tuple(session_ids),
                    )
                execute(
                    connection,
                    "DELETE FROM class_learning_cycles WHERE id IN ("
                    + cycle_placeholders
                    + ")",
                    tuple(cycle_ids),
                )
            execute(
                connection,
                "DELETE FROM class_learning_bindings WHERE id IN ("
                + placeholders
                + ")",
                tuple(binding_ids),
            )
        execute(
            connection,
            "DELETE FROM learning_plan_versions WHERE id=?",
            (plan_id,),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _attach_event_to_current_cycle(fixture: dict, event_group_id: int) -> None:
    with transaction() as connection:
        execute(
            connection,
            "UPDATE class_learning_cycles SET source_event_group_id=? "
            "WHERE binding_id=(SELECT id FROM class_learning_bindings "
            "WHERE class_org_unit_id=? AND status='ACTIVE' LIMIT 1) "
            "AND cycle_status='OPEN'",
            (event_group_id, fixture["class_id"]),
        )


def _exact_2026_plan() -> int:
    """Create the production-shaped plan identity in the isolated test DB."""

    existing = fetch_one(
        "SELECT id FROM learning_plan_versions "
        "WHERE plan_key='standard-3y' AND version_label='2026' LIMIT 1"
    )
    if existing:
        return int(existing["id"])
    now = _now()
    with transaction() as connection:
        plan_cursor = execute(
            connection,
            "INSERT INTO learning_plan_versions "
            "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES ('standard-3y', '标准班三年学习计划', '2026', 36, 'PUBLISHED', ?, ?)",
            (now, now),
        )
        plan_id = int(plan_cursor.lastrowid)
        cycle_cursor = execute(
            connection,
            "INSERT INTO learning_plan_cycles "
            "(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
            "VALUES (?, 1, 1, 1, '第1学习周期', ?, ?)",
            (plan_id, now, now),
        )
        cycle_id = int(cycle_cursor.lastrowid)
        execute(
            connection,
            "INSERT INTO learning_plan_tasks "
            "(plan_cycle_id, task_type, title, credit_points, is_required, sort_order, created_at, updated_at) "
            "VALUES (?, 'GROUP_MEETING', '小组学习会', 4, 1, 1, ?, ?)",
            (cycle_id, now, now),
        )
    return plan_id


def test_g5_3_production_shaped_mapping_projects_18_4_40_and_zero_writes() -> None:
    fixture = _seed_group_leader_fixture()
    plan_id = _exact_2026_plan()
    admin_id = _admin_id()

    # RESTART exercises the same new-round binding path used by operations;
    # the exact plan identity is intentionally different from both policies.
    restarted = restart_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["class_id"],
        plan_version_id=plan_id,
        cohort_month=1,
        started_at="2026-09-06T00:00:00+00:00",
        reason="G5.3 生产同构映射验证",
    )
    binding = fetch_one(
        "SELECT b.id, b.credit_rule_version_id, b.course_credit_rule_version_id, "
        "p.plan_key, p.version_label "
        "FROM class_learning_bindings b "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.id=?",
        (restarted["binding"]["id"],),
    )
    assert binding == {
        "id": binding["id"],
        "credit_rule_version_id": binding["credit_rule_version_id"],
        "course_credit_rule_version_id": binding["course_credit_rule_version_id"],
        "plan_key": "standard-3y",
        "version_label": "2026",
    }
    assert binding["credit_rule_version_id"] is not None
    assert binding["course_credit_rule_version_id"] is not None
    mapping = _resolve_mapping()
    assert mapping
    assert mapping["generic_rule_set_key"] == "STANDARD_3Y_2026"
    assert mapping["generic_rule_version_label"] == "2026.1"
    assert mapping["course_rule_plan_key"] == "STANDARD_3Y_2026"
    assert mapping["course_rule_version_label"] == "2026.1"
    assert int(binding["credit_rule_version_id"]) == int(mapping["generic_rule_version_id"])
    assert int(binding["course_credit_rule_version_id"]) == int(mapping["course_credit_rule_version_id"])

    # The class-meeting helper uses the binding's already-frozen generic id;
    # it must not manufacture a plan-named policy version.
    class_meeting_id = _create_class_meeting(
        fixture,
        member_ids=[fixture["member_id"]],
        scores={fixture["member_id"]: {"MORNING": 7, "AFTERNOON": 7, "KONPA": 4}},
    )
    _attach_event_to_current_cycle(fixture, class_meeting_id)
    before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    class_preview = dry_run_class_meeting_settlement(
        actor_user_id=admin_id, event_group_id=class_meeting_id
    )
    class_item = class_preview["entries"][0]
    assert class_item["status"] == "READY"
    assert class_item["morning_points"] == 7
    assert class_item["afternoon_points"] == 7
    assert class_item["konpa_points"] == 4
    assert class_item["final_points"] == 18

    # One home-group member and one cross-group member each receive a single
    # cycle attendance proposal and a single 40-point course proposal.
    session = create(fixture, ["Y1-ACCOUNTING-ANALYSIS-TASK"])
    _submit_without_evidence(session)
    course_snapshot = session["courses"][0]
    course_reference = json.loads(course_snapshot["rule_reference_json"])
    assert course_snapshot["course_credit_snapshot"] == 40
    assert course_snapshot["credit_rule_version_id"] == binding["course_credit_rule_version_id"]
    assert course_reference["mapping_status"] == "BOUND"
    assert course_reference["course_rule_version_id"] == binding["course_credit_rule_version_id"]
    study_preview = dry_run_study_meeting_settlement(
        actor_user_id=admin_id, session_id=session["id"]
    )
    assert study_preview["persisted"] is False
    assert study_preview["totals"]["proposed_points"] == 88
    for member_id in (fixture["member_id"], fixture["other_member_id"]):
        member_entries = [
            item for item in study_preview["entries"] if item["member_id"] == member_id
        ]
        assert {item["entry_kind"] for item in member_entries} == {
            "GROUP_MEETING_ATTENDANCE",
            "COURSE_COMPLETION",
        }
        assert {
            item["entry_kind"]: (item["status"], item["points"])
            for item in member_entries
        } == {
            "GROUP_MEETING_ATTENDANCE": ("READY", 4.0),
            "COURSE_COMPLETION": ("READY", 40.0),
        }
        assert sum(item["points"] for item in member_entries) == 44.0
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before
    assert class_preview["write_proof"]["ledger_entries_delta"] == 0
    assert study_preview["write_proof"]["ledger_entries_delta"] == 0


def test_initial_resume_and_restart_all_freeze_the_explicit_mapping() -> None:
    fixture = _seed_group_leader_fixture()
    plan_id = _exact_2026_plan()
    admin_id = _admin_id()

    initial = bind_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["other_class_id"],
        plan_version_id=plan_id,
        cohort_month=1,
        started_at="2026-09-06T00:00:00+00:00",
    )
    resumed = resume_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["other_class_id"],
        plan_version_id=plan_id,
        cohort_month=1,
        started_at="2026-10-01T00:00:00+00:00",
        start_cycle_index=1,
        reason="G5.3 RESUME 映射冻结验证",
    )
    restarted = restart_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["other_class_id"],
        plan_version_id=plan_id,
        cohort_month=1,
        started_at="2026-11-01T00:00:00+00:00",
        reason="G5.3 RESTART 映射冻结验证",
    )
    rows = [
        initial["binding"],
        resumed["binding"],
        restarted["binding"],
    ]
    assert all(row["credit_rule_version_id"] is not None for row in rows)
    assert all(row["course_credit_rule_version_id"] is not None for row in rows)
    assert len({row["credit_rule_version_id"] for row in rows}) == 1
    assert len({row["course_credit_rule_version_id"] for row in rows}) == 1


def _resolve_mapping() -> dict | None:
    from app.db import connect

    connection = connect()
    try:
        return resolve_credit_rule_mapping(
            connection, plan_key="standard-3y", version_label="2026"
        )
    finally:
        connection.close()


def test_0048_backfills_existing_exact_plan_binding_without_repricing() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    migration_root = repo_root / "migrations" / "sqlite"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        for path in sorted(migration_root.glob("*.sql")):
            if path.name == "0048_learning_plan_credit_rule_mapping.sql":
                continue
            connection.executescript(path.read_text(encoding="utf-8"))
        now = _now()
        connection.execute(
            "INSERT INTO org_units(id, unit_code, name, unit_type, is_active, created_at, updated_at) "
            "VALUES ('g5-3-backfill-class', 'G5_3_BACKFILL_CLASS', 'G5.3回填班', 'CLASS', 1, ?, ?)",
            (now, now),
        )
        plan_cursor = connection.execute(
            "INSERT INTO learning_plan_versions "
            "(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES ('standard-3y', '标准班三年学习计划', '2026', 36, 'PUBLISHED', ?, ?)",
            (now, now),
        )
        plan_id = int(plan_cursor.lastrowid)
        cycle_cursor = connection.execute(
            "INSERT INTO learning_plan_cycles "
            "(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
            "VALUES (?, 1, 1, 1, '第1学习周期', ?, ?)",
            (plan_id, now, now),
        )
        legacy_rule_cursor = connection.execute(
            "INSERT INTO learning_credit_rule_versions "
            "(rule_set_key, version_label, status, created_at, updated_at) "
            "VALUES ('LEGACY_G5_3', '0.9', 'PUBLISHED', ?, ?)",
            (now, now),
        )
        legacy_rule_id = int(legacy_rule_cursor.lastrowid)
        connection.execute(
            "INSERT INTO class_learning_bindings "
            "(class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
            "credit_rule_version_id, created_at, updated_at) "
            "VALUES ('g5-3-backfill-class', ?, 1, ?, 'ACTIVE', ?, ?, ?)",
            (plan_id, now, legacy_rule_id, now, now),
        )
        connection.commit()
        connection.executescript(
            (migration_root / "0048_learning_plan_credit_rule_mapping.sql").read_text(
                encoding="utf-8"
            )
        )
        row = connection.execute(
            "SELECT b.credit_rule_version_id, b.course_credit_rule_version_id, "
            "g.rule_set_key, g.version_label AS generic_version, "
            "c.plan_key AS course_plan_key, c.version_label AS course_version "
            "FROM class_learning_bindings b "
            "JOIN learning_credit_rule_versions g ON g.id=b.credit_rule_version_id "
            "JOIN learning_plan_credit_rule_versions c "
            "ON c.id=b.course_credit_rule_version_id "
            "WHERE b.class_org_unit_id='g5-3-backfill-class'"
        ).fetchone()
        assert row
        assert row["credit_rule_version_id"] is not None
        assert row["course_credit_rule_version_id"] is not None
        assert row["rule_set_key"] == "LEGACY_G5_3"
        assert row["generic_version"] == "0.9"
        assert row["course_plan_key"] == "STANDARD_3Y_2026"
        assert row["course_version"] == "2026.1"
    finally:
        connection.close()


def test_unmapped_binding_captures_pending_course_without_guessing_points() -> None:
    fixture = _seed_group_leader_fixture()
    session = create(fixture, ["Y1-ACCOUNTING-ANALYSIS-TASK"])
    _submit_without_evidence(session)

    preview = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    group_item = next(
        item for item in preview["entries"]
        if item["entry_kind"] == "GROUP_MEETING_ATTENDANCE"
    )
    course_item = next(
        item for item in preview["entries"]
        if item["entry_kind"] == "COURSE_COMPLETION"
    )
    assert group_item["status"] == "BLOCKED"
    assert group_item["points"] == 0
    assert any("RULE_MAPPING_MISSING" in reason for reason in group_item["reasons"])
    assert course_item["status"] == "BLOCKED"
    assert course_item["points"] == 0
    assert any("RULE_MAPPING_MISSING" in reason for reason in course_item["reasons"])
    stored = fetch_one(
        "SELECT course_credit_snapshot, course_rule_status, credit_rule_version_id "
        "FROM study_meeting_courses WHERE study_meeting_session_id=?",
        (session["id"],),
    )
    assert stored == {
        "course_credit_snapshot": None,
        "course_rule_status": "PENDING",
        "credit_rule_version_id": None,
    }
    assert preview["totals"]["proposed_points"] == 0
    assert preview["write_proof"]["ledger_entries_delta"] == 0


def test_bound_plan_and_generic_rule_can_be_retired_without_blocking_existing_preview() -> None:
    fixture = _seed_group_leader_fixture()
    plan_id = _exact_2026_plan()
    admin_id = _admin_id()
    restarted = restart_class_learning_plan(
        actor_user_id=admin_id,
        class_org_unit_id=fixture["class_id"],
        plan_version_id=plan_id,
        cohort_month=1,
        started_at="2026-09-06T00:00:00+00:00",
        reason="G5.3 RETIRED 兼容验证",
    )
    binding_id = restarted["binding"]["id"]
    binding = fetch_one(
        "SELECT credit_rule_version_id, course_credit_rule_version_id "
        "FROM class_learning_bindings WHERE id=?",
        (binding_id,),
    )
    class_meeting_id = _create_class_meeting(
        fixture,
        member_ids=[fixture["member_id"]],
        event_date="2026-09-06",
    )
    _attach_event_to_current_cycle(fixture, class_meeting_id)
    session = create(fixture, ["Y1-ACCOUNTING-ANALYSIS-TASK"])
    _submit_without_evidence(session)
    ledger_before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    with transaction() as connection:
        execute(
            connection,
            "UPDATE learning_plan_versions SET status='RETIRED' WHERE id=?",
            (plan_id,),
        )
        execute(
            connection,
            "UPDATE learning_credit_rule_versions SET status='RETIRED' WHERE id=?",
            (binding["credit_rule_version_id"],),
        )

    try:
        class_preview = dry_run_class_meeting_settlement(
            actor_user_id=admin_id, event_group_id=class_meeting_id
        )
        assert class_preview["entries"][0]["status"] == "READY"
        study_preview = dry_run_study_meeting_settlement(
            actor_user_id=admin_id, session_id=session["id"]
        )
        assert all(item["status"] == "READY" for item in study_preview["entries"])
        assert study_preview["totals"]["proposed_points"] == 88

        with pytest.raises(ValueError, match="只能选择已发布"):
            restart_class_learning_plan(
                actor_user_id=admin_id,
                class_org_unit_id=fixture["class_id"],
                plan_version_id=plan_id,
                cohort_month=1,
                started_at="2026-10-01T00:00:00+00:00",
                reason="退役计划不得开启新轮次",
            )

        assert _resolve_mapping() is None
        assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == ledger_before
    finally:
        # Keep the isolated database reusable for the rest of the suite; this
        # is not a production mutation and does not affect the RETIRED
        # assertions above.
        with transaction() as connection:
            execute(
                connection,
                "UPDATE learning_plan_versions SET status='PUBLISHED' WHERE id=?",
                (plan_id,),
            )
            execute(
                connection,
                "UPDATE learning_credit_rule_versions SET status='PUBLISHED' WHERE id=?",
                (binding["credit_rule_version_id"],),
            )
