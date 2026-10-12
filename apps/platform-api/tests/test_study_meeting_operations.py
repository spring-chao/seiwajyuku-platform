import csv
import io
from unittest.mock import patch

import pytest

from app.db import execute, fetch_one, transaction
from app.services.study_meeting_operations import meeting_credit_preview, export_meeting_records
from app.services.study_meetings import confirm_study_meeting_course_completion, StudyMeetingError
from app.services.study_meetings import correct_meeting_courses, get_study_meeting_record_for_operations
from app.services.study_meeting_attendees import correct_attendees
from app.services.learning_credits import _insert_entry, dry_run_study_meeting_settlement
from app.services.credit_settlement_batches import dry_run_study_meeting_batch, submit_study_meeting_batch_for_approval
from test_learning_credit_ledger import _admin_id, _use_credit_plan, _submit_without_evidence
from test_study_meeting_evidence import create
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_ATTENDEE_EDIT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    monkeypatch.setenv("STUDY_MEETING_PRE_SETTLEMENT_REVIEW_ENABLED", "false")


def prepared(course=False):
    f = _seed_group_leader_fixture()
    _use_credit_plan(f, "OPERATOR_VIDEO" if course else None)
    if course:
        with transaction() as connection:
            execute(connection, "UPDATE learning_plan_credit_rule_versions SET status='PUBLISHED' WHERE id=(SELECT course_credit_rule_version_id FROM class_learning_bindings WHERE class_org_unit_id=?)", (f["class_id"],))
    session = create(f, ["OPERATOR_VIDEO"] if course else None)
    _submit_without_evidence(session)
    return f, session


def test_preview_confirmation_removal_export_share_real_calculation():
    f, session = prepared(course=True)
    args = {"actor_user_id": _admin_id(), "session_id": session["id"]}
    before = fetch_one("SELECT COUNT(*) n FROM learning_credit_entries")["n"]
    preview = meeting_credit_preview(**args)
    assert [item["expected_points"] for item in preview["members"]] == [44, 44]
    confirm_study_meeting_course_completion(**args, course_key="OPERATOR_VIDEO", member_id=f["member_id"], completion_status="NOT_COMPLETED", expected_completion_status="CONFIRMED")
    preview = meeting_credit_preview(**args)
    member = next(item for item in preview["members"] if item["member_id"] == f["member_id"])
    assert member["expected_points"] == 4 and member["course_points"] == 0
    other = next(item for item in preview["members"] if item["member_id"] == f["other_member_id"])
    assert other["course_points"] == 40 and other["expected_points"] == 44
    assert preview["totals"]["proposed_points"] == 48
    with pytest.raises(StudyMeetingError, match="其他人修改"):
        confirm_study_meeting_course_completion(**args, course_key="OPERATOR_VIDEO", member_id=f["member_id"], completion_status="CONFIRMED", expected_completion_status="CONFIRMED")
    correct_attendees(**args, member_ids=[f["member_id"]], expected_member_ids=[f["member_id"], f["other_member_id"]])
    preview = meeting_credit_preview(**args)
    assert len(preview["members"]) == 1 and preview["totals"]["proposed_points"] == 4
    rows = list(csv.DictReader(io.StringIO(export_meeting_records(**args).lstrip("\ufeff"))))
    assert len(rows) == 1 and float(rows[0]["预计新增学分"]) == 4
    assert "未观看（人工确认）" in rows[0]["观看确认"]
    assert "phone" not in rows[0] and f["phone"] not in str(rows)
    assert fetch_one("SELECT COUNT(*) n FROM learning_credit_entries")["n"] == before
    assert fetch_one("SELECT id FROM audit_logs WHERE action='exports.study_meetings.normal' AND resource_id=?", (str(session["id"]),))


def test_preview_deduplicates_posted_credit(monkeypatch):
    f, session = prepared()
    args = {"actor_user_id": _admin_id(), "session_id": session["id"]}
    proposals = dry_run_study_meeting_settlement(**args)
    # Seed a prior approved settlement in the isolated ledger; the retired
    # direct settlement endpoint deliberately remains closed.
    with transaction() as connection:
        for entry in proposals["entries"]:
            _insert_entry(connection, {**entry, "occurred_at": session["meeting_date"]}, status="POSTED", actor_user_id=_admin_id())
    preview = meeting_credit_preview(**args)
    assert all(item["expected_points"] == 0 for item in preview["members"])
    assert all(item["reasons"] for item in preview["members"])


