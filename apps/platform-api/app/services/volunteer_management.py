"""Volunteer 2.0 service organizations and current appointment management.

The service unit is deliberately distinct from the formal learning
organization: its parent path describes volunteer governance, while its
service target describes the formal organization receiving service.  This
module never derives either relationship from an organization name.
"""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from collections import Counter
from contextlib import nullcontext
from typing import Any
from uuid import uuid4

from app.core.settings import get_settings
from app.db import execute, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids
from app.services.volunteer_positions import (
    _db_timestamp,
    _ensure_member_scope,
    _member_person,
    get_volunteer_position,
    list_volunteer_positions,
    validate_position_target,
)


SYSTEM_TYPES = frozenset({"CLASS_TEAM", "GOVERNANCE", "COMMITTEE_LINE", "ACTIVITY"})
LINE_TYPES = frozenset({"LEARNING", "OPERATIONS", "DEVELOPMENT", "GENERAL", "SUPERVISION"})
CURRENT_APPOINTMENT_STATUSES = frozenset({"ACTIVE", "SUSPENDED"})
APPOINTMENT_STATUSES = frozenset({"ACTIVE", "SUSPENDED", "ENDED", "REVOKED"})
SYSTEM_TYPE_NAMES = {
    "CLASS_TEAM": "班级志工团队",
    "GOVERNANCE": "治理组织",
    "COMMITTEE_LINE": "三大委纵向组织",
    "ACTIVITY": "专项活动组织",
}
LINE_TYPE_NAMES = {
    "LEARNING": "学习践行线",
    "OPERATIONS": "运营管理线",
    "DEVELOPMENT": "发展建设线",
    "GENERAL": "综合",
    "SUPERVISION": "监事线",
}
STATUS_NAMES = {
    "ACTIVE": "当前有效",
    "SUSPENDED": "已暂停",
    "ENDED": "已结束",
    "REVOKED": "已撤销",
}
VOLUNTEER2_MANUAL_SOURCE = "VOLUNTEER2_MANUAL"
VOLUNTEER2_RECOMMENDED_SOURCE = "VOLUNTEER2_RECOMMENDED"


def _feature_gate(*, write: bool = False) -> None:
    settings = get_settings()
    if not settings.identity_authorization_enabled:
        raise PermissionError("身份与任职功能尚未启用")
    if write and not settings.identity_admin_writes_enabled:
        raise PermissionError("身份与任职写入尚未获准")
    if write and settings.is_production and not settings.allow_production_mutations:
        raise PermissionError("生产身份与任职写入未获批准")


def _normal_enum(value: str, allowed: frozenset[str], field_name: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in allowed:
        raise ValueError(f"{field_name}无效")
    return normalized


def _schema_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "no such table" in text or "doesn't exist" in text or "unknown column" in text


def _require_service_scope(actor_user_id: int, *org_ids: str | None) -> None:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is None:
        return
    if any(org_id and org_id not in allowed for org_id in org_ids):
        raise PermissionError("志工服务组织不在当前组织授权范围内")


def _service_unit_row(connection, service_unit_id: str, *, active_only: bool = False) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT vsu.id, vsu.unit_code, vsu.name, vsu.system_type, vsu.line_type, "
        "vsu.parent_id, vsu.home_shuku_org_unit_id, vsu.service_target_org_unit_id, "
        "vsu.is_active, vsu.sort_order, vsu.created_at, vsu.updated_at, "
        "parent.name AS parent_name, home.name AS home_shuku_name, "
        "target.name AS service_target_name, target.unit_type AS service_target_unit_type "
        "FROM volunteer_service_units vsu "
        "LEFT JOIN volunteer_service_units parent ON parent.id=vsu.parent_id "
        "LEFT JOIN org_units home ON home.id=vsu.home_shuku_org_unit_id "
        "JOIN org_units target ON target.id=vsu.service_target_org_unit_id "
        "WHERE vsu.id=?" + (" AND vsu.is_active=1" if active_only else ""),
        (service_unit_id,),
    ).fetchone()
    if not row:
        raise ValueError("志工服务组织不存在或已停用")
    return dict(row)


