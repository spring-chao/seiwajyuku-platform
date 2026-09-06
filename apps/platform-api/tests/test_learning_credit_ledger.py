from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.db import execute, fetch_one, transaction
from app.services.learning_credits import (
    dry_run_study_meeting_settlement,
    post_credit_entry,
    reverse_credit_entry,
    settle_study_meeting,
)
from app.services.study_meetings import (
    confirm_study_meeting_course_completion,
)
from test_study_meeting_evidence import create
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def credit_test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_EVIDENCE_ENABLED", "false")
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _use_credit_plan(f: dict, course_key: str | None = None) -> None:
    binding = fetch_one(
        "SELECT b.id, p.plan_key, p.version_label FROM class_learning_bindings b "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id WHERE b.class_org_unit_id=?",
        (f["class_id"],),
    )
    assert binding
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO learning_credit_rule_versions "
            "(rule_set_key, version_label, status, created_at, updated_at) "
            "VALUES (?, ?, 'PUBLISHED', ?, ?)",
            (binding["plan_key"], binding["version_label"], "2026-09-06", "2026-09-06"),
        )
        generic_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        execute(
            connection,
            "INSERT INTO learning_credit_rules "
            "(rule_version_id, rule_key, credit_category, credit_type, settlement_model, points, rule_snapshot_json, created_at, updated_at) "
            "VALUES (?, 'GROUP_MEETING_ATTENDANCE', 'STANDARD_LEARNING', 'GROUP_MEETING_ATTENDANCE', 'CYCLE_ONCE', 4, '{}', ?, ?)",
            (generic_id, "2026-09-06", "2026-09-06"),
        )
        execute(
            connection,
            "INSERT INTO learning_credit_rules "
            "(rule_version_id, rule_key, credit_category, credit_type, settlement_model, rule_snapshot_json, created_at, updated_at) "
            "VALUES (?, 'COURSE_COMPLETION', 'STANDARD_LEARNING', 'COURSE_COMPLETION', 'COURSE_COMPLETION', '{}', ?, ?)",
            (generic_id, "2026-09-06", "2026-09-06"),
        )
        execute(
            connection,
            "UPDATE class_learning_bindings SET credit_rule_version_id=? "
            "WHERE class_org_unit_id=?",
            (generic_id, f["class_id"]),
        )
        if course_key:
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rule_versions "
                "(plan_key, version_label, status, created_at, updated_at) VALUES (?, ?, 'DRAFT', ?, ?)",
                (binding["plan_key"], binding["version_label"], "2026-09-06", "2026-09-06"),
            )
            rule_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            execute(
                connection,
                "INSERT INTO learning_plan_credit_rules "
                "(rule_version_id, course_key, course_name, year_index, credit_points, status, source, aliases_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 40, 'CONFIGURED', 'BASELINE', '[]', ?, ?)",
                (rule_id, course_key, course_key, "2026-09-06", "2026-09-06"),
            )


def _submit_without_evidence(session: dict) -> None:
    with transaction() as connection:
        execute(
            connection,
            "UPDATE study_meeting_sessions SET status='SUBMITTED', submitted_at=updated_at "
            "WHERE id=?",
            (session["id"],),
        )


def test_group_meeting_dry_run_is_cycle_once_and_does_not_write() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f)
    session = create(f)
    _submit_without_evidence(session)

    before = fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]
    preview = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )

    assert preview["mode"] == "DRY_RUN"
    assert preview["persisted"] is False
    assert preview["totals"]["proposed_points"] == 8
    assert {item["idempotency_key"] for item in preview["entries"]} == {
        f"GROUP_MEETING:{f['member_id']}:{f['learning_cycle_id']}",
        f"GROUP_MEETING:{f['other_member_id']}:{f['learning_cycle_id']}",
    }
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"] == before


