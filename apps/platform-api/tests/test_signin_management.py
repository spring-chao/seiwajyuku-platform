from __future__ import annotations

import os
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_password
from app.db import execute, fetch_all, transaction
from app.main import app
from app.services import signin_engine
from app.services.attendance_entry_codes import resolve_entry
from test_v12_mvp import _seed_group_leader_fixture


ENV = {
    "SIGNIN_MANAGEMENT_ENABLED": "true", "SIGNIN_MEMBER_CHECKIN_ENABLED": "true",
    "SIGNIN_API_BASE_URL": "http://127.0.0.1:19801",
    "SIGNIN_PLATFORM_API_KEY": "isolated-platform-test-key",
    "SIGNIN_SERVICE_API_KEY": "isolated-sync-test-key",
    "WECHAT_MEMBER_BINDING_ENABLED": "true", "WECHAT_LOCAL_TEST_MODE": "true",
    "SIGNIN_LEGACY_URL": "https://checkin.example.test/index.html",
}


def _operator(permissions: set[str], org_id: str | None = None) -> tuple[dict, dict]:
    suffix = uuid4().hex
    now = datetime.now(UTC).isoformat()
    role = "test-signin-" + suffix
    with transaction() as connection:
        uid = execute(connection, "INSERT INTO app_users(username,display_name,password_hash,is_active,token_version,created_at,updated_at) VALUES (?,?,?,1,1,?,?)",
                      (suffix, "合成签到工作人员", hash_password("isolated-password"), now, now)).lastrowid
        execute(connection, "INSERT INTO roles(role_key,role_name,is_system,is_active,created_at,updated_at) VALUES (?,?,0,1,?,?)", (role, role, now, now))
        for permission in permissions:
            execute(connection, "INSERT INTO role_permissions(role_key,permission_key) VALUES (?,?)", (role, permission))
        execute(connection, "INSERT INTO user_roles(user_id,role_key,created_at) VALUES (?,?,?)", (uid, role, now))
        execute(connection, "INSERT INTO data_scope_grants(user_id,scope_type,org_unit_id,created_at) VALUES (?,?,?,?)", (uid, "SUBTREE" if org_id else "ALL", org_id, now))
    token = create_token(uid, 1, "access", timedelta(minutes=15))
    return {"Authorization": "Bearer " + token}, {"id": uid, "permissions": sorted(permissions)}


def _bound(client: TestClient, fixture: dict) -> dict:
    with patch("app.services.wechat_identity.exchange_wechat_code", return_value={
        "appid": "isolated-checkin-test", "openid": "isolated-checkin-" + fixture["suffix"],
    }):
        response = client.post("/api/v1/wechat/member-bindings/verify", json={
            "code": "checkin-" + fixture["suffix"], "name": "V1.2组长", "phone": fixture["phone"],
        })
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["data"]["access_token"]}


def _event(fixture: dict, event_id: str = "event-morning") -> dict:
    return {"event_id": event_id, "name": "合成班级学习会", "event_date": datetime.now(UTC).date().isoformat(),
            "activity_type": "CLASS_MEETING", "session_code": "MORNING",
            "org_unit_id": fixture["class_id"], "class_org_unit_id": fixture["class_id"]}


def test_management_requires_auth_and_granular_permission_before_engine_call():
    with patch.dict(os.environ, ENV), TestClient(app) as client, patch.object(signin_engine, "engine_request") as request:
        headers, _ = _operator({"attendance:view"})
        assert client.post("/api/v1/attendance/manage/create_event", json={}).status_code == 401
        assert client.post("/api/v1/attendance/manage/create_event", headers=headers, json={}).status_code == 403
        assert client.post("/api/v1/attendance/manage/clear_all", headers=headers, json={}).status_code == 404
        request.assert_not_called()


def test_organization_scope_fail_closed_and_never_forwards_client_authority():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, ENV), TestClient(app) as client, patch.object(signin_engine, "engine_request") as request:
        headers, _ = _operator({"attendance:create"}, fixture["class_id"])
        for payload, status in (({}, 400), ({"org_unit_id": "missing"}, 400),
                                ({"org_unit_id": fixture["other_class_id"]}, 403),
                                ({"org_unit_id": fixture["class_id"], "actor": {"id": 1}}, 400)):
            response = client.post("/api/v1/attendance/manage/create_event", headers=headers, json=payload)
            assert response.status_code == status, response.text
        request.assert_not_called()