def _public_service_unit(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "unit_code": row["unit_code"],
        "name": row["name"],
        "system_type": row["system_type"],
        "system_name": SYSTEM_TYPE_NAMES.get(row["system_type"], row["system_type"]),
        "line_type": row["line_type"],
        "line_name": LINE_TYPE_NAMES.get(row["line_type"], row["line_type"]),
        "parent_id": row.get("parent_id"),
        "parent_name": row.get("parent_name"),
        "home_shuku_org_unit_id": row.get("home_shuku_org_unit_id"),
        "home_shuku_name": row.get("home_shuku_name"),
        "service_target_org_unit_id": row["service_target_org_unit_id"],
        "service_target_name": row.get("service_target_name"),
        "service_target_unit_type": row.get("service_target_unit_type"),
        "is_active": bool(row.get("is_active")),
        "sort_order": int(row.get("sort_order") or 0),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_service_units(
    actor_user_id: int,
    *,
    system_type: str | None = None,
    line_type: str | None = None,
    parent_id: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    _feature_gate()
    where = ["1=1"]
    params: list[Any] = []
    if active_only:
        where.append("vsu.is_active=1")
    if system_type:
        where.append("vsu.system_type=?")
        params.append(_normal_enum(system_type, SYSTEM_TYPES, "志工体系"))
    if line_type:
        where.append("vsu.line_type=?")
        params.append(_normal_enum(line_type, LINE_TYPES, "志工线"))
    if parent_id:
        where.append("vsu.parent_id=?")
        params.append(parent_id)
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None:
        if not allowed:
            return []
        placeholders = ",".join("?" for _ in allowed)
        where.append(
            "(vsu.service_target_org_unit_id IN (" + placeholders + ") "
            "OR vsu.home_shuku_org_unit_id IN (" + placeholders + "))"
        )
        params.extend(sorted(allowed))
        params.extend(sorted(allowed))
    with transaction() as connection:
        try:
            rows = [
                _public_service_unit(dict(row))
                for row in execute(
                    connection,
                    "SELECT vsu.id, vsu.unit_code, vsu.name, vsu.system_type, vsu.line_type, "
                    "vsu.parent_id, vsu.home_shuku_org_unit_id, vsu.service_target_org_unit_id, "
                    "vsu.is_active, vsu.sort_order, vsu.created_at, vsu.updated_at, "
                    "parent.name AS parent_name, home.name AS home_shuku_name, "
                    "target.name AS service_target_name, target.unit_type AS service_target_unit_type "
                    "FROM volunteer_service_units vsu "
                    "LEFT JOIN volunteer_service_units parent ON parent.id=vsu.parent_id "
                    "LEFT JOIN org_units home ON home.id=vsu.home_shuku_org_unit_id "
                    "JOIN org_units target ON target.id=vsu.service_target_org_unit_id "
                    "WHERE " + " AND ".join(where) + " "
                    "ORDER BY vsu.sort_order, vsu.name, vsu.unit_code",
                    tuple(params),
                ).fetchall()
            ]
        except Exception as exc:
            if _schema_error(exc):
                raise ValueError("志工服务组织尚未完成数据库迁移") from exc
            raise
    return rows


def _validate_unit_parent(connection, *, parent_id: str | None, service_unit_id: str | None = None) -> dict[str, Any] | None:
    if not parent_id:
        return None
    if parent_id == service_unit_id:
        raise ValueError("志工服务组织不能将自身设为上级")
    parent = _service_unit_row(connection, parent_id, active_only=True)
    cursor = parent
    visited = {parent_id}
    while cursor.get("parent_id"):
        ancestor_id = cursor["parent_id"]
        if ancestor_id == service_unit_id or ancestor_id in visited:
            raise ValueError("志工服务组织层级存在循环")
        visited.add(ancestor_id)
        cursor = _service_unit_row(connection, ancestor_id)
    return parent


def create_service_unit(
    actor_user_id: int,
    *,
    unit_code: str,
    name: str,
    system_type: str,
    line_type: str,
    parent_id: str | None,
    home_shuku_org_unit_id: str | None,
    service_target_org_unit_id: str,
    sort_order: int = 0,
) -> dict[str, Any]:
    _feature_gate(write=True)
    code = unit_code.strip()
    display_name = name.strip()
    if len(code) < 2 or len(code) > 128:
        raise ValueError("志工服务组织编码长度应为 2 至 128 个字符")
    if not display_name:
        raise ValueError("志工服务组织名称不能为空")
    system = _normal_enum(system_type, SYSTEM_TYPES, "志工体系")
    line = _normal_enum(line_type, LINE_TYPES, "志工线")
    _require_service_scope(actor_user_id, home_shuku_org_unit_id, service_target_org_unit_id)
    with transaction() as connection:
        target = execute(
            connection,
            "SELECT id, is_active FROM org_units WHERE id=?",
            (service_target_org_unit_id,),
        ).fetchone()
        if not target or not target["is_active"]:
            raise ValueError("服务对象正式组织不存在或已停用")
        if home_shuku_org_unit_id:
            home = execute(
                connection,
                "SELECT id, unit_type, is_active FROM org_units WHERE id=?",
                (home_shuku_org_unit_id,),
            ).fetchone()
            if not home or not home["is_active"] or str(home["unit_type"]).upper() != "ROOT":
                raise ValueError("归属塾必须是有效的塾级正式组织")
        parent = _validate_unit_parent(connection, parent_id=parent_id)
        if parent and parent["home_shuku_org_unit_id"] != home_shuku_org_unit_id:
            raise ValueError("下级志工服务组织必须归属与上级相同的塾")
        duplicate = execute(
            connection,
            "SELECT id FROM volunteer_service_units WHERE unit_code=?",
            (code,),
        ).fetchone()
        if duplicate:
            raise ValueError("志工服务组织编码已存在")
        now = _db_timestamp(connection)
        service_unit_id = f"vsu-{uuid4()}"
        execute(
            connection,
            "INSERT INTO volunteer_service_units "
            "(id, unit_code, name, system_type, line_type, parent_id, home_shuku_org_unit_id, "
            "service_target_org_unit_id, is_active, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (
                service_unit_id, code, display_name, system, line, parent_id,
                home_shuku_org_unit_id, service_target_org_unit_id, int(sort_order), now, now,
            ),
        )
        row = _service_unit_row(connection, service_unit_id)
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="volunteer.service_unit.create",
            resource_type="volunteer_service_unit",
            resource_id=service_unit_id,
            org_unit_id=service_target_org_unit_id,
            after=_public_service_unit(row),
        )
        return _public_service_unit(row)


