from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.members import get_member_edit_profile, update_member
from app.services.volunteer_positions import (
    get_member_volunteer_services,
    read_member_current_volunteer_position,
    set_member_current_volunteer_position,
)
from app.services.wechat_identity import get_member_role_scopes, role_for_target


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _fixture(*, with_class: bool = True, with_group: bool = True) -> dict[str, str | int]:
    suffix = uuid4().hex[:10]
    center_id = f"current-center-{suffix}"
    class_id = f"current-class-{suffix}"
    other_class_id = f"current-class-other-{suffix}"
    group_id = f"current-group-{suffix}"
    other_group_id = f"current-group-other-{suffix}"
    now = _now()
    with transaction() as connection:
        units = [
            (center_id, "REGIONAL_CENTER", "org-suzhou", "当前志工测试中心"),
            (class_id, "CLASS", center_id, "当前志工测试班"),
            (other_class_id, "CLASS", center_id, "当前志工测试二班"),
            (group_id, "GROUP", class_id, "当前志工测试小组"),
            (other_group_id, "GROUP", class_id, "当前志工测试二组"),
        ]
        for unit_id, unit_type, parent_id, name in units:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (unit_id, unit_id.upper(), name, unit_type, parent_id, now, now),
            )
        cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, class_name, group_name, created_at, updated_at) "
            "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
            (
                f"CURRENT-{suffix}",
                "当前志工测试学员",
                center_id,
                "当前志工测试班" if with_class else None,
                "当前志工测试小组" if with_group else None,
                now,
                now,
            ),
        )
        member_id = int(cursor.lastrowid)
        relations = [("PRIMARY_REGION", center_id)]
        if with_class:
            relations.append(("STUDY_CLASS", class_id))
        if with_group:
            relations.append(("STUDY_GROUP", group_id))
        for relation_type, unit_id in relations:
            execute(
                connection,
                "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (member_id, unit_id, relation_type, now, now),
            )
    return {
        "center_id": center_id,
        "class_id": class_id,
        "other_class_id": other_class_id,
        "group_id": group_id,
        "other_group_id": other_group_id,
        "member_id": member_id,
    }


def test_current_group_service_is_one_active_row_with_capability_and_audit() -> None:
    data = _fixture()
    actor = _admin_id()

    result = set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_group_counselor"
    )

    assert result["is_volunteer"] is True
    assert result["position_key"] == "volunteer_group_counselor"
    assert result["scope_org_unit_id"] == data["group_id"]
    assert result["scope_name"] == "当前志工测试小组"
    assert result["capabilities"] == ["STUDY_MEETING_MANAGE"]
    row = fetch_one(
        "SELECT va.status, va.org_unit_id, va.source_reference, va.ends_at FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id WHERE mi.member_id=?",
        (data["member_id"],),
    )
    assert row["status"] == "ACTIVE"
    assert row["org_unit_id"] == data["group_id"]
    assert row["source_reference"] == "MEMBER_ADMIN_CURRENT_SERVICE"
    assert row["ends_at"] is None
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM audit_logs WHERE action='members.current_volunteer_position.update' "
        "AND resource_id=?",
        (str(data["member_id"]),),
    )["count"] == 1

    current = read_member_current_volunteer_position(int(data["member_id"]))
    assert current["position_key"] == "volunteer_group_counselor"
    profile = get_member_edit_profile(int(data["member_id"]), actor)
    assert profile["current_volunteer_position_key"] == "volunteer_group_counselor"
    assert profile["current_volunteer_scope_name"] == "当前志工测试小组"
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["group_id"])
    ) == "GROUP_LEADER"


def test_clearing_current_service_ends_history_but_keeps_member_identity() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_group_leader"
    )

    result = set_member_current_volunteer_position(actor, int(data["member_id"]), None)

    assert result["is_volunteer"] is False
    row = fetch_one(
        "SELECT va.status, va.ends_at, mi.status AS identity_status, pp.status AS person_status "
        "FROM volunteer_appointments va JOIN member_identities mi ON mi.person_id=va.person_id "
        "JOIN person_profiles pp ON pp.id=va.person_id WHERE mi.member_id=?",
        (data["member_id"],),
    )
    assert row["status"] == "ENDED"
    assert row["ends_at"]
    assert row["identity_status"] == "ACTIVE"
    assert row["person_status"] == "ACTIVE"
    assert get_member_volunteer_services(int(data["member_id"]))["roles"] == []
    assert get_member_role_scopes(int(data["member_id"])) == []


