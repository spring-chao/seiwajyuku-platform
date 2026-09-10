from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api import renewals as renewals_api
from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.members import create_member
from app.services.renewal_support_network import (
    build_renewal_support_network,
    create_renewal_support_request,
    update_renewal_support_request,
)
from app.services.renewals import get_action_card, list_cycles
from app.services.volunteer_management import (
    change_appointment_status,
    create_appointment,
    create_service_unit,
)


def _admin_id() -> int:
    row = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert row is not None
    return int(row["id"])


def _support_fixture() -> dict[str, int | str]:
    suffix = uuid4().hex[:10]
    admin_id = _admin_id()
    now = datetime.now(UTC)
    now_text = now.isoformat()
    cycle_year = now.year + 1
    center_id = f"renewal-support-center-{suffix}"
    class_id = f"renewal-support-class-{suffix}"
    group_id = f"renewal-support-group-{suffix}"
    other_center_id = f"renewal-support-other-{suffix}"
    center_name = f"关系助力测试分中心-{suffix}"
    with transaction() as connection:
        for org_id, code, name, unit_type, parent_id in (
            (center_id, f"SUPPORT_CENTER_{suffix}", center_name, "REGIONAL_CENTER", "org-suzhou"),
            (class_id, f"SUPPORT_CLASS_{suffix}", "关系助力测试班", "CLASS", center_id),
            (group_id, f"SUPPORT_GROUP_{suffix}", "关系助力测试组", "GROUP", class_id),
            (other_center_id, f"SUPPORT_OTHER_{suffix}", "关系助力范围外分中心", "REGIONAL_CENTER", "org-suzhou"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (org_id, code, name, unit_type, parent_id, now_text, now_text),
            )

    def member(code: str, name: str, **kwargs) -> int:
        return create_member(
            admin_id,
            member_code=f"SUPPORT-{code}-{suffix}",
            name=name,
            org_unit_id=center_id,
            development_org_unit_id=None,
            phone=None,
            renewal_month=f"{now.year + 1}-09",
            **kwargs,
        )

    leader_id = member("LEADER", "关系网组长")
    target_id = member(
        "TARGET",
        "待协同续费学长",
        class_org_unit_id=class_id,
        group_org_unit_id=group_id,
        referrer="关系网组长",
        referrer_center=center_name,
    )
    counselor_id = member("COUNSELOR", "关系网辅导员")
    teacher_one_id = member("TEACHER1", "关系网班主任甲")
    teacher_two_id = member("TEACHER2", "关系网班主任乙")
    deputy_teacher_id = member("DEPUTY", "关系网副班主任")
    class_development_id = member("CLASSDEV", "关系网班级发展委")
    center_development_one_id = member("CENTERDEV1", "关系网分中心发展委甲")
    center_development_two_id = member("CENTERDEV2", "关系网分中心发展委乙")
    ended_id = member("ENDED", "已结束志工")
    revoked_id = member("REVOKED", "已撤销志工")
    expired_id = member("EXPIRED", "已到期志工")
    future_id = member("FUTURE", "未开始志工")

    group_team = create_service_unit(
        admin_id,
        unit_code=f"SUPPORT_GROUP_TEAM_{suffix}",
        name="关系助力小组志工团队",
        system_type="CLASS_TEAM",
        line_type="GENERAL",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=group_id,
    )
    class_team = create_service_unit(
        admin_id,
        unit_code=f"SUPPORT_CLASS_TEAM_{suffix}",
        name="关系助力班级志工团队",
        system_type="CLASS_TEAM",
        line_type="GENERAL",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=class_id,
    )
    class_development = create_service_unit(
        admin_id,
        unit_code=f"SUPPORT_CLASS_DEVELOPMENT_{suffix}",
        name="关系助力班级发展建设委",
        system_type="COMMITTEE_LINE",
        line_type="DEVELOPMENT",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=class_id,
    )
    center_development = create_service_unit(
        admin_id,
        unit_code=f"SUPPORT_CENTER_DEVELOPMENT_{suffix}",
        name="关系助力分中心发展建设委",
        system_type="GOVERNANCE",
        line_type="DEVELOPMENT",
        parent_id=None,
        home_shuku_org_unit_id="org-suzhou",
        service_target_org_unit_id=center_id,
    )

    def appoint(member_id: int, service_unit_id: str, position_key: str) -> int:
        return int(
            create_appointment(
                admin_id,
                member_id=member_id,
                service_unit_id=service_unit_id,
                position_key=position_key,
                confirmation_note="关系助力网络本地测试任职",
            )["id"]
        )

    appoint(leader_id, group_team["id"], "volunteer_group_leader")
    appoint(counselor_id, group_team["id"], "volunteer_group_counselor")
    appoint(teacher_one_id, class_team["id"], "volunteer_class_counselor")
    appoint(teacher_two_id, class_team["id"], "volunteer_class_counselor")
    appoint(deputy_teacher_id, class_team["id"], "volunteer_deputy_class_teacher")
    appoint(class_development_id, class_development["id"], "volunteer_committee_development")
    appoint(
        center_development_one_id,
        center_development["id"],
        "volunteer_center_development_vice_chair",
    )
    appoint(
        center_development_two_id,
        center_development["id"],
        "volunteer_center_development_director",
    )
    ended_appointment_id = appoint(ended_id, group_team["id"], "volunteer_group_leader")
    revoked_appointment_id = appoint(
        revoked_id,
        class_development["id"],
        "volunteer_committee_development",
    )
    expired_appointment_id = appoint(expired_id, group_team["id"], "volunteer_group_leader")
    future_appointment_id = appoint(future_id, group_team["id"], "volunteer_group_leader")
    change_appointment_status(
        admin_id,
        ended_appointment_id,
        status="ENDED",
        reason="本地测试结束任职，不应作为当前助力人",
    )
    change_appointment_status(
        admin_id,
        revoked_appointment_id,
        status="REVOKED",
        reason="本地测试撤销任职，不应作为当前助力人",
    )
    with transaction() as connection:
        execute(
            connection,
            "UPDATE volunteer_appointments SET ends_at=?, updated_at=? WHERE id=?",
            ((now - timedelta(days=1)).isoformat(), now_text, expired_appointment_id),
        )
        execute(
            connection,
            "UPDATE volunteer_appointments SET starts_at=?, updated_at=? WHERE id=?",
            ((now + timedelta(days=1)).isoformat(), now_text, future_appointment_id),
        )
        cycle_id = int(
            execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'IN_COMMUNICATION', ?, ?)",
                (target_id, cycle_year, center_id, now.month, now_text, now_text),
            ).lastrowid
        )

    scoped_user_id = create_user(
        admin_id,
        username=f"renewal-support-user-{suffix}",
        display_name="关系助力范围内运营",
        password="renewal-support-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    denied_user_id = create_user(
        admin_id,
        username=f"renewal-support-denied-{suffix}",
        display_name="关系助力范围外运营",
        password="renewal-support-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "UNIT", "org_unit_id": other_center_id}],
    )
    ambiguous_target_id = member(
        "AMBIGUOUS",
        "推荐人待核对学长",
        referrer="同名推荐人",
        referrer_center=center_name,
    )
    member("AMBIGUOUS1", "同名推荐人")
    member("AMBIGUOUS2", "同名推荐人")
    return {
        "admin_id": admin_id,
        "scoped_user_id": scoped_user_id,
        "denied_user_id": denied_user_id,
        "target_id": target_id,
        "ambiguous_target_id": ambiguous_target_id,
        "leader_id": leader_id,
        "cycle_id": cycle_id,
        "center_id": center_id,
        "year": cycle_year,
    }


