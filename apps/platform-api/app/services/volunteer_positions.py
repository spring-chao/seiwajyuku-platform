"""Configuration-driven volunteer positions and member-facing appointments.

The appointment row remains the historical source of truth.  This module only
adds a catalog that explains a position to the application (display name,
scope level and capabilities) and provides the explicit member-management
entry point for creating/listing appointments.  Legacy free-text member
fields are intentionally never converted automatically.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


STUDY_MEETING_MANAGE = "STUDY_MEETING_MANAGE"
CAPABILITY_NAMES = {
    STUDY_MEETING_MANAGE: "登记小组学习会",
}

# These defaults are also useful to old installations while 0039 is being
# rolled out.  Once the catalog table exists, database rows are authoritative.
POSITION_DEFAULTS: dict[str, dict[str, Any]] = {
    "volunteer_class_counselor": {
        "position_name": "班主任",
        "scope_level": "CLASS",
        "capabilities": [STUDY_MEETING_MANAGE],
        "sort_order": 10,
    },
    "volunteer_deputy_class_teacher": {
        "position_name": "副班主任",
        "scope_level": "CLASS",
        "capabilities": [STUDY_MEETING_MANAGE],
        "sort_order": 20,
    },
    "volunteer_class_monitor": {
        "position_name": "班长",
        "scope_level": "CLASS",
        "capabilities": [STUDY_MEETING_MANAGE],
        "sort_order": 30,
    },
    "volunteer_group_counselor": {
        "position_name": "辅导员",
        "scope_level": "GROUP",
        "capabilities": [STUDY_MEETING_MANAGE],
        "sort_order": 40,
    },
    "volunteer_group_leader": {
        "position_name": "组长",
        "scope_level": "GROUP",
        "capabilities": [STUDY_MEETING_MANAGE],
        "sort_order": 50,
    },
    "volunteer_director": {
        "position_name": "理事志工",
        "scope_level": "REGIONAL_CENTER",
        "capabilities": [],
        "sort_order": 100,
    },
    "volunteer_regional_lead": {
        "position_name": "分中心负责人志工",
        "scope_level": "REGIONAL_CENTER",
        "capabilities": [],
        "sort_order": 110,
    },
    "volunteer_regional_service": {
        "position_name": "分中心服务志工",
        "scope_level": "REGIONAL_CENTER",
        "capabilities": [],
        "sort_order": 120,
    },
    "volunteer_class_committee": {
        "position_name": "班委",
        "scope_level": "CLASS",
        "capabilities": [],
        "sort_order": 130,
    },
    "volunteer_group_committee": {
        "position_name": "组委",
        "scope_level": "GROUP",
        "capabilities": [],
        "sort_order": 140,
    },
    "volunteer_activity": {
        "position_name": "专项活动志工",
        "scope_level": "ANY",
        "capabilities": [],
        "sort_order": 200,
    },
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_timestamp(connection) -> str:
    current = datetime.now(UTC)
    if isinstance(connection, sqlite3.Connection):
        return current.isoformat()
    return current.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _write_gate(*, write: bool = False) -> None:
    settings = get_settings()
    if not settings.identity_authorization_enabled:
        raise PermissionError("身份与任职功能尚未启用")
    if write and not settings.identity_admin_writes_enabled:
        raise PermissionError("身份与任职写入尚未获准")
    if write and settings.is_production and not settings.allow_production_mutations:
        raise PermissionError("生产身份与任职写入未获批准")


def _row_to_position(row: dict[str, Any], capabilities: list[str]) -> dict[str, Any]:
    return {
        "position_key": row["position_key"],
        "position_name": row["position_name"],
        "scope_level": row["scope_level"],
        "is_active": bool(row.get("is_active", 1)),
        "sort_order": int(row.get("sort_order", 0)),
        "capabilities": capabilities,
        "capability_names": [CAPABILITY_NAMES.get(key, key) for key in capabilities],
    }


def _fallback_position(position_key: str) -> dict[str, Any] | None:
    default = POSITION_DEFAULTS.get(position_key)
    if not default:
        return None
    return {
        "position_key": position_key,
        "position_name": default["position_name"],
        "scope_level": default["scope_level"],
        "is_active": True,
        "sort_order": default["sort_order"],
        "capabilities": list(default["capabilities"]),
        "capability_names": [
            CAPABILITY_NAMES.get(key, key) for key in default["capabilities"]
        ],
    }


def _catalog_rows(connection=None, *, active_only: bool = True) -> list[dict[str, Any]]:
    try:
        if connection is None:
            rows = fetch_all(
                "SELECT position_key, position_name, scope_level, is_active, sort_order "
                "FROM volunteer_position_catalog "
                + ("WHERE is_active=1 " if active_only else "")
                + "ORDER BY sort_order, position_name, position_key"
            )
            capabilities = fetch_all(
                "SELECT position_key, capability_key FROM volunteer_position_capabilities"
            )
        else:
            rows = [
                dict(row)
                for row in execute(
                    connection,
                    "SELECT position_key, position_name, scope_level, is_active, sort_order "
                    "FROM volunteer_position_catalog "
                    + ("WHERE is_active=1 " if active_only else "")
                    + "ORDER BY sort_order, position_name, position_key",
                ).fetchall()
            ]
            capabilities = [
                dict(row)
                for row in execute(
                    connection,
                    "SELECT position_key, capability_key FROM volunteer_position_capabilities",
                ).fetchall()
            ]
    except Exception as exc:
        # A pre-0039 read remains useful during a rolling deployment.  Do not
        # hide arbitrary database failures once the table is present.
        if "no such table" not in str(exc).lower() and "doesn't exist" not in str(exc).lower():
            raise
        return [
            _fallback_position(key)
            for key in POSITION_DEFAULTS
            if not active_only or _fallback_position(key)
        ]
    by_key: dict[str, list[str]] = {}
    for capability in capabilities:
        by_key.setdefault(capability["position_key"], []).append(
            capability["capability_key"]
        )
    return [_row_to_position(row, by_key.get(row["position_key"], [])) for row in rows]


def list_volunteer_positions(*, active_only: bool = True) -> list[dict[str, Any]]:
    """Return operator-facing position definitions without technical IAM roles."""

    _write_gate()
    return _catalog_rows(active_only=active_only)


def get_volunteer_position(position_key: str, connection=None) -> dict[str, Any] | None:
    key = (position_key or "").strip()
    if not key:
        return None
    rows = _catalog_rows(connection, active_only=False)
    catalog_row = next((row for row in rows if row["position_key"] == key), None)
    if catalog_row is not None:
        return catalog_row if catalog_row["is_active"] else None
    return _fallback_position(key)


def validate_position_target(
    connection,
    *,
    position_key: str,
    org_unit_id: str,
    scope_type: str,
) -> dict[str, Any]:
    """Validate a position's service level against the selected org unit."""

    position = get_volunteer_position(position_key, connection)
    if not position or not position["is_active"]:
        raise ValueError("未知或已停用的志工岗位")
    normalized_scope = scope_type.upper().strip()
    if normalized_scope not in {"UNIT", "SUBTREE"}:
        raise ValueError("志工任职范围必须是 UNIT 或 SUBTREE")
    unit = execute(
        connection,
        "SELECT id, name, unit_type, is_active FROM org_units WHERE id=?",
        (org_unit_id,),
    ).fetchone()
    if not unit or not unit["is_active"]:
        raise ValueError("任职组织不存在或已停用")
    unit_type = str(unit["unit_type"] or "").upper()
    level = position["scope_level"]
    if level in {"CLASS", "GROUP"}:
        if unit_type != level:
            label = "班级" if level == "CLASS" else "小组"
            raise ValueError(f"{position['position_name']}只能服务{label}")
        if normalized_scope != "UNIT":
            raise ValueError(f"{position['position_name']}只能绑定一个{('班级' if level == 'CLASS' else '小组')}")
    elif level == "REGIONAL_CENTER" and unit_type in {"CLASS", "GROUP"}:
        raise ValueError("分中心岗位不能绑定班级或小组")
    return {
        **position,
        "org_unit_id": org_unit_id,
        "org_name": unit["name"],
        "org_unit_type": unit_type,
        "scope_type": normalized_scope,
    }