def test_management_reuses_engine_and_writes_sanitized_before_after_audit():
    fixture = _seed_group_leader_fixture()
    before = _event(fixture)
    after = {**before, "name": "合成活动新名称"}
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, user = _operator({"attendance:update"}, fixture["class_id"])
        def engine(path, payload, **kwargs):
            assert payload["actor"]["id"] == user["id"]
            assert payload["allowed_org_unit_ids"] == sorted([fixture["class_id"], fixture["group_id"], fixture["other_group_id"]])
            if path.endswith("event_detail"):
                return {"ok": True, "event": before}
            assert path.endswith("event_update")
            return {"ok": True, "audit": {"target": before["event_id"], "before": before, "after": after},
                    "phone": "synthetic-contact-must-be-removed"}
        with patch.object(signin_engine, "engine_request", side_effect=engine):
            response = client.post("/api/v1/attendance/manage/event_update", headers=headers,
                                   json={"event_id": before["event_id"], "name": after["name"]})
        assert response.status_code == 200, response.text
        assert "phone" not in response.json()
        audit = fetch_all("SELECT result,before_json,after_json FROM audit_logs WHERE actor_user_id=? AND action='attendance.manage.event_update' ORDER BY id", (user["id"],))
        assert [row["result"] for row in audit] == ["PENDING", "SUCCESS"]
        assert "合成活动新名称" in audit[-1]["after_json"]


def test_native_checkin_resolves_identity_server_side_and_preserves_pending_success():
    fixture = _seed_group_leader_fixture()
    event = _event(fixture)
    calls = []
    def engine(path, payload, **kwargs):
        calls.append((path, payload))
        assert payload["member"]["member_id"] == fixture["member_id"]
        if path.endswith("lookup"):
            return {"ok": True, "event": event, "registration": {"id": "registration-a"}, "can_checkin": True}
        return {"ok": True, "msg": "签到成功", "sync_status": "PENDING", "data": {"checked_at": "2026-10-07T01:00:00Z"}}
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers = _bound(client, fixture)
        with patch.object(signin_engine, "engine_request", side_effect=engine):
            response = client.post("/api/v1/wechat/checkin/confirm", headers=headers, json={"event_id": event["event_id"]})
            forged = client.post("/api/v1/wechat/checkin/confirm", headers=headers,
                                 json={"event_id": event["event_id"], "member_id": fixture["other_member_id"]})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "CHECKED_IN"
        assert response.json()["data"]["sync_status"] == "PENDING"
        assert response.json()["data"]["history_kind"] == "learning"
        assert forged.status_code == 422
        assert len(calls) == 2
        assert calls[-1][1]["registration_id"] == "registration-a"


def test_anonymous_context_contains_no_identity_or_registration():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        with patch.object(signin_engine, "engine_request", return_value={"ok": True, "event": _event(fixture),
                         "registration": {"id": "must-not-be-shown"}, "can_checkin": True}) as engine:
            response = client.get("/api/v1/wechat/checkin/context?event_id=event-morning")
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["member"] is None and data["registration"] is None
            assert data["can_checkin"] is False
            assert engine.call_args.args[1]["member"] is None
        assert client.get("/api/v1/wechat/checkin/events").status_code == 401
        assert client.post("/api/v1/wechat/checkin/confirm", json={"event_id": "event-morning"}).status_code == 401


def test_code_rotation_revokes_old_route_token_and_contains_no_identity():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, user = _operator({"attendance:code"}, fixture["class_id"])
        with patch("app.services.attendance_entry_codes.event_detail", return_value=_event(fixture)), \
             patch("app.services.attendance_entry_codes.wechat_code", return_value=(b"synthetic-image", "image/png")) as provider:
            first = client.post("/api/v1/attendance/manage/events/event-morning/code", headers=headers, json={"env_version": "develop"})
            second = client.post("/api/v1/attendance/manage/events/event-morning/code", headers=headers, json={"env_version": "develop"})
        assert first.status_code == second.status_code == 200
        first_token = first.json()["data"]["token"]
        second_token = second.json()["data"]["token"]
        assert 16 <= len(second_token) <= 32
        assert resolve_entry(second_token) == "event-morning"
        with pytest.raises(signin_engine.SigninEngineError, match="失效"):
            resolve_entry(first_token)
        assert provider.call_args.args == (second_token, "develop")
        audit = fetch_all("SELECT after_json FROM audit_logs WHERE actor_user_id=? AND action='attendance.code.regenerate'", (user["id"],))
        assert all(second_token not in row["after_json"] and first_token not in row["after_json"] for row in audit)


def test_immediate_sync_acknowledges_only_complete_increment_and_keeps_retry_on_failure():
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers = {"X-API-Key": ENV["SIGNIN_SERVICE_API_KEY"]}
        assert client.post("/api/v1/attendance/sync/immediate", json={"event_id": "event-a"}).status_code == 401
        with patch("app.api.attendance_manage.sync_from_signin", return_value={"status": "SUCCESS", "run_id": 1, "received_sessions": 1}) as sync:
            response = client.post("/api/v1/attendance/sync/immediate", headers=headers, json={"event_id": "event-a"})
            assert response.status_code == 200
            sync.assert_called_once_with(event_id="event-a")
        for result in ({"status": "PARTIAL", "received_sessions": 1}, {"status": "SUCCESS", "received_sessions": 0}):
            with patch("app.api.attendance_manage.sync_from_signin", return_value=result):
                assert client.post("/api/v1/attendance/sync/immediate", headers=headers, json={"event_id": "event-a"}).status_code == 503
        with patch("app.api.attendance_manage.sync_from_signin", side_effect=RuntimeError("unavailable")):
            assert client.post("/api/v1/attendance/sync/immediate", headers=headers, json={"event_id": "event-a"}).status_code == 503