def _add_followup(cycle_id: int, actor_user_id: int, *, needs_support: bool) -> None:
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO renewal_followups(renewal_cycle_id, followed_at, followed_by, channel, summary, intention, needs_support, next_action, next_followup_at, created_at) "
            "VALUES (?, ?, ?, 'PHONE', '本地测试沟通摘要', '待确认', ?, NULL, NULL, ?)",
            (cycle_id, now, actor_user_id, int(needs_support), now),
        )


def test_support_network_uses_current_formal_roles_and_deduplicates_people() -> None:
    fixture = _support_fixture()
    network = build_renewal_support_network(
        int(fixture["target_id"]), int(fixture["scoped_user_id"])
    )
    roles = network["roles"]
    assert roles["referrers"] == [
        {
            **roles["referrers"][0],
            "role": "REFERRER",
            "name": "关系网组长",
            "member_id": fixture["leader_id"],
            "resolved": True,
        }
    ]
    assert {item["name"] for item in roles["group_leaders"]} == {"关系网组长"}
    assert {item["name"] for item in roles["group_counselors"]} == {"关系网辅导员"}
    assert {item["name"] for item in roles["class_teachers"]} == {
        "关系网班主任甲",
        "关系网班主任乙",
        "关系网副班主任",
    }
    assert {item["role"] for item in roles["class_teachers"]} == {
        "CLASS_TEACHER",
        "DEPUTY_CLASS_TEACHER",
    }
    assert {item["name"] for item in roles["class_development"]} == {"关系网班级发展委"}
    assert {item["name"] for item in roles["center_development"]} == {
        "关系网分中心发展委甲",
        "关系网分中心发展委乙",
    }
    visible_names = {
        item["name"]
        for entries in roles.values()
        for item in entries
    }
    assert {"已结束志工", "已撤销志工", "已到期志工", "未开始志工"}.isdisjoint(visible_names)
    recommended_leader = next(
        item
        for item in network["recommended_supporters"]
        if item["member_id"] == fixture["leader_id"]
    )
    assert recommended_leader["roles"] == ["REFERRER", "GROUP_LEADER"]
    assert recommended_leader["primary_role"] == "REFERRER"
    assert network["data_quality"]["referrer_resolution"] == "RESOLVED"
    assert "phone" not in json.dumps(network, ensure_ascii=False).lower()