def _ensure_member_scope(actor_user_id: int, member: dict[str, Any]) -> None:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and member["org_unit_id"] not in allowed:
        raise PermissionError("学员不在当前组织授权范围内")


def _member_person(connection, member_id: int, *, actor_user_id: int, source: str) -> str:
    identity = execute(
        connection,
        "SELECT mi.person_id, mi.status FROM member_identities mi WHERE mi.member_id=?",
        (member_id,),
    ).fetchone()
    if identity:
        if identity["status"] != "ACTIVE":
            raise ValueError("学员身份档案不是有效状态")
        return identity["person_id"]
    member = execute(
        connection,
        "SELECT id, name, org_unit_id, status FROM members WHERE id=?",
        (member_id,),
    ).fetchone()
    if not member or member["status"] != "ACTIVE":
        raise ValueError("仅可为有效学员建立正式志工任职")
    now = _db_timestamp(connection)
    person_id = f"person-{uuid4()}"
    execute(
        connection,
        "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
        "VALUES (?, ?, 'ACTIVE', ?, ?)",
        (person_id, member["name"], now, now),
    )
    execute(
        connection,
        "INSERT INTO member_identities(member_id, person_id, status, source_reference, created_at, updated_at) "
        "VALUES (?, ?, 'ACTIVE', ?, ?, ?)",
        (member_id, person_id, source, now, now),
    )
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="identity.member_person.link",
        resource_type="member_identity",
        resource_id=str(member_id),
        org_unit_id=member["org_unit_id"],
        purpose="学员管理中明确添加正式志工任职",
        after={"member_id": member_id, "person_id": person_id, "source_reference": source},
    )
    return person_id


