from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app
from app.services.attendance_sync import DeletedAttendanceEvent, _upsert_event_group, sync_from_signin
from app.services.signin_engine import SigninEngineError
from test_signin_management import ENV, _operator
from test_v12_mvp import _seed_group_leader_fixture
from test_wechat_learning_summary import _insert_ledger_entry


def _group(fixture, *, status="ACTIVE", present=False):
    suffix = uuid4().hex
    now = datetime.now(UTC).isoformat()
    with transaction() as c:
        group = execute(c, "INSERT INTO attendance_event_groups(source_key,external_group_id,org_unit_id,study_org_unit_id,title,activity_type,event_date,status,created_at,updated_at) VALUES ('signin',?,?,?,'合成测试活动','CLASS_MEETING','2099-01-01',?,?,?)",
                        (suffix, fixture["class_id"], fixture["class_id"], status, now, now)).lastrowid
        for code in ["MORNING", "AFTERNOON"]:
            session = execute(c, "INSERT INTO attendance_sessions(event_group_id,external_session_id,session_code,status,created_at,updated_at) VALUES (?,?,?,'ACTIVE',?,?)", (group, suffix+code, code, now, now)).lastrowid
            execute(c, "INSERT INTO attendance_records(attendance_session_id,external_record_id,participant_type,attendance_status,score_eligible,received_at,created_at,updated_at) VALUES (?,?,'MEMBER',?,0,?,?,?)", (session, suffix+code, "PRESENT" if present else "ABSENT", now, now, now))
    return group, suffix


def _remote(operation, payload, *, user):
    if operation == "stats":
        return {"checked": 0}
    assert operation == "event_lifecycle_update"
    assert payload["lifecycle_status"] == "CANCELLED"
    return {"ok": True, "lifecycle_status": "CANCELLED"}


def test_delete_removes_all_sessions_and_review_queue_preserves_evidence_and_is_idempotent():
    fixture = _seed_group_leader_fixture()
    group, external = _group(fixture)
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage", "attendance:view", "members:read", "members:detail_view"}, fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request", side_effect=_remote) as remote:
            response = client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers, json={"expected_title": "合成测试活动"})
            assert response.status_code == 200, response.text
            assert response.json()["data"]["session_count"] == 2
            assert remote.call_count == 4
            assert fetch_one("SELECT status FROM attendance_event_groups WHERE id=?", (group,))["status"] == "DELETED"
            assert all(s["status"] == "DELETED" for s in fetch_all("SELECT status FROM attendance_sessions WHERE event_group_id=?", (group,)))
            assert fetch_one("SELECT COUNT(*) AS n FROM attendance_records r JOIN attendance_sessions s ON s.id=r.attendance_session_id WHERE s.event_group_id=?", (group,))["n"] == 2
            assert not any(row["id"] == group for row in client.get("/api/v1/attendance/activity-sessions", headers=headers).json()["data"])
            assert not any(row["id"] == group for row in client.get("/api/v1/attendance/event-groups", headers=headers).json()["data"])
            assert client.get(f"/api/v1/attendance/event-groups/{group}", headers=headers).status_code == 404
            repeated = client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers, json={"expected_title": "合成测试活动"})
            assert repeated.json()["data"]["already_deleted"]
            assert remote.call_count == 4
        with transaction() as c, pytest.raises(DeletedAttendanceEvent):
            _upsert_event_group(c, source_key="signin", external_group_id=external, group_data={}, now=datetime.now(UTC).isoformat())


@pytest.mark.parametrize("permissions,other_scope,expected", [
    ({"attendance:view"}, False, 403),
    ({"attendance:manage", "attendance:view"}, True, 403),
])
def test_delete_cannot_be_used_without_permission_or_outside_scope(permissions, other_scope, expected):
    fixture = _seed_group_leader_fixture()
    group, _ = _group(fixture)
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator(permissions, fixture["other_class_id"] if other_scope else fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request") as remote:
            assert client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers, json={"expected_title": "合成测试活动"}).status_code == expected
            remote.assert_not_called()