def test_export_scopes_permissions_and_csv_formula_safety():
    f, session = prepared()
    args = {"actor_user_id": _admin_id(), "session_id": session["id"]}
    with patch("app.services.study_meeting_operations.user_context", return_value={"permissions": ["exports:normal"]}), pytest.raises(PermissionError):
        export_meeting_records(**args)
    with patch("app.services.study_meeting_operations.accessible_org_ids", return_value={f["other_class_id"]}), pytest.raises(PermissionError):
        export_meeting_records(**args)
    with patch("app.services.learning_credits.accessible_org_ids", return_value={f["other_class_id"]}), pytest.raises(PermissionError):
        meeting_credit_preview(**args)
    with transaction() as connection:
        execute(connection, "UPDATE members SET name='=FORMULA()' WHERE id=?", (f["member_id"],))
    rows = list(csv.DictReader(io.StringIO(export_meeting_records(**args).lstrip("\ufeff"))))
    assert next(item for item in rows if int(item["学员编号"]) == f["member_id"])["姓名"] == "'=FORMULA()"


def test_pre_settlement_review_has_an_independent_gate_and_keeps_ledger_untouched(monkeypatch):
    f, session = prepared(course=True)
    args = {"actor_user_id": _admin_id(), "session_id": session["id"]}
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "false")
    monkeypatch.setenv("STUDY_MEETING_ATTENDEE_EDIT_ENABLED", "false")
    assert not get_study_meeting_record_for_operations(**args)["can_edit_courses"]
    monkeypatch.setenv("STUDY_MEETING_PRE_SETTLEMENT_REVIEW_ENABLED", "true")
    assert get_study_meeting_record_for_operations(**args)["can_edit_courses"]
    assert get_study_meeting_record_for_operations(**args)["can_edit_attendees"]
    before = fetch_one("SELECT COUNT(*) n FROM learning_credit_entries")["n"]
    confirm_study_meeting_course_completion(**args, course_key="OPERATOR_VIDEO", member_id=f["member_id"], completion_status="NOT_COMPLETED")
    correct_attendees(**args, member_ids=[f["member_id"]], expected_member_ids=[f["member_id"], f["other_member_id"]])
    assert fetch_one("SELECT COUNT(*) n FROM learning_credit_entries")["n"] == before


@pytest.mark.parametrize("protection", ["ledger", "approval", "posting", "course_ledger"])
def test_posted_or_frozen_sources_reject_all_manual_corrections(protection, monkeypatch):
    f, session = prepared(course=protection == "course_ledger")
    args = {"actor_user_id": _admin_id(), "session_id": session["id"]}
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_PRE_SETTLEMENT_REVIEW_ENABLED", "true")
    if protection in {"ledger", "course_ledger"}:
        preview = dry_run_study_meeting_settlement(**args)
        entry = next(item for item in preview["entries"] if item["entry_kind"] == ("COURSE_COMPLETION" if protection == "course_ledger" else "GROUP_MEETING_ATTENDANCE"))
        with transaction() as connection:
            _insert_entry(connection, {**entry, "occurred_at": session["meeting_date"]}, status="POSTED", actor_user_id=_admin_id())
    else:
        monkeypatch.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
        batch = dry_run_study_meeting_batch(**args)
        submit_study_meeting_batch_for_approval(actor_user_id=_admin_id(), batch_id=batch["id"])
        if protection == "posting":
            with transaction() as connection:
                execute(connection, "UPDATE learning_credit_settlement_batches SET status='POSTING' WHERE id=?", (batch["id"],))
    detail = get_study_meeting_record_for_operations(**args)
    assert not detail["can_edit_courses"] and not detail["can_edit_attendees"]
    assert detail["correction_lock_reason"]
    before = fetch_one("SELECT COUNT(*) n FROM learning_credit_entries")["n"]
    with pytest.raises(StudyMeetingError):
        correct_attendees(**args, member_ids=[f["member_id"]], expected_member_ids=[f["member_id"], f["other_member_id"]])
    with pytest.raises(StudyMeetingError):
        correct_meeting_courses(**args, course_keys=[], expected_course_keys=["OPERATOR_VIDEO"] if protection == "course_ledger" else [])
    if protection == "course_ledger":
        with pytest.raises(StudyMeetingError):
            confirm_study_meeting_course_completion(**args, course_key="OPERATOR_VIDEO", member_id=f["member_id"], completion_status="NOT_COMPLETED")
    assert fetch_one("SELECT COUNT(*) n FROM learning_credit_entries")["n"] == before
