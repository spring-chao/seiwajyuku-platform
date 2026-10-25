from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import enrollment
from app.services.study_meetings import submit_study_meeting
from test_staff_mobile_operations_v1 import _seed_staff_mobile_v1
from test_v12_mvp import _seed_group_leader_fixture
from test_study_meeting_evidence import create, upload
from test_learning_credit_ledger import _use_credit_plan


PREFIX = "/api/v1/wechat/operations"
EXTRA = ("plans:read", "plans:credit_settlement_preview", "plans:credit_settlement_manage",
         "plans:credit_settlement_post", "study_meetings:attendees_edit",
         "enrollment:read", "enrollment:review", "enrollment:payment_confirm", "enrollment:enroll", "exports:normal", "attendance:view", "attendance:create", "attendance:update", "attendance:manage")


def grant(f, permissions, org_id):
    now = datetime.now(UTC).isoformat()
    role = "mobile_v2_" + uuid4().hex
    with transaction() as connection:
        execute(connection, "INSERT INTO roles(role_key,role_name,is_system,is_active,created_at,updated_at) VALUES (?,'移动测试',0,1,?,?)", (role, now, now))
        for permission in permissions:
            execute(connection, "INSERT INTO role_permissions(role_key,permission_key) VALUES (?,?)", (role, permission))
        execute(connection, "INSERT INTO employee_authorization_grants(employment_id,role_key,org_unit_id,scope_type,status,source_reference,created_by,created_at,updated_at) "
                "VALUES (?,?,?,'SUBTREE','ACTIVE','synthetic-mobile-v2',?,?,?)",
                (f["employment_id"], role, org_id, f["user_id"], now, now))


