"""Configuration-driven volunteer positions and member-facing appointments.

The appointment row remains the historical source of truth.  This module only
adds a catalog that explains a position to the application (display name,
scope level and capabilities) and provides the explicit member-management
entry point for creating/listing appointments.  Legacy free-text member
fields are never converted while a profile is opened or edited; the separate
legacy-adoption service owns any explicitly confirmed batch conversion.
"""

from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


STUDY_MEETING_MANAGE = "STUDY_MEETING_MANAGE"
MEMBER_ADMIN_MANUAL_SOURCE = "MEMBER_ADMIN_MANUAL"
MEMBER_ADMIN_CURRENT_SERVICE_SOURCE = "MEMBER_ADMIN_CURRENT_SERVICE"
MEMBER_APPOINTMENT_DEFAULT_PURPOSE = "学员管理手工添加正式志工任职"
_MEMBER_ADMIN_MANAGED_SOURCES = {
    MEMBER_ADMIN_MANUAL_SOURCE,
    MEMBER_ADMIN_CURRENT_SERVICE_SOURCE,
    "LEGACY_POSITION_AUTO_ADOPT",
}
CAPABILITY_NAMES = {
    STUDY_MEETING_MANAGE: "登记小组学习会",
}
VOLUNTEER_STATUS_NAMES = {
    "PLANNED": "待开始",
    "ACTIVE": "服务中",
    "SUSPENDED": "已暂停",
    "ENDED": "已结束",
    "REVOKED": "已撤销",
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

# These umbrella labels are retained for legacy identity/appointment reads,
# but they are not actionable choices in the learner editor's current-service
# selector.  Concrete positions such as "辅导员" remain available.
CURRENT_VOLUNTEER_HIDDEN_POSITION_KEYS = frozenset(
    {
        "volunteer_regional_service",
        "volunteer_class_committee",
        "volunteer_group_committee",
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_timestamp(connection) -> str:
    current = datetime.now(UTC)
    if isinstance(connection, sqlite3.Connection):
        return current.isoformat()
    return current.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _appointment_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return (
        parsed.replace(tzinfo=UTC)
        if parsed.tzinfo is None
        else parsed.astimezone(UTC)
    )


def _public_appointment_timestamp(value: Any) -> str | None:
    parsed = _appointment_datetime(value)
    return parsed.isoformat() if parsed is not None else None


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


def _empty_current_volunteer_position(
    member_id: int,
    *,
    needs_manual_review: bool = False,
    review_message: str | None = None,
) -> dict[str, Any]:
    """Return the privacy-safe, explicit ordinary-learner representation."""

    return {
        "member_id": member_id,
        "is_volunteer": False,
        "position_key": None,
        "position_name": None,
        "scope_level": None,
        "scope_type": None,
        "scope_org_unit_id": None,
        "org_unit_id": None,
        "scope_name": None,
        "capabilities": [],
        "capability_names": [],
        "source_reference": None,
        "appointment_id": None,
        "starts_at": None,
        "ends_at": None,
        "needs_manual_review": needs_manual_review,
        "review_message": review_message,
    }


def _position_summary(
    row: dict[str, Any], position: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Map an appointment to the current-service vocabulary used by API/UI."""

    position = position or get_volunteer_position(row["appointment_key"])
    position_active = bool(position and position.get("is_active", True))
    position_name = (
        row.get("position_name")
        or (position or {}).get("position_name")
        or row["appointment_key"]
    )
    scope_level = row.get("scope_level") or (position or {}).get("scope_level")
    capabilities = (
        list((position or {}).get("capabilities", [])) if position_active else []
    )
    return {
        "appointment_id": int(row["id"]),
        "member_id": row.get("member_id"),
        "is_volunteer": True,
        "position_key": row["appointment_key"],
        "position_name": position_name,
        "scope_level": scope_level,
        "scope_type": row.get("scope_type"),
        "scope_org_unit_id": row.get("org_unit_id"),
        "org_unit_id": row.get("org_unit_id"),
        "scope_name": row.get("scope_name"),
        "capabilities": capabilities,
        "capability_names": [CAPABILITY_NAMES.get(key, key) for key in capabilities],
        "source_reference": row.get("source_reference"),
        "starts_at": row.get("starts_at"),
        "ends_at": row.get("ends_at"),
        "status": row.get("status"),
    }


def _effective_current_appointments(
    connection, member_id: int
) -> list[dict[str, Any]]:
    """Read ACTIVE, currently effective appointments without using old text fields."""

    now_value = datetime.now(UTC)
    try:
        rows = [
            dict(row)
            for row in execute(
                connection,
                "SELECT va.id, va.person_id, mi.member_id, va.appointment_key, "
                "va.org_unit_id, va.scope_type, va.starts_at, va.ends_at, va.status, "
                "va.source_reference, va.created_at, va.updated_at, "
                "c.position_name, c.scope_level, o.name AS scope_name, "
                "o.unit_type AS scope_org_unit_type "
                "FROM member_identities mi "
                "JOIN person_profiles pp ON pp.id=mi.person_id "
                "JOIN volunteer_appointments va ON va.person_id=mi.person_id "
                "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
                "LEFT JOIN org_units o ON o.id=va.org_unit_id "
                "WHERE mi.member_id=? AND mi.status='ACTIVE' AND pp.status='ACTIVE' "
                "AND va.status='ACTIVE' "
                "ORDER BY va.starts_at DESC, va.id DESC",
                (member_id,),
            ).fetchall()
        ]
    except Exception as exc:
        # The catalog was introduced after the appointment table. Keep the
        # current-service reader safe during a rolling schema deployment.
        message = str(exc).lower()
        if "volunteer_position_catalog" not in message and "no such table" not in message and "doesn't exist" not in message:
            raise
        rows = [
            dict(row)
            for row in execute(
                connection,
                "SELECT va.id, va.person_id, mi.member_id, va.appointment_key, "
                "va.org_unit_id, va.scope_type, va.starts_at, va.ends_at, va.status, "
                "va.source_reference, va.created_at, va.updated_at, "
                "o.name AS scope_name, o.unit_type AS scope_org_unit_type "
                "FROM member_identities mi "
                "JOIN person_profiles pp ON pp.id=mi.person_id "
                "JOIN volunteer_appointments va ON va.person_id=mi.person_id "
                "LEFT JOIN org_units o ON o.id=va.org_unit_id "
                "WHERE mi.member_id=? AND mi.status='ACTIVE' AND pp.status='ACTIVE' "
                "AND va.status='ACTIVE' "
                "ORDER BY va.starts_at DESC, va.id DESC",
                (member_id,),
            ).fetchall()
        ]
    rows = [
        row
        for row in rows
        if (start := _appointment_datetime(row.get("starts_at"))) is not None
        and start <= now_value
        and (
            row.get("ends_at") is None
            or (
                (end := _appointment_datetime(row.get("ends_at"))) is not None
                and end >= now_value
            )
        )
    ]
    for row in rows:
        position = get_volunteer_position(row["appointment_key"], connection)
        if position:
            row["position_name"] = row.get("position_name") or position["position_name"]
            row["scope_level"] = row.get("scope_level") or position["scope_level"]
        row["current_summary"] = _position_summary(row, position)
    return rows


def _manual_review_payload(
    member_id: int, appointments: list[dict[str, Any]]
) -> dict[str, Any]:
    payload = _empty_current_volunteer_position(
        member_id,
        needs_manual_review=True,
        review_message="当前存在多个有效志工岗位，请先人工确认主要岗位。",
    )
    payload["is_volunteer"] = bool(appointments)
    payload["active_appointments"] = [
        {
            "appointment_id": item["id"],
            "position_key": item["appointment_key"],
            "position_name": item["current_summary"]["position_name"],
            "scope_org_unit_id": item.get("org_unit_id"),
            "scope_name": item.get("scope_name"),
            "source_reference": item.get("source_reference"),
        }
        for item in appointments
    ]
    return payload


def read_member_current_volunteer_position(
    member_id: int, connection=None
) -> dict[str, Any]:
    """Resolve the one current service position for member edit/profile views."""

    if not get_settings().identity_authorization_enabled:
        return _empty_current_volunteer_position(member_id)
    context = nullcontext(connection) if connection is not None else transaction()
    try:
        with context as current_connection:
            appointments = _effective_current_appointments(current_connection, member_id)
            if len(appointments) > 1:
                return _manual_review_payload(member_id, appointments)
            if not appointments:
                return _empty_current_volunteer_position(member_id)
            result = dict(appointments[0]["current_summary"])
            result["member_id"] = member_id
            result["needs_manual_review"] = False
            result["review_message"] = None
            return result
    except Exception as exc:
        message = str(exc).lower()
        if "no such table" not in message and "doesn't exist" not in message:
            raise
        return _empty_current_volunteer_position(member_id)


def get_member_volunteer_services(member_id: int) -> dict[str, Any]:
    """Return current volunteer roles and capabilities independently of study meetings."""

    if not get_settings().identity_authorization_enabled:
        return {
            "member_id": member_id,
            "is_volunteer": False,
            "roles": [],
            "needs_manual_review": False,
            "review_message": None,
        }
    with transaction() as connection:
        appointments = _effective_current_appointments(connection, member_id)
    if len(appointments) > 1:
        review = _manual_review_payload(member_id, appointments)
        roles = [
            {
                key: value
                for key, value in item["current_summary"].items()
                if key
                in {
                    "position_key",
                    "position_name",
                    "scope_level",
                    "scope_type",
                    "scope_org_unit_id",
                    "org_unit_id",
                    "scope_name",
                    "capabilities",
                    "capability_names",
                }
            }
            for item in appointments
        ]
        return {
            "member_id": member_id,
            "is_volunteer": True,
            "roles": roles,
            "needs_manual_review": True,
            "review_message": review["review_message"],
        }
    roles = [
        {
            key: value
            for key, value in item["current_summary"].items()
            if key
            in {
                "position_key",
                "position_name",
                "scope_level",
                "scope_type",
                "scope_org_unit_id",
                "org_unit_id",
                "scope_name",
                "capabilities",
                "capability_names",
            }
        }
        for item in appointments
    ]
    return {
        "member_id": member_id,
        "is_volunteer": bool(roles),
        "roles": roles,
        "needs_manual_review": False,
        "review_message": None,
    }


def get_member_volunteer_history(member_id: int) -> dict[str, Any]:
    """Return only the current member's display-safe formal appointment history."""

    if not get_settings().identity_authorization_enabled:
        return {"appointments": []}
    with transaction() as connection:
        try:
            rows = [
                dict(row)
                for row in execute(
                    connection,
                    "SELECT va.appointment_key, c.position_name, o.name AS scope_name, "
                    "va.status, va.starts_at, va.ends_at "
                    "FROM member_identities mi "
                    "JOIN person_profiles pp ON pp.id=mi.person_id "
                    "JOIN volunteer_appointments va ON va.person_id=mi.person_id "
                    "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
                    "LEFT JOIN org_units o ON o.id=va.org_unit_id "
                    "WHERE mi.member_id=? AND mi.status='ACTIVE' AND pp.status='ACTIVE' "
                    "ORDER BY va.starts_at DESC, va.id DESC",
                    (member_id,),
                ).fetchall()
            ]
        except Exception as exc:
            message = str(exc).lower()
            if (
                "volunteer_position_catalog" not in message
                and "no such table" not in message
                and "doesn't exist" not in message
            ):
                raise
            rows = [
                dict(row)
                for row in execute(
                    connection,
                    "SELECT va.appointment_key, o.name AS scope_name, va.status, "
                    "va.starts_at, va.ends_at "
                    "FROM member_identities mi "
                    "JOIN person_profiles pp ON pp.id=mi.person_id "
                    "JOIN volunteer_appointments va ON va.person_id=mi.person_id "
                    "LEFT JOIN org_units o ON o.id=va.org_unit_id "
                    "WHERE mi.member_id=? AND mi.status='ACTIVE' AND pp.status='ACTIVE' "
                    "ORDER BY va.starts_at DESC, va.id DESC",
                    (member_id,),
                ).fetchall()
            ]

    appointments = []
    for row in rows:
        fallback = _fallback_position(row["appointment_key"])
        appointments.append(
            {
                "position_name": row.get("position_name")
                or (fallback or {}).get("position_name")
                or "志工",
                "scope_name": row.get("scope_name") or "服务范围暂未记录",
                "status_name": VOLUNTEER_STATUS_NAMES.get(
                    str(row.get("status") or "").upper(), "状态待确认"
                ),
                "starts_at": _public_appointment_timestamp(row.get("starts_at")),
                "ends_at": _public_appointment_timestamp(row.get("ends_at")),
            }
        )
    return {"appointments": appointments}


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
        fallback_rows = []
        for key in POSITION_DEFAULTS:
            if active_only and key in CURRENT_VOLUNTEER_HIDDEN_POSITION_KEYS:
                continue
            position = _fallback_position(key)
            if position is not None:
                fallback_rows.append(position)
        return fallback_rows
    by_key: dict[str, list[str]] = {}
    for capability in capabilities:
        by_key.setdefault(capability["position_key"], []).append(
            capability["capability_key"]
        )
    if active_only:
        rows = [
            row
            for row in rows
            if row["position_key"] not in CURRENT_VOLUNTEER_HIDDEN_POSITION_KEYS
        ]
    return [_row_to_position(row, by_key.get(row["position_key"], [])) for row in rows]


def list_volunteer_positions(*, active_only: bool = True) -> list[dict[str, Any]]:
    """Return operator-facing positions, hiding non-actionable umbrella labels.

    ``active_only=False`` is intentionally retained for identity and historical
    reads, so hiding a selector option does not delete or rewrite old records.
    """

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
        class_like = level == "CLASS" and unit_type in {"CLASS", "SPECIAL_COHORT"}
        if not (class_like or unit_type == level):
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


def _member_person(
    connection,
    member_id: int,
    *,
    actor_user_id: int,
    source: str,
    audit_purpose: str = "学员管理中明确添加正式志工任职",
) -> str:
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
        purpose=audit_purpose,
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
    ends_at: str | None,
    source_reference: str,
    confirmation_note: str,
) -> int:
    target = validate_position_target(
        connection,
        position_key=position_key,
        org_unit_id=org_unit_id,
        scope_type=scope_type,
    )
    if ends_at is None:
        overlapping = execute(
            connection,
            "SELECT id FROM volunteer_appointments WHERE person_id=? "
            "AND appointment_key=? AND org_unit_id=? "
            "AND status IN ('PLANNED','ACTIVE','SUSPENDED') "
            "AND (ends_at IS NULL OR ends_at>?) LIMIT 1",
            (person_id, position_key, org_unit_id, starts_at),
        ).fetchone()
    else:
        overlapping = execute(
            connection,
            "SELECT id FROM volunteer_appointments WHERE person_id=? "
            "AND appointment_key=? AND org_unit_id=? "
            "AND status IN ('PLANNED','ACTIVE','SUSPENDED') "
            "AND starts_at<? AND (ends_at IS NULL OR ends_at>?) LIMIT 1",
            (person_id, position_key, org_unit_id, ends_at, starts_at),
        ).fetchone()
    if overlapping:
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


def _parse_term(
    starts_at: str | None, ends_at: str | None
) -> tuple[datetime, datetime | None]:
    start_text = (starts_at or "").strip()
    end_text = (ends_at or "").strip()
    try:
        # MySQL DATETIME(0) rounds fractional seconds on INSERT. A default
        # "effective now" at .9s could otherwise be stored in the NEXT second,
        # briefly denying the newly assigned volunteer's capability.
        start = datetime.fromisoformat(start_text) if start_text else datetime.now(UTC).replace(microsecond=0)
    except (TypeError, ValueError) as exc:
        raise ValueError("任职开始时间格式无效") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    else:
        start = start.astimezone(UTC)
    if not end_text:
        return start, None
    try:
        end = datetime.fromisoformat(end_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("任职结束时间格式无效") from exc
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    else:
        end = end.astimezone(UTC)
    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    if end <= datetime.now(UTC):
        raise ValueError("结束时间必须晚于当前时间")
    return start, end


def _member_current_scope(
    connection, member_id: int, position: dict[str, Any]
) -> dict[str, Any]:
    """Derive a current service target only from formal member relations."""

    now = _db_timestamp(connection)
    level = position["scope_level"]
    if level == "GROUP":
        row = execute(
            connection,
            "SELECT r.org_unit_id, o.name AS org_name, o.unit_type "
            "FROM member_org_relations r JOIN org_units o ON o.id=r.org_unit_id "
            "WHERE r.member_id=? AND r.relation_type='STUDY_GROUP' "
            "AND o.unit_type='GROUP' AND o.is_active=1 "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) "
            "ORDER BY r.is_primary DESC, r.id DESC LIMIT 1",
            (member_id, now, now),
        ).fetchone()
        if not row:
            raise ValueError(
                f"无法设置“{position['position_name']}”：该学长尚未关联正式小组，请先维护班级/小组。"
            )
    elif level == "CLASS":
        row = execute(
            connection,
            "SELECT r.org_unit_id, o.name AS org_name, o.unit_type "
            "FROM member_org_relations r JOIN org_units o ON o.id=r.org_unit_id "
            "WHERE r.member_id=? AND r.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
            "AND o.unit_type IN ('CLASS','SPECIAL_COHORT') AND o.is_active=1 "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) "
            "ORDER BY r.is_primary DESC, r.id DESC LIMIT 1",
            (member_id, now, now),
        ).fetchone()
        if not row:
            raise ValueError(
                f"无法设置“{position['position_name']}”：该学长尚未关联正式班级，请先维护班级/小组。"
            )
    else:
        row = execute(
            connection,
            "SELECT r.org_unit_id, o.name AS org_name, o.unit_type "
            "FROM member_org_relations r JOIN org_units o ON o.id=r.org_unit_id "
            "WHERE r.member_id=? AND r.relation_type='PRIMARY_REGION' "
            "AND o.unit_type='REGIONAL_CENTER' AND o.is_active=1 "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?) "
            "ORDER BY r.is_primary DESC, r.id DESC LIMIT 1",
            (member_id, now, now),
        ).fetchone()
        if not row:
            row = execute(
                connection,
                "SELECT m.org_unit_id, o.name AS org_name, o.unit_type "
                "FROM members m JOIN org_units o ON o.id=m.org_unit_id "
                "WHERE m.id=? AND o.unit_type='REGIONAL_CENTER' AND o.is_active=1",
                (member_id,),
            ).fetchone()
        if not row:
            raise ValueError(
                f"无法设置“{position['position_name']}”：该学长尚未关联正式分中心，请先维护分中心。"
            )
    target = validate_position_target(
        connection,
        position_key=position["position_key"],
        org_unit_id=row["org_unit_id"],
        scope_type="UNIT",
    )
    return {
        "org_unit_id": row["org_unit_id"],
        "org_name": row["org_name"],
        "unit_type": row["unit_type"],
        "scope_type": target["scope_type"],
    }


def _end_current_appointment(
    connection,
    appointment: dict[str, Any],
    *,
    actor_user_id: int,
    member_id: int,
    reason: str,
) -> str:
    now = _db_timestamp(connection)
    execute(
        connection,
        "UPDATE volunteer_appointments SET status='ENDED', ends_at=?, updated_at=? WHERE id=?",
        (now, now, appointment["id"]),
    )
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="identity.volunteer_appointment.status_change",
        resource_type="volunteer_appointment",
        resource_id=str(appointment["id"]),
        org_unit_id=appointment.get("org_unit_id"),
        purpose=reason,
        before={
            "status": appointment.get("status"),
            "ends_at": appointment.get("ends_at"),
        },
        after={
            "status": "ENDED",
            "ends_at": now,
            "member_id": member_id,
            "position_key": appointment.get("appointment_key"),
        },
    )
    return now


