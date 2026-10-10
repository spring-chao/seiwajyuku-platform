from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.privacy import phone_hash
from app.db import execute, transaction
from app.main import app
from app.services import signin_engine
from app.services.attendance_registration_identity import registration_identities
from test_v12_mvp import _seed_group_leader_fixture
from test_signin_management import ENV, _operator


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