def test_group_relation_change_follows_current_service_in_same_transaction() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_group_counselor"
    )

    update_member(
        actor,
        int(data["member_id"]),
        {"group_org_unit_id": str(data["other_group_id"])},
    )

    appointment = fetch_one(
        "SELECT va.org_unit_id FROM volunteer_appointments va JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )
    assert appointment["org_unit_id"] == data["other_group_id"]
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["other_group_id"])
    ) == "GROUP_LEADER"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM audit_logs WHERE action='members.current_volunteer_scope.sync' "
        "AND resource_type='volunteer_appointment'",
    )["count"] >= 1


def test_class_service_follows_class_relation_change() -> None:
    data = _fixture(with_group=False)
    actor = _admin_id()
    set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_class_counselor"
    )

    update_member(
        actor,
        int(data["member_id"]),
        {"class_org_unit_id": str(data["other_class_id"])},
    )

    appointment = fetch_one(
        "SELECT va.org_unit_id FROM volunteer_appointments va JOIN member_identities mi ON mi.person_id=va.person_id "
        "WHERE mi.member_id=? AND va.status='ACTIVE'",
        (data["member_id"],),
    )
    assert appointment["org_unit_id"] == data["other_class_id"]
    assert role_for_target(
        int(data["member_id"]), str(data["other_class_id"]), str(data["group_id"])
    ) == "CLASS_COUNSELOR"


@pytest.mark.parametrize(
    ("position_key", "with_class", "with_group", "message"),
    [
        ("volunteer_group_leader", True, False, "正式小组"),
        ("volunteer_class_counselor", False, True, "正式班级"),
    ],
)
def test_scope_is_derived_from_formal_relations_and_does_not_create_placeholder(
    position_key: str, with_class: bool, with_group: bool, message: str
) -> None:
    data = _fixture(with_class=with_class, with_group=with_group)
    actor = _admin_id()

    with pytest.raises(ValueError, match=message):
        set_member_current_volunteer_position(actor, int(data["member_id"]), position_key)

    assert fetch_one(
        "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
        (data["member_id"],),
    )["count"] == 0
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM person_profiles pp "
        "JOIN member_identities mi ON mi.person_id=pp.id WHERE mi.member_id=?",
        (data["member_id"],),
    )["count"] == 0


def test_member_profile_and_current_service_update_roll_back_together() -> None:
    data = _fixture(with_group=False)
    actor = _admin_id()

    with pytest.raises(ValueError, match="正式小组"):
        update_member(
            actor,
            int(data["member_id"]),
            {
                "name": "不应落库的名字",
                "current_volunteer_position_key": "volunteer_group_leader",
            },
        )

    member = fetch_one("SELECT name FROM members WHERE id=?", (data["member_id"],))
    assert member["name"] == "当前志工测试学员"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
        (data["member_id"],),
    )["count"] == 0


def test_ordinary_member_has_no_study_role_and_regional_no_capability_is_still_volunteer() -> None:
    ordinary = _fixture()
    assert get_member_volunteer_services(int(ordinary["member_id"])) == {
        "member_id": int(ordinary["member_id"]),
        "is_volunteer": False,
        "roles": [],
        "needs_manual_review": False,
        "review_message": None,
    }
    assert get_member_role_scopes(int(ordinary["member_id"])) == []

    regional = _fixture()
    actor = _admin_id()
    service = set_member_current_volunteer_position(
        actor, int(regional["member_id"]), "volunteer_regional_service"
    )
    assert service["scope_org_unit_id"] == regional["center_id"]
    services = get_member_volunteer_services(int(regional["member_id"]))
    assert services["is_volunteer"] is True
    assert services["roles"][0]["capabilities"] == []
    assert get_member_role_scopes(int(regional["member_id"])) == []


