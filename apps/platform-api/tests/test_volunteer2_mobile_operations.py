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
from app.services.members import create_member, update_member
from app.services.volunteer_management import (
    change_appointment_status,
    create_appointment,
    create_service_unit,
    list_appointments,
    member_editor_catalog,
    migration_preview,
)
from app.services.volunteer_positions import (
    STUDY_MEETING_MANAGE,
    create_member_volunteer_appointment,
    get_member_volunteer_history,
    get_member_volunteer_services,
    read_member_current_volunteer_position,
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
    center_learning_committee = create_service_unit(
        admin_id,
        unit_code=f"V2-CENTER-LEARNING-COMMITTEE-{suffix}",
        name="V2分中心学习践行委",
        system_type="COMMITTEE_LINE",
        line_type="LEARNING",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=center_id,
    )
    learning_committee = create_service_unit(
        admin_id,
        unit_code=f"V2-LEARNING-COMMITTEE-{suffix}",
        name="V2学习践行委",
        system_type="COMMITTEE_LINE",
        line_type="LEARNING",
        parent_id=center_learning_committee["id"],
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=class_id,
    )
    assert learning_committee["parent_id"] == center_learning_committee["id"]
    assert learning_committee["service_target_org_unit_id"] == class_id
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
            {"today_actions", "member_search", "care_records"}
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


def test_member_editor_supports_three_independent_cross_org_posts() -> None:
    """封名晏测试 fixture: study affiliation never limits three service posts."""

    suffix = uuid4().hex[:10]
    admin_id = _admin_id()
    center_id = f"v2-multi-center-{suffix}"
    study_class_id = f"v2-multi-study-class-{suffix}"
    teacher_class_id = f"v2-multi-teacher-class-{suffix}"
    group_class_id = f"v2-multi-group-class-{suffix}"
    service_group_id = f"v2-multi-service-group-{suffix}"
    now = _now()
    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (
                center_id,
                f"V2_MULTI_{suffix}_CENTER",
                "昆山分中心验收样本",
                "REGIONAL_CENTER",
                "org-suzhou",
            ),
            (
                study_class_id,
                f"V2_MULTI_{suffix}_STUDY_CLASS",
                "炎武二班验收样本",
                "CLASS",
                center_id,
            ),
            (
                teacher_class_id,
                f"V2_MULTI_{suffix}_TEACHER_CLASS",
                "炎武三班验收样本",
                "CLASS",
                center_id,
            ),
            (
                group_class_id,
                f"V2_MULTI_{suffix}_GROUP_CLASS",
                "炎武一班验收样本",
                "CLASS",
                center_id,
            ),
            (
                service_group_id,
                f"V2_MULTI_{suffix}_SERVICE_GROUP",
                "感恩组验收样本",
                "GROUP",
                group_class_id,
            ),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )

    member_id = create_member(
        admin_id,
        member_code=f"V2-MULTI-MEMBER-{suffix}",
        name="封名晏（自动化测试）",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=None,
        class_org_unit_id=study_class_id,
    )
    governance = create_service_unit(
        admin_id,
        unit_code=f"V2-MULTI-GOV-{suffix}",
        name="昆山分中心发展建设委验收样本",
        system_type="GOVERNANCE",
        line_type="DEVELOPMENT",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=center_id,
    )
    class_team = create_service_unit(
        admin_id,
        unit_code=f"V2-MULTI-CLASS-{suffix}",
        name="炎武三班班组委验收样本",
        system_type="CLASS_TEAM",
        line_type="GENERAL",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=teacher_class_id,
    )
    group_team = create_service_unit(
        admin_id,
        unit_code=f"V2-MULTI-GROUP-{suffix}",
        name="炎武一班感恩组班组委验收样本",
        system_type="CLASS_TEAM",
        line_type="GENERAL",
        parent_id=class_team["id"],
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=service_group_id,
    )

    created = [
        create_appointment(
            admin_id,
            member_id=member_id,
            service_unit_id=governance["id"],
            position_key="volunteer_center_development_vice_chair",
            confirmation_note="",
        ),
        create_appointment(
            admin_id,
            member_id=member_id,
            service_unit_id=class_team["id"],
            position_key="volunteer_class_counselor",
            confirmation_note="",
        ),
        create_appointment(
            admin_id,
            member_id=member_id,
            service_unit_id=group_team["id"],
            position_key="volunteer_group_counselor",
            confirmation_note="",
        ),
    ]

    appointments = list_appointments(admin_id, member_id=member_id)
    assert len(appointments) == 3
    assert {item["status"] for item in appointments} == {"ACTIVE"}
    assert {item["service_target_org_unit_id"] for item in appointments} == {
        center_id,
        teacher_class_id,
        service_group_id,
    }
    services = get_member_volunteer_services(member_id)
    assert services["needs_manual_review"] is False
    assert len(services["roles"]) == 3
    assert STUDY_MEETING_MANAGE in {
        capability
        for role in services["roles"]
        for capability in role["capabilities"]
    }
    compatibility = read_member_current_volunteer_position(member_id)
    assert compatibility["is_volunteer"] is True
    assert compatibility["needs_manual_review"] is False
    assert len(compatibility["active_appointments"]) == 3

    catalog = member_editor_catalog(admin_id)["service_units"]
    by_id = {item["id"]: item for item in catalog}
    assert "volunteer_center_development_vice_chair" in {
        item["position_key"] for item in by_id[governance["id"]]["positions"]
    }
    assert {item["position_key"] for item in by_id[group_team["id"]]["positions"]}.issuperset(
        {"volunteer_group_counselor", "volunteer_group_leader"}
    )
    assert all(item["system_type"] != "ACTIVITY" for item in catalog)

    update_member(
        admin_id,
        member_id,
        {"class_org_unit_id": group_class_id},
    )
    after_study_move = list_appointments(admin_id, member_id=member_id)
    assert len(after_study_move) == 3
    assert {item["service_target_org_unit_id"] for item in after_study_move} == {
        center_id,
        teacher_class_id,
        service_group_id,
    }

    class_appointment_id = created[1]["id"]
    change_appointment_status(
        admin_id, class_appointment_id, status="ENDED", reason=""
    )
    remaining = get_member_volunteer_services(member_id)
    assert len(remaining["roles"]) == 2
    assert "volunteer_class_counselor" not in {
        item["position_key"] for item in remaining["roles"]
    }
    assert STUDY_MEETING_MANAGE in {
        capability
        for role in remaining["roles"]
        for capability in role["capabilities"]
    }
    history = get_member_volunteer_history(member_id)["appointments"]
    assert any(
        item["position_name"] == "班主任" and item["status_name"] == "已结束"
        for item in history
    )

    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET status='INACTIVE', updated_at=? WHERE id=?",
            (_now(), member_id),
        )
    assert get_member_volunteer_services(member_id)["roles"] == []
    with transaction() as connection:
        execute(
            connection,
            "UPDATE members SET status='ACTIVE', updated_at=? WHERE id=?",
            (_now(), member_id),
        )
    restored = get_member_volunteer_services(member_id)
    assert len(restored["roles"]) == 2
    assert "volunteer_class_counselor" not in {
        item["position_key"] for item in restored["roles"]
    }