def _position_for_service_unit(connection, service_unit: dict[str, Any], position_key: str) -> dict[str, Any]:
    position = get_volunteer_position(position_key, connection)
    if not position or not position.get("is_active") or not position.get("is_selectable"):
        raise ValueError("未知、已停用或不可新增的志工岗位")
    if position["system_type"] != service_unit["system_type"]:
        raise ValueError("岗位与志工服务组织体系不匹配")
    if position["line_type"] != service_unit["line_type"]:
        raise ValueError("岗位与志工服务组织线别不匹配")
    validate_position_target(
        connection,
        position_key=position_key,
        org_unit_id=service_unit["service_target_org_unit_id"],
        scope_type="UNIT",
    )
    return position


def list_position_options(actor_user_id: int, *, service_unit_id: str) -> dict[str, Any]:
    _feature_gate()
    with transaction() as connection:
        service_unit = _service_unit_row(connection, service_unit_id, active_only=True)
        _require_service_scope(
            actor_user_id,
            service_unit["home_shuku_org_unit_id"],
            service_unit["service_target_org_unit_id"],
        )
        positions: list[dict[str, Any]] = []
        for position in list_volunteer_positions(active_only=True):
            if (
                position["system_type"] != service_unit["system_type"]
                or position["line_type"] != service_unit["line_type"]
            ):
                continue
            try:
                _position_for_service_unit(connection, service_unit, position["position_key"])
            except ValueError:
                continue
            positions.append(position)
    return {"service_unit": _public_service_unit(service_unit), "positions": positions}


def member_editor_catalog(actor_user_id: int) -> dict[str, Any]:
    """Return one business-ready catalog for the learner edit page.

    The response keeps Volunteer 2.0 service-unit identity for writes, while
    allowing the UI to expose only business labels and target organizations.
    Activity units are an advanced workflow and intentionally stay out of the
    ordinary learner editor.
    """

    positions = [
        position
        for position in list_volunteer_positions(active_only=True)
        if position.get("is_selectable")
        and position.get("system_type") != "ACTIVITY"
    ]
    units: list[dict[str, Any]] = []
    for service_unit in list_service_units(actor_user_id, active_only=True):
        if service_unit["system_type"] == "ACTIVITY":
            continue
        options = list_position_options(
            actor_user_id, service_unit_id=service_unit["id"]
        )["positions"]
        if options:
            units.append({**service_unit, "positions": options})
    return {"positions": positions, "service_units": units}


def _formal_org_row(connection, org_unit_id: str) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT id, name, unit_type, parent_id, is_active FROM org_units WHERE id=?",
        (org_unit_id,),
    ).fetchone()
    if not row or not row["is_active"]:
        raise ValueError("服务对象正式组织不存在或已停用")
    return dict(row)


def _formal_org_ancestors(connection, org_unit_id: str) -> list[dict[str, Any]]:
    ancestors: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = _formal_org_row(connection, org_unit_id)
    while current and current["id"] not in seen:
        seen.add(current["id"])
        ancestors.append(current)
        parent_id = current.get("parent_id")
        if not parent_id:
            break
        current = _formal_org_row(connection, str(parent_id))
    return ancestors


def _auto_service_unit_name(
    target: dict[str, Any], *, system_type: str, line_type: str
) -> str:
    if system_type == "COMMITTEE_LINE":
        suffix = {
            "LEARNING": "学习践行委",
            "OPERATIONS": "运营管理委",
            "DEVELOPMENT": "发展建设委",
        }.get(line_type, "志工委员会")
    elif system_type == "GOVERNANCE":
        suffix = "志工治理组织"
    else:
        suffix = "班组委"
    return f"{target['name']} · {suffix}"[:255]


