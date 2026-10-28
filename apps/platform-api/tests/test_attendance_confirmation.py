from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import attendance_confirmation as service
from app.services.iam import user_context
from app.services.learning_credits import LearningCreditError
from app.services.attendance_sync import _upsert_record
from test_class_meeting_credits import _create_class_meeting, _admin_id, _admin_headers
from test_v12_mvp import _seed_group_leader_fixture


@pytest.fixture
def meeting():
    return _create_class_meeting(_seed_group_leader_fixture(), event_date="2026-10-27")


def test_makeup_first_arrival_late_and_sync_preserved(meeting):
    record = fetch_one("SELECT r.* FROM attendance_records r JOIN attendance_sessions s "
                       "ON s.id=r.attendance_session_id WHERE s.event_group_id=? LIMIT 1", (meeting,))
    with transaction() as connection:
        execute(connection, "UPDATE attendance_records SET attendance_status='ABSENT', checked_at=NULL WHERE id=?", (record["id"],))
    client = TestClient(app)
    url = f"/api/v1/attendance/records/{record['id']}/makeup"
    payload = {"occurred_at": "2026-10-27T17:07:00+08:00", "reason": "现场纸质名单核实"}
    assert client.post(url, json=payload).status_code == 401
    response = client.post(url, json=payload, headers=_admin_headers())
    assert response.status_code == 200, response.text
    assert fetch_one("SELECT is_late FROM attendance_score_records WHERE attendance_record_id=?", (record["id"],))["is_late"] == 1
    assert client.post(url, json={**payload, "occurred_at": "2026-10-27T17:30:00+08:00"}, headers=_admin_headers()).status_code == 400
    with transaction() as connection:
        _upsert_record(connection, session_id=record["attendance_session_id"], now=datetime.now(UTC).isoformat(), record_data={
            "external_record_id": record["external_record_id"], "member_id": record["member_id"],
            "attendance_status": "ABSENT", "revision": 999})
    updated = fetch_one("SELECT * FROM attendance_records WHERE id=?", (record["id"],))
    assert updated["attendance_status"] == "MANUAL_PRESENT"
    assert "09:07:00" in updated["checked_at"]


def test_october_confirmation_no_posting_and_locks_staff_edits(meeting, monkeypatch):
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    user = user_context(_admin_id())
    result = service.confirm_attendance(group_id=meeting, user=user, reason="全部场次名单核实")
    assert result["settlement_status"] == "BEFORE_START_DATE"
    record = fetch_one("SELECT r.id FROM attendance_records r JOIN attendance_sessions s "
                       "ON s.id=r.attendance_session_id WHERE s.event_group_id=? LIMIT 1", (meeting,))
    response = TestClient(app).post(f"/api/v1/attendance/records/{record['id']}/adjudications",
        json={"adjudication_type": "EARLY_LEAVE", "reason": "不应允许确认后修改"}, headers=_admin_headers())
    assert response.status_code == 409
    assert not fetch_one("SELECT id FROM learning_credit_entries WHERE source_type='ATTENDANCE_CLASS_MEETING' AND source_id=?", (str(meeting),))


def test_november_confirmation_gate_atomic_and_idempotent(monkeypatch):
    class December(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 1, tzinfo=UTC).astimezone(tz)
    monkeypatch.setattr(service, "datetime", December)
    meeting = _create_class_meeting(_seed_group_leader_fixture(), event_date="2026-11-10")
    user = user_context(_admin_id())
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    assert service.confirm_attendance(group_id=meeting, user=user, reason="全部名单复核完成")["settlement_status"] == "PENDING_ENABLEMENT"
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    result = service.confirm_attendance(group_id=meeting, user=user, reason="全部名单复核完成")
    assert result["settlement_status"] == "POSTED"
    count = fetch_one("SELECT COUNT(*) n FROM learning_credit_entries WHERE source_type='ATTENDANCE_CLASS_MEETING' AND source_id=?", (str(meeting),))["n"]
    assert count == 2
    assert service.confirm_attendance(group_id=meeting, user=user, reason="重复确认名单完成")["settlement_status"] == "POSTED"
    assert fetch_one("SELECT COUNT(*) n FROM learning_credit_entries WHERE source_type='ATTENDANCE_CLASS_MEETING' AND source_id=?", (str(meeting),))["n"] == count


def test_confirmation_scope_and_unfinished(meeting, monkeypatch):
    monkeypatch.setattr("app.services.iam.accessible_org_ids", lambda *a, **k: set())
    with pytest.raises(PermissionError):
        service.confirm_attendance(group_id=meeting, user=user_context(_admin_id()), reason="越权核实名单测试")


def test_unfinished_and_missing_identity_cannot_confirm(meeting):
    with pytest.raises(LearningCreditError, match="数据已变化"):
        service.confirm_attendance(group_id=meeting, user=user_context(_admin_id()), reason="核对期间数据变化", expected_fingerprint="0" * 64)
    with transaction() as connection:
        execute(connection, "UPDATE attendance_event_groups SET event_date='2099-01-01' WHERE id=?", (meeting,))
    with pytest.raises(LearningCreditError, match="尚未结束"):
        service.confirm_attendance(group_id=meeting, user=user_context(_admin_id()), reason="未来活动不能确认")
    with transaction() as connection:
        execute(connection, "UPDATE attendance_event_groups SET event_date='2026-10-27' WHERE id=?", (meeting,))
        execute(connection, "UPDATE attendance_records SET member_id=NULL WHERE attendance_session_id IN "
                "(SELECT id FROM attendance_sessions WHERE event_group_id=?)", (meeting,))
    with pytest.raises(LearningCreditError, match="待匹配"):
        service.confirm_attendance(group_id=meeting, user=user_context(_admin_id()), reason="身份未匹配不能确认")


def test_posting_failure_rolls_back_every_entry(monkeypatch):
    class December(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 12, 1, tzinfo=UTC).astimezone(tz)
    monkeypatch.setattr(service, "datetime", December)
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    meeting = _create_class_meeting(_seed_group_leader_fixture(), event_date="2026-11-11")
    insert = service._insert_entry
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("模拟第二条入账失败")
        return insert(*args, **kwargs)
    monkeypatch.setattr(service, "_insert_entry", fail_second)
    with pytest.raises(RuntimeError):
        service.confirm_attendance(group_id=meeting, user=user_context(_admin_id()), reason="验证整笔事务回滚")
    assert not fetch_one("SELECT id FROM learning_credit_entries WHERE source_type='ATTENDANCE_CLASS_MEETING' AND source_id=?", (str(meeting),))
    with transaction() as connection:
        assert service.confirmation_state(connection, meeting) == {"confirmed": False}