@pytest.fixture
def mobile(monkeypatch, tmp_path):
    for key, value in {
        "WECHAT_MEMBER_BINDING_ENABLED": "true", "WECHAT_STAFF_MOBILE_OPERATIONS_ENABLED": "true",
        "WECHAT_LOCAL_TEST_MODE": "false", "WECHAT_MINIPROGRAM_APP_ID": "staff-mobile-v1-test-app",
        "WECHAT_MINIPROGRAM_APP_SECRET": "staff-mobile-v1-test-secret",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_COURSE_EDIT_ENABLED", "true")
    monkeypatch.setenv("STUDY_MEETING_PRE_SETTLEMENT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    f = _seed_staff_mobile_v1()
    grant(f, EXTRA, f["center_id"])
    with patch("app.services.wechat_identity.exchange_wechat_code", return_value={"appid": "staff-mobile-v1-test-app", "openid": uuid4().hex}), patch("app.services.wechat_identity.verify_wechat_phone_ownership", return_value=True), TestClient(app) as client:
        binding = client.post("/api/v1/wechat/person-bindings/verify", json={"wx_login_code": "synthetic-login", "name": f["staff_name"], "phone": f["staff_phone"], "phone_verification": "synthetic-phone-proof"})
        assert binding.status_code == 200, binding.text
        headers = {"Authorization": "Bearer " + binding.json()["data"]["access_token"]}
        yield f, client, headers


def test_renewal_copy_status_conflict_scope_and_birthday(mobile):
    f, client, headers = mobile
    cycle_id = f["renewal_cycles"]["FOLLOW_1"]
    detail = client.get(f"{PREFIX}/renewals/{cycle_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["wechat_copy"] and detail.json()["data"]["can_manage"]
    copy = client.get(f"{PREFIX}/members/{f['focal_member_id']}/birthday-copy", headers=headers)
    assert copy.status_code == 200 and "生日快乐" in copy.json()["data"]["draft"]
    assert str(f["focal_phone"]) not in copy.text
    update = client.patch(f"{PREFIX}/renewals/{cycle_id}", headers=headers, json={"status": "RENEWED", "expected_status": "PENDING_FIRST_CONTACT"})
    assert update.status_code == 200, update.text
    assert fetch_one("SELECT status FROM renewal_cycles WHERE id=?", (cycle_id,))["status"] == "RENEWED"
    assert fetch_one("SELECT COUNT(*) AS n FROM renewal_status_history WHERE renewal_cycle_id=? AND to_status='RENEWED'", (cycle_id,))["n"] == 1
    again = client.patch(f"{PREFIX}/renewals/{cycle_id}", headers=headers, json={"status": "NOT_RENEWING", "expected_status": "PENDING_FIRST_CONTACT"})
    assert again.status_code == 400 and "变化" in again.json()["detail"]
    outside = client.get(f"{PREFIX}/members/{f['outside_member_id']}/birthday-copy", headers=headers)
    assert outside.status_code == 403


def test_activity_engine_reuse_receipt_scope_and_temporary_registration(mobile, monkeypatch):
    from app.services import signin_engine
    from test_signin_management import ENV
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    f, client, headers = mobile
    events, registrations, writes = {}, {}, []
    def engine(path, envelope, **kwargs):
        payload = envelope["payload"]
        operation = path.rsplit("/", 1)[-1]
        assert envelope["actor"]["id"] == f["user_id"]
        if operation == "create_event":
            event_id = "synthetic-mobile-event"
            events[event_id] = {"event_id": event_id, "name": payload["event_name"], "org_unit_id": payload["org_unit_id"], "event_date": payload["event_date"], "lifecycle_status": "DRAFT"}
            writes.append(operation)
            return {"ok": True, "event_id": event_id}
        if operation == "admin_events":
            return {"ok": True, "items": list(events.values()), "has_more": False}
        event_id = payload["event_id"]
        if operation == "event_detail":
            return {"ok": True, "event": events[event_id], "rows": registrations.get(event_id, [])}
        writes.append(operation)
        if operation == "registration":
            assert "member_id" not in payload and "platform_member_id" not in payload
            registrations.setdefault(event_id, []).append({"registration_id": "synthetic-registration", "name": payload["name"], "checked": False})
            return {"ok": True, "registration_id": "synthetic-registration"}
        if operation == "event_lifecycle_update":
            events[event_id]["lifecycle_status"] = payload["lifecycle_status"]
            return {"ok": True}
        return {"ok": True}
    monkeypatch.setattr(signin_engine, "engine_request", engine)
    before = fetch_one("SELECT COUNT(*) AS n FROM attendance_records")["n"]
    payload = {"title": "合成现场活动", "event_date": datetime.now(UTC).date().isoformat(), "org_unit_id": f["center_id"], "request_id": uuid4().hex}
    result = client.post(f"{PREFIX}/activities", headers=headers, json=payload)
    assert result.status_code == 200, result.text
    event_id = result.json()["data"]["id"]
    assert client.post(f"{PREFIX}/activities", headers=headers, json=payload).json()["data"]["replayed"]
    assert writes == ["create_event"]
    detail = client.get(f"{PREFIX}/activities/{event_id}", headers=headers).json()["data"]
    assert detail["can_manage"] and detail["can_update"]
    attendee = {"name": "合成临时来宾", "request_id": uuid4().hex}
    first = client.post(f"{PREFIX}/activities/{event_id}/attendees", headers=headers, json=attendee)
    assert first.status_code == 200, first.text
    assert client.post(f"{PREFIX}/activities/{event_id}/attendees", headers=headers, json=attendee).json()["data"]["replayed"]
    final = client.get(f"{PREFIX}/activities/{event_id}", headers=headers).json()["data"]
    assert len(final["attendees"]) == 1 and not final["attendees"][0]["checked"]
    assert fetch_one("SELECT COUNT(*) AS n FROM attendance_records")["n"] == before
    state = client.post(f"{PREFIX}/activities/{event_id}/lifecycle", headers=headers, json={"status": "CONFIRMED"})
    assert state.status_code == 200, state.text
    outside_org = fetch_one("SELECT org_unit_id FROM members WHERE id=?", (f["outside_member_id"],))["org_unit_id"]
    grant(f, ["attendance:view"], outside_org)
    events["outside"] = {"event_id": "outside", "name": "范围外活动", "org_unit_id": outside_org, "event_date": "2026-10-25"}
    assert client.post(f"{PREFIX}/activities/outside/attendees", headers=headers, json={**attendee, "request_id": uuid4().hex}).status_code == 403
    assert client.post(f"{PREFIX}/activities", headers=headers, json={**payload, "org_unit_id": outside_org, "request_id": uuid4().hex}).status_code == 403


def test_uncertain_remote_activity_result_is_never_reissued(mobile, monkeypatch):
    from app.services import signin_engine
    from test_signin_management import ENV
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    f, client, headers = mobile
    calls = []
    def engine(path, envelope, **kwargs):
        calls.append(path)
        raise signin_engine.SigninEngineError("合成未知结果", 502)
    monkeypatch.setattr(signin_engine, "engine_request", engine)
    payload = {"title": "合成未知结果活动", "event_date": "2026-10-25", "org_unit_id": f["center_id"], "request_id": uuid4().hex}
    assert client.post(f"{PREFIX}/activities", headers=headers, json=payload).status_code == 502
    repeated = client.post(f"{PREFIX}/activities", headers=headers, json=payload)
    assert repeated.status_code == 400 and "待核对" in repeated.json()["detail"]
    assert len(calls) == 1


def test_meeting_rate_detail_verify_gate_revision_and_repeat(mobile, monkeypatch):
    f, client, headers = mobile
    meeting_f = _seed_group_leader_fixture()
    _use_credit_plan(meeting_f)
    meeting = create(meeting_f)
    upload(meeting_f, meeting)
    submit_study_meeting(member_id=meeting_f["member_id"], session_id=meeting["id"])
    assert client.get(f"{PREFIX}/study-meeting-records/{meeting['id']}", headers=headers).status_code == 403
    grant(f, ["plans:read", "plans:credit_settlement_preview", "plans:credit_settlement_manage", "plans:credit_settlement_post", "study_meetings:attendees_edit"], meeting_f["class_id"])
    detail = client.get(f"{PREFIX}/study-meeting-records/{meeting['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["participation"]["home_present_count"] == 1
    assert data["participation"]["cross_group_count"] == 1
    assert data["can_settle"] and not data["settlement_enabled"]
    month = str(meeting["meeting_date"])[:7]
    rows = client.get(f"{PREFIX}/study-meeting-records?month={month}", headers=headers).json()["data"]
    row = next(row for row in rows if row["id"] == meeting["id"])
    assert row["participation"]["rate"] == data["participation"]["rate"]
    evidence = client.get(f"{PREFIX}/study-meeting-records/{meeting['id']}/evidence", headers=headers)
    assert evidence.status_code == 200 and evidence.headers["content-type"] == "image/jpeg"
    endpoint = f"{PREFIX}/study-meeting-records/{meeting['id']}/verify-settle"
    blocked = client.post(endpoint, headers=headers, json={"expected_revision": data["revision"]})
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["data"]["review_status"] == "CONFIRMED"
    assert "message" in blocked.json()["data"]["credit_settlement"]
    from test_study_meeting_operations import _automatic_flags
    _automatic_flags(monkeypatch)
    assert client.post(endpoint, headers=headers, json={"expected_revision": "0" * 64}).status_code == 400
    result = client.post(endpoint, headers=headers, json={"expected_revision": data["revision"]})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["credit_settlement"]["status"] == "POSTED", result.text
    repeated = client.post(endpoint, headers=headers, json={"expected_revision": data["revision"]})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["credit_settlement"]["status"] == "POSTED"
    assert fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries WHERE source_type='STUDY_MEETING_ATTENDANCE' AND source_id=?", (str(meeting["id"]),))["n"] == 2


def _application(f):
    now = datetime.now(UTC).isoformat()
    # Complete, synthetic application through the real public service.
    admin = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    link = enrollment.create_enrollment_link(admin, name="合成入塾入口")
    if "raw_token" not in link:
        link = enrollment.rotate_enrollment_link(admin, link["id"])
    values = {"name": "合成入塾学长", "target_shuku_org_unit_id": "org-suzhou", "phone": f"137{uuid4().int % 100_000_000:08d}", "gender": "MALE", "birthday": "1980-01-02", "political_status": "群众", "company_name": "合成企业", "company_address": "合成测试地址", "position": "总经理", "referrer": "合成介绍人", "invoice_type": "NORMAL", "invoice_title": "合成企业", "invoice_tax_id": "SYNTHETIC2026", "industry_category": "制造业", "company_products": "合成产品", "employee_count": 5, "annual_sales": "合成测试区间", "profit_margin": "GE_10_PERCENT", "goal_years": "3", "revenue_growth_target": "2", "profit_growth_target": "1.5", "rules_acknowledged": True}
    enrollment.submit_public_enrollment(link["raw_token"], values, "synthetic-mobile-v2")
    application_id = int(fetch_one("SELECT id FROM member_enrollment_applications WHERE name='合成入塾学长' ORDER BY id DESC")["id"])
    with transaction() as connection:
        execute(connection, "UPDATE member_enrollment_applications SET org_unit_id=?,join_date=?,updated_at=? WHERE id=?", (f["center_id"], "2026-10-25", now, application_id))
    return application_id, values["phone"]


def test_enrollment_review_payment_enroll_and_masked_export(mobile):
    f, client, headers = mobile
    application_id, phone = _application(f)
    endpoint = f"{PREFIX}/applications/{application_id}"
    detail = client.get(endpoint, headers=headers)
    assert detail.status_code == 200, detail.text
    assert phone not in detail.text
    rows = client.get(f"{PREFIX}/applications", headers=headers)
    assert rows.status_code == 200 and phone not in rows.text
    image = client.get(endpoint + "/image-summary", headers=headers)
    assert image.status_code == 200 and phone not in image.text
    assert set(image.json()["data"]) == {"application_no", "name", "phone_masked", "company_name", "target_shuku_name", "org_unit_name", "application_status", "payment_status", "join_date"}
    revision = detail.json()["data"]["application"]["updated_at"]
    approved = client.patch(endpoint + "/review", headers=headers, json={"decision": "APPROVE", "review_note": "合成资料核实通过", "expected_updated_at": revision})
    assert approved.status_code == 200, approved.text
    stale = client.patch(endpoint + "/review", headers=headers, json={"decision": "SAVE", "expected_updated_at": revision})
    assert stale.status_code == 400
    updated = client.get(endpoint, headers=headers).json()["data"]["application"]
    assert client.post(endpoint + "/enroll", headers=headers, json={"expected_updated_at": updated["updated_at"]}).status_code == 400
    payment = client.post(endpoint + "/payment", headers=headers, json={"amount": "100.00", "expected_updated_at": updated["updated_at"]})
    assert payment.status_code == 200, payment.text
    updated = client.get(endpoint, headers=headers).json()["data"]["application"]
    enrolled = client.post(endpoint + "/enroll", headers=headers, json={"expected_updated_at": updated["updated_at"]})
    assert enrolled.status_code == 200, enrolled.text
    assert fetch_one("SELECT application_status FROM member_enrollment_applications WHERE id=?", (application_id,))["application_status"] == "ENROLLED"