def test_production_mutations_fail_closed_even_with_feature_flags():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, {**ENV, "APP_ENV": "production", "ALLOW_PRODUCTION_MUTATIONS": "false"}), TestClient(app) as client:
        headers, _ = _operator({"attendance:create"})
        with patch.object(signin_engine, "engine_request") as engine:
            response = client.post("/api/v1/attendance/manage/create_event", headers=headers, json={"org_unit_id": fixture["class_id"]})
            assert response.status_code == 403
            engine.assert_not_called()


def test_group_scope_accepts_verified_parent_context_and_rejects_unrelated_parent():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:create"}, fixture["group_id"])
        payload = {"org_unit_id": fixture["class_id"], "class_org_unit_id": fixture["class_id"],
                   "group_org_unit_id": fixture["group_id"]}
        with patch.object(signin_engine, "engine_request", return_value={"ok": True}) as engine:
            assert client.post("/api/v1/attendance/manage/create_event", headers=headers, json=payload).status_code == 200
            assert engine.call_args.args[1]["allowed_org_unit_ids"] == [fixture["group_id"]]
            engine.reset_mock()
            response = client.post("/api/v1/attendance/manage/create_event", headers=headers,
                                   json={**payload, "org_unit_id": fixture["other_class_id"]})
            assert response.status_code == 400
            engine.assert_not_called()


def test_mixed_action_intersects_permission_scopes_and_status_only_needs_manage():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:manage"}, fixture["class_id"])
        with patch.object(signin_engine, "engine_request", return_value={"ok": True, "event": _event(fixture)}) as engine:
            assert client.post("/api/v1/attendance/manage/event_update", headers=headers,
                               json={"event_id": "event-morning", "status": "OPEN"}).status_code == 200
            engine.reset_mock()
            assert client.post("/api/v1/attendance/manage/event_update", headers=headers,
                               json={"event_id": "event-morning", "status": "OPEN", "name": "合成名称"}).status_code == 403
            engine.assert_not_called()
        with patch.object(signin_engine, "accessible_org_ids", side_effect=[{fixture["class_id"]}, {fixture["other_class_id"]}]):
            with pytest.raises(signin_engine.SigninEngineError, match="共同"):
                signin_engine.combined_scope(1, ["attendance:manage", "attendance:update"])


def test_context_ticket_is_signed_for_verified_identity_one_event_and_five_minutes():
    fixture = _seed_group_leader_fixture()
    with patch.dict(os.environ, {**ENV, "SIGNIN_API_BASE_URL": "https://engine.example.test"}), TestClient(app) as client:
        headers = _bound(client, fixture)
        with patch.object(signin_engine, "engine_request", return_value={"ok": True, "event": _event(fixture),
                         "registration": {"id": "reg-ticket"}, "can_checkin": True}), \
             patch("app.services.native_checkin_ticket.time.time", return_value=100000):
            response = client.get("/api/v1/wechat/checkin/context?event_id=event-morning", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        encoded, signature = data["checkin_ticket"].split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        signed = json.loads(decode(encoded))
        assert signed["purpose"] == "MEMBER_CHECKIN" and signed["exp"] == 100300
        assert signed["event_id"] == "event-morning"
        assert signed["member"]["member_id"] == fixture["member_id"]
        assert signed["member"]["home_class_org_unit_id"] == fixture["class_id"]
        assert signed["binding_id"] > 0 and signed["token_version"] >= 1
        assert "binding_id" not in data["member"] and "token_version" not in data["member"]
        assert "phone" not in signed["member"]
        assert hmac.compare_digest(decode(signature), hmac.new(ENV["SIGNIN_PLATFORM_API_KEY"].encode(), encoded.encode(), hashlib.sha256).digest())
        assert data["engine_confirm_url"] == "https://engine.example.test/native/v1/checkin/confirm"


def test_immediate_sync_keeps_retry_until_exact_registration_fact_is_saved():
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers = {"X-API-Key": ENV["SIGNIN_SERVICE_API_KEY"]}
        result = {"status": "SUCCESS", "run_id": 1, "received_sessions": 1}
        with patch("app.api.attendance_manage.sync_from_signin", return_value=result), \
             patch("app.api.attendance_manage.fetch_one", return_value=None):
            assert client.post("/api/v1/attendance/sync/immediate", headers=headers,
                               json={"event_id": "event-a", "registration_id": "reg-a"}).status_code == 503
        with patch("app.api.attendance_manage.sync_from_signin", return_value=result), \
             patch("app.api.attendance_manage.fetch_one", return_value={"id": 123}) as saved:
            assert client.post("/api/v1/attendance/sync/immediate", headers=headers,
                               json={"event_id": "event-a", "registration_id": "reg-a"}).status_code == 200
            assert saved.call_args.args[1] == ("event-a", "reg-a")
