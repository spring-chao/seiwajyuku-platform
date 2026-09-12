from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.privacy import protected_phone
from app.core.security import hash_password
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.followups import create_task
from app.services.iam import mobile_iam_context, resolve_employee_mobile_principal
from app.services.members import create_member


MOBILE_V1_PERMISSIONS = (
    "members:read",
    "members:detail_view",
    "followups:manage",
    "renewals:read",
    "renewals:manage",
    "contact:reveal",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone(prefix: str = "139") -> str:
    return f"{prefix}{uuid4().int % 100_000_000:08d}"


def _admin_id() -> int:
    row = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert row
    return int(row["id"])


def _due_after(months: int) -> tuple[int, int]:
    current = datetime.now(UTC)
    offset = current.month - 1 + months
    return current.year + offset // 12, offset % 12 + 1


def _set_join_date(member_id: int, *, years_ago: int) -> None:
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET join_date=?, updated_at=? WHERE id=?",
            (f"{datetime.now(UTC).year - years_ago}-01-15", _now(), member_id),
        )


def _seed_staff_mobile_v1() -> dict[str, object]:
    """Create one staff-only person with a single IAM2 grant and safe fixtures."""

    suffix = uuid4().hex[:10]
    now = _now()
    admin_id = _admin_id()
    center_id = f"mobile-v1-center-{suffix}"
    class_id = f"mobile-v1-class-{suffix}"
    outside_center_id = f"mobile-v1-outside-{suffix}"
    person_id = f"mobile-v1-person-{suffix}"
    role_key = f"mobile_v1_role_{suffix}"
    staff_name = f"移动专职-{suffix[:6]}"
    staff_phone = _phone("136")
    phone_fields = protected_phone(staff_phone)

    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (center_id, f"MOBILE_V1_{suffix}_CENTER", "移动运营授权分中心", "REGIONAL_CENTER", "org-suzhou"),
            (class_id, f"MOBILE_V1_{suffix}_CLASS", "移动运营测试班", "CLASS", center_id),
            (outside_center_id, f"MOBILE_V1_{suffix}_OUTSIDE", "移动运营范围外分中心", "REGIONAL_CENTER", "org-suzhou"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )
        execute(
            connection,
            "INSERT INTO roles(role_key, role_name, is_system, is_active, created_at, updated_at) "
            "VALUES (?, '移动运营V1测试角色', 0, 1, ?, ?)",
            (role_key, now, now),
        )
        for permission in MOBILE_V1_PERMISSIONS:
            execute(
                connection,
                "INSERT INTO role_permissions(role_key, permission_key) VALUES (?, ?)",
                (role_key, permission),
            )
        execute(
            connection,
            "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (person_id, staff_name, now, now),
        )
        user_id = int(
            execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (f"mobile-v1-user-{suffix}", staff_name, hash_password("mobile-v1-test-password"), now, now),
            ).lastrowid
        )
        execute(
            connection,
            "INSERT INTO account_person_links(user_id, person_id, linked_at, linked_by, source_reference) "
            "VALUES (?, ?, ?, ?, 'staff-mobile-v1-test')",
            (user_id, person_id, now, admin_id),
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
        employment_id = int(
            execute(
                connection,
                "INSERT INTO operations_employments(person_id, institution_id, employment_status, "
                "source_reference, created_at, updated_at) "
                "VALUES (?, 'institution-suzhou-operations', 'ACTIVE', 'staff-mobile-v1-test', ?, ?)",
                (person_id, now, now),
            ).lastrowid
        )
        execute(
            connection,
            "INSERT INTO employee_authorization_grants(employment_id, role_key, org_unit_id, scope_type, "
            "status, source_reference, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 'SUBTREE', 'ACTIVE', 'staff-mobile-v1-test', ?, ?, ?)",
            (employment_id, role_key, center_id, admin_id, now, now),
        )

    focal_phone = _phone()
    focal_member_id = create_member(
        admin_id,
        member_code=f"MOBILE-V1-FOCAL-{suffix}",
        name=f"移动关爱学长-{suffix[:6]}",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=focal_phone,
        class_org_unit_id=class_id,
    )
    _set_join_date(focal_member_id, years_ago=2)
    outside_member_id = create_member(
        admin_id,
        member_code=f"MOBILE-V1-OUTSIDE-{suffix}",
        name=f"移动范围外学长-{suffix[:6]}",
        org_unit_id=outside_center_id,
        development_org_unit_id=None,
        phone=_phone("138"),
    )
    _set_join_date(outside_member_id, years_ago=1)

    renewal_cycles: dict[str, int] = {}
    for stage, months_ahead in (("OBSERVE_3", 3), ("RENEW_2", 2), ("FOLLOW_1", 1)):
        member_id = focal_member_id
        if stage != "FOLLOW_1":
            member_id = create_member(
                admin_id,
                member_code=f"MOBILE-V1-{stage}-{suffix}",
                name=f"移动{stage}-{suffix[:6]}",
                org_unit_id=center_id,
                development_org_unit_id=None,
                phone=_phone(),
                class_org_unit_id=class_id,
            )
            _set_join_date(member_id, years_ago=2)
        renewal_year, due_month = _due_after(months_ahead)
        with transaction() as connection:
            renewal_cycles[stage] = int(
                execute(
                    connection,
                    "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, "
                    "assigned_user_id, created_at, updated_at) VALUES (?, ?, ?, ?, 'PENDING_FIRST_CONTACT', ?, ?, ?)",
                    (member_id, renewal_year, center_id, due_month, user_id, _now(), _now()),
                ).lastrowid
            )

    principal = resolve_employee_mobile_principal(person_id, verified_user_id=user_id)
    assert principal
    with mobile_iam_context(principal, "followups:manage"):
        task_id = create_task(
            user_id,
            member_id=focal_member_id,
            task_type="OTHER",
            service_purpose="移动端今日关爱",
            assigned_user_id=user_id,
            due_at=_now(),
            confidentiality_level="ASSIGNEE",
        )
    return {
        "center_id": center_id,
        "person_id": person_id,
        "user_id": user_id,
        "employment_id": employment_id,
        "staff_name": staff_name,
        "staff_phone": staff_phone,
        "focal_member_id": focal_member_id,
        "focal_phone": focal_phone,
        "outside_member_id": outside_member_id,
        "renewal_cycles": renewal_cycles,
        "task_id": task_id,
    }


def _wechat_context():
    return patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_STAFF_MOBILE_OPERATIONS_ENABLED": "true",
            "WECHAT_LOCAL_TEST_MODE": "false",
            "WECHAT_MINIPROGRAM_APP_ID": "staff-mobile-v1-test-app",
            "WECHAT_MINIPROGRAM_APP_SECRET": "staff-mobile-v1-test-secret",
        },
    )


