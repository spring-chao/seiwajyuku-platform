from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import fetch_all, fetch_one, execute, transaction
from app.main import app
from app.services.course_credit_rules import list_course_credit_rules
from app.services.study_evidence_storage import EvidenceStorage, EvidenceStorageError, normalize_image, MAX_BYTES
from app.services.study_meeting_evidence import upload_evidence, read_evidence, cleanup_evidence
from app.services.study_meetings import (
    StudyMeetingError, StudyMeetingPermissionError, create_study_meeting,
    submit_study_meeting, get_study_meeting, get_study_meeting_record_for_operations,
    correct_meeting_courses, list_study_meeting_records,
)
from app.services.wechat_identity import verify_member_binding, authorized_group_targets
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def isolated_evidence(monkeypatch, tmp_path):
    for key in ("STUDY_MEETING_SUBMISSION_ENABLED", "STUDY_MEETING_EVIDENCE_ENABLED",
                "STUDY_MEETING_COURSE_EDIT_ENABLED", "WECHAT_MEMBER_BINDING_ENABLED"):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("STUDY_EVIDENCE_RETENTION_HOURS", "168")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")


def photo(fmt="JPEG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, fmt)
    return output.getvalue()


def create(fixture, keys=None):
    keys = keys or []
    return create_study_meeting(
        member_id=fixture["member_id"], group_org_unit_id=fixture["group_id"],
        meeting_date=None, member_ids=[fixture["member_id"]],
        cross_group_member_ids=[fixture["other_member_id"]], has_course=bool(keys),
        course_keys=keys,
    )


def upload(fixture, session):
    return upload_evidence(member_id=fixture["member_id"], session_id=session["id"],
                           content=photo(), content_type="image/jpeg")


def admin_id():
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


@pytest.mark.parametrize("count", [0, 1, 3, 4])
def test_multi_course_fact_snapshot_and_one_photo(count):
    f = _seed_group_leader_fixture()
    directory = list_course_credit_rules()["rules"]
    configured = next(r for r in directory if r["status"] == "CONFIGURED")
    pending = next(r for r in directory if r["status"] == "PENDING")
    ordered = [configured, pending] + [r for r in directory if r not in [configured, pending]]
    keys = [r["course_key"] for r in ordered[:count]]
    original_relations = fetch_all("SELECT * FROM member_org_relations WHERE member_id=?", (f["other_member_id"],))
    before_credits = fetch_one("SELECT COUNT(*) AS n FROM attendance_score_records")["n"]
    before_cycle = fetch_one("SELECT * FROM class_learning_cycles WHERE id=?", (f["learning_cycle_id"],))
    session = create(f, keys)
    assert len(session["courses"]) == count
    assert session["has_course"] == bool(count)
    legacy = fetch_one("SELECT course_key, course_credit_snapshot FROM study_meeting_sessions WHERE id=?", (session["id"],))
    assert legacy == {"course_key": None, "course_credit_snapshot": None}
    if count >= 2:
        assert session["courses"][1]["course_credit_snapshot"] is None
        assert session["courses"][1]["course_rule_status"] == "PENDING"
    for course in session["courses"]:
        assert json.loads(course["rule_reference_json"])["version_label"] == "2026.1"
    with pytest.raises(StudyMeetingError, match="合影"):
        submit_study_meeting(member_id=f["member_id"], session_id=session["id"])
    upload(f, session)
    submitted = submit_study_meeting(member_id=f["member_id"], session_id=session["id"])
    assert submitted["status"] == "SUBMITTED"
    assert len(submitted["courses"]) == count
    assert submitted["evidence"]
    assert submit_study_meeting(member_id=f["member_id"], session_id=session["id"])["id"] == session["id"]
    assert fetch_one("SELECT COUNT(*) AS n FROM attendance_score_records")["n"] == before_credits
    assert fetch_one("SELECT * FROM class_learning_cycles WHERE id=?", (f["learning_cycle_id"],)) == before_cycle
    assert fetch_all("SELECT * FROM member_org_relations WHERE member_id=?", (f["other_member_id"],)) == original_relations
    second = create(f, keys)
    assert second["id"] != session["id"]  # Same cycle may have a second real meeting.
    listed = next(r for r in list_study_meeting_records(actor_user_id=admin_id()) if r["id"] == session["id"])
    assert len(listed["courses"]) == count


def test_duplicates_rejected_and_snapshot_does_not_reprice():
    f = _seed_group_leader_fixture()
    key = list_course_credit_rules()["rules"][0]["course_key"]
    with pytest.raises(StudyMeetingError, match="重复"):
        create(f, [key, key])
    session = create(f, [key])
    upload(f, session)
    submit_study_meeting(member_id=f["member_id"], session_id=session["id"])
    before = session["courses"][0]
    directory = list_course_credit_rules()
    for rule in directory["rules"]:
        if rule["course_key"] == key:
            rule["credit_points"] = 99
    with patch("app.services.study_meetings.list_course_credit_rules", return_value=directory):
        corrected = correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"],
                                            course_keys=[key], expected_course_keys=[key])
    assert corrected["courses"][0]["course_credit_snapshot"] == before["course_credit_snapshot"]
    assert corrected["courses"][0]["rule_reference_json"] == before["rule_reference_json"]


