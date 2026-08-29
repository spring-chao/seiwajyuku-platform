from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import patch

import pytest

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.volunteer_positions import (
    change_member_volunteer_appointment_status,
    create_member_volunteer_appointment,
    list_member_volunteer_appointments,
    list_volunteer_positions,
)
from app.services.wechat_identity import get_member_role_scopes, role_for_target


def _stamp(days: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def test_default_start_never_rounds_forward_in_mysql_datetime_zero():
    from app.services.volunteer_positions import _parse_term
    instant = datetime(2026, 8, 27, 12, 0, 0, 900000, tzinfo=UTC)
    with patch("app.services.volunteer_positions.datetime", wraps=datetime) as clock:
        clock.now.return_value = instant
        start, end = _parse_term(None, None)
    assert start == instant.replace(microsecond=0)
    assert start <= instant and end is None


def _admin_id() -> int:
    return int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])


def _fixture() -> dict[str, str | int]:
    suffix = uuid4().hex[:10]
    center_id = f"m2-center-{suffix}"
    class_id = f"m2-class-{suffix}"
    group_id = f"m2-group-{suffix}"
    other_group_id = f"m2-group-other-{suffix}"
    member_code = f"M2-{suffix}"
    now = _stamp()
    with transaction() as connection:
        for unit_id, unit_type, parent_id, name in (
            (center_id, "REGIONAL_CENTER", "org-suzhou", "M2测试中心"),
            (class_id, "CLASS", center_id, "M2测试班"),
            (group_id, "GROUP", class_id, "M2第一小组"),
            (other_group_id, "GROUP", class_id, "M2第二小组"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (unit_id, unit_id.upper(), name, unit_type, parent_id, now, now),
            )
        cursor = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, class_committee_name, created_at, updated_at) "
            "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (member_code, "M2测试学员", class_id, "历史组长文本", now, now),
        )
        member_id = int(cursor.lastrowid)
    return {
        "center_id": center_id,
        "class_id": class_id,
        "group_id": group_id,
        "other_group_id": other_group_id,
        "member_id": member_id,
    }


def _payload(position_key: str, org_unit_id: str) -> dict[str, str]:
    return {
        "position_key": position_key,
        "org_unit_id": org_unit_id,
        "starts_at": _stamp(-1),
        "ends_at": _stamp(30),
        "source_reference": "M2业务确认表-001",
        "confirmation_note": "业务负责人已确认本次志工任职范围",
    }


def test_catalog_seeds_five_positions_and_only_their_capability() -> None:
    positions = list_volunteer_positions()
    by_name = {item["position_name"]: item for item in positions}
    assert {"班主任", "副班主任", "辅导员", "班长", "组长"}.issubset(by_name)
    assert all(
        "STUDY_MEETING_MANAGE" in by_name[name]["capabilities"]
        for name in ("班主任", "副班主任", "辅导员", "班长", "组长")
    )
    assert by_name["班主任"]["scope_level"] == "CLASS"
    assert by_name["辅导员"]["scope_level"] == "GROUP"
    assert by_name["专项活动志工"]["capabilities"] == []


def test_current_service_catalog_hides_umbrella_labels_but_keeps_full_catalog() -> None:
    hidden_keys = {
        "volunteer_regional_service",
        "volunteer_class_committee",
        "volunteer_group_committee",
    }

    current_keys = {item["position_key"] for item in list_volunteer_positions()}
    full_catalog = {
        item["position_key"]: item
        for item in list_volunteer_positions(active_only=False)
    }

    assert hidden_keys.isdisjoint(current_keys)
    assert hidden_keys <= full_catalog.keys()
    assert all(full_catalog[key]["is_active"] for key in hidden_keys)
    assert "volunteer_group_counselor" in current_keys


def test_member_entry_validates_scope_and_preserves_multiple_appointments() -> None:
    data = _fixture()
    actor = _admin_id()
    class_appointment = create_member_volunteer_appointment(
        actor,
        int(data["member_id"]),
        **_payload("volunteer_class_monitor", str(data["class_id"])),
    )
    group_appointment = create_member_volunteer_appointment(
        actor,
        int(data["member_id"]),
        **_payload("volunteer_group_leader", str(data["group_id"])),
    )
    assert class_appointment["id"] != group_appointment["id"]
    appointments = list_member_volunteer_appointments(actor, int(data["member_id"]))
    assert len(appointments["appointments"]) == 2
    assert appointments["person_id"]
    assert fetch_one(
        "SELECT class_committee_name FROM members WHERE id=?",
        (data["member_id"],),
    )["class_committee_name"] == "历史组长文本"

    with pytest.raises(ValueError, match="只能服务小组"):
        create_member_volunteer_appointment(
            actor,
            int(data["member_id"]),
            **_payload("volunteer_group_counselor", str(data["class_id"])),
        )
    with pytest.raises(ValueError, match="只能服务班级"):
        create_member_volunteer_appointment(
            actor,
            int(data["member_id"]),
            **_payload("volunteer_class_monitor", str(data["group_id"])),
        )


