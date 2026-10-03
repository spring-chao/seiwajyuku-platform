from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.privacy import protected_phone
from app.core.security import hash_password
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.members import create_member
from app.services.wechat_identity import WeChatProviderError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone() -> str:
    return f"137{uuid4().int % 100_000_000:08d}"


def _admin_id() -> int:
    row = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert row
    return int(row["id"])


def _seed_member(*, name: str, phone: str, person_id: str | None = None) -> tuple[int, str | None]:
    member_id = create_member(
        _admin_id(),
        member_code=f"PERSON-BIND-MEMBER-{uuid4().hex[:10]}",
        name=name,
        org_unit_id="org-wuxi-guidance-1",
        development_org_unit_id=None,
        phone=phone,
    )
    if person_id:
        now = _now()
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (person_id, name, now, now),
            )
            execute(
                connection,
                "INSERT INTO member_identities(member_id, person_id, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', 'person-binding-test', ?, ?)",
                (member_id, person_id, now, now),
            )
    return member_id, person_id


def _seed_staff(*, name: str, phone: str, person_id: str | None = None) -> tuple[str, int]:
    person_id = person_id or f"person-staff-{uuid4().hex[:10]}"
    username = f"person-staff-{uuid4().hex[:10]}"
    password = "test-password"
    now = _now()
    phone_fields = protected_phone(phone)
    with transaction() as connection:
        if not execute(
            connection, "SELECT id FROM person_profiles WHERE id=?", (person_id,)
        ).fetchone():
            execute(
                connection,
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (person_id, name, now, now),
            )
        user_id = int(
            execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (username, name, hash_password(password), now, now),
            ).lastrowid
        )
        execute(
            connection,
            "INSERT INTO account_person_links(user_id, person_id, linked_at, linked_by, source_reference) "
            "VALUES (?, ?, ?, ?, 'person-binding-test')",
            (user_id, person_id, now, _admin_id()),
        )
        execute(
            connection,
            "INSERT INTO employee_profile_details(person_id, work_phone_ciphertext, work_phone_hash, "
            "work_phone_last4, work_phone_masked, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                person_id,
                phone_fields["phone_ciphertext"],
                phone_fields["phone_hash"],
                phone_fields["phone_last4"],
                phone_fields["phone_masked"],
                now,
                now,
            ),
        )
        execute(
            connection,
            "INSERT INTO operations_employments(person_id, institution_id, employment_status, "
            "source_reference, created_at, updated_at) VALUES (?, 'institution-suzhou-operations', 'ACTIVE', 'person-binding-test', ?, ?)",
            (person_id, now, now),
        )
    return person_id, user_id


def _client_context():
    return patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_STAFF_MOBILE_OPERATIONS_ENABLED": "true",
            "WECHAT_LOCAL_TEST_MODE": "false",
            "WECHAT_MINIPROGRAM_APP_ID": "person-binding-test-app",
            "WECHAT_MINIPROGRAM_APP_SECRET": "person-binding-test-secret",
        },
    )


def test_unified_binding_matches_member_without_staff_phone_authorization() -> None:
    name = f"统一学员-{uuid4().hex[:8]}"
    phone = _phone()
    member_id, _ = _seed_member(name=name, phone=phone, person_id=f"person-member-{uuid4().hex[:10]}")
    with _client_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "person-binding-test-app", "openid": f"member-{member_id}"},
    ), TestClient(app) as client:
        response = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={"wx_login_code": "login-code", "name": name, "phone": phone},
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["member"]["member_id"] == member_id
    assert data["identities"]["identity_kinds"] == ["MEMBER"]


def test_unified_binding_requires_wechat_phone_ownership_for_staff() -> None:
    name = f"统一专职-{uuid4().hex[:8]}"
    phone = _phone()
    _seed_staff(name=name, phone=phone)
    with _client_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "person-binding-test-app", "openid": f"staff-{uuid4().hex[:10]}"},
    ), TestClient(app) as client:
        response = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={"wx_login_code": "login-code", "name": name, "phone": phone},
        )
    assert response.status_code == 400, response.text
    assert "微信获取手机号" in response.json()["detail"]