def test_replacement_access_and_cleanup_are_audited_and_idempotent():
    f = _seed_group_leader_fixture()
    session = create(f)
    first = upload(f, session)
    second = upload(f, session)
    assert second["id"] != first["id"]
    rows = fetch_all("SELECT * FROM study_meeting_evidence WHERE study_meeting_session_id=?", (session["id"],))
    assert sum(r["active_slot"] == 1 for r in rows) == 1
    with pytest.raises(StudyMeetingPermissionError):
        read_evidence(session_id=session["id"], member_id=f["other_member_id"])
    content, content_type = read_evidence(session_id=session["id"], member_id=f["member_id"])
    assert content and content_type == "image/jpeg"
    assert read_evidence(session_id=session["id"], actor_user_id=admin_id())[0] == content
    submit_study_meeting(member_id=f["member_id"], session_id=session["id"])
    with pytest.raises(StudyMeetingError, match="草稿"):
        upload(f, session)
    with transaction() as connection:
        execute(connection, "UPDATE study_meeting_evidence SET expires_at='2000-01-01 00:00:00' WHERE id=?", (second["id"],))
    with pytest.raises(StudyMeetingError, match="清理"):
        read_evidence(session_id=session["id"], actor_user_id=admin_id())
    assert cleanup_evidence()["deleted"] == 0
    assert cleanup_evidence(apply=True)["deleted"] >= 2
    assert cleanup_evidence(apply=True)["deleted"] == 0
    assert get_study_meeting(member_id=f["member_id"], session_id=session["id"])["evidence"] is None
    assert submit_study_meeting(member_id=f["member_id"], session_id=session["id"])["status"] == "SUBMITTED"
    actions = {r["action"] for r in fetch_all("SELECT action FROM audit_logs WHERE resource_type='study_meeting_session' AND resource_id=?", (str(session["id"]),))}
    assert {"study_meeting.evidence_upload", "study_meeting.evidence_view", "study_meeting.evidence_cleanup"} <= actions


@pytest.mark.parametrize("content,kind", [(b"script", "image/jpeg"), (b"", "image/png"),
                                        (b"x" * (MAX_BYTES + 1), "image/jpeg")],
                         ids=["fake-image", "empty", "oversized"])
def test_invalid_photo_rejected(content, kind):
    with pytest.raises(EvidenceStorageError):
        normalize_image(content, kind)


def test_decoding_metadata_and_private_storage_guards(monkeypatch):
    output = io.BytesIO()
    image = Image.new("RGB", (32, 24))
    exif = Image.Exif()
    exif[0x010E] = "private metadata"
    image.save(output, "JPEG", exif=exif)
    clean, kind, ext = normalize_image(output.getvalue() + b"<script>payload</script>", "image/jpeg")
    with Image.open(io.BytesIO(clean)) as decoded:
        assert not decoded.getexif()
    assert b"payload" not in clean and kind == "image/jpeg" and ext == "jpg"
    assert normalize_image(photo("PNG"), "image/png")[1] == "image/png"
    with pytest.raises(EvidenceStorageError):
        normalize_image(photo("PNG"), "image/jpeg")
    with pytest.raises(EvidenceStorageError):
        normalize_image(photo("GIF"), "image/gif")
    with pytest.raises(EvidenceStorageError):
        EvidenceStorage().get("../outside.jpg")
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(EvidenceStorageError):
        EvidenceStorage()
    with pytest.raises(StudyMeetingPermissionError):
        cleanup_evidence(apply=True)