def _ensure_business_service_unit(
    connection,
    *,
    actor_user_id: int,
    service_target_org_unit_id: str,
    position: dict[str, Any],
    validate_target: bool = True,
    check_scope: bool = True,
) -> dict[str, Any]:
    """Resolve or create the technical service unit behind the simple member UI.

    The target is always a formal organization selected by the operator.  The
    position profile decides system/line and the formal parent path decides a
    class-team or committee parent; no organization name is interpreted.
    """

    target = _formal_org_row(connection, service_target_org_unit_id)
    if check_scope:
        _require_service_scope(actor_user_id, service_target_org_unit_id)
    if validate_target:
        validate_position_target(
            connection,
            position_key=position["position_key"],
            org_unit_id=service_target_org_unit_id,
            scope_type="UNIT",
        )
    system_type = position["system_type"]
    line_type = position["line_type"]
    existing = execute(
        connection,
        "SELECT id FROM volunteer_service_units "
        "WHERE service_target_org_unit_id=? AND system_type=? AND line_type=? AND is_active=1 "
        "ORDER BY sort_order, created_at, id LIMIT 1",
        (service_target_org_unit_id, system_type, line_type),
    ).fetchone()
    if existing:
        return _service_unit_row(connection, existing["id"], active_only=True)

    ancestors = _formal_org_ancestors(connection, service_target_org_unit_id)
    home = next(
        (item for item in ancestors if str(item["unit_type"]).upper() == "ROOT"),
        None,
    )
    parent_service_unit_id: str | None = None
    target_type = str(target["unit_type"] or "").upper()
    parent_target: dict[str, Any] | None = None
    if system_type == "CLASS_TEAM" and target_type == "GROUP":
        parent_target = next(
            (
                item
                for item in ancestors[1:]
                if str(item["unit_type"]).upper() in {"CLASS", "SPECIAL_COHORT"}
            ),
            None,
        )
    elif system_type == "COMMITTEE_LINE" and target_type in {
        "CLASS",
        "SPECIAL_COHORT",
    }:
        parent_target = next(
            (
                item
                for item in ancestors[1:]
                if str(item["unit_type"]).upper() == "REGIONAL_CENTER"
            ),
            None,
        )
    if parent_target:
        parent_service_unit = _ensure_business_service_unit(
            connection,
            actor_user_id=actor_user_id,
            service_target_org_unit_id=parent_target["id"],
            position=position,
            validate_target=False,
            check_scope=False,
        )
        parent_service_unit_id = parent_service_unit["id"]

    fingerprint = sha256(
        f"{system_type}:{line_type}:{service_target_org_unit_id}".encode("utf-8")
    ).hexdigest()[:24]
    unit_code = f"AUTO_{system_type}_{line_type}_{fingerprint}"
    duplicate_code = execute(
        connection,
        "SELECT id, is_active FROM volunteer_service_units WHERE unit_code=?",
        (unit_code,),
    ).fetchone()
    if duplicate_code and duplicate_code["is_active"]:
        return _service_unit_row(connection, duplicate_code["id"], active_only=True)
    if duplicate_code:
        unit_code = f"{unit_code}_{uuid4().hex[:8]}"

    now = _db_timestamp(connection)
    service_unit_id = f"vsu-{uuid4()}"
    execute(
        connection,
        "INSERT INTO volunteer_service_units "
        "(id, unit_code, name, system_type, line_type, parent_id, home_shuku_org_unit_id, "
        "service_target_org_unit_id, is_active, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
        (
            service_unit_id,
            unit_code,
            _auto_service_unit_name(
                target, system_type=system_type, line_type=line_type
            ),
            system_type,
            line_type,
            parent_service_unit_id,
            home["id"] if home else None,
            service_target_org_unit_id,
            now,
            now,
        ),
    )
    created = _service_unit_row(connection, service_unit_id)
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="volunteer.service_unit.auto_create",
        resource_type="volunteer_service_unit",
        resource_id=service_unit_id,
        org_unit_id=service_target_org_unit_id,
        purpose="学员管理页按正式服务对象建立志工服务组织",
        after=_public_service_unit(created),
    )
    return created


def _appointment_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "member_id": int(row["member_id"]),
        "member_name": row.get("member_name"),
        "member_status": row.get("member_status"),
        "home_shuku_name": row.get("home_shuku_name"),
        "system_type": row.get("system_type"),
        "system_name": SYSTEM_TYPE_NAMES.get(row.get("system_type"), row.get("system_type")),
        "line_type": row.get("line_type"),
        "line_name": LINE_TYPE_NAMES.get(row.get("line_type"), row.get("line_type")),
        "service_unit_id": row.get("volunteer_service_unit_id"),
        "service_unit_name": row.get("service_unit_name"),
        "position_key": row.get("appointment_key"),
        "position_name": row.get("position_name") or row.get("appointment_key"),
        "service_target_org_unit_id": row.get("service_target_org_unit_id") or row.get("org_unit_id"),
        "service_target_name": row.get("service_target_name") or row.get("legacy_target_name"),
        "scope_type": row.get("scope_type"),
        "capabilities": list(row.get("capabilities") or []),
        "status": row.get("status"),
        "status_name": STATUS_NAMES.get(row.get("status"), row.get("status")),
        "created_at": row.get("created_at"),
        "ended_at": row.get("ends_at"),
        "source_reference": row.get("source_reference"),
    }