def test_unified_binding_exposes_staff_feature_gate_after_person_resolution() -> None:
    name = f"开关阻断专职-{uuid4().hex[:8]}"
    phone = _phone()
    _seed_staff(name=name, phone=phone)
    with _client_context(), patch.dict(
        os.environ, {"WECHAT_STAFF_MOBILE_OPERATIONS_ENABLED": "false"}
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "person-binding-test-app", "openid": f"gate-{uuid4().hex[:10]}"},
    ), TestClient(app) as client:
        response = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={"wx_login_code": "login-code", "name": name, "phone": phone},
        )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "工作人员移动运营功能尚未开启"


def test_unified_binding_returns_member_volunteer_and_staff_for_one_person() -> None:
    name = f"统一复合身份-{uuid4().hex[:8]}"
    phone = _phone()
    person_id = f"person-composite-{uuid4().hex[:10]}"
    member_id, _ = _seed_member(name=name, phone=phone, person_id=person_id)
    _seed_staff(name=name, phone=phone, person_id=person_id)
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
            "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, ?, 'volunteer_activity', 'org-suzhou', 'UNIT', ?, NULL, 'ACTIVE', 'person-binding-test', ?, ?)",
            (person_id, member_id, now, now, now),
        )
    with _client_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "person-binding-test-app", "openid": f"composite-{member_id}"},
    ), patch(
        "app.services.wechat_identity.verify_wechat_phone_ownership", return_value=True
    ), TestClient(app) as client:
        response = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={
                "wx_login_code": "login-code",
                "name": name,
                "phone": phone,
                "phone_verification": "phone-code",
            },
        )
    assert response.status_code == 200, response.text
    kinds = set(response.json()["data"]["identities"]["identity_kinds"])
    assert {"MEMBER", "VOLUNTEER", "OPERATIONS_EMPLOYEE"}.issubset(kinds)


def test_unified_binding_rejects_two_people_with_same_name_and_phone() -> None:
    name = f"重复人员-{uuid4().hex[:8]}"
    phone = _phone()
    _seed_member(name=name, phone=phone)
    _seed_staff(name=name, phone=phone)
    with _client_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "person-binding-test-app", "openid": f"ambiguous-{uuid4().hex[:10]}"},
    ), TestClient(app) as client:
        response = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={"wx_login_code": "login-code", "name": name, "phone": phone},
        )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "检测到重复身份资料，请联系工作人员核对。"


def test_lost_local_session_recovers_unbind_and_rebinds_another_person() -> None:
    first_name, second_name = [f"解绑恢复-{uuid4().hex[:8]}" for _ in range(2)]
    first_phone, second_phone = _phone(), _phone()
    first_id, _ = _seed_member(name=first_name, phone=first_phone)
    second_id, _ = _seed_member(name=second_name, phone=second_phone)
    openid = f"recovery-{uuid4().hex}"
    with _client_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "person-binding-test-app", "openid": openid},
    ), TestClient(app) as client:
        def bind(name, phone):
            return client.post("/api/v1/wechat/person-bindings/verify", json={
                "wx_login_code": "fresh-login", "name": name, "phone": phone})
        first = bind(first_name, first_phone)
        assert first.status_code == 200, first.text
        old_headers = {"Authorization": "Bearer " + first.json()["data"]["access_token"]}
        blocked = bind(second_name, second_phone)
        assert blocked.status_code == 400
        assert "当前微信已绑定其他人员" in blocked.json()["detail"]
        # The old client token can be absent or expired. A provider-issued
        # WeChat code still proves ownership of exactly this WeChat binding.
        recovered = client.post("/api/v1/wechat/member-bindings/revoke",
            headers={"Authorization": "Bearer expired-app-token"},
            json={"wx_login_code": "fresh-revoke-login"})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json() == {"success": True, "data": {"revoked": True}}
        assert client.get("/api/v1/wechat/me", headers=old_headers).status_code == 401
        row = fetch_one("SELECT status, active_slot, token_version FROM wechat_member_bindings WHERE openid=?", (openid,))
        assert row == {"status": "REVOKED", "active_slot": None, "token_version": 2}
        # A repeated explicit unbind is safe and does not rotate/audit twice.
        again = client.post("/api/v1/wechat/member-bindings/revoke", json={"wx_login_code": "another-fresh-code"})
        assert again.status_code == 200
        assert fetch_one("SELECT token_version FROM wechat_member_bindings WHERE openid=?", (openid,))["token_version"] == 2
        rebound = bind(second_name, second_phone)
        assert rebound.status_code == 200, rebound.text
        new_headers = {"Authorization": "Bearer " + rebound.json()["data"]["access_token"]}
        assert client.get("/api/v1/wechat/me", headers=new_headers).json()["data"]["member"]["member_id"] == second_id
        assert client.post("/api/v1/wechat/member-bindings/revoke", headers=old_headers).status_code == 401
        assert client.get("/api/v1/wechat/me", headers=new_headers).status_code == 200
        assert fetch_one("SELECT status FROM members WHERE id=?", (first_id,))["status"] == "ACTIVE"