def set_member_current_volunteer_position(
    actor_user_id: int,
    member_id: int,
    position_key: str | None,
    *,
    connection=None,
) -> dict[str, Any]:
    """Set the single operator-managed current volunteer position.

    The appointment history remains the source of truth.  A null position
    explicitly ends the operator-managed current appointment and never creates
    an ordinary-learner placeholder or converts the legacy free-text field.
    """

    _write_gate(write=True)
    normalized_key = (position_key or "").strip() or None
    context = nullcontext(connection) if connection is not None else transaction()
    with context as current_connection:
        member_row = execute(
            current_connection,
            "SELECT id, name, org_unit_id, status FROM members WHERE id=?",
            (member_id,),
        ).fetchone()
        if not member_row:
            raise ValueError("学员不存在")
        member = dict(member_row)
        if member["status"] != "ACTIVE":
            raise ValueError("仅可为在册学员维护当前志工岗位")
        _ensure_member_scope(actor_user_id, member)
        appointments = _effective_current_appointments(current_connection, member_id)
        if len(appointments) > 1:
            raise ValueError("当前存在多个有效志工岗位，请先人工确认主要岗位")
        existing = appointments[0] if appointments else None
        if existing and existing.get("source_reference") not in _MEMBER_ADMIN_MANAGED_SOURCES:
            raise ValueError("当前志工岗位来自其他来源，请先人工核对后再维护")

        before = (
            dict(existing["current_summary"])
            if existing
            else _empty_current_volunteer_position(member_id)
        )
        if not normalized_key:
            if existing:
                _end_current_appointment(
                    current_connection,
                    existing,
                    actor_user_id=actor_user_id,
                    member_id=member_id,
                    reason="学员管理明确取消当前志工岗位",
                )
                cleared = _empty_current_volunteer_position(member_id)
                cleared["source"] = MEMBER_ADMIN_CURRENT_SERVICE_SOURCE
                write_audit(
                    current_connection,
                    actor_user_id=actor_user_id,
                    action="members.current_volunteer_position.update",
                    resource_type="member",
                    resource_id=str(member_id),
                    org_unit_id=member["org_unit_id"],
                    purpose="学员管理维护当前志工岗位",
                    before=before,
                    after=cleared,
                )
            return _empty_current_volunteer_position(member_id)

        position = get_volunteer_position(normalized_key, current_connection)
        if not position or not position["is_active"]:
            raise ValueError("未知或已停用的志工岗位")
        target = _member_current_scope(current_connection, member_id, position)
        if (
            existing
            and existing["appointment_key"] == normalized_key
            and existing["org_unit_id"] == target["org_unit_id"]
            and existing.get("scope_type") == target["scope_type"]
            and existing.get("source_reference") == MEMBER_ADMIN_CURRENT_SERVICE_SOURCE
        ):
            result = dict(existing["current_summary"])
            result["member_id"] = member_id
            result["needs_manual_review"] = False
            result["review_message"] = None
            return result

        person_id = (
            existing["person_id"]
            if existing
            else _member_person(
                current_connection,
                member_id,
                actor_user_id=actor_user_id,
                source=MEMBER_ADMIN_CURRENT_SERVICE_SOURCE,
            )
        )
        if existing:
            _end_current_appointment(
                current_connection,
                existing,
                actor_user_id=actor_user_id,
                member_id=member_id,
                reason="学员管理更新当前志工岗位",
            )
        starts_at = _db_timestamp(current_connection)
        appointment_id = _insert_appointment(
            current_connection,
            person_id=person_id,
            member_id=member_id,
            actor_user_id=actor_user_id,
            position_key=normalized_key,
            org_unit_id=target["org_unit_id"],
            scope_type=target["scope_type"],
            starts_at=starts_at,
            ends_at=None,
            source_reference=MEMBER_ADMIN_CURRENT_SERVICE_SOURCE,
            confirmation_note="学员管理维护当前志工岗位",
        )
        after = {
            "appointment_id": appointment_id,
            "member_id": member_id,
            "is_volunteer": True,
            "position_key": normalized_key,
            "position_name": position["position_name"],
            "scope_level": position["scope_level"],
            "scope_type": target["scope_type"],
            "scope_org_unit_id": target["org_unit_id"],
            "org_unit_id": target["org_unit_id"],
            "scope_name": target["org_name"],
            "capabilities": list(position.get("capabilities", [])),
            "capability_names": list(position.get("capability_names", [])),
            "source_reference": MEMBER_ADMIN_CURRENT_SERVICE_SOURCE,
            "source": MEMBER_ADMIN_CURRENT_SERVICE_SOURCE,
            "starts_at": starts_at,
            "ends_at": None,
            "status": "ACTIVE",
            "needs_manual_review": False,
            "review_message": None,
        }
        write_audit(
            current_connection,
            actor_user_id=actor_user_id,
            action="members.current_volunteer_position.update",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=target["org_unit_id"],
            purpose="学员管理维护当前志工岗位",
            before=before,
            after=after,
        )
        return after