def _capabilities_by_position(connection) -> dict[str, list[str]]:
    rows = execute(
        connection,
        "SELECT position_key, capability_key FROM volunteer_position_capabilities",
    ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["position_key"], []).append(row["capability_key"])
    return result


def list_appointments(
    actor_user_id: int,
    *,
    member_id: int | None = None,
    system_type: str | None = None,
    line_type: str | None = None,
    service_unit_id: str | None = None,
    position_key: str | None = None,
    home_shuku_org_unit_id: str | None = None,
    service_target_org_unit_id: str | None = None,
    member_name: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    _feature_gate()
    where = ["1=1"]
    params: list[Any] = []
    filters = {
        "va.member_id": member_id,
        "vsu.system_type": _normal_enum(system_type, SYSTEM_TYPES, "志工体系") if system_type else None,
        "vsu.line_type": _normal_enum(line_type, LINE_TYPES, "志工线") if line_type else None,
        "va.volunteer_service_unit_id": service_unit_id,
        "va.appointment_key": position_key,
        "vsu.home_shuku_org_unit_id": home_shuku_org_unit_id,
        "vsu.service_target_org_unit_id": service_target_org_unit_id,
        "va.status": str(status).upper() if status else None,
    }
    for column, value in filters.items():
        if value is not None:
            if column == "va.status" and value not in APPOINTMENT_STATUSES:
                raise ValueError("志工任职状态无效")
            where.append(f"{column}=?")
            params.append(value)
    if member_name and member_name.strip():
        where.append("m.name LIKE ?")
        params.append("%" + member_name.strip() + "%")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None:
        if not allowed:
            return []
        placeholders = ",".join("?" for _ in allowed)
        where.append("COALESCE(vsu.service_target_org_unit_id, va.org_unit_id) IN (" + placeholders + ")")
        params.extend(sorted(allowed))
    with transaction() as connection:
        try:
            rows = [
                dict(row)
                for row in execute(
                    connection,
                    "SELECT va.id, va.member_id, m.name AS member_name, m.status AS member_status, "
                    "va.appointment_key, c.position_name, va.org_unit_id, va.scope_type, va.status, "
                    "va.source_reference, va.created_at, va.ends_at, va.volunteer_service_unit_id, "
                    "vsu.name AS service_unit_name, vsu.system_type, vsu.line_type, "
                    "vsu.service_target_org_unit_id, home.name AS home_shuku_name, "
                    "target.name AS service_target_name, legacy_target.name AS legacy_target_name "
                    "FROM volunteer_appointments va "
                    "JOIN members m ON m.id=va.member_id "
                    "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
                    "LEFT JOIN volunteer_service_units vsu ON vsu.id=va.volunteer_service_unit_id "
                    "LEFT JOIN org_units home ON home.id=vsu.home_shuku_org_unit_id "
                    "LEFT JOIN org_units target ON target.id=vsu.service_target_org_unit_id "
                    "LEFT JOIN org_units legacy_target ON legacy_target.id=va.org_unit_id "
                    "WHERE " + " AND ".join(where) + " ORDER BY va.created_at DESC, va.id DESC",
                    tuple(params),
                ).fetchall()
            ]
            capabilities = _capabilities_by_position(connection)
        except Exception as exc:
            if _schema_error(exc):
                raise ValueError("志工服务组织尚未完成数据库迁移") from exc
            raise
    for row in rows:
        row["capabilities"] = capabilities.get(row["appointment_key"], [])
    return [_appointment_payload(row) for row in rows]


def _insert_v2_appointment(
    connection,
    *,
    actor_user_id: int,
    member_id: int,
    service_unit_id: str,
    position_key: str,
    source_reference: str,
    confirmation_note: str,
) -> tuple[int, dict[str, Any]]:
    member = execute(
        connection,
        "SELECT id, name, org_unit_id, status FROM members WHERE id=?",
        (member_id,),
    ).fetchone()
    if not member or member["status"] != "ACTIVE":
        raise ValueError("仅可为在册学长建立当前志工岗位")
    _ensure_member_scope(actor_user_id, dict(member))
    service_unit = _service_unit_row(connection, service_unit_id, active_only=True)
    # Appointment authority follows the actual service target.  The home shuku
    # is governance metadata and must not block a center-scoped operator whose
    # target class/group is already inside their IAM2 scope.
    _require_service_scope(actor_user_id, service_unit["service_target_org_unit_id"])
    position = _position_for_service_unit(connection, service_unit, position_key)
    existing = execute(
        connection,
        "SELECT id FROM volunteer_appointments WHERE member_id=? AND volunteer_service_unit_id=? "
        "AND appointment_key=? AND status IN ('ACTIVE','SUSPENDED') LIMIT 1",
        (member_id, service_unit_id, position_key),
    ).fetchone()
    if existing:
        raise ValueError("该学长在此志工服务组织中已拥有相同当前岗位")
    person_id = _member_person(
        connection,
        member_id,
        actor_user_id=actor_user_id,
        source=source_reference,
        audit_purpose="志工任职管理中确认关联在册学长与志工岗位",
    )
    now = _db_timestamp(connection)
    cursor = execute(
        connection,
        "INSERT INTO volunteer_appointments "
        "(person_id, member_id, volunteer_service_unit_id, appointment_key, org_unit_id, scope_type, "
        "starts_at, ends_at, status, source_reference, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'UNIT', ?, NULL, 'ACTIVE', ?, ?, ?)",
        (
            person_id, member_id, service_unit_id, position_key,
            service_unit["service_target_org_unit_id"], now, source_reference, now, now,
        ),
    )
    appointment_id = int(cursor.lastrowid)
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="volunteer.appointment.create",
        resource_type="volunteer_appointment",
        resource_id=str(appointment_id),
        org_unit_id=service_unit["service_target_org_unit_id"],
        purpose=confirmation_note,
        after={
            "member_id": member_id,
            "person_id": person_id,
            "service_unit_id": service_unit_id,
            "position_key": position_key,
            "position_name": position["position_name"],
            "service_target_org_unit_id": service_unit["service_target_org_unit_id"],
            "status": "ACTIVE",
            "source_reference": source_reference,
        },
    )
    return appointment_id, service_unit