def test_unresolved_referrer_and_scope_are_safe() -> None:
    fixture = _support_fixture()
    network = build_renewal_support_network(
        int(fixture["ambiguous_target_id"]), int(fixture["scoped_user_id"])
    )
    referrer = network["roles"]["referrers"][0]
    assert referrer["name"] == "同名推荐人"
    assert referrer["resolved"] is False
    assert referrer["member_id"] is None
    assert network["data_quality"]["referrer_resolution"] == "AMBIGUOUS"
    with pytest.raises(PermissionError):
        build_renewal_support_network(
            int(fixture["target_id"]), int(fixture["denied_user_id"])
        )


def test_support_request_lifecycle_updates_action_card_and_list_state() -> None:
    fixture = _support_fixture()
    cycle_id = int(fixture["cycle_id"])
    admin_id = int(fixture["admin_id"])
    target_id = int(fixture["target_id"])

    initial = get_action_card(cycle_id, admin_id)
    assert initial["support"]["needed"] is False
    assert initial["support"]["status"] == "NONE"

    _add_followup(cycle_id, admin_id, needs_support=True)
    pending = get_action_card(cycle_id, admin_id)
    assert pending["support"]["needed"] is True
    assert pending["support"]["status"] == "PENDING"
    leader = next(
        item
        for item in pending["support"]["network"]["roles"]["group_leaders"]
        if item["member_id"] == fixture["leader_id"]
    )
    created = create_renewal_support_request(
        cycle_id,
        admin_id,
        supporter_role=leader["role"],
        supporter_member_id=leader["member_id"],
        supporter_person_id=leader["person_id"],
        supporter_name_snapshot=leader["name"],
    )
    assert created["created"] is True
    duplicate = renewals_api.create_cycle_support_request(
        cycle_id,
        renewals_api.RenewalSupportRequestPayload(
            supporter_role="GROUP_LEADER",
            supporter_member_id=int(fixture["leader_id"]),
            supporter_name_snapshot="关系网组长",
        ),
        user={"id": admin_id},
    )
    assert duplicate["success"] is True
    assert duplicate["data"]["created"] is False

    in_progress = get_action_card(cycle_id, admin_id)
    assert in_progress["support"]["status"] == "IN_PROGRESS"
    list_row = next(
        row
        for row in list_cycles(
            admin_id,
            int(fixture["year"]),
            org_unit_id=str(fixture["center_id"]),
            renewal_status="ALL",
            include_past=True,
        )
        if row["member_id"] == target_id
    )
    assert list_row["support_status"] == "IN_PROGRESS"

    feedback = renewals_api.edit_cycle_support_request(
        cycle_id,
        int(created["id"]),
        renewals_api.RenewalSupportRequestUpdatePayload(
            status="FEEDBACK_RECEIVED",
            feedback_summary="已沟通，计划班会后再关心一次",
            next_action="运营专员下周确认后续安排",
        ),
        user={"id": admin_id},
    )
    assert feedback["success"] is True
    assert feedback["data"]["status"] == "FEEDBACK_RECEIVED"
    with_feedback = get_action_card(cycle_id, admin_id)
    assert with_feedback["support"]["status"] == "FEEDBACK_RECEIVED"
    assert with_feedback["support"]["current_requests"][0]["feedback_summary"] == "已沟通，计划班会后再关心一次"
    assert "phone" not in json.dumps(with_feedback["support"], ensure_ascii=False).lower()
    assert fetch_one(
        "SELECT id FROM audit_logs WHERE action='renewals.support_request.create' AND resource_id=?",
        (str(created["id"]),),
    )
    assert fetch_one(
        "SELECT id FROM audit_logs WHERE action='renewals.support_request.update' AND resource_id=?",
        (str(created["id"]),),
    )

    with transaction() as connection:
        execute(
            connection,
            "UPDATE renewal_cycles SET status='RENEWED', updated_at=? WHERE id=?",
            (datetime.now(UTC).isoformat(), cycle_id),
        )
    closed = get_action_card(cycle_id, admin_id)
    assert closed["support"]["needed"] is False
    assert closed["support"]["status"] == "NONE"
    assert len(closed["support"]["current_requests"]) == 1
    with pytest.raises(ValueError, match="已闭环"):
        create_renewal_support_request(
            cycle_id,
            admin_id,
            supporter_role=leader["role"],
            supporter_member_id=leader["member_id"],
            supporter_person_id=leader["person_id"],
            supporter_name_snapshot=leader["name"],
        )