def test_staff_mobile_v1_high_frequency_closed_loop_and_live_iam2_scope() -> None:
    """STAFF_MOBILE_HOME through CARE_ACTION_REFRESH, without a mobile role copy."""

    fixture = _seed_staff_mobile_v1()
    with _wechat_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "staff-mobile-v1-test-app", "openid": f"mobile-v1-{uuid4().hex}"},
    ), patch(
        "app.services.wechat_identity.verify_wechat_phone_ownership", return_value=True
    ), TestClient(app) as client:
        # STAFF_ONLY_PERSON: this person has no member or volunteer identity.
        bind = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={
                "wx_login_code": "test-login-code",
                "name": fixture["staff_name"],
                "phone": fixture["staff_phone"],
                "phone_verification": "test-phone-code",
            },
        )
        assert bind.status_code == 200, bind.text
        assert bind.json()["data"]["identities"]["identity_kinds"] == ["OPERATIONS_EMPLOYEE"]
        headers = {"Authorization": f"Bearer {bind.json()['data']['access_token']}"}

        # STAFF_MOBILE_HOME
        workbench = client.get("/api/v1/wechat/operations/workbench", headers=headers)
        assert workbench.status_code == 200, workbench.text
        assert {entry["key"] for entry in workbench.json()["data"]["entries"]} == {
            "today_actions",
            "member_search",
            "care_records",
            "renewal_watch",
        }

        # TODAY_ACTIONS_SCOPE: only the scoped member's current action appears.
        today = client.get("/api/v1/wechat/operations/today-actions", headers=headers)
        assert today.status_code == 200, today.text
        pending = today.json()["data"]["pending"]
        focal_pending = next(
            item for item in pending if item["member_id"] == fixture["focal_member_id"]
        )
        assert {"member_name", "org_name", "action_type_name", "reason", "key_time"}.issubset(focal_pending)
        assert all(item["member_id"] != fixture["outside_member_id"] for item in pending)

        # MEMBER_SEARCH_SCOPE: both name and phone-last4 remain inside IAM2 scope.
        search = client.get(
            "/api/v1/wechat/operations/member-search",
            params={"keyword": "移动关爱学长"},
            headers=headers,
        )
        assert search.status_code == 200, search.text
        search_data = search.json()["data"]
        assert [item["member_id"] for item in search_data] == [fixture["focal_member_id"]]
        assert fixture["focal_phone"] not in json.dumps(search_data, ensure_ascii=False)
        last4 = str(fixture["focal_phone"])[-4:]
        last4_search = client.get(
            "/api/v1/wechat/operations/member-search",
            params={"keyword": last4},
            headers=headers,
        )
        assert last4_search.status_code == 200, last4_search.text
        assert {item["member_id"] for item in last4_search.json()["data"]} == {fixture["focal_member_id"]}

        # MEMBER_DETAIL: context is compact and does not carry the full phone.
        detail = client.get(
            f"/api/v1/wechat/operations/members/{fixture['focal_member_id']}",
            headers=headers,
        )
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert {"member", "learning", "care", "renewal", "volunteer_roles", "actions"}.issubset(detail_data)
        assert detail_data["actions"]["can_record_care"] is True
        assert detail_data["actions"]["can_reveal_contact"] is True
        assert fixture["focal_phone"] not in json.dumps(detail_data, ensure_ascii=False)

        # CARE_CREATE and CARE_ACTION_REFRESH: a short mobile record closes
        # the pending today-action projection and immediately returns it as done.
        care = client.post(
            f"/api/v1/wechat/operations/members/{fixture['focal_member_id']}/care-records",
            json={"channel": "WECHAT", "situation": "已联系", "next_action": None, "next_followup_at": None},
            headers=headers,
        )
        assert care.status_code == 200, care.text
        refreshed = care.json()["data"]["today_actions"]
        assert all(item["member_id"] != fixture["focal_member_id"] for item in refreshed["pending"] if item["source"] == "FOLLOWUP")
        assert any(item["member_id"] == fixture["focal_member_id"] for item in refreshed["completed"])

        # Full contact is a separate, per-use audited response, never a list/detail field.
        contact = client.post(
            f"/api/v1/wechat/operations/members/{fixture['focal_member_id']}/contact-access",
            json={"purpose": "移动端关爱联系"},
            headers=headers,
        )
        assert contact.status_code == 200, contact.text
        contact_data = contact.json()["data"]
        assert len(contact_data["phone"]) == 11
        assert contact_data["phone"].endswith(last4)
        assert fetch_one(
            "SELECT COUNT(*) AS n FROM contact_access_logs WHERE member_id=? AND actor_user_id=?",
            (fixture["focal_member_id"], fixture["user_id"]),
        )["n"] == 1

        # RENEWAL_WATCH3 / RENEWAL_RENEW2 / RENEWAL_CHASE1 all come from the
        # existing cycle fact source, including the December-to-January boundary.
        watch = client.get("/api/v1/wechat/operations/renewal-watch", headers=headers)
        assert watch.status_code == 200, watch.text
        watch_groups = {group["key"]: group["items"] for group in watch.json()["data"]["groups"]}
        assert fixture["renewal_cycles"]["OBSERVE_3"] in {item["cycle_id"] for item in watch_groups["OBSERVE_3"]}
        assert fixture["renewal_cycles"]["RENEW_2"] in {item["cycle_id"] for item in watch_groups["RENEW_2"]}
        assert fixture["renewal_cycles"]["FOLLOW_1"] in {item["cycle_id"] for item in watch_groups["FOLLOW_1"]}
        renewal_care = client.post(
            f"/api/v1/wechat/operations/members/{fixture['focal_member_id']}/renewal-care-records",
            json={
                "renewal_cycle_id": fixture["renewal_cycles"]["FOLLOW_1"],
                "channel": "PHONE",
                "situation": "好",
                "next_action": None,
                "next_followup_at": None,
            },
            headers=headers,
        )
        assert renewal_care.status_code == 200, renewal_care.text
        assert fetch_one(
            "SELECT COUNT(*) AS n FROM renewal_followups WHERE renewal_cycle_id=?",
            (fixture["renewal_cycles"]["FOLLOW_1"],),
        )["n"] == 1

        # OUT_OF_SCOPE_MEMBER_DENIED on every member-specific mobile path.
        assert client.get(
            f"/api/v1/wechat/operations/members/{fixture['outside_member_id']}", headers=headers
        ).status_code == 403
        assert client.post(
            f"/api/v1/wechat/operations/members/{fixture['outside_member_id']}/care-records",
            json={"channel": "WECHAT", "situation": "不应写入", "next_action": None, "next_followup_at": None},
            headers=headers,
        ).status_code == 403

        # DISABLED_STAFF_DENIED: the same person binding remains natural-person
        # evidence, but staff operations disappear as soon as the account stops.
        with transaction() as connection:
            execute(
                connection,
                "UPDATE app_users SET is_active=0, updated_at=? WHERE id=?",
                (_now(), fixture["user_id"]),
            )
        assert client.get("/api/v1/wechat/operations/workbench", headers=headers).status_code == 403