def test_course_completion_fact_and_plan_rule_version_gate() -> None:
    f = _seed_group_leader_fixture()
    key = "Y1-ACCOUNTING-ANALYSIS-TASK"
    _use_credit_plan(f, key)
    session = create(f, [key])
    _submit_without_evidence(session)

    pending_plan = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    course = next(item for item in pending_plan["entries"] if item["entry_kind"] == "COURSE_COMPLETION")
    assert course["points"] == 40
    assert course["status"] == "BLOCKED"
    assert any("课程积分版本未发布" in reason for reason in course["reasons"])
    completion_count = fetch_one(
        "SELECT COUNT(*) AS n FROM study_meeting_course_completions "
        "WHERE study_meeting_course_id=(SELECT id FROM study_meeting_courses WHERE study_meeting_session_id=?)",
        (session["id"],),
    )["n"]
    assert completion_count == 2

    with transaction() as connection:
        now = "2026-09-06T00:00:00+00:00"
        execute(
            connection,
            "UPDATE learning_plan_credit_rule_versions SET status='PUBLISHED' "
            "WHERE plan_key=(SELECT p.plan_key FROM learning_plan_versions p "
            "JOIN class_learning_bindings b ON b.plan_version_id=p.id WHERE b.class_org_unit_id=?)",
            (f["class_id"],),
        )
    ready = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    course_rows = [item for item in ready["entries"] if item["entry_kind"] == "COURSE_COMPLETION"]
    assert len(course_rows) == 2
    assert all(item["status"] == "READY" and item["points"] == 40 for item in course_rows)

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        posted = settle_study_meeting(actor_user_id=_admin_id(), session_id=session["id"])
        repeated = settle_study_meeting(actor_user_id=_admin_id(), session_id=session["id"])
    assert posted["persisted"] is True
    assert posted["total_points"] == 88
    assert repeated["idempotent"] is True
    assert fetch_one(
        "SELECT COUNT(*) AS n FROM learning_credit_entries "
        "WHERE source_type IN ('STUDY_MEETING_ATTENDANCE', 'STUDY_MEETING_COURSE') "
        "AND source_id IN (?, ?)",
        (str(session["id"]), str(fetch_one("SELECT id FROM study_meeting_courses WHERE study_meeting_session_id=?", (session["id"],))["id"])),
    )["n"] == 4


def test_completion_confirmation_can_block_only_the_course_credit() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f, "Y1-HAPPINESS-ASSESSMENT")
    session = create(f, ["Y1-HAPPINESS-ASSESSMENT"])
    _submit_without_evidence(session)
    confirm_study_meeting_course_completion(
        actor_user_id=_admin_id(), session_id=session["id"],
        course_key="Y1-HAPPINESS-ASSESSMENT", member_id=f["member_id"],
        completion_status="NOT_COMPLETED", note="对账抽查",
    )
    preview = dry_run_study_meeting_settlement(
        actor_user_id=_admin_id(), session_id=session["id"]
    )
    group = [item for item in preview["entries"] if item["entry_kind"] == "GROUP_MEETING_ATTENDANCE"]
    course = [item for item in preview["entries"] if item["entry_kind"] == "COURSE_COMPLETION"]
    assert all(item["status"] == "READY" for item in group)
    assert course[0]["status"] == "BLOCKED"
    assert any("实际完成尚未确认" in reason for reason in course[0]["reasons"])


def test_settlement_posts_ready_group_entries_when_course_is_blocked() -> None:
    f = _seed_group_leader_fixture()
    _use_credit_plan(f, "Y1-ACCOUNTING-ANALYSIS-TASK")
    session = create(f, ["Y1-ACCOUNTING-ANALYSIS-TASK"])
    _submit_without_evidence(session)
    confirm_study_meeting_course_completion(
        actor_user_id=_admin_id(), session_id=session["id"],
        course_key="Y1-ACCOUNTING-ANALYSIS-TASK", member_id=f["member_id"],
        completion_status="NOT_COMPLETED", note="仅阻塞课程分",
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        settled = settle_study_meeting(actor_user_id=_admin_id(), session_id=session["id"])

    assert settled["total_points"] == 8
    assert len(settled["entries"]) == 2
    assert all(item["credit_type"] == "GROUP_MEETING_ATTENDANCE" for item in settled["entries"])


def test_ledger_reversal_is_append_only_and_idempotent() -> None:
    actor = _admin_id()
    key = f"MANUAL:{uuid4().hex}"
    item = {
        "member_id": int(fetch_one("SELECT id FROM members ORDER BY id LIMIT 1")["id"]),
        "credit_category": "STANDARD_LEARNING",
        "credit_type": "MANUAL_ADJUSTMENT",
        "points": 40,
        "source_type": "MANUAL_ADJUSTMENT",
        "source_id": key,
        "rule_key": "MANUAL_ADJUSTMENT",
        "rule_version": "2026.1",
        "rule_snapshot": {"rule_version_status": "PUBLISHED", "reason": "测试补分"},
        "occurred_at": "2026-09-06",
        "idempotency_key": key,
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        original = post_credit_entry(actor_user_id=actor, item=item)
        reversal = reverse_credit_entry(actor_user_id=actor, entry_id=original["id"], reason="核对后冲销")
        repeated = reverse_credit_entry(actor_user_id=actor, entry_id=original["id"], reason="核对后冲销")
    assert reversal["points"] == -40
    assert repeated["id"] == reversal["id"]
    assert fetch_one("SELECT status FROM learning_credit_entries WHERE id=?", (original["id"],))["status"] == "REVERSED"
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries WHERE idempotency_key=?", (f"REVERSAL:{original['id']}",))["n"] == 1