def _insert_appointment(
    connection,
    *,
    person_id: str,
    member_id: int | None,
    actor_user_id: int,
    position_key: str,
    org_unit_id: str,
    scope_type: str,
    starts_at: str,
    ends_at: str,
    source_reference: str,
    confirmation_note: str,
) -> int:
    target = validate_position_target(
        connection,
        position_key=position_key,
        org_unit_id=org_unit_id,
        scope_type=scope_type,
    )
    if execute(
        connection,
        "SELECT id FROM volunteer_appointments WHERE person_id=? "
        "AND appointment_key=? AND org_unit_id=? "
        "AND status IN ('PLANNED','ACTIVE','SUSPENDED') "
        "AND starts_at<? AND ends_at>? LIMIT 1",
        (person_id, position_key, org_unit_id, ends_at, starts_at),
    ).fetchone():
        raise ValueError("相同组织和任职存在重叠任期")
    now = _db_timestamp(connection)
    try:
        start_value = datetime.fromisoformat(starts_at)
        if start_value.tzinfo is None:
            start_value = start_value.replace(tzinfo=UTC)
        else:
            start_value = start_value.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise ValueError("任职时间格式无效") from exc
    status = "PLANNED" if start_value > datetime.now(UTC) else "ACTIVE"
    cursor = execute(
        connection,
        "INSERT INTO volunteer_appointments"
        "(person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, "
        "status, source_reference, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            person_id,
            position_key,
            org_unit_id,
            target["scope_type"],
            starts_at,
            ends_at,
            status,
            source_reference,
            now,
            now,
        ),
    )
    appointment_id = int(cursor.lastrowid)
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="identity.volunteer_appointment.create",
        resource_type="volunteer_appointment",
        resource_id=str(appointment_id),
        org_unit_id=org_unit_id,
        purpose=confirmation_note,
        after={
            "member_id": member_id,
            "person_id": person_id,
            "position_key": position_key,
            "position_name": target["position_name"],
            "scope_level": target["scope_level"],
            "scope_type": target["scope_type"],
            "starts_at": starts_at,
            "ends_at": ends_at,
            "source_reference": source_reference,
        },
    )
    return appointment_id