def test_same_member_can_serve_as_class_teacher_in_two_classes_and_end_one_only() -> None:
    """The same position in two formal classes is two independent appointments."""

    suffix = uuid4().hex[:10]
    admin_id = _admin_id()
    center_id = f"v2-two-class-center-{suffix}"
    class_a_id = f"v2-two-class-a-{suffix}"
    class_b_id = f"v2-two-class-b-{suffix}"
    now = _now()
    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (center_id, f"V2_TWO_{suffix}_CENTER", "双班任职分中心", "REGIONAL_CENTER", "org-suzhou"),
            (class_a_id, f"V2_TWO_{suffix}_A", "跨班验收甲班", "CLASS", center_id),
            (class_b_id, f"V2_TWO_{suffix}_B", "跨班验收乙班", "CLASS", center_id),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )

    member_id = create_member(
        admin_id,
        member_code=f"V2-TWO-CLASS-MEMBER-{suffix}",
        name="跨班双班主任验收学长",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=None,
        class_org_unit_id=class_a_id,
    )
    service_units = []
    for marker, class_id, class_name in (
        ("A", class_a_id, "跨班验收甲班"),
        ("B", class_b_id, "跨班验收乙班"),
    ):
        service_units.append(
            create_service_unit(
                admin_id,
                unit_code=f"V2-TWO-CLASS-{marker}-{suffix}",
                name=f"{class_name}班组委",
                system_type="CLASS_TEAM",
                line_type="GENERAL",
                parent_id=None,
                home_shuku_org_unit_id="org-suzhou",
                service_target_org_unit_id=class_id,
            )
        )

    appointments = [
        create_appointment(
            admin_id,
            member_id=member_id,
            service_unit_id=unit["id"],
            position_key="volunteer_class_counselor",
            confirmation_note="",
        )
        for unit in service_units
    ]
    current = list_appointments(admin_id, member_id=member_id, status="ACTIVE")
    assert len(current) == 2
    assert {item["service_target_org_unit_id"] for item in current} == {
        class_a_id,
        class_b_id,
    }

    change_appointment_status(
        admin_id, appointments[0]["id"], status="ENDED", reason=""
    )
    remaining = list_appointments(admin_id, member_id=member_id, status="ACTIVE")
    assert len(remaining) == 1
    assert remaining[0]["service_target_org_unit_id"] == class_b_id
    history = list_appointments(admin_id, member_id=member_id)
    assert {item["status"] for item in history} == {"ACTIVE", "ENDED"}