def test_mobile_bff_keeps_each_iam2_permission_in_its_own_org_scope() -> None:
    """A members-read grant must not lend its organization scope to care writes."""

    fixture = _seed_staff_mobile_v1()
    suffix = uuid4().hex[:10]
    detail_role = f"mobile_v1_detail_{suffix}"
    care_role = f"mobile_v1_care_{suffix}"
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE employee_authorization_grants SET status='REVOKED', updated_at=? WHERE employment_id=?",
            (now, fixture["employment_id"]),
        )
        for role_key, role_name, permissions in (
            (detail_role, "移动V1资料范围测试", ("members:read", "members:detail_view")),
            (care_role, "移动V1关爱范围测试", ("followups:manage",)),
        ):
            execute(
                connection,
                "INSERT INTO roles(role_key, role_name, is_system, is_active, created_at, updated_at) "
                "VALUES (?, ?, 0, 1, ?, ?)",
                (role_key, role_name, now, now),
            )
            for permission in permissions:
                execute(
                    connection,
                    "INSERT INTO role_permissions(role_key, permission_key) VALUES (?, ?)",
                    (role_key, permission),
                )
        for role_key, org_unit_id in (
            (detail_role, fixture["center_id"]),
            (care_role, f"mobile-v1-outside-{str(fixture['person_id']).removeprefix('mobile-v1-person-')}"),
        ):
            execute(
                connection,
                "INSERT INTO employee_authorization_grants(employment_id, role_key, org_unit_id, scope_type, "
                "status, source_reference, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, 'SUBTREE', 'ACTIVE', 'staff-mobile-v1-split-test', ?, ?, ?)",
                (fixture["employment_id"], role_key, org_unit_id, _admin_id(), now, now),
            )

    with _wechat_context(), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "staff-mobile-v1-test-app", "openid": f"split-scope-{uuid4().hex}"},
    ), patch(
        "app.services.wechat_identity.verify_wechat_phone_ownership", return_value=True
    ), TestClient(app) as client:
        bind = client.post(
            "/api/v1/wechat/person-bindings/verify",
            json={
                "wx_login_code": "split-scope-login",
                "name": fixture["staff_name"],
                "phone": fixture["staff_phone"],
                "phone_verification": "split-scope-phone-code",
            },
        )
        assert bind.status_code == 200, bind.text
        headers = {"Authorization": f"Bearer {bind.json()['data']['access_token']}"}

        # Detail reads use only the detail role's center, not the care role's.
        assert client.get(
            f"/api/v1/wechat/operations/members/{fixture['focal_member_id']}", headers=headers
        ).status_code == 200
        assert client.get(
            f"/api/v1/wechat/operations/members/{fixture['outside_member_id']}", headers=headers
        ).status_code == 403

        # Care writes use only the followups grant's separate center. A union
        # of the two grants would incorrectly allow the first request below.
        assert client.post(
            f"/api/v1/wechat/operations/members/{fixture['focal_member_id']}/care-records",
            json={"channel": "WECHAT", "situation": "不应跨范围写入", "next_action": None, "next_followup_at": None},
            headers=headers,
        ).status_code == 403
        assert client.post(
            f"/api/v1/wechat/operations/members/{fixture['outside_member_id']}/care-records",
            json={"channel": "WECHAT", "situation": "范围内关爱", "next_action": None, "next_followup_at": None},
            headers=headers,
        ).status_code == 200

        # Revoking the last effective grants immediately removes mobile access.
        with transaction() as connection:
            execute(
                connection,
                "UPDATE employee_authorization_grants SET status='REVOKED', updated_at=? WHERE employment_id=?",
                (_now(), fixture["employment_id"]),
            )
        assert client.get("/api/v1/wechat/operations/workbench", headers=headers).status_code == 403