def _parse_term(starts_at: str, ends_at: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.fromisoformat(starts_at)
        end = datetime.fromisoformat(ends_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("任职时间格式无效") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    else:
        start = start.astimezone(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    else:
        end = end.astimezone(UTC)
    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    if end <= datetime.now(UTC):
        raise ValueError("结束时间必须晚于当前时间")
    return start, end


def create_member_volunteer_appointment(
    actor_user_id: int,
    member_id: int,
    *,
    position_key: str,
    org_unit_id: str,
    starts_at: str,
    ends_at: str,
    source_reference: str,
    confirmation_note: str,
) -> dict[str, Any]:
    """Explicitly add a formal appointment from the member-management page."""

    _write_gate(write=True)
    source = source_reference.strip()
    note = confirmation_note.strip()
    if len(source) < 4:
        raise ValueError("确认依据至少填写 4 个字符")
    if len(note) < 8:
        raise ValueError("业务确认说明至少填写 8 个字符")
    _parse_term(starts_at, ends_at)
    member = fetch_one(
        "SELECT id, name, org_unit_id, status FROM members WHERE id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学员不存在")
    _ensure_member_scope(actor_user_id, member)
    with transaction() as connection:
        person_id = _member_person(
            connection, member_id, actor_user_id=actor_user_id, source=source
        )
        appointment_id = _insert_appointment(
            connection,
            person_id=person_id,
            member_id=member_id,
            actor_user_id=actor_user_id,
            position_key=position_key.strip(),
            org_unit_id=org_unit_id.strip(),
            scope_type="UNIT",
            starts_at=starts_at,
            ends_at=ends_at,
            source_reference=source,
            confirmation_note=note,
        )
    return {
        "id": appointment_id,
        "member_id": member_id,
        "person_id": person_id,
        "position_key": position_key.strip(),
        "org_unit_id": org_unit_id.strip(),
    }


def list_member_volunteer_appointments(
    actor_user_id: int, member_id: int
) -> dict[str, Any]:
    _write_gate()
    member = fetch_one(
        "SELECT id, name, org_unit_id, status FROM members WHERE id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学员不存在")
    _ensure_member_scope(actor_user_id, member)
    identity = fetch_one(
        "SELECT mi.person_id, mi.status FROM member_identities mi WHERE mi.member_id=?",
        (member_id,),
    )
    if not identity:
        return {"member_id": member_id, "person_id": None, "identity_status": None, "appointments": []}
    appointments = fetch_all(
        "SELECT va.id, va.appointment_key, c.position_name, c.scope_level, "
        "va.org_unit_id, o.name AS org_name, o.unit_type AS org_unit_type, "
        "va.scope_type, va.starts_at, va.ends_at, va.status, va.source_reference, "
        "va.created_at, va.updated_at "
        "FROM volunteer_appointments va "
        "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
        "JOIN org_units o ON o.id=va.org_unit_id "
        "WHERE va.person_id=? ORDER BY va.starts_at DESC, va.id DESC",
        (identity["person_id"],),
    )
    for item in appointments:
        fallback = _fallback_position(item["appointment_key"])
        if not item.get("position_name") and fallback:
            item["position_name"] = fallback["position_name"]
        if not item.get("scope_level") and fallback:
            item["scope_level"] = fallback["scope_level"]
    return {
        "member_id": member_id,
        "person_id": identity["person_id"],
        "identity_status": identity["status"],
        "appointments": appointments,
    }


def change_member_volunteer_appointment_status(
    actor_user_id: int,
    member_id: int,
    appointment_id: int,
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    _write_gate(write=True)
    normalized_status = status.upper().strip()
    if normalized_status not in {"SUSPENDED", "ENDED", "REVOKED"}:
        raise ValueError("志工任职状态只能是 SUSPENDED、ENDED 或 REVOKED")
    reason = reason.strip()
    if len(reason) < 6:
        raise ValueError("任职状态变更原因至少填写 6 个字符")
    member = fetch_one(
        "SELECT id, org_unit_id, status FROM members WHERE id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学员不存在")
    _ensure_member_scope(actor_user_id, member)
    with transaction() as connection:
        identity = execute(
            connection,
            "SELECT person_id FROM member_identities WHERE member_id=?",
            (member_id,),
        ).fetchone()
        if not identity:
            raise ValueError("该学员尚未建立正式身份档案")
        appointment = execute(
            connection,
            "SELECT va.*, c.position_name FROM volunteer_appointments va "
            "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
            "WHERE va.id=? AND va.person_id=? LIMIT 1",
            (appointment_id, identity["person_id"]),
        ).fetchone()
        if not appointment:
            raise ValueError("志工任职记录不存在或不属于该学员")
        if appointment["status"] in {"ENDED", "REVOKED"}:
            raise ValueError("该任职已经结束，不能再次变更")
        now = _db_timestamp(connection)
        execute(
            connection,
            "UPDATE volunteer_appointments SET status=?, updated_at=? WHERE id=?",
            (normalized_status, now, appointment_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.volunteer_appointment.status_change",
            resource_type="volunteer_appointment",
            resource_id=str(appointment_id),
            org_unit_id=appointment["org_unit_id"],
            purpose=reason,
            before={"status": appointment["status"]},
            after={
                "status": normalized_status,
                "member_id": member_id,
                "position_key": appointment["appointment_key"],
                "position_name": appointment["position_name"],
            },
        )
    return {"id": appointment_id, "member_id": member_id, "status": normalized_status}