def test_multiple_active_appointments_require_review_and_are_not_deleted() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_group_leader"
    )
    identity = fetch_one(
        "SELECT person_id FROM member_identities WHERE member_id=?", (data["member_id"],)
    )
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, 'volunteer_activity', ?, 'UNIT', ?, NULL, 'ACTIVE', 'LEGACY_IMPORT', ?, ?)",
            (identity["person_id"], data["other_group_id"], (datetime.now(UTC) - timedelta(days=1)).isoformat(), now, now),
        )

    services = get_member_volunteer_services(int(data["member_id"]))
    assert services["is_volunteer"] is True
    assert services["needs_manual_review"] is True
    assert len(services["roles"]) == 2
    with pytest.raises(ValueError, match="多个有效志工岗位"):
        set_member_current_volunteer_position(actor, int(data["member_id"]), None)
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments WHERE person_id=? AND status='ACTIVE'",
        (identity["person_id"],),
    )["count"] == 2


def test_ordinary_profile_edit_does_not_touch_multiple_active_volunteer_appointments() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_group_counselor"
    )
    identity = fetch_one(
        "SELECT person_id FROM member_identities WHERE member_id=?", (data["member_id"],)
    )
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO volunteer_appointments(person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
            "VALUES (?, 'volunteer_activity', ?, 'UNIT', ?, NULL, 'ACTIVE', 'LEGACY_IMPORT', ?, ?)",
            (
                identity["person_id"],
                data["other_group_id"],
                (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                now,
                now,
            ),
        )

    before = fetch_all(
        "SELECT id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference "
        "FROM volunteer_appointments WHERE person_id=? AND status='ACTIVE' ORDER BY id",
        (identity["person_id"],),
    )
    assert get_member_edit_profile(int(data["member_id"]), actor)[
        "current_volunteer_needs_manual_review"
    ] is True

    update_member(
        actor,
        int(data["member_id"]),
        {"company_name": "普通资料编辑不应触碰任职"},
    )

    after = fetch_all(
        "SELECT id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference "
        "FROM volunteer_appointments WHERE person_id=? AND status='ACTIVE' ORDER BY id",
        (identity["person_id"],),
    )
    assert before == after
    assert fetch_one(
        "SELECT company_name FROM members WHERE id=?", (data["member_id"],)
    )["company_name"] == "普通资料编辑不应触碰任职"


def test_ordinary_profile_edit_does_not_touch_legacy_import_current_appointment() -> None:
    data = _fixture()
    actor = _admin_id()
    set_member_current_volunteer_position(
        actor, int(data["member_id"]), "volunteer_group_counselor"
    )
    identity = fetch_one(
        "SELECT person_id FROM member_identities WHERE member_id=?", (data["member_id"],)
    )
    with transaction() as connection:
        execute(
            connection,
            "UPDATE volunteer_appointments SET source_reference='LEGACY_IMPORT' "
            "WHERE person_id=? AND status='ACTIVE'",
            (identity["person_id"],),
        )

    before = fetch_all(
        "SELECT id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference "
        "FROM volunteer_appointments WHERE person_id=? AND status='ACTIVE'",
        (identity["person_id"],),
    )
    assert len(before) == 1
    assert before[0]["source_reference"] == "LEGACY_IMPORT"

    update_member(
        actor,
        int(data["member_id"]),
        {"notes": "普通资料编辑不应重置历史导入任职"},
    )

    after = fetch_all(
        "SELECT id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, status, source_reference "
        "FROM volunteer_appointments WHERE person_id=? AND status='ACTIVE'",
        (identity["person_id"],),
    )
    assert before == after
    assert fetch_one("SELECT notes FROM members WHERE id=?", (data["member_id"],))["notes"] == (
        "普通资料编辑不应重置历史导入任职"
    )


def test_ordinary_member_profile_edit_does_not_create_identity_or_appointment() -> None:
    data = _fixture()
    actor = _admin_id()

    update_member(
        actor,
        int(data["member_id"]),
        {"company_name": "普通学员资料修改"},
    )

    assert fetch_one(
        "SELECT company_name FROM members WHERE id=?", (data["member_id"],)
    )["company_name"] == "普通学员资料修改"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM member_identities WHERE member_id=?",
        (data["member_id"],),
    )["count"] == 0
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments va "
        "JOIN member_identities mi ON mi.person_id=va.person_id WHERE mi.member_id=?",
        (data["member_id"],),
    )["count"] == 0
