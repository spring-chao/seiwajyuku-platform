from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app
from app.migrations import MIGRATION_ROOT
from app.services.study_meeting_attendees import attendee_options, correct_attendees
from app.services.study_meetings import StudyMeetingError, StudyMeetingPermissionError, _db_timestamp, get_study_meeting, submit_study_meeting
from app.services.wechat_identity import verify_member_binding
from app.services.course_credit_rules import list_course_credit_rules
from test_study_meeting_evidence import isolated_evidence, create, upload, admin_id
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture(autouse=True)
def enable_operator_correction(monkeypatch):
    monkeypatch.setenv("STUDY_MEETING_ATTENDEE_EDIT_ENABLED", "true")


def extra_member(f, *, group=None, status="ACTIVE"):
    with transaction() as connection:
        now = _db_timestamp(connection)
        cursor = execute(connection, "INSERT INTO members(member_code, name, status, org_unit_id, created_at, updated_at) "
                         "VALUES (?, '补录测试学长', ?, ?, ?, ?)",
                         ("B212-" + uuid4().hex[:12], status, f["class_id"], now, now))
        member_id = int(cursor.lastrowid)
        for relation_type, org_id in [("STUDY_CLASS", f["class_id"]), ("STUDY_GROUP", group or f["group_id"])]:
            execute(connection, "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)", (member_id, org_id, relation_type, now, now))
    return member_id


def prepared():
    f = _seed_group_leader_fixture()
    session = create(f, [rule["course_key"] for rule in list_course_credit_rules()["rules"][:3]])
    upload(f, session)
    submit_study_meeting(member_id=f["member_id"], session_id=session["id"])
    return f, session


def correct(f, session, ids, **kwargs):
    return correct_attendees(actor_user_id=admin_id(), session_id=session["id"], member_ids=ids,
                             expected_member_ids=kwargs.pop("expected", [f["member_id"], f["other_member_id"]]), **kwargs)


def facts(session):
    return {
        "attendees": fetch_all("SELECT * FROM study_meeting_attendances WHERE study_meeting_session_id=? ORDER BY member_id", (session["id"],)),
        "courses": fetch_all("SELECT * FROM study_meeting_courses WHERE study_meeting_session_id=? ORDER BY id", (session["id"],)),
        "evidence": fetch_all("SELECT * FROM study_meeting_evidence WHERE study_meeting_session_id=? ORDER BY id", (session["id"],)),
        "session": fetch_one("SELECT * FROM study_meeting_sessions WHERE id=?", (session["id"],)),
        "credits": fetch_all("SELECT * FROM attendance_score_records ORDER BY id"),
    }


def test_remove_and_add_home_cross_preserve_evidence_courses_orgs_and_audit():
    f, session = prepared()
    home = extra_member(f)
    cross = extra_member(f, group=f["other_group_id"])
    before = facts(session)
    orgs = fetch_all("SELECT * FROM member_org_relations ORDER BY id")
    cycles = fetch_all("SELECT * FROM class_learning_cycles ORDER BY id")
    result = correct(f, session, [home, cross, f["other_member_id"]], note="合成合影核对补录")
    assert [item["member_id"] for item in result["home_attendees"]] == [home]
    assert {item["member_id"] for item in result["cross_group_attendees"]} == {cross, f["other_member_id"]}
    assert result["status"] == "SUBMITTED" and result["evidence"]
    assert next(item for item in result["attendees"] if item["member_id"] == home)["added_by_member_id"] is None
    assert next(item for item in result["attendees"] if item["member_id"] == home)["added_by_user_id"] == admin_id()
    # Original retained rows still identify the original submitting member.
    original = next(item for item in before["attendees"] if item["member_id"] == f["other_member_id"])
    assert fetch_one("SELECT * FROM study_meeting_attendances WHERE id=?", (original["id"],)) == original
    after = facts(session)
    for key in ("courses", "evidence", "credits"):
        assert after[key] == before[key]
    assert fetch_all("SELECT * FROM member_org_relations ORDER BY id") == orgs
    assert fetch_all("SELECT * FROM class_learning_cycles ORDER BY id") == cycles
    audit = fetch_one("SELECT * FROM audit_logs WHERE action='study_meeting.attendees_correct' AND resource_id=? ORDER BY id DESC", (str(session["id"]),))
    assert audit["actor_user_id"] == admin_id() and audit["purpose"] == "合成合影核对补录" and audit["created_at"]
    original_ids = json.loads(audit["before_json"])
    revised = json.loads(audit["after_json"])
    assert [item["member_id"] for item in original_ids["home_attendees"]] == [f["member_id"]]
    assert {item["member_id"] for item in revised["cross_group_attendees"]} == {cross, f["other_member_id"]}
    # Submitting member can still read the final facts, but cannot replace the photo/roster.
    assert len(get_study_meeting(member_id=f["member_id"], session_id=session["id"])["attendees"]) == 3


