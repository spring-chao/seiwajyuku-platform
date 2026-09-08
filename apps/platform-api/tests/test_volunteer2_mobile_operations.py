from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.iam import user_context
from app.services.members import create_member
from app.services.volunteer_management import (
    create_appointment,
    create_service_unit,
    migration_preview,
)
from app.services.volunteer_positions import (
    create_member_volunteer_appointment,
    get_member_volunteer_services,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    row = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert row
    return int(row["id"])


def test_volunteer2_multiple_current_posts_and_staff_mobile_iam2_boundary() -> None:
    """One person can be member+volunteer+employee without backend role leakage."""

    suffix = uuid4().hex[:10]
    admin_id = _admin_id()
    center_id = f"v2-center-{suffix}"
    class_id = f"v2-class-{suffix}"
    other_center_id = f"v2-other-center-{suffix}"
    person_id = f"v2-person-{suffix}"
    username = f"v2-worker-{suffix}"
    password = "Volunteer2-test-password"
    now = _now()
    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (center_id, f"V2_{suffix}_CENTER", "V2授权分中心", "REGIONAL_CENTER", "org-suzhou"),
            (class_id, f"V2_{suffix}_CLASS", "V2班级", "CLASS", center_id),
            (other_center_id, f"V2_{suffix}_OTHER", "V2范围外分中心", "REGIONAL_CENTER", "org-suzhou"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )

    member_id = create_member(
        admin_id,
        member_code=f"V2-MEMBER-{suffix}",
        name="志工与工作人员学长",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=None,
        class_org_unit_id=class_id,
    )
    create_member(
        admin_id,
        member_code=f"V2-OUTSIDE-{suffix}",
        name="范围外学长",
        org_unit_id=other_center_id,
        development_org_unit_id=None,
        phone=None,
    )
    legacy_member_id = create_member(
        admin_id,
        member_code=f"V2-LEGACY-{suffix}",
        name="待迁移志工学长",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=None,
        class_org_unit_id=class_id,
    )

    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
            "VALUES (?, '志工与工作人员学长', 'ACTIVE', ?, ?)",
            (person_id, now, now),
        )
        execute(
            connection,
            "INSERT INTO member_identities(member_id, person_id, status, source_reference, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', 'test-volunteer2', ?, ?)",
            (member_id, person_id, now, now),
        )
        user_id = int(
            execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
                "VALUES (?, '志工与工作人员学长', ?, 1, ?, ?)",
                (username, hash_password(password), now, now),
            ).lastrowid
        )
        execute(
            connection,
            "INSERT INTO account_person_links(user_id, person_id, linked_at, linked_by, source_reference) "
            "VALUES (?, ?, ?, ?, 'test-volunteer2')",
            (user_id, person_id, now, admin_id),
        )
        employment_id = int(
            execute(
                connection,
                "INSERT INTO operations_employments(person_id, institution_id, employment_status, started_on, ended_on, source_reference, created_at, updated_at) "
                "VALUES (?, 'institution-suzhou-operations', 'ACTIVE', '2030-01-01', '2020-01-01', 'test-volunteer2', ?, ?)",
                (person_id, now, now),
            ).lastrowid
        )
        execute(
            connection,
            "INSERT INTO employee_authorization_grants(employment_id, role_key, org_unit_id, scope_type, status, source_reference, created_by, created_at, updated_at) "
            "VALUES (?, 'employee_member_management', ?, 'SUBTREE', 'ACTIVE', 'test-volunteer2', ?, ?, ?)",
            (employment_id, center_id, admin_id, now, now),
        )

    class_team = create_service_unit(
        admin_id,
        unit_code=f"V2-CLASS-TEAM-{suffix}",
        name="V2班级志工团队",
        system_type="CLASS_TEAM",
        line_type="GENERAL",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=class_id,
    )
    learning_committee = create_service_unit(
        admin_id,
        unit_code=f"V2-LEARNING-COMMITTEE-{suffix}",
        name="V2学习践行委",
        system_type="COMMITTEE_LINE",
        line_type="LEARNING",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=class_id,
    )
    legacy_appointment = create_member_volunteer_appointment(
        admin_id,
        legacy_member_id,
        position_key="volunteer_class_counselor",
        org_unit_id=class_id,
        confirmation_note="历史班主任志工任职迁移预检样本",
    )
    create_appointment(
        admin_id,
        member_id=member_id,
        service_unit_id=class_team["id"],
        position_key="volunteer_class_counselor",
        confirmation_note="确认承担本班班主任志工服务",
    )
    create_appointment(
        admin_id,
        member_id=member_id,
        service_unit_id=learning_committee["id"],
        position_key="volunteer_committee_learning",
        confirmation_note="确认承担学习践行志工服务",
    )

    volunteer = get_member_volunteer_services(member_id)
    assert volunteer["is_volunteer"] is True
    assert volunteer["needs_manual_review"] is False
    assert {item["position_key"] for item in volunteer["roles"]} == {
        "volunteer_class_counselor",
        "volunteer_committee_learning",
    }
    # V2 appointment capability belongs to the mini-program service model;
    # it must not create an old backend volunteer role for the staff account.
    context = user_context(user_id)
    assert context and "employee_member_management" in context["roles"]
    assert "volunteer_class_counselor" not in context["roles"]

    count_before = fetch_one(
        "SELECT COUNT(*) AS n FROM volunteer_appointments WHERE member_id=?", (member_id,)
    )["n"]
    preview = migration_preview(admin_id)
    assert preview["migration_executed"] is False
    legacy_preview = next(
        entry
        for entry in preview["entries"]
        if entry["appointment"]["id"] == legacy_appointment["id"]
    )
    assert legacy_preview["classification"] == "SAFE_TO_MIGRATE"
    assert legacy_preview["planned_service_unit"]["id"] == class_team["id"]
    assert legacy_preview["planned_position_key"] == "volunteer_class_counselor"
    assert fetch_one(
        "SELECT COUNT(*) AS n FROM volunteer_appointments WHERE member_id=?", (member_id,)
    )["n"] == count_before

    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_STAFF_MOBILE_OPERATIONS_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": f"v2-mobile-{suffix}",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v2-mobile-test-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": f"v2-mobile-{suffix}", "openid": f"v2-openid-{suffix}"},
    ), TestClient(app) as client:
        bind = client.post(
            "/api/v1/wechat/staff-bindings/verify",
            json={"code": "test-code", "username": username, "password": password},
        )
        assert bind.status_code == 200, bind.text
        headers = {"Authorization": f"Bearer {bind.json()['data']['access_token']}"}
        me = client.get("/api/v1/wechat/me", headers=headers)
        assert me.status_code == 200, me.text
        kinds = me.json()["data"]["identities"]["identity_kinds"]
        assert {"MEMBER", "VOLUNTEER", "OPERATIONS_EMPLOYEE"}.issubset(kinds)
        workbench = client.get("/api/v1/wechat/operations/workbench", headers=headers)
        assert workbench.status_code == 200, workbench.text
        assert {item["key"] for item in workbench.json()["data"]["entries"]}.issuperset(
            {"member_search", "followup_records"}
        )
        search = client.get(
            "/api/v1/wechat/operations/member-search",
            params={"name": "工作人员"},
            headers=headers,
        )
        assert search.status_code == 200, search.text
        assert {item["member_id"] for item in search.json()["data"]} == {member_id}

        with transaction() as connection:
            execute(
                connection,
                "UPDATE operations_employments SET employment_status='LEAVE', updated_at=? WHERE id=?",
                (_now(), employment_id),
            )
        assert client.get("/api/v1/wechat/operations/workbench", headers=headers).status_code == 403
        after_leave = client.get("/api/v1/wechat/me", headers=headers)
        assert after_leave.status_code == 200, after_leave.text
        after_kinds = after_leave.json()["data"]["identities"]["identity_kinds"]
        assert "MEMBER" in after_kinds and "VOLUNTEER" in after_kinds
        assert "OPERATIONS_EMPLOYEE" not in after_kinds

    with transaction() as connection:
        execute(connection, "UPDATE members SET status='INACTIVE', updated_at=? WHERE id=?", (_now(), member_id))
    assert get_member_volunteer_services(member_id)["roles"] == []