def test_recovery_proof_cannot_select_or_revoke_another_wechat_binding() -> None:
    fixtures = [(f"隔离解绑-{uuid4().hex[:8]}", _phone(), f"openid-{uuid4().hex}") for _ in range(2)]
    for name, phone, _ in fixtures:
        _seed_member(name=name, phone=phone)
    with _client_context(), patch("app.services.wechat_identity.exchange_wechat_code") as provider, TestClient(app) as client:
        tokens = []
        for name, phone, openid in fixtures:
            provider.return_value = {"appid": "person-binding-test-app", "openid": openid}
            bound = client.post("/api/v1/wechat/person-bindings/verify", json={"wx_login_code": "login", "name": name, "phone": phone})
            assert bound.status_code == 200, bound.text
            tokens.append(bound.json()["data"]["access_token"])
        provider.return_value = {"appid": "person-binding-test-app", "openid": fixtures[0][2]}
        malicious = client.post("/api/v1/wechat/member-bindings/revoke", json={"wx_login_code": "fresh-code", "openid": fixtures[1][2]})
        assert malicious.status_code == 422
        assert client.post("/api/v1/wechat/member-bindings/revoke", json={"wx_login_code": "fresh-code"}).status_code == 200
        assert client.get("/api/v1/wechat/me", headers={"Authorization": "Bearer " + tokens[0]}).status_code == 401
        assert client.get("/api/v1/wechat/me", headers={"Authorization": "Bearer " + tokens[1]}).status_code == 200


def test_recovery_requires_provider_proof_and_preserves_existing_token_api() -> None:
    with _client_context(), patch("app.services.wechat_identity.exchange_wechat_code", side_effect=WeChatProviderError("微信登录凭证已失效")), TestClient(app) as client:
        assert client.post("/api/v1/wechat/member-bindings/revoke").status_code == 401
        assert client.post("/api/v1/wechat/member-bindings/revoke", json={}).status_code == 422
        assert client.post("/api/v1/wechat/member-bindings/revoke", json={"wx_login_code": "invalid"}).status_code == 503


def test_staff_only_binding_can_be_revoked_without_member_or_cached_token() -> None:
    name, phone = f"专职解绑-{uuid4().hex[:8]}", _phone()
    _seed_staff(name=name, phone=phone)
    with _client_context(), patch("app.services.wechat_identity.exchange_wechat_code", return_value={"appid": "person-binding-test-app", "openid": f"staff-revoke-{uuid4().hex}"}), patch("app.services.wechat_identity.verify_wechat_phone_ownership", return_value=True), TestClient(app) as client:
        bound = client.post("/api/v1/wechat/person-bindings/verify", json={"wx_login_code": "login", "name": name, "phone": phone, "phone_verification": "phone-code"})
        assert bound.status_code == 200, bound.text
        token = bound.json()["data"]["access_token"]
        assert client.post("/api/v1/wechat/member-bindings/revoke", json={"wx_login_code": "new-login"}).status_code == 200
        assert client.get("/api/v1/wechat/me", headers={"Authorization": "Bearer " + token}).status_code == 401