@pytest.mark.parametrize("case", ["other_class", "inactive", "duplicate", "missing", "ambiguous", "expired", "empty"])
def test_invalid_roster_is_rejected_atomically(case):
    f, session = prepared()
    candidate = extra_member(f, status="INACTIVE" if case == "inactive" else "ACTIVE")
    ids = [f["member_id"], candidate]
    if case == "other_class":
        ids = [f["member_id"], f["foreign_member_id"]]
    elif case == "duplicate":
        ids.append(candidate)
    elif case == "missing":
        ids.append(999999999)
    elif case == "empty":
        ids = []
    elif case == "ambiguous":
        with transaction() as connection:
            now = _db_timestamp(connection)
            execute(connection, "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
                    "VALUES (?, ?, 'STUDY_GROUP', 0, ?, ?)", (candidate, f["other_group_id"], now, now))
    elif case == "expired":
        with transaction() as connection:
            execute(connection, "UPDATE member_org_relations SET valid_until='2000-01-01' WHERE member_id=? AND relation_type='STUDY_GROUP'", (candidate,))
    before = facts(session)
    with pytest.raises(StudyMeetingError):
        correct(f, session, ids)
    assert facts(session) == before


def test_options_are_same_class_only_and_report_unavailable_originals():
    f, session = prepared()
    home = extra_member(f)
    invalid = extra_member(f, status="INACTIVE")
    options = attendee_options(actor_user_id=admin_id(), session_id=session["id"])
    assert home in {item["member_id"] for item in options["home_members"]}
    assert f["other_member_id"] in {item["member_id"] for item in options["cross_group_members"]}
    assert invalid not in {item["member_id"] for item in options["home_members"]}
    assert f["foreign_member_id"] not in {item["member_id"] for item in options["cross_group_members"]}
    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (f["other_member_id"],))
    options = attendee_options(actor_user_id=admin_id(), session_id=session["id"])
    assert [item["member_id"] for item in options["unavailable_attendees"]] == [f["other_member_id"]]


@pytest.mark.parametrize("gate", ["flag", "readonly", "credits", "permission", "scope", "draft", "cancelled"])
def test_editor_gates_protect_read_and_write(gate, monkeypatch):
    f, session = prepared()
    if gate == "flag": monkeypatch.setenv("STUDY_MEETING_ATTENDEE_EDIT_ENABLED", "false")
    if gate == "readonly": monkeypatch.setenv("DEPLOYMENT_READ_ONLY", "true")
    if gate == "credits": monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    if gate == "permission": monkeypatch.setattr("app.services.study_meeting_attendees.user_context", lambda actor: {"permissions": ["plans:read"]})
    if gate == "scope": monkeypatch.setattr("app.services.study_meetings.accessible_org_ids", lambda actor: {f["other_class_id"]})
    if gate in ("draft", "cancelled"):
        with transaction() as connection:
            execute(connection, "UPDATE study_meeting_sessions SET status=? WHERE id=?", (gate.upper(), session["id"]))
    before = facts(session)
    for call in (lambda: correct(f, session, [f["member_id"]]),
                 lambda: attendee_options(actor_user_id=admin_id(), session_id=session["id"])):
        with pytest.raises((StudyMeetingError, StudyMeetingPermissionError)):
            call()
    assert facts(session) == before


def test_concurrent_corrections_have_one_winner_and_one_conflict():
    f, session = prepared()
    new_home = extra_member(f)
    new_cross = extra_member(f, group=f["other_group_id"])
    barrier = Barrier(2)
    def change(member_id):
        barrier.wait(timeout=5)
        try:
            correct(f, session, [f["member_id"], member_id])
            return "saved"
        except StudyMeetingError as exc:
            assert "参加人员已被其他人修改" in str(exc)
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, [new_home, new_cross])) == ["conflict", "saved"]
    assert fetch_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action='study_meeting.attendees_correct' AND resource_id=?", (str(session["id"]),))["n"] == 1