def create_appointment(
    actor_user_id: int,
    *,
    member_id: int,
    service_unit_id: str | None = None,
    service_target_org_unit_id: str | None = None,
    position_key: str,
    confirmation_note: str,
) -> dict[str, Any]:
    _feature_gate(write=True)
    note = confirmation_note.strip() or "学员管理页添加志工任职"
    with transaction() as connection:
        resolved_service_unit_id = service_unit_id
        if service_unit_id:
            explicit_unit = _service_unit_row(
                connection, service_unit_id, active_only=True
            )
            if (
                service_target_org_unit_id
                and explicit_unit["service_target_org_unit_id"]
                != service_target_org_unit_id
            ):
                raise ValueError("服务组织与服务对象不一致")
        else:
            if not service_target_org_unit_id:
                raise ValueError("请选择服务组织")
            position = get_volunteer_position(position_key.strip(), connection)
            if not position or not position.get("is_active") or not position.get(
                "is_selectable"
            ):
                raise ValueError("未知、已停用或不可新增的志工岗位")
            generated_unit = _ensure_business_service_unit(
                connection,
                actor_user_id=actor_user_id,
                service_target_org_unit_id=service_target_org_unit_id,
                position=position,
            )
            resolved_service_unit_id = generated_unit["id"]
        assert resolved_service_unit_id
        appointment_id, service_unit = _insert_v2_appointment(
            connection,
            actor_user_id=actor_user_id,
            member_id=member_id,
            service_unit_id=resolved_service_unit_id,
            position_key=position_key.strip(),
            source_reference=VOLUNTEER2_MANUAL_SOURCE,
            confirmation_note=note,
        )
        rules = _recommendation_rows(
            connection,
            source_service_unit_id=resolved_service_unit_id,
            source_position_key=position_key.strip(),
            active_only=True,
        )
    return {
        "id": appointment_id,
        "service_unit": _public_service_unit(service_unit),
        "recommendations": rules,
        "automatic_followup_created": False,
    }


