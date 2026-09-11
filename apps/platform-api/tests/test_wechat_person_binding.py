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