def test_audit_failure_rolls_back_roster_and_http_rejects_member_sessions_and_spoofing(monkeypatch):
    f, session = prepared()
    before = facts(session)
    with patch("app.services.study_meeting_attendees.write_audit", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError): correct(f, session, [f["member_id"]])
    assert facts(session) == before
    monkeypatch.setenv("WECHAT_LOCAL_TEST_MODE", "true")
    monkeypatch.setenv("WECHAT_MINIPROGRAM_APP_ID", "b212-test")
    binding = verify_member_binding(code="local-test", name="V1.2组长", phone=f["phone"])
    user = fetch_one("SELECT id, token_version FROM app_users WHERE username='admin'")
    admin_header = {"Authorization": "Bearer " + create_token(user["id"], user["token_version"], "access", timedelta(minutes=5))}
    url = f"/api/v1/study-meetings/records/{session['id']}/attendees"
    payload = {"member_ids": [f["member_id"]], "expected_member_ids": [f["member_id"], f["other_member_id"]]}
    with TestClient(app) as client:
        assert client.patch(url, json=payload).status_code == 401
        assert client.patch(url, json=payload, headers={"Authorization": "Bearer " + binding["access_token"]}).status_code == 401
        with patch("app.api.auth.user_context", return_value={**dict(user), "permissions": ["plans:read"]}):
            assert client.patch(url, json=payload, headers=admin_header).status_code == 403
        assert client.patch(url, json={**payload, "attendance_type": "HOME_GROUP"}, headers=admin_header).status_code == 422
        response = client.patch(url, json=payload, headers=admin_header)
        assert response.status_code == 200, response.text
        assert len(response.json()["data"]["attendees"]) == 1
        assert client.get(f"/api/v1/study-meetings/records/{session['id']}/evidence", headers=admin_header).status_code == 200


def test_sqlite_0042_preserves_original_rows_and_refuses_lossy_rollback():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
      PRAGMA foreign_keys=ON;
      CREATE TABLE members(id INTEGER PRIMARY KEY);
      CREATE TABLE org_units(id TEXT PRIMARY KEY);
      CREATE TABLE class_learning_cycles(id INTEGER PRIMARY KEY);
      CREATE TABLE app_users(id INTEGER PRIMARY KEY);
      CREATE TABLE roles(role_key TEXT PRIMARY KEY);
      CREATE TABLE permissions(permission_key TEXT PRIMARY KEY, permission_name TEXT, sensitive_level TEXT, created_at TEXT);
      CREATE TABLE role_permissions(role_key TEXT, permission_key TEXT, UNIQUE(role_key, permission_key));
      CREATE TABLE schema_migrations(version TEXT PRIMARY KEY);
      INSERT INTO members VALUES (1), (2);
      INSERT INTO org_units VALUES ('class'), ('group');
      INSERT INTO class_learning_cycles VALUES (1);
      INSERT INTO app_users VALUES (1);
      INSERT INTO roles VALUES ('system_admin'), ('operations_admin'), ('read_only');
    """)
    connection.executescript((MIGRATION_ROOT / "sqlite/0036_study_meetings.sql").read_text(encoding="utf-8"))
    connection.execute("INSERT INTO study_meeting_sessions VALUES (1,'legacy','class','group',1,'2026-01-01',1,'GROUP_LEADER',0,NULL,NULL,NULL,'SUBMITTED',NULL,'2026-01-01','2026-01-01')")
    connection.execute("INSERT INTO study_meeting_attendances VALUES (7,1,1,'group','group','HOME_GROUP',1,'2026-01-01','2026-01-01')")
    original = connection.execute("SELECT * FROM study_meeting_attendances").fetchone()
    forward = (MIGRATION_ROOT / "sqlite/0042_study_meeting_attendee_correction.sql").read_text(encoding="utf-8")
    down = (MIGRATION_ROOT / "rollback/sqlite/0042_study_meeting_attendee_correction.down.sql").read_text(encoding="utf-8")
    connection.executescript(forward)
    assert connection.execute("SELECT added_by_user_id FROM study_meeting_attendances").fetchone()[0] is None
    assert {row[0] for row in connection.execute("SELECT role_key FROM role_permissions")} == {"system_admin", "operations_admin"}
    connection.executescript(down)
    assert connection.execute("SELECT * FROM study_meeting_attendances").fetchone() == original
    connection.executescript(forward)
    connection.execute("INSERT INTO study_meeting_attendances(study_meeting_session_id,member_id,home_study_group_org_unit_id,attended_study_group_org_unit_id,attendance_type,added_by_user_id,created_at,updated_at) VALUES (1,2,'group','group','HOME_GROUP',1,'2026-01-01','2026-01-01')")
    with pytest.raises(sqlite3.IntegrityError): connection.executescript(down)
    assert connection.execute("SELECT added_by_user_id FROM study_meeting_attendances WHERE member_id=2").fetchone()[0] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