def change_appointment_status(
    actor_user_id: int,
    appointment_id: int,
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    _feature_gate(write=True)
    desired = _normal_enum(status, APPOINTMENT_STATUSES, "志工任职状态")
    note = reason.strip() or "学员管理页确认结束志工任职"
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT va.id, va.member_id, va.status, va.volunteer_service_unit_id, va.org_unit_id, "
            "COALESCE(vsu.service_target_org_unit_id, va.org_unit_id) AS target_org_unit_id "
            "FROM volunteer_appointments va "
            "LEFT JOIN volunteer_service_units vsu ON vsu.id=va.volunteer_service_unit_id "
            "WHERE va.id=?",
            (appointment_id,),
        ).fetchone()
        if not row:
            raise ValueError("志工任职记录不存在")
        _require_service_scope(actor_user_id, row["target_org_unit_id"])
        previous = str(row["status"]).upper()
        if previous in {"ENDED", "REVOKED"} and desired == "ACTIVE":
            raise ValueError("已结束或已撤销的历史任职不能自动恢复，请重新建立当前任职")
        now = _db_timestamp(connection)
        ends_at = now if desired in {"ENDED", "REVOKED"} else None
        execute(
            connection,
            "UPDATE volunteer_appointments SET status=?, ends_at=?, updated_at=? WHERE id=?",
            (desired, ends_at, now, appointment_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="volunteer.appointment.status_change",
            resource_type="volunteer_appointment",
            resource_id=str(appointment_id),
            org_unit_id=row["target_org_unit_id"],
            purpose=note,
            before={"status": previous},
            after={"status": desired, "ended_at": ends_at},
        )
    return {"id": appointment_id, "status": desired, "status_name": STATUS_NAMES[desired]}


def _recommendation_rows(
    connection,
    *,
    source_service_unit_id: str | None = None,
    source_position_key: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    where = ["1=1"]
    params: list[Any] = []
    if source_service_unit_id:
        where.append("r.source_service_unit_id=?")
        params.append(source_service_unit_id)
    if source_position_key:
        where.append("r.source_position_key=?")
        params.append(source_position_key)
    if active_only:
        where.append("r.is_active=1")
    rows = execute(
        connection,
        "SELECT r.id, r.source_service_unit_id, source.name AS source_service_unit_name, "
        "r.source_position_key, source_position.position_name AS source_position_name, "
        "r.target_service_unit_id, target.name AS target_service_unit_name, "
        "r.target_position_key, target_position.position_name AS target_position_name, "
        "r.is_active, r.created_at, r.updated_at "
        "FROM volunteer_appointment_recommendation_rules r "
        "JOIN volunteer_service_units source ON source.id=r.source_service_unit_id "
        "JOIN volunteer_service_units target ON target.id=r.target_service_unit_id "
        "JOIN volunteer_position_catalog source_position ON source_position.position_key=r.source_position_key "
        "JOIN volunteer_position_catalog target_position ON target_position.position_key=r.target_position_key "
        "WHERE " + " AND ".join(where) + " ORDER BY r.id DESC",
        tuple(params),
    ).fetchall()
    return [
        {
            **dict(row),
            "is_active": bool(row["is_active"]),
        }
        for row in rows
    ]


def list_recommendation_rules(
    actor_user_id: int,
    *,
    source_service_unit_id: str | None = None,
    source_position_key: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    _feature_gate()
    with transaction() as connection:
        rows = _recommendation_rows(
            connection,
            source_service_unit_id=source_service_unit_id,
            source_position_key=source_position_key,
            active_only=active_only,
        )
    allowed = accessible_org_ids(actor_user_id)
    if allowed is None:
        return rows
    visible_units = {item["id"] for item in list_service_units(actor_user_id, active_only=False)}
    return [
        row for row in rows
        if row["source_service_unit_id"] in visible_units
        and row["target_service_unit_id"] in visible_units
    ]


def create_recommendation_rule(
    actor_user_id: int,
    *,
    source_service_unit_id: str,
    source_position_key: str,
    target_service_unit_id: str,
    target_position_key: str,
) -> dict[str, Any]:
    _feature_gate(write=True)
    with transaction() as connection:
        source = _service_unit_row(connection, source_service_unit_id, active_only=True)
        target = _service_unit_row(connection, target_service_unit_id, active_only=True)
        _require_service_scope(
            actor_user_id,
            source["service_target_org_unit_id"],
            target["service_target_org_unit_id"],
        )
        _position_for_service_unit(connection, source, source_position_key)
        _position_for_service_unit(connection, target, target_position_key)
        now = _db_timestamp(connection)
        try:
            cursor = execute(
                connection,
                "INSERT INTO volunteer_appointment_recommendation_rules "
                "(source_service_unit_id, source_position_key, target_service_unit_id, target_position_key, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (source_service_unit_id, source_position_key, target_service_unit_id, target_position_key, now, now),
            )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError("相同的志工任职推荐规则已存在") from exc
            raise
        rule_id = int(cursor.lastrowid)
        row = _recommendation_rows(connection)
        created = next(item for item in row if item["id"] == rule_id)
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="volunteer.appointment_recommendation.create",
            resource_type="volunteer_appointment_recommendation_rule",
            resource_id=str(rule_id),
            org_unit_id=source["service_target_org_unit_id"],
            after=created,
        )
        return created


def accept_recommendation(
    actor_user_id: int,
    *,
    source_appointment_id: int,
    recommendation_rule_id: int,
    confirmation_note: str,
) -> dict[str, Any]:
    _feature_gate(write=True)
    note = confirmation_note.strip()
    if len(note) < 8:
        raise ValueError("业务确认说明至少填写 8 个字符")
    with transaction() as connection:
        source = execute(
            connection,
            "SELECT id, member_id, appointment_key, volunteer_service_unit_id, status "
            "FROM volunteer_appointments WHERE id=?",
            (source_appointment_id,),
        ).fetchone()
        if not source or source["status"] != "ACTIVE" or not source["volunteer_service_unit_id"]:
            raise ValueError("仅当前有效的 Volunteer 2.0 任职可接受推荐")
        rule = execute(
            connection,
            "SELECT id, source_service_unit_id, source_position_key, target_service_unit_id, target_position_key, is_active "
            "FROM volunteer_appointment_recommendation_rules WHERE id=?",
            (recommendation_rule_id,),
        ).fetchone()
        if not rule or not rule["is_active"]:
            raise ValueError("志工任职推荐规则不存在或已停用")
        if (
            rule["source_service_unit_id"] != source["volunteer_service_unit_id"]
            or rule["source_position_key"] != source["appointment_key"]
        ):
            raise ValueError("推荐规则与来源任职不匹配")
        target_id, target_unit = _insert_v2_appointment(
            connection,
            actor_user_id=actor_user_id,
            member_id=int(source["member_id"]),
            service_unit_id=rule["target_service_unit_id"],
            position_key=rule["target_position_key"],
            source_reference=VOLUNTEER2_RECOMMENDED_SOURCE,
            confirmation_note=note,
        )
        now = _db_timestamp(connection)
        execute(
            connection,
            "INSERT INTO volunteer_appointment_links "
            "(source_appointment_id, target_appointment_id, recommendation_rule_id, link_type, created_at) "
            "VALUES (?, ?, ?, 'RECOMMENDED_PAIR', ?)",
            (source_appointment_id, target_id, recommendation_rule_id, now),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="volunteer.appointment_recommendation.accept",
            resource_type="volunteer_appointment_link",
            resource_id=f"{source_appointment_id}:{target_id}",
            org_unit_id=target_unit["service_target_org_unit_id"],
            purpose=note,
            after={
                "source_appointment_id": source_appointment_id,
                "target_appointment_id": target_id,
                "recommendation_rule_id": recommendation_rule_id,
            },
        )
    return {
        "source_appointment_id": source_appointment_id,
        "target_appointment_id": target_id,
        "recommendation_rule_id": recommendation_rule_id,
        "automatic_followup_created": False,
    }


def migration_preview(actor_user_id: int) -> dict[str, Any]:
    """Classify legacy rows only; this endpoint never changes appointments."""

    _feature_gate()
    existing = list_appointments(actor_user_id)
    with transaction() as connection:
        units = [
            _service_unit_row(connection, item["id"])
            for item in execute(connection, "SELECT id FROM volunteer_service_units").fetchall()
        ]
        member_counts = Counter(item["member_id"] for item in existing)
        entries: list[dict[str, Any]] = []
        for item in existing:
            if item["service_unit_id"]:
                classification = "ALREADY_V2"
                reason = "已关联正式志工服务组织，不需要迁移"
                candidates: list[dict[str, Any]] = []
            else:
                position = get_volunteer_position(item["position_key"], connection)
                candidates = []
                if position:
                    for unit in units:
                        if unit["service_target_org_unit_id"] != item["service_target_org_unit_id"]:
                            continue
                        if (
                            unit["system_type"] == position["system_type"]
                            and unit["line_type"] == position["line_type"]
                        ):
                            try:
                                _position_for_service_unit(connection, unit, item["position_key"])
                                candidates.append(_public_service_unit(unit))
                            except ValueError:
                                continue
                if len(candidates) == 1:
                    classification = "SAFE_TO_MIGRATE"
                    reason = "存在唯一的、由配置明确匹配的服务组织；仍需人工确认后单条执行"
                elif len(candidates) > 1:
                    classification = "MANUAL_REVIEW"
                    reason = "存在多个可能的服务组织，系统不会按名称或层级猜测"
                else:
                    classification = "MANUAL_REVIEW"
                    reason = "没有唯一的配置化服务组织匹配项"
            entries.append(
                {
                    "appointment": item,
                    "member_has_multiple_appointments": member_counts[item["member_id"]] > 1,
                    "candidate_service_units": candidates,
                    "planned_position_key": item["position_key"] if len(candidates) == 1 else None,
                    "planned_position_name": item["position_name"] if len(candidates) == 1 else None,
                    "planned_service_unit": candidates[0] if len(candidates) == 1 else None,
                    "planned_line_type": candidates[0]["line_type"] if len(candidates) == 1 else None,
                    "planned_service_target_name": (
                        candidates[0].get("service_target_name") if len(candidates) == 1 else None
                    ),
                    "classification": classification,
                    "reason": reason,
                }
            )
    counts = Counter(item["classification"] for item in entries)
    return {
        "migration_executed": False,
        "entries": entries,
        "counts": dict(counts),
        "notice": "本预览不迁移、不结束、不重写任何志工任职记录。",
    }