def test_capability_resolver_uses_position_catalog_and_exact_scope() -> None:
    data = _fixture()
    actor = _admin_id()
    create_member_volunteer_appointment(
        actor,
        int(data["member_id"]),
        **_payload("volunteer_class_counselor", str(data["class_id"])),
    )
    scopes = get_member_role_scopes(int(data["member_id"]))
    assert scopes[0]["position_key"] == "volunteer_class_counselor"
    assert scopes[0]["position_name"] == "班主任"
    assert scopes[0]["scope_level"] == "CLASS"
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["other_group_id"])
    ) == "CLASS_COUNSELOR"

    # A new configured position with no capability is a valid appointment but
    # cannot silently grant study-meeting access.
    now = _stamp()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO volunteer_position_catalog "
            "(position_key, position_name, scope_level, is_active, sort_order, created_at, updated_at) "
            "VALUES (?, ?, 'GROUP', 1, 999, ?, ?)",
            (f"volunteer_m2_future_{uuid4().hex[:6]}", "学习委员", now, now),
        )
    future_key = fetch_one(
        "SELECT position_key FROM volunteer_position_catalog WHERE position_name='学习委员' ORDER BY sort_order DESC"
    )["position_key"]
    create_member_volunteer_appointment(
        actor,
        int(data["member_id"]),
        **_payload(future_key, str(data["group_id"])),
    )
    assert not any(
        scope.get("position_key") == future_key
        for scope in get_member_role_scopes(int(data["member_id"]))
    )


def test_group_position_is_limited_to_its_group_and_status_keeps_history() -> None:
    data = _fixture()
    actor = _admin_id()
    appointment = create_member_volunteer_appointment(
        actor,
        int(data["member_id"]),
        **_payload("volunteer_group_counselor", str(data["group_id"])),
    )
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["group_id"])
    ) == "GROUP_LEADER"
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["other_group_id"])
    ) is None
    change_member_volunteer_appointment_status(
        actor,
        int(data["member_id"]),
        int(appointment["id"]),
        status="ENDED",
        reason="业务负责人确认该任职已结束",
    )
    row = fetch_one(
        "SELECT status FROM volunteer_appointments WHERE id=?", (appointment["id"],)
    )
    assert row["status"] == "ENDED"
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["group_id"])
    ) is None
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM volunteer_appointments WHERE id=?",
        (appointment["id"],),
    )["count"] == 1


def test_member_entry_defaults_to_machine_source_and_open_ended_term() -> None:
    data = _fixture()
    actor = _admin_id()
    appointment = create_member_volunteer_appointment(
        actor,
        int(data["member_id"]),
        position_key="volunteer_group_leader",
        org_unit_id=str(data["group_id"]),
    )

    row = fetch_one(
        "SELECT starts_at, ends_at, source_reference, status "
        "FROM volunteer_appointments WHERE id=?",
        (appointment["id"],),
    )
    assert row["starts_at"]
    assert row["ends_at"] is None
    assert row["source_reference"] == "MEMBER_ADMIN_MANUAL"
    assert row["status"] == "ACTIVE"
    assert appointment["ends_at"] is None
    assert role_for_target(
        int(data["member_id"]), str(data["class_id"]), str(data["group_id"])
    ) == "GROUP_LEADER"

    listed = list_member_volunteer_appointments(actor, int(data["member_id"]))
    assert listed["appointments"][0]["ends_at"] is None

    change_member_volunteer_appointment_status(
        actor,
        int(data["member_id"]),
        int(appointment["id"]),
        status="ENDED",
    )
    audit = fetch_one(
        "SELECT purpose FROM audit_logs "
        "WHERE action='identity.volunteer_appointment.status_change' "
        "AND resource_id=? ORDER BY id DESC LIMIT 1",
        (str(appointment["id"]),),
    )
    assert audit["purpose"] == "运营人员在学员管理中确认结束该志工任职"
