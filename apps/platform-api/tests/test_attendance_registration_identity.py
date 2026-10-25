from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.privacy import phone_hash
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import signin_engine
from app.services.attendance_registration_identity import registration_identities
from app.services.attendance_registration_identity import attach_current_registration_names
from test_v12_mvp import _seed_group_leader_fixture
from test_signin_management import ENV, _operator


def test_master_rename_refreshes_authorized_event_display_without_rewriting_registration_or_actual_person():
    import copy
    fixture = _seed_group_leader_fixture()
    member = fetch_one("SELECT id,member_code,name FROM members WHERE id=?", (fixture["member_id"],))
    event = {"event_id": "synthetic-rename-event", "name": "合成活动", "org_unit_id": fixture["class_id"]}
    original = {"registration_id": "existing-slot", "platform_member_id": str(member["id"]),
                "member_code": member["member_code"], "registered_name": "合成学长（旧）", "name": "合成学长（旧）",
                "is_team": True, "actual_attendee_name": "合成团队实际来宾", "checked_at": "2026-10-25T01:00:00Z"}
    with transaction() as connection:
        execute(connection, "UPDATE members SET name='合成学长' WHERE id=?", (member["id"],))
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        headers, _ = _operator({"attendance:view"}, fixture["class_id"])
        with patch.object(signin_engine, "engine_request", side_effect=lambda *a, **k: {"ok": True, "event": event, "rows": [copy.deepcopy(original)]}):
            response = client.post("/api/v1/attendance/manage/event_detail", headers=headers, json={"event_id": event["event_id"]})
            assert response.status_code == 200, response.text
            row = response.json()["rows"][0]
            assert row["registered_display_name"] == "合成学长"
            assert row["registered_name_changed"]
            assert row["registered_name"] == original["registered_name"]
            assert row["actual_attendee_name"] == "合成团队实际来宾"
            with transaction() as connection:
                execute(connection, "UPDATE members SET name='合成学长新名' WHERE id=?", (member["id"],))
            again = client.post("/api/v1/attendance/manage/event_detail", headers=headers, json={"event_id": event["event_id"]})
            assert again.json()["rows"][0]["registered_display_name"] == "合成学长新名"
    assert "registered_display_name" not in original


def test_rename_projection_uses_stable_ids_and_codes_never_same_name_guessing():
    fixture = _seed_group_leader_fixture()
    member = fetch_one("SELECT id,member_code,name FROM members WHERE id=?", (fixture["member_id"],))
    rows = [{"name": member["name"]}, {"member_code": member["member_code"], "name": "旧名"},
            {"platform_member_id": str(member["id"]), "member_code": "mismatched-code", "name": "旧名"},
            {"platform_member_id": "missing-id", "member_code": member["member_code"], "name": "旧名"},
            {"platform_member_id": "9" * 100, "name": "旧名"}]
    attach_current_registration_names(rows)
    assert rows[1]["registered_display_name"] == member["name"]
    assert all("registered_display_name" not in rows[index] for index in (0, 2, 3, 4))
    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (member["id"],))
    inactive = [{"platform_member_id": str(member["id"]), "name": "旧名"}]
    attach_current_registration_names(inactive)
    assert "registered_display_name" not in inactive[0]


def test_legacy_name_projection_uses_exact_synced_event_registration_link():
    fixture = _seed_group_leader_fixture()
    with transaction() as connection:
        now = "2026-10-25 01:00:00"
        group_id = int(execute(connection, "INSERT INTO attendance_event_groups(source_key,external_group_id,org_unit_id,event_date,created_at,updated_at) VALUES ('signin',?,?, '2026-10-25',?,?)",
                               (uuid4().hex, fixture["class_id"], now, now)).lastrowid)
        session_id = int(execute(connection, "INSERT INTO attendance_sessions(event_group_id,external_session_id,session_code,created_at,updated_at) VALUES (?,'exact-event-rename','MORNING',?,?)", (group_id, now, now)).lastrowid)
        execute(connection, "INSERT INTO attendance_records(attendance_session_id,external_record_id,member_id,name_snapshot,received_at,created_at,updated_at) VALUES (?,'exact-existing-slot',?,'合成旧名',?,?,?)",
                (session_id, fixture["member_id"], now, now, now))
        execute(connection, "UPDATE members SET name='合成主档新名' WHERE id=?", (fixture["member_id"],))
    rows = [{"registration_id": "exact-existing-slot", "registered_name": "合成旧名"}]
    attach_current_registration_names(rows, event_id="different-event")
    assert "registered_display_name" not in rows[0]
    attach_current_registration_names(rows, event_id="exact-event-rename")
    assert rows[0]["registered_display_name"] == "合成主档新名"
    assert rows[0]["registered_name"] == "合成旧名"


