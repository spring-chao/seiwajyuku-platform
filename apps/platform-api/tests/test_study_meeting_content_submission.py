"""A leader's viewing choice must survive create/submit and operations reads."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.study_meetings import _member_session
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.study_meeting_evidence import upload_evidence
from app.services.study_meetings import (
    StudyMeetingError, create_study_meeting, get_study_meeting_context,
    get_study_meeting_record_for_operations, list_study_meeting_records,
    submit_study_meeting,
)
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture
def meeting(monkeypatch, tmp_path):
    for key in ("STUDY_MEETING_SUBMISSION_ENABLED", "STUDY_MEETING_EVIDENCE_ENABLED"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    return _seed_group_leader_fixture()


def content_payload(f, cycle_index=4, completed=True, confirmed=True):
    with transaction() as connection:
        cycle = execute(connection, "SELECT * FROM class_learning_cycles WHERE id=?", (f["learning_cycle_id"],)).fetchone()
        execute(connection, "UPDATE learning_plan_cycles SET cycle_index=?, year_index=? WHERE id=?",
                (cycle_index, (cycle_index - 1) // 12 + 1, cycle["plan_cycle_id"]))
        execute(connection, "UPDATE class_learning_cycles SET learning_cycle_index=? WHERE id=?",
                (cycle_index, f["learning_cycle_id"]))
    assignment = get_study_meeting_context(member_id=f["member_id"], group_org_unit_id=f["group_id"])["assignment"]
    results = [{"content_key": item["content_key"], "completed": completed, "confirmed": confirmed}
               for item in assignment["meeting_plan"]["learning_contents"]]
    return dict(group_org_unit_id=f["group_id"], member_ids=[f["member_id"]],
                cross_group_member_ids=[f["other_member_id"]], meeting_date=None,
                learning_cycle_id=f["learning_cycle_id"], learning_content_results=results)


def create(f, payload):
    return create_study_meeting(member_id=f["member_id"], has_course=False, **payload)


@pytest.mark.parametrize("cycle_index,course_count", [(4, 1), (6, 1), (28, 0)])
def test_viewing_choice_survives_api_submit_and_operations(meeting, cycle_index, course_count):
    f = meeting
    payload = content_payload(f, cycle_index)
    app.dependency_overrides[_member_session] = lambda: {"member_id": f["member_id"]}
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/study-meetings", json=payload)
        assert response.status_code == 200, response.text
        draft = response.json()["data"]
    finally:
        app.dependency_overrides.pop(_member_session, None)
    assert draft["has_course"] == bool(course_count)
    assert len(draft["courses"]) == course_count
    assert draft["learning_content_results"] == payload["learning_content_results"]
    image = io.BytesIO()
    Image.new("RGB", (12, 12), "white").save(image, "JPEG")
    upload_evidence(member_id=f["member_id"], session_id=draft["id"], content=image.getvalue(), content_type="image/jpeg")
    submitted = submit_study_meeting(member_id=f["member_id"], session_id=draft["id"])
    assert submitted["learning_content_results"] == payload["learning_content_results"]
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    row = next(item for item in list_study_meeting_records(actor_user_id=admin_id) if item["id"] == draft["id"])
    assert row["has_course"] == bool(course_count)
    assert len(row["courses"]) == course_count
    detail = get_study_meeting_record_for_operations(actor_user_id=admin_id, session_id=draft["id"])
    assert detail["courses"] == submitted["courses"]
    with pytest.raises(StudyMeetingError, match="已有学习会"):
        create(f, payload)
    if course_count:
        # No credit-policy mapping means a readable course fact, not invented points.
        assert detail["courses"][0]["course_rule_status"] == "PENDING"
        assert detail["courses"][0]["course_credit_snapshot"] is None


@pytest.mark.parametrize("confirmed", [True, False])
def test_not_watched_or_unanswered_never_becomes_a_course(meeting, confirmed):
    payload = content_payload(meeting, completed=False, confirmed=confirmed)
    session = create(meeting, payload)
    assert session["courses"] == []
    assert session["learning_content_results"] == payload["learning_content_results"]


@pytest.mark.parametrize("invalid", ["cycle", "unknown", "duplicate", "missing", "unconfirmed", "legacy"])
def test_invalid_content_is_rejected_without_creating_a_fact(meeting, invalid):
    payload = content_payload(meeting)
    if invalid == "cycle": payload["learning_cycle_id"] += 10000
    if invalid == "unknown": payload["learning_content_results"][0]["content_key"] = "foreign-cycle-content"
    if invalid == "duplicate": payload["learning_content_results"] *= 2
    if invalid == "missing": payload["learning_content_results"] = []
    if invalid == "unconfirmed": payload["learning_content_results"][0]["confirmed"] = False
    if invalid == "legacy": payload["course_keys"] = []
    with pytest.raises(StudyMeetingError):
        create(meeting, payload)
    assert fetch_one("SELECT COUNT(*) AS n FROM study_meeting_sessions WHERE study_group_org_unit_id=?", (meeting["group_id"],))["n"] == 0


def test_failed_upload_draft_can_sync_a_changed_viewing_choice(meeting):
    payload = content_payload(meeting, completed=False)
    first = create(meeting, payload)
    payload["learning_content_results"][0]["completed"] = True
    updated = create(meeting, payload)
    assert updated["id"] == first["id"]
    assert updated["has_course"] is True
    assert updated["learning_content_results"] == payload["learning_content_results"]
    assert create(meeting, payload)["id"] == first["id"]
    audit = fetch_one("SELECT after_json FROM audit_logs WHERE action='study_meeting.draft_content_update' AND resource_id=?", (str(first["id"]),))
    assert json.loads(audit["after_json"])["learning_content_results"] == payload["learning_content_results"]
    payload["learning_content_results"][0]["completed"] = False
    assert create(meeting, payload)["courses"] == []


def test_legacy_empty_course_records_stay_unknown(meeting):
    payload = content_payload(meeting)
    del payload["learning_content_results"]
    del payload["learning_cycle_id"]
    session = create(meeting, payload)
    assert session["courses"] == []
    assert session["learning_content_results"] is None


@pytest.mark.parametrize("change", ["boolean", "points"])
def test_api_rejects_coerced_booleans_and_client_pricing(meeting, change):
    payload = content_payload(meeting)
    if change == "boolean": payload["learning_content_results"][0]["completed"] = "true"
    else: payload["learning_content_results"][0]["credit_points"] = 999
    app.dependency_overrides[_member_session] = lambda: {"member_id": meeting["member_id"]}
    try:
        with TestClient(app) as client:
            assert client.post("/api/v1/study-meetings", json=payload).status_code == 422
    finally:
        app.dependency_overrides.pop(_member_session, None)