def sync_member_current_volunteer_scope(
    connection, actor_user_id: int, member_id: int
) -> dict[str, Any] | None:
    """Follow a current-service appointment when formal org relations move."""

    settings = get_settings()
    if (
        not settings.identity_authorization_enabled
        or not settings.identity_admin_writes_enabled
        or (settings.is_production and not settings.allow_production_mutations)
    ):
        return None
    appointments = _effective_current_appointments(connection, member_id)
    if len(appointments) != 1:
        if len(appointments) > 1:
            raise ValueError("当前存在多个有效志工岗位，请先人工确认主要岗位")
        return None
    existing = appointments[0]
    if existing.get("source_reference") not in _MEMBER_ADMIN_MANAGED_SOURCES:
        return _position_summary(existing)
    position = get_volunteer_position(existing["appointment_key"], connection)
    if not position or not position["is_active"]:
        return _position_summary(existing)
    target = _member_current_scope(connection, member_id, position)
    before = dict(existing["current_summary"])
    if (
        existing["org_unit_id"] == target["org_unit_id"]
        and existing.get("scope_type") == target["scope_type"]
    ):
        return before
    now = _db_timestamp(connection)
    execute(
        connection,
        "UPDATE volunteer_appointments SET org_unit_id=?, scope_type=?, updated_at=? WHERE id=?",
        (target["org_unit_id"], target["scope_type"], now, existing["id"]),
    )
    after = {
        **before,
        "scope_org_unit_id": target["org_unit_id"],
        "org_unit_id": target["org_unit_id"],
        "scope_name": target["org_name"],
        "scope_type": target["scope_type"],
        "source": existing.get("source_reference"),
    }
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="members.current_volunteer_scope.sync",
        resource_type="volunteer_appointment",
        resource_id=str(existing["id"]),
        org_unit_id=target["org_unit_id"],
        purpose="正式班级/小组/分中心关系变更后自动同步当前志工服务范围",
        before=before,
        after=after,
    )
    return after