def test_excel_and_manual_only_associate_unique_active_name_phone_and_strip_client_ids():
    fixture = _seed_group_leader_fixture()
    row = {"name": "V1.2组长", "phone": fixture["phone"], "platform_member_id": "forged", "member_code": "forged"}
    assert registration_identities([row])[0]["platform_member_id"] == str(fixture["member_id"])
    assert registration_identities([{**row, "name": "姓名不匹配"}, {**row, "phone": ""}]) == [None, None]
    with patch.dict(os.environ, ENV), TestClient(app) as client, patch.object(signin_engine, "engine_request", return_value={"ok": True}) as request:
        headers, _ = _operator({"attendance:manage", "attendance:import"})
        for operation, payload in (("registration", row), ("upload_preview", {"attendees": [row, {**row, "phone": ""}]}),
                                   ("import_preview", {"attendees": [row]}), ("import_apply", {"attendees": [row]})):
            response = client.post("/api/v1/attendance/manage/" + operation, headers=headers, json=payload)
            assert response.status_code == 200, response.text
            sent = request.call_args.args[1]
            assert sent["registration_identity_verified"] is True
            resolved = sent["payload"] if operation == "registration" else sent["payload"]["attendees"][0]
            assert resolved["platform_member_id"] == str(fixture["member_id"])
            assert "forged" not in str(sent)
            if operation == "upload_preview":
                assert "platform_member_id" not in sent["payload"]["attendees"][1]


def test_conflicting_and_inactive_identities_are_not_guessed():
    first = _seed_group_leader_fixture()
    row = {"name": "V1.2组长", "phone": first["phone"]}
    # The schema currently makes phone_hash unique; retain fail-closed behavior
    # if an upstream identity source ever returns conflicting matches.
    with patch("app.services.attendance_registration_identity.fetch_all", return_value=[
        {"id": 51, "member_code": "M51", "name": row["name"], "phone_hash": phone_hash(first["phone"])},
        {"id": 52, "member_code": "M52", "name": row["name"], "phone_hash": phone_hash(first["phone"])},
    ]):
        assert registration_identities([row]) == [None]
    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (first["member_id"],))
    assert registration_identities([row]) == [None]


def test_legacy_registration_matching_endpoint_requires_service_auth_and_never_exposes_phone():
    fixture = _seed_group_leader_fixture()
    payload = {"event_id": "isolated-event", "rows": [{"registration_id": "existing-slot", "name": "V1.2组长", "phone": fixture["phone"]}]}
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        path = "/api/v1/checkin-rosters/registration-identities"
        assert client.post(path, json=payload).status_code == 401
        response = client.post(path, headers={"X-API-Key": ENV["SIGNIN_SERVICE_API_KEY"]}, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["matches"][0]["platform_member_id"] == str(fixture["member_id"])
        assert "phone" not in response.text and fixture["phone"] not in response.text
        assert "phone_hash" not in response.text


def test_name_scan_requires_selection_and_lookup_never_confirms_a_missing_enrollment():
    with patch.dict(os.environ, ENV), TestClient(app) as client, patch("app.api.wechat_checkin.exchange_wechat_code", return_value={"appid": "isolated-app", "openid": "isolated-person"}), patch.object(signin_engine, "engine_request", return_value={"ok": True, "status": "NOT_REGISTERED", "candidates": []}) as request:
        payload = {"event_id": "isolated-event", "name": "未报名的人", "wx_login_code": "isolated-code"}
        assert client.post("/api/v1/wechat/checkin/guest-confirm", json=payload).status_code == 409
        request.assert_not_called()
        response = client.post("/api/v1/wechat/checkin/guest-lookup", json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["candidates"] == []
        assert request.call_args.args[0].endswith("/lookup")
        assert "openid" not in str(request.call_args)


def test_bound_name_confirmation_uses_exact_active_bound_identity_despite_namesakes():
    fixture = _seed_group_leader_fixture()
    name = '合成唯一报名' + uuid4().hex
    with transaction() as connection:
        execute(connection, 'UPDATE members SET name=? WHERE id=?', (name, fixture['member_id']))
    bound = {'member_id': str(fixture['member_id']), 'member_code':
             fetch_one('SELECT member_code FROM members WHERE id=?', (fixture['member_id'],))['member_code'], 'name': name}
    row = {'registration_id': 'existing-slot', 'name': name, 'phone': ''}
    assert registration_identities([row]) == [None], 'imports must still require name+phone'
    assert registration_identities([row], bound_member=bound)[0]['platform_member_id'] == bound['member_id']
    for wrong in [{**bound, 'member_id': 'wrong'}, {**bound, 'member_code': 'wrong'}, {**bound, 'name': '其他报名人'}]:
        assert registration_identities([row], bound_member=wrong) == [None]
    assert registration_identities([{**row, 'phone': 'invalid'}], bound_member=bound)[0]['identity_match']=='BOUND_NAME_SELF_CONFIRMATION'
    assert registration_identities([{**row, 'member_code': 'OTHER'}], bound_member=bound) == [None]
    with patch.dict(os.environ, ENV), TestClient(app) as client:
        path = '/api/v1/checkin-rosters/registration-identities'
        payload = {'event_id': 'isolated-event', 'rows': [row], 'bound_member': bound}
        assert client.post(path, json=payload).status_code == 401
        response = client.post(path, headers={'X-API-Key': ENV['SIGNIN_SERVICE_API_KEY']}, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['data']['source'] == 'PLATFORM_BOUND_REGISTRATION_IDENTITY'
        assert response.json()['data']['matches'][0]['platform_member_id'] == bound['member_id']
        assert 'phone' not in response.text
    other = _seed_group_leader_fixture()
    with transaction() as connection:
        execute(connection, 'UPDATE members SET name=? WHERE id=?', (name, other['member_id']))
    assert registration_identities([row], bound_member=bound)[0]['platform_member_id']==bound['member_id'], 'bound ID resolves the person despite an active namesake'
    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (other['member_id'],))
    assert registration_identities([row], bound_member=bound)[0]['platform_member_id'] == bound['member_id']
    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (fixture['member_id'],))
    assert registration_identities([row], bound_member=bound) == [None]