def test_operator_correction_scope_flags_and_history(monkeypatch):
    f = _seed_group_leader_fixture()
    keys = [r["course_key"] for r in list_course_credit_rules()["rules"][:3]]
    session = create(f, keys[:1])
    with pytest.raises(StudyMeetingError, match="已提交"):
        correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"], course_keys=keys, expected_course_keys=keys[:1])
    upload(f, session)
    submit_study_meeting(member_id=f["member_id"], session_id=session["id"])
    with patch("app.services.study_meetings.accessible_org_ids", return_value=set()):
        with pytest.raises(StudyMeetingPermissionError):
            correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"], course_keys=keys, expected_course_keys=keys[:1])
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "false")
    with pytest.raises(StudyMeetingPermissionError):
        correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"], course_keys=keys, expected_course_keys=keys[:1])
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "true")
    result = correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"],
                                     course_keys=keys, expected_course_keys=keys[:1], note="补充实际观看课程")
    assert result["status"] == "SUBMITTED" and len(result["courses"]) == 3
    with pytest.raises(StudyMeetingError, match="其他人"):
        correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"], course_keys=[], expected_course_keys=keys[:1])
    audit = fetch_one("SELECT * FROM audit_logs WHERE action='study_meeting.courses_correct' AND resource_id=? ORDER BY id DESC", (str(session["id"]),))
    assert audit["actor_user_id"] == admin_id()
    assert len(json.loads(audit["before_json"])["courses"]) == 1
    assert len(json.loads(audit["after_json"])["courses"]) == 3
    assert audit["created_at"] and audit["purpose"]
    cleared = correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"], course_keys=[], expected_course_keys=keys)
    assert cleared["courses"] == [] and not cleared["has_course"]


def test_legacy_single_course_read_and_clear_never_resurrects():
    f = _seed_group_leader_fixture()
    rule = list_course_credit_rules()["rules"][0]
    session = create(f)
    with transaction() as connection:
        execute(connection, "UPDATE study_meeting_sessions SET course_details_initialized=0, has_course=1, course_key=?, "
                "course_name_snapshot=?, course_credit_snapshot=20, status='SUBMITTED' WHERE id=?",
                (rule["course_key"], rule["course_name"], session["id"]))
    before = get_study_meeting_record_for_operations(actor_user_id=admin_id(), session_id=session["id"])
    assert before["courses"][0]["legacy"]
    corrected = correct_meeting_courses(actor_user_id=admin_id(), session_id=session["id"],
                                        course_keys=[], expected_course_keys=[rule["course_key"]])
    assert corrected["courses"] == [] and not corrected["has_course"]
    assert fetch_one("SELECT course_key FROM study_meeting_sessions WHERE id=?", (session["id"],))["course_key"] == rule["course_key"]


def test_failed_upload_is_tracked_and_cleanup_retryable():
    f = _seed_group_leader_fixture()
    session = create(f)
    with patch.object(EvidenceStorage, "put", side_effect=EvidenceStorageError("unavailable")):
        with pytest.raises(StudyMeetingError):
            upload(f, session)
    row = fetch_one("SELECT * FROM study_meeting_evidence WHERE study_meeting_session_id=?", (session["id"],))
    assert row["active_slot"] is None and row["deleted_at"] is not None
    with patch.object(EvidenceStorage, "delete", side_effect=EvidenceStorageError("unavailable")):
        assert cleanup_evidence(apply=True)["errors"] >= 1
    assert cleanup_evidence(apply=True)["deleted"] >= 1


def test_counselor_fixture_uses_group_capability_and_local_identity_switch(monkeypatch):
    leader = _seed_group_leader_fixture()
    counselor = _seed_group_leader_fixture()
    with transaction() as connection:
        execute(connection, "UPDATE volunteer_appointments SET appointment_key='volunteer_group_counselor' "
                "WHERE person_id=(SELECT person_id FROM member_identities WHERE member_id=?)", (counselor["member_id"],))
    targets = authorized_group_targets(counselor["member_id"])
    assert len(targets) == 1 and targets[0]["group_org_unit_id"] == counselor["group_id"]
    assert targets[0]["position_name"] == "辅导员"
    monkeypatch.setenv("WECHAT_LOCAL_TEST_MODE", "true")
    monkeypatch.setenv("WECHAT_MINIPROGRAM_APP_ID", "b21-local-test")
    a = verify_member_binding(code="dev-code", name="V1.2组长", phone=leader["phone"])
    b = verify_member_binding(code="dev-code", name="V1.2组长", phone=counselor["phone"])
    assert a["member"]["member_id"] != b["member"]["member_id"]
    session = create(counselor)
    upload(counselor, session)
    assert submit_study_meeting(member_id=counselor["member_id"], session_id=session["id"])["status"] == "SUBMITTED"
    with pytest.raises(StudyMeetingPermissionError):
        upload_evidence(member_id=leader["member_id"], session_id=session["id"], content=photo(), content_type="image/jpeg")