def test_member_editor_catalog_does_not_depend_on_precreated_service_units() -> None:
    """Formal positions and targets remain usable before advanced setup exists."""

    suffix = uuid4().hex[:10]
    admin_id = _admin_id()
    center_id = f"v2-auto-center-{suffix}"
    class_id = f"v2-auto-class-{suffix}"
    group_id = f"v2-auto-group-{suffix}"
    now = _now()
    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (center_id, f"V2_AUTO_{suffix}_CENTER", "自动服务组织分中心", "REGIONAL_CENTER", "org-suzhou"),
            (class_id, f"V2_AUTO_{suffix}_CLASS", "自动服务组织班级", "CLASS", center_id),
            (group_id, f"V2_AUTO_{suffix}_GROUP", "自动服务组织小组", "GROUP", class_id),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now, now),
            )

    member_id = create_member(
        admin_id,
        member_code=f"V2-AUTO-MEMBER-{suffix}",
        name="岗位下拉无预配置验收学长",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=None,
        class_org_unit_id=class_id,
        group_org_unit_id=group_id,
    )
    catalog = member_editor_catalog(admin_id)
    assert {
        "volunteer_class_counselor",
        "volunteer_group_counselor",
        "volunteer_committee_operations",
        "volunteer_center_development_vice_chair",
    }.issubset({item["position_key"] for item in catalog["positions"]})
    assert not any(
        item["service_target_org_unit_id"] in {center_id, class_id, group_id}
        for item in catalog["service_units"]
    )

    scoped_org_ids = {center_id, class_id, group_id}
    with patch(
        "app.services.volunteer_management.accessible_org_ids",
        return_value=scoped_org_ids,
    ), patch(
        "app.services.volunteer_positions.accessible_org_ids",
        return_value=scoped_org_ids,
    ):
        group_appointment = create_appointment(
            admin_id,
            member_id=member_id,
            service_target_org_unit_id=group_id,
            position_key="volunteer_group_counselor",
            confirmation_note="",
        )
        committee_appointment = create_appointment(
            admin_id,
            member_id=member_id,
            service_target_org_unit_id=class_id,
            position_key="volunteer_committee_operations",
            confirmation_note="",
        )
    assert group_appointment["service_unit"]["service_target_org_unit_id"] == group_id
    assert committee_appointment["service_unit"]["service_target_org_unit_id"] == class_id

    group_unit = fetch_one(
        "SELECT parent_id FROM volunteer_service_units WHERE id=?",
        (group_appointment["service_unit"]["id"],),
    )
    group_parent = fetch_one(
        "SELECT service_target_org_unit_id FROM volunteer_service_units WHERE id=?",
        (group_unit["parent_id"],),
    )
    assert group_parent["service_target_org_unit_id"] == class_id

    committee_unit = fetch_one(
        "SELECT parent_id FROM volunteer_service_units WHERE id=?",
        (committee_appointment["service_unit"]["id"],),
    )
    committee_parent = fetch_one(
        "SELECT service_target_org_unit_id, system_type, line_type "
        "FROM volunteer_service_units WHERE id=?",
        (committee_unit["parent_id"],),
    )
    assert committee_parent == {
        "service_target_org_unit_id": center_id,
        "system_type": "COMMITTEE_LINE",
        "line_type": "OPERATIONS",
    }