def create_member_volunteer_appointment(
    actor_user_id: int,
    member_id: int,
    *,
    position_key: str,
    org_unit_id: str,
    starts_at: str | None = None,
    ends_at: str | None = None,
    source_reference: str | None = None,
    confirmation_note: str | None = None,
) -> dict[str, Any]:
    """Add a member-management appointment with operator-friendly defaults.

    ``source_reference`` remains an accepted keyword for callers compiled
    against the M2 API, but the member-management entry point deliberately
    replaces it with a stable machine source. This prevents a technical audit
    field from becoming a required operator input while preserving an
    auditable actor and timestamp.
    """

    _write_gate(write=True)
    start_value, end_value = _parse_term(starts_at, ends_at)
    normalized_starts_at = start_value.isoformat()
    normalized_ends_at = end_value.isoformat() if end_value else None
    source = MEMBER_ADMIN_MANUAL_SOURCE
    note = (confirmation_note or "").strip() or MEMBER_APPOINTMENT_DEFAULT_PURPOSE
    if len(note) > 1000:
        raise ValueError("备注不能超过 1000 个字符")
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
            starts_at=normalized_starts_at,
            ends_at=normalized_ends_at,
            source_reference=source,
            confirmation_note=note,
        )
    return {
        "id": appointment_id,
        "member_id": member_id,
        "person_id": person_id,
        "position_key": position_key.strip(),
        "org_unit_id": org_unit_id.strip(),
        "starts_at": normalized_starts_at,
        "ends_at": normalized_ends_at,
        "source_reference": source,
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
    reason: str | None = None,
) -> dict[str, Any]:
    _write_gate(write=True)
    normalized_status = status.upper().strip()
    if normalized_status not in {"SUSPENDED", "ENDED", "REVOKED"}:
        raise ValueError("志工任职状态只能是 SUSPENDED、ENDED 或 REVOKED")
    reason = (reason or "").strip() or "运营人员在学员管理中确认结束该志工任职"
    if len(reason) > 1000:
        raise ValueError("任职状态变更备注不能超过 1000 个字符")
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
        if normalized_status in {"ENDED", "REVOKED"}:
            execute(
                connection,
                "UPDATE volunteer_appointments SET status=?, ends_at=?, updated_at=? WHERE id=?",
                (normalized_status, now, now, appointment_id),
            )
        else:
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
                "ends_at": now if normalized_status in {"ENDED", "REVOKED"} else appointment.get("ends_at"),
            },
        )
    return {"id": appointment_id, "member_id": member_id, "status": normalized_status}