def test_api_auth_photo_and_operator_correction(monkeypatch):
    f = _seed_group_leader_fixture()
    monkeypatch.setenv("WECHAT_LOCAL_TEST_MODE", "true")
    binding = verify_member_binding(code="dev-code", name="V1.2组长", phone=f["phone"])
    headers = {"Authorization": "Bearer " + binding["access_token"]}
    session = create(f)
    with TestClient(app) as client:
        url = f"/api/v1/study-meetings/{session['id']}/evidence"
        assert client.get(url).status_code == 401
        assert client.post(url, headers=headers, files={"photo": ("evil.jpg", b"not an image", "image/jpeg")}).status_code == 400
        assert client.post(url, headers=headers, files={"photo": ("photo.jpg", photo(), "image/jpeg")}).status_code == 200
        viewed = client.get(url, headers=headers)
        assert viewed.status_code == 200 and viewed.headers["cache-control"] == "private, no-store"
        assert viewed.headers["x-content-type-options"] == "nosniff"
        assert client.patch(f"/api/v1/study-meetings/records/{session['id']}/courses", headers=headers,
                            json={"course_keys": [], "expected_course_keys": []}).status_code == 401
        oversized = client.post(url, headers=headers, content=b"x" * (MAX_BYTES + 65537))
        assert oversized.status_code == 413


def test_defaults_and_disabled_evidence_cannot_bypass_photo(monkeypatch):
    from app.core.settings import get_settings
    f = _seed_group_leader_fixture()
    session = create(f)
    monkeypatch.delenv("STUDY_MEETING_EVIDENCE_ENABLED")
    monkeypatch.delenv("STUDY_MEETING_COURSE_EDIT_ENABLED")
    assert not get_settings().study_meeting_evidence_enabled
    assert not get_settings().study_meeting_course_edit_enabled
    with pytest.raises(StudyMeetingError, match="尚未开启"):
        upload(f, session)
    with pytest.raises(StudyMeetingError, match="合影"):
        submit_study_meeting(member_id=f["member_id"], session_id=session["id"])


def test_cos_adapter_private_acl_and_no_public_url(monkeypatch):
    monkeypatch.setenv("STUDY_EVIDENCE_STORAGE_BACKEND", "cos")
    monkeypatch.setenv("STUDY_EVIDENCE_COS_BUCKET", "isolated-test-123")
    monkeypatch.setenv("STUDY_EVIDENCE_COS_REGION", "ap-shanghai")
    monkeypatch.setenv("STUDY_EVIDENCE_COS_SECRET_ID", "fake-test-id")
    monkeypatch.setenv("STUDY_EVIDENCE_COS_SECRET_KEY", "fake-test-key")
    client = MagicMock()
    client.get_object.return_value = {"Body": MagicMock(get_raw_stream=lambda: io.BytesIO(b"photo"))}
    with patch("qcloud_cos.CosS3Client", return_value=client):
        storage = EvidenceStorage()
        key = "study-evidence/test/1/" + "a" * 32 + ".jpg"
        storage.put(key, b"photo", "image/jpeg")
        assert client.put_object.call_args.kwargs["ACL"] == "private"
        assert storage.get(key) == b"photo"
        storage.delete(key)
        client.delete_object.assert_called_once_with(Bucket="isolated-test-123", Key=key)


def test_0041_sqlite_forward_rollback_preserves_legacy_record():
    from app.migrations import MIGRATION_ROOT
    connection = sqlite3.connect(":memory:")
    connection.executescript("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT);")
    for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
        if path.name < "0041":
            connection.executescript(path.read_text(encoding="utf-8"))
    # Isolated migration test: no business seeding needed to test legacy columns.
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "INSERT INTO study_meeting_sessions(id, session_code, class_org_unit_id, study_group_org_unit_id, "
        "learning_cycle_id, meeting_date, created_by_member_id, created_by_role, has_course, course_key, "
        "course_name_snapshot, course_credit_snapshot, status, created_at, updated_at) "
        "VALUES (1, 'legacy', 'class', 'group', 1, '2026-01-01', 1, 'GROUP_LEADER', 1, 'old', 'Legacy', 20, 'SUBMITTED', '2026-01-01', '2026-01-01')")
    connection.commit()
    original = connection.execute("SELECT course_key, course_credit_snapshot FROM study_meeting_sessions").fetchone()
    connection.executescript((MIGRATION_ROOT / "sqlite/0041_study_meeting_courses_evidence.sql").read_text(encoding="utf-8"))
    assert connection.execute("SELECT course_details_initialized FROM study_meeting_sessions").fetchone()[0] == 0
    connection.executescript((MIGRATION_ROOT / "rollback/sqlite/0041_study_meeting_courses_evidence.down.sql").read_text(encoding="utf-8"))
    assert connection.execute("SELECT course_key, course_credit_snapshot FROM study_meeting_sessions").fetchone() == original
    connection.close()