def test_support_request_requires_latest_needs_support_and_scope() -> None:
    fixture = _support_fixture()
    cycle_id = int(fixture["cycle_id"])
    admin_id = int(fixture["admin_id"])
    network = build_renewal_support_network(int(fixture["target_id"]), admin_id)
    leader = network["roles"]["group_leaders"][0]
    with pytest.raises(ValueError, match="未标记需要协同助力"):
        create_renewal_support_request(
            cycle_id,
            admin_id,
            supporter_role=leader["role"],
            supporter_member_id=leader["member_id"],
            supporter_person_id=leader["person_id"],
            supporter_name_snapshot=leader["name"],
        )
    _add_followup(cycle_id, admin_id, needs_support=True)
    with pytest.raises(PermissionError):
        create_renewal_support_request(
            cycle_id,
            int(fixture["denied_user_id"]),
            supporter_role=leader["role"],
            supporter_member_id=leader["member_id"],
            supporter_person_id=leader["person_id"],
            supporter_name_snapshot=leader["name"],
        )
    with pytest.raises(ValueError, match="关系网络"):
        create_renewal_support_request(
            cycle_id,
            admin_id,
            supporter_role="GROUP_LEADER",
            supporter_name_snapshot="不存在的关系人",
        )


def test_support_request_feedback_requires_a_summary() -> None:
    fixture = _support_fixture()
    cycle_id = int(fixture["cycle_id"])
    admin_id = int(fixture["admin_id"])
    _add_followup(cycle_id, admin_id, needs_support=True)
    leader = build_renewal_support_network(
        int(fixture["target_id"]), admin_id
    )["roles"]["group_leaders"][0]
    created = create_renewal_support_request(
        cycle_id,
        admin_id,
        supporter_role=leader["role"],
        supporter_member_id=leader["member_id"],
        supporter_person_id=leader["person_id"],
        supporter_name_snapshot=leader["name"],
    )
    with pytest.raises(ValueError, match="请填写简短反馈"):
        update_renewal_support_request(
            cycle_id,
            int(created["id"]),
            admin_id,
            status="FEEDBACK_RECEIVED",
        )