@pytest.mark.parametrize("present,remote_stats,expected", [
    (True, {"checked": 0}, 409),
    (False, {"checked": 1}, 409),
    (False, {}, 409),
])
def test_delete_rejects_checkin_facts_and_incomplete_source_statistics(present, remote_stats, expected):
    fixture = _seed_group_leader_fixture()
    group, _ = _group(fixture, present=present)
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage", "attendance:view"}, fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request", return_value=remote_stats) as remote:
            assert client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers, json={"expected_title": "合成测试活动"}).status_code == expected
            assert all(call.args[0] == "stats" for call in remote.call_args_list)
            assert fetch_one("SELECT status FROM attendance_event_groups WHERE id=?", (group,))["status"] == "ACTIVE"


def test_source_failure_keeps_platform_visible_and_audit_records_uncertain_outcome():
    fixture = _seed_group_leader_fixture()
    group, _ = _group(fixture)
    def failing(operation, payload, *, user):
        if operation == "stats": return {"checked": 0}
        raise SigninEngineError("合成源故障", 502)
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage", "attendance:view"}, fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request", side_effect=failing):
            r = client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers, json={"expected_title": "合成测试活动"})
            assert r.status_code == 502
        assert fetch_one("SELECT status FROM attendance_event_groups WHERE id=?", (group,))["status"] == "ACTIVE"
        assert {r["result"] for r in fetch_all("SELECT result FROM audit_logs WHERE action='attendance.event.delete' AND resource_id=?", (str(group),))} == {"PENDING", "FAILED"}


def test_explicit_test_confirmation_allows_test_checkins_but_preserves_original_records():
    fixture = _seed_group_leader_fixture()
    group, _ = _group(fixture, present=True)
    def source(operation, payload, *, user):
        return {"checked": 2} if operation == "stats" else {"lifecycle_status": "CANCELLED"}
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage", "attendance:view"}, fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request", side_effect=source):
            r = client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers,
                               json={"expected_title": "合成测试活动", "confirm_test_activity": True})
            assert r.status_code == 200, r.text
        assert fetch_one("SELECT COUNT(*) AS n FROM attendance_records r JOIN attendance_sessions s ON s.id=r.attendance_session_id WHERE s.event_group_id=? AND r.attendance_status='PRESENT'", (group,))["n"] == 2


def test_changed_title_does_not_cancel_the_source():
    fixture = _seed_group_leader_fixture()
    group, _ = _group(fixture)
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage", "attendance:view"}, fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request") as remote:
            assert client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers,
                                  json={"expected_title": "另一活动"}).status_code == 409
            remote.assert_not_called()


def test_test_confirmation_cannot_delete_settled_learning_credits():
    fixture = _seed_group_leader_fixture()
    group, _ = _group(fixture, present=True)
    _insert_ledger_entry(member_id=fixture["member_id"], class_id=fixture["class_id"],
                        credit_type="CLASS_MEETING_SCORE", credit_category="STANDARD_LEARNING",
                        points=1, occurred_at="2026-09-28T00:00:00+00:00",
                        source_type="ATTENDANCE_CLASS_MEETING", idempotency_key=str(group))
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage", "attendance:view"}, fixture["class_id"])
        with patch("app.services.attendance_deletion.signin_engine.management_request") as remote:
            response = client.request("DELETE", f"/api/v1/attendance/event-groups/{group}", headers=headers,
                                      json={"expected_title": "合成测试活动", "confirm_test_activity": True})
            assert response.status_code == 409
            remote.assert_not_called()
        assert fetch_one("SELECT status FROM attendance_event_groups WHERE id=?", (group,))["status"] == "ACTIVE"
        assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries WHERE source_type='ATTENDANCE_CLASS_MEETING' AND source_id=?", (str(group),))["n"] == 1


def test_sync_does_not_fetch_records_or_resurrect_deleted_groups():
    fixture = _seed_group_leader_fixture()
    group, external = _group(fixture, status="DELETED")
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"items": [{"session_id": external, "event_group": {"external_group_id": external}}]}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs):
            assert url.endswith("/sessions"), "Deleted activities must not import new registration records"
            return Response()
    with patch.dict(os.environ, ENV), patch("app.services.attendance_sync.httpx.Client", Client):
        result = sync_from_signin()
    assert result["status"] == "SUCCESS"
    assert result["ignored"] == 1
    assert result["received_records"] == 0
    assert fetch_one("SELECT status FROM attendance_event_groups WHERE id=?", (group,))["status"] == "DELETED"
