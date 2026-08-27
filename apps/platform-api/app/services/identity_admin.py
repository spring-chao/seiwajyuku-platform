from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.privacy import mask_login_identifier
from app.core.security import hash_password
from app.core.settings import get_settings
from app.db import execute, fetch_all, transaction
from app.services.audit import write_audit
from app.services.iam import PERMISSIONS, ROLE_NAMES, ROLE_PERMISSIONS
from app.services.volunteer_positions import (
    get_volunteer_position,
    list_volunteer_positions,
    validate_position_target,
)


POSITION_KEYS = {
    # Compatibility position retained for the named 苏州塾运营管理员 role.
    # It carries the existing operations_admin permission template while the
    # account remains governed by the dated employment record.
    "operations_admin",
    "ops_center_director",
    "ops_center_operations",
    "ops_center_learning",
    "ops_center_development",
    "ops_center_management",
    "ops_center_data",
    "ops_center_finance",
    "ops_center_administration",
}
APPOINTMENT_KEYS = {
    "volunteer_director",
    "volunteer_regional_lead",
    "volunteer_regional_service",
    "volunteer_class_counselor",
    "volunteer_deputy_class_teacher",
    "volunteer_class_monitor",
    "volunteer_group_counselor",
    "volunteer_class_committee",
    "volunteer_group_leader",
    "volunteer_group_committee",
    "volunteer_activity",
}
TERMINAL_STATUSES = {"SUSPENDED", "ENDED", "REVOKED"}
ASSIGNMENT_TABLES = {
    "employment": ("operations_employments", "employment_status", "operations_employment"),
    "position": ("operations_position_assignments", "status", "operations_position"),
    "service_responsibility": (
        "employee_service_responsibilities",
        "status",
        "employee_service_responsibility",
    ),
    "volunteer": ("volunteer_appointments", "status", "volunteer_appointment"),
    "technical": ("technical_admin_assignments", "status", "technical_admin_assignment"),
}


def _feature_gate(*, write: bool = False) -> None:
    settings = get_settings()
    if not settings.identity_authorization_enabled:
        raise PermissionError("身份与任职功能尚未启用")
    if write and not settings.identity_admin_writes_enabled:
        raise PermissionError("身份与任职写入尚未获准")
    if write and settings.is_production and not settings.allow_production_mutations:
        raise PermissionError("生产身份与任职写入未获批准")


def assert_identity_write_enabled() -> None:
    """Apply the same explicit gate to direct account-management writes."""
    _feature_gate(write=True)


def _as_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}格式无效") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _validate_term(starts_at: str, ends_at: str, *, require_future_end: bool = True) -> None:
    start = _as_datetime(starts_at, "开始时间")
    end = _as_datetime(ends_at, "结束时间")
    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")
    if require_future_end and end <= datetime.now(UTC):
        raise ValueError("结束时间必须晚于当前时间")


def _validate_confirmation(source_reference: str, confirmation_note: str) -> tuple[str, str]:
    source = source_reference.strip()
    note = confirmation_note.strip()
    if len(source) < 4:
        raise ValueError("确认依据至少填写 4 个字符")
    if len(note) < 8:
        raise ValueError("业务确认说明至少填写 8 个字符")
    return source, note


def _person_for_user(connection, user_id: int) -> str:
    row = execute(
        connection,
        "SELECT apl.person_id FROM account_person_links apl "
        "JOIN app_users u ON u.id=apl.user_id "
        "JOIN person_profiles p ON p.id=apl.person_id "
        "WHERE apl.user_id=? AND u.is_active=1 AND p.status='ACTIVE'",
        (user_id,),
    ).fetchone()
    if not row:
        raise ValueError("账号尚未完成自然人关联")
    return row["person_id"]


def catalogs() -> dict[str, Any]:
    _feature_gate()
    settings = get_settings()
    return {
        "position_keys": sorted(POSITION_KEYS),
        "appointment_keys": sorted(APPOINTMENT_KEYS),
        "volunteer_positions": list_volunteer_positions(active_only=False),
        "scope_types": ["UNIT", "SUBTREE"],
        "terminal_statuses": sorted(TERMINAL_STATUSES),
        "writes_enabled": settings.identity_admin_writes_enabled,
        "permission_matrix": [
            {
                "role_key": role_key,
                "role_name": ROLE_NAMES[role_key],
                "permissions": [
                    {
                        "permission_key": permission_key,
                        "permission_name": PERMISSIONS[permission_key][0],
                        "sensitive_level": PERMISSIONS[permission_key][1],
                    }
                    for permission_key in sorted(ROLE_PERMISSIONS[role_key])
                ],
            }
            for role_key in ROLE_NAMES
        ],
    }


def list_org_options() -> list[dict[str, Any]]:
    _feature_gate()
    return fetch_all(
        "SELECT id, unit_code, name, unit_type, parent_id "
        "FROM org_units WHERE is_active=1 ORDER BY unit_type, name"
    )


def list_identity_accounts() -> list[dict[str, Any]]:
    _feature_gate()
    rows = fetch_all(
        "SELECT u.id, u.username, u.display_name, u.is_active, apl.person_id "
        "FROM app_users u LEFT JOIN account_person_links apl ON apl.user_id=u.id "
        "ORDER BY u.is_active DESC, u.display_name, u.id"
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        raw_username = row["username"]
        person_id = row.get("person_id")
        item = {
            **row,
            "username": mask_login_identifier(raw_username),
            "is_platform_admin": raw_username == get_settings().bootstrap_admin_username,
            "legacy_roles": [
                role["role_key"]
                for role in fetch_all(
                    "SELECT role_key FROM user_roles WHERE user_id=? ORDER BY role_key",
                    (row["id"],),
                )
            ],
            "employments": [],
            "volunteer_appointments": [],
            "technical_assignments": [],
        }
        if person_id:
            employments = fetch_all(
                "SELECT oe.id, oe.institution_id, oi.name AS institution_name, "
                "oe.employment_status AS status, oe.started_on, oe.ended_on, "
                "oe.source_reference FROM operations_employments oe "
                "JOIN operating_institutions oi ON oi.id=oe.institution_id "
                "WHERE oe.person_id=? ORDER BY oe.id DESC",
                (person_id,),
            )
            for employment in employments:
                employment["positions"] = fetch_all(
                    "SELECT id, position_key, valid_from, valid_until, status, source_reference "
                    "FROM operations_position_assignments WHERE employment_id=? ORDER BY id",
                    (employment["id"],),
                )
                employment["service_responsibilities"] = fetch_all(
                    "SELECT esr.id, esr.org_unit_id, o.name AS org_name, esr.scope_type, "
                    "esr.valid_from, esr.valid_until, esr.status, esr.source_reference "
                    "FROM employee_service_responsibilities esr "
                    "JOIN org_units o ON o.id=esr.org_unit_id "
                    "WHERE esr.employment_id=? ORDER BY o.name, esr.id",
                    (employment["id"],),
                )
            item["employments"] = employments
            item["volunteer_appointments"] = fetch_all(
                "SELECT va.id, va.appointment_key, c.position_name, c.scope_level, "
                "va.org_unit_id, o.name AS org_name, "
                "va.scope_type, va.starts_at, va.ends_at, va.status, va.source_reference "
                "FROM volunteer_appointments va "
                "LEFT JOIN volunteer_position_catalog c ON c.position_key=va.appointment_key "
                "JOIN org_units o ON o.id=va.org_unit_id "
                "WHERE va.person_id=? ORDER BY va.id DESC",
                (person_id,),
            )
            item["technical_assignments"] = fetch_all(
                "SELECT id, assignment_purpose, starts_at, ends_at, status, source_reference "
                "FROM technical_admin_assignments WHERE person_id=? ORDER BY id DESC",
                (person_id,),
            )
        result.append(item)
    return result


def onboard_employee(
    actor_user_id: int,
    *,
    user_id: int | None,
    new_account: dict[str, str] | None,
    position_keys: list[str],
    started_on: str,
    ended_on: str,
    service_responsibilities: list[dict[str, str]],
    source_reference: str,
    confirmation_note: str,
) -> dict[str, Any]:
    """Atomically create/select an account, link a person, and add scoped employment."""
    _feature_gate(write=True)
    source, note = _validate_confirmation(source_reference, confirmation_note)
    if (user_id is None) == (new_account is None):
        raise ValueError("必须且只能选择现有账号或新建账号")

    normalized_positions: list[str] = []
    for key in position_keys:
        normalized = key.strip()
        if normalized and normalized not in normalized_positions:
            normalized_positions.append(normalized)
    if not normalized_positions:
        raise ValueError("至少指定一个运营中心岗位")
    if any(key not in POSITION_KEYS for key in normalized_positions):
        raise ValueError("未知运营中心岗位")

    start = _as_datetime(started_on, "入职时间")
    end = _as_datetime(ended_on, "任职结束时间")
    if end <= start:
        raise ValueError("任职结束时间必须晚于入职时间")
    if end <= datetime.now(UTC):
        raise ValueError("任职结束时间必须晚于当前时间")
    status = "PLANNED" if start > datetime.now(UTC) else "ACTIVE"

    normalized_responsibilities: list[tuple[str, str]] = []
    seen_orgs: set[str] = set()
    for responsibility in service_responsibilities:
        scope_type = responsibility["scope_type"].upper().strip()
        org_unit_id = responsibility["org_unit_id"].strip()
        if scope_type not in {"UNIT", "SUBTREE"} or not org_unit_id:
            raise ValueError("服务责任范围必须指定 UNIT 或 SUBTREE 组织")
        if org_unit_id in seen_orgs:
            raise ValueError("同一服务责任组织不能重复添加")
        seen_orgs.add(org_unit_id)
        normalized_responsibilities.append((scope_type, org_unit_id))
    if not normalized_responsibilities:
        raise ValueError("一站式录入至少指定一个服务责任范围")

    account_values: dict[str, str] | None = None
    password_hash: str | None = None
    if new_account is not None:
        account_password = new_account.get("password", "")
        account_values = {
            "username": new_account.get("username", "").strip(),
            "display_name": new_account.get("display_name", "").strip(),
        }
        if len(account_values["username"]) < 3:
            raise ValueError("账号至少填写 3 个字符")
        if not account_values["display_name"]:
            raise ValueError("人员名称不能为空")
        if len(account_password) < 10:
            raise ValueError("临时密码至少 10 个字符")
        password_hash = hash_password(account_password)

    now = datetime.now(UTC).isoformat()
    account_created = False
    person_link_created = False
    with transaction() as connection:
        if account_values is not None:
            if execute(
                connection,
                "SELECT id FROM app_users WHERE username=?",
                (account_values["username"],),
            ).fetchone():
                raise ValueError("账号已存在，请改为选择现有账号")
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (
                    account_values["username"],
                    account_values["display_name"],
                    password_hash,
                    now,
                    now,
                ),
            )
            user_id = cursor.lastrowid
            account_created = True
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="iam.user.create",
                resource_type="app_user",
                resource_id=str(user_id),
                purpose=note,
                after={
                    "username_masked": mask_login_identifier(account_values["username"]),
                    "roles": [],
                    "scopes": [],
                    "creation_path": "identity.employee_onboarding",
                },
            )

        user = execute(
            connection,
            "SELECT id, username, display_name, is_active FROM app_users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not user or not user["is_active"]:
            raise ValueError("账号不存在或已停用")
        if user["username"] == get_settings().bootstrap_admin_username:
            raise ValueError("平台最高管理账号不作为自然人、雇佣或任职试点账号")
        if execute(
            connection,
            "SELECT role_key FROM user_roles WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone():
            raise ValueError(
                "现有账号仍有旧角色，不能叠加一站式任职；请先完成角色迁移或新建个人账号"
            )

        link = execute(
            connection,
            "SELECT person_id FROM account_person_links WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if link:
            person_id = link["person_id"]
        else:
            person_id = f"person-{uuid4()}"
            execute(
                connection,
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (person_id, user["display_name"], now, now),
            )
            execute(
                connection,
                "INSERT INTO account_person_links"
                "(user_id, person_id, linked_at, linked_by, source_reference) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, person_id, now, actor_user_id, source),
            )
            person_link_created = True
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="identity.person.link",
                resource_type="person_profile",
                resource_id=person_id,
                purpose=note,
                after={"user_id": user_id, "source_reference": source},
            )

        if execute(
            connection,
            "SELECT id FROM operations_employments WHERE person_id=? "
            "AND employment_status IN ('PLANNED','ACTIVE','LEAVE') LIMIT 1",
            (person_id,),
        ).fetchone():
            raise ValueError("该自然人已有未结束的运营中心雇佣记录")
        for _, org_unit_id in normalized_responsibilities:
            if not execute(
                connection,
                "SELECT id FROM org_units WHERE id=? AND is_active=1",
                (org_unit_id,),
            ).fetchone():
                raise ValueError("服务责任组织不存在或已停用")

        cursor = execute(
            connection,
            "INSERT INTO operations_employments"
            "(person_id, institution_id, employment_status, started_on, ended_on, "
            "source_reference, created_at, updated_at) "
            "VALUES (?, 'institution-suzhou-operations', ?, ?, ?, ?, ?, ?)",
            (person_id, status, started_on, ended_on, source, now, now),
        )
        employment_id = cursor.lastrowid
        for position_key in normalized_positions:
            execute(
                connection,
                "INSERT INTO operations_position_assignments"
                "(employment_id, position_key, valid_from, valid_until, status, "
                "source_reference, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employment_id,
                    position_key,
                    started_on,
                    ended_on,
                    status,
                    source,
                    now,
                    now,
                ),
            )
        for scope_type, org_unit_id in normalized_responsibilities:
            execute(
                connection,
                "INSERT INTO employee_service_responsibilities"
                "(employment_id, org_unit_id, scope_type, valid_from, valid_until, "
                "status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employment_id,
                    org_unit_id,
                    scope_type,
                    started_on,
                    ended_on,
                    status,
                    source,
                    now,
                    now,
                ),
            )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.employment.create",
            resource_type="operations_employment",
            resource_id=str(employment_id),
            purpose=note,
            after={
                "user_id": user_id,
                "position_keys": normalized_positions,
                "started_on": started_on,
                "ended_on": ended_on,
                "service_responsibilities": [
                    {"scope_type": scope, "org_unit_id": org}
                    for scope, org in normalized_responsibilities
                ],
                "source_reference": source,
                "entry_path": "identity.employee_onboarding",
            },
        )

    return {
        "user_id": user_id,
        "person_id": person_id,
        "employment_id": employment_id,
        "account_created": account_created,
        "person_link_created": person_link_created,
    }


def initialize_person_link(
    actor_user_id: int,
    user_id: int,
    *,
    source_reference: str,
    confirmation_note: str,
) -> str:
    _feature_gate(write=True)
    source, note = _validate_confirmation(source_reference, confirmation_note)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        user = execute(
            connection,
            "SELECT id, username, display_name, is_active FROM app_users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not user or not user["is_active"]:
            raise ValueError("账号不存在或已停用")
        if user["username"] == get_settings().bootstrap_admin_username:
            raise ValueError("平台最高管理账号不作为自然人、雇佣或任职试点账号")
        if execute(
            connection, "SELECT person_id FROM account_person_links WHERE user_id=?", (user_id,)
        ).fetchone():
            raise ValueError("账号已经关联自然人")
        person_id = f"person-{uuid4()}"
        execute(
            connection,
            "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (person_id, user["display_name"], now, now),
        )
        execute(
            connection,
            "INSERT INTO account_person_links"
            "(user_id, person_id, linked_at, linked_by, source_reference) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, person_id, now, actor_user_id, source),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.person.link",
            resource_type="person_profile",
            resource_id=person_id,
            purpose=note,
            after={"user_id": user_id, "source_reference": source},
        )
    return person_id


def change_account_status(
    actor_user_id: int,
    user_id: int,
    *,
    status: str,
    reason: str,
) -> None:
    """Suspend or reactivate an account with an audit trail."""
    _feature_gate(write=True)
    status = status.upper().strip()
    reason = reason.strip()
    if status not in {"ACTIVE", "SUSPENDED"}:
        raise ValueError("账号状态只能是 ACTIVE 或 SUSPENDED")
    if len(reason) < 6:
        raise ValueError("账号状态变更原因至少填写 6 个字符")
    if actor_user_id == user_id and status == "SUSPENDED":
        raise ValueError("不能停用当前登录账号")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        user = execute(
            connection,
            "SELECT id, username, is_active FROM app_users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("账号不存在")
        settings = get_settings()
        if user["username"] == settings.bootstrap_admin_username and status == "SUSPENDED":
            raise ValueError("平台最高管理账号不可停用")
        next_active = 1 if status == "ACTIVE" else 0
        if int(user["is_active"]) == next_active:
            raise ValueError("账号已经处于目标状态")
        execute(
            connection,
            "UPDATE app_users SET is_active=?, token_version=token_version+1, updated_at=? "
            "WHERE id=?",
            (next_active, now, user_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.account.status_change",
            resource_type="app_user",
            resource_id=str(user_id),
            purpose=reason,
            before={"is_active": int(user["is_active"])},
            after={"is_active": next_active, "status": status},
        )


def create_employment(
    actor_user_id: int,
    user_id: int,
    *,
    position_keys: list[str] | None = None,
    position_key: str | None = None,
    started_on: str,
    ended_on: str | None,
    service_responsibilities: list[dict[str, str]],
    source_reference: str,
    confirmation_note: str,
) -> int:
    _feature_gate(write=True)
    source, note = _validate_confirmation(source_reference, confirmation_note)
    normalized_positions: list[str] = []
    for key in list(position_keys or []) + ([position_key] if position_key else []):
        normalized = key.strip()
        if normalized and normalized not in normalized_positions:
            normalized_positions.append(normalized)
    if not normalized_positions:
        raise ValueError("至少指定一个运营中心岗位")
    unknown_positions = [key for key in normalized_positions if key not in POSITION_KEYS]
    if unknown_positions:
        raise ValueError("未知运营中心岗位")
    start = _as_datetime(started_on, "入职时间")
    end = _as_datetime(ended_on, "离职时间") if ended_on else None
    if end and end <= start:
        raise ValueError("离职时间必须晚于入职时间")
    if end and end <= datetime.now(UTC):
        raise ValueError("不能创建已经结束的雇佣记录")
    status = "PLANNED" if start > datetime.now(UTC) else "ACTIVE"
    normalized_responsibilities: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for responsibility in service_responsibilities:
        scope_type = responsibility["scope_type"].upper()
        org_unit_id = responsibility["org_unit_id"].strip()
        if scope_type not in {"UNIT", "SUBTREE"} or not org_unit_id:
            raise ValueError("服务责任范围必须指定 UNIT 或 SUBTREE 组织")
        key = (scope_type, org_unit_id)
        if key not in seen:
            seen.add(key)
            normalized_responsibilities.append(key)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        person_id = _person_for_user(connection, user_id)
        if execute(
            connection,
            "SELECT id FROM operations_employments WHERE person_id=? "
            "AND employment_status IN ('PLANNED','ACTIVE','LEAVE') LIMIT 1",
            (person_id,),
        ).fetchone():
            raise ValueError("该自然人已有未结束的运营中心雇佣记录")
        for _, org_unit_id in normalized_responsibilities:
            if not execute(
                connection,
                "SELECT id FROM org_units WHERE id=? AND is_active=1",
                (org_unit_id,),
            ).fetchone():
                raise ValueError("服务责任组织不存在或已停用")
        cursor = execute(
            connection,
            "INSERT INTO operations_employments"
            "(person_id, institution_id, employment_status, started_on, ended_on, "
            "source_reference, created_at, updated_at) "
            "VALUES (?, 'institution-suzhou-operations', ?, ?, ?, ?, ?, ?)",
            (person_id, status, started_on, ended_on, source, now, now),
        )
        employment_id = cursor.lastrowid
        for normalized_position in normalized_positions:
            execute(
                connection,
                "INSERT INTO operations_position_assignments"
                "(employment_id, position_key, valid_from, valid_until, status, "
                "source_reference, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employment_id,
                    normalized_position,
                    started_on,
                    ended_on,
                    status,
                    source,
                    now,
                    now,
                ),
            )
        for scope_type, org_unit_id in normalized_responsibilities:
            execute(
                connection,
                "INSERT INTO employee_service_responsibilities"
                "(employment_id, org_unit_id, scope_type, valid_from, valid_until, "
                "status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employment_id,
                    org_unit_id,
                    scope_type,
                    started_on,
                    ended_on,
                    status,
                    source,
                    now,
                    now,
                ),
            )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.employment.create",
            resource_type="operations_employment",
            resource_id=str(employment_id),
            purpose=note,
            after={
                "user_id": user_id,
                "position_keys": normalized_positions,
                "started_on": started_on,
                "ended_on": ended_on,
                "service_responsibilities": [
                    {"scope_type": scope, "org_unit_id": org}
                    for scope, org in normalized_responsibilities
                ],
                "source_reference": source,
            },
        )
    return employment_id


def create_volunteer_appointment(
    actor_user_id: int,
    user_id: int,
    *,
    appointment_key: str,
    org_unit_id: str,
    scope_type: str,
    starts_at: str,
    ends_at: str,
    source_reference: str,
    confirmation_note: str,
) -> int:
    _feature_gate(write=True)
    source, note = _validate_confirmation(source_reference, confirmation_note)
    appointment_key = appointment_key.strip()
    if not get_volunteer_position(appointment_key):
        raise ValueError("未知志工任职")
    scope_type = scope_type.upper()
    if scope_type not in {"UNIT", "SUBTREE"}:
        raise ValueError("志工任职范围必须是 UNIT 或 SUBTREE")
    _validate_term(starts_at, ends_at)
    status = "PLANNED" if _as_datetime(starts_at, "开始时间") > datetime.now(UTC) else "ACTIVE"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        person_id = _person_for_user(connection, user_id)
        target = validate_position_target(
            connection,
            position_key=appointment_key,
            org_unit_id=org_unit_id,
            scope_type=scope_type,
        )
        if execute(
            connection,
            "SELECT id FROM volunteer_appointments WHERE person_id=? "
            "AND appointment_key=? AND org_unit_id=? "
            "AND status IN ('PLANNED','ACTIVE','SUSPENDED') "
            "AND starts_at<? AND ends_at>? LIMIT 1",
            (person_id, appointment_key, org_unit_id, ends_at, starts_at),
        ).fetchone():
            raise ValueError("相同组织和任职存在重叠任期")
        cursor = execute(
            connection,
            "INSERT INTO volunteer_appointments"
            "(person_id, appointment_key, org_unit_id, scope_type, starts_at, ends_at, "
            "status, source_reference, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person_id,
                appointment_key,
                org_unit_id,
                scope_type,
                starts_at,
                ends_at,
                status,
                source,
                now,
                now,
            ),
        )
        appointment_id = cursor.lastrowid
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.volunteer_appointment.create",
            resource_type="volunteer_appointment",
            resource_id=str(appointment_id),
            org_unit_id=org_unit_id,
            purpose=note,
            after={
                "user_id": user_id,
                "appointment_key": appointment_key,
                "position_name": target["position_name"],
                "scope_level": target["scope_level"],
                "scope_type": scope_type,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "source_reference": source,
            },
        )
    return appointment_id


def create_technical_assignment(
    actor_user_id: int,
    user_id: int,
    *,
    assignment_purpose: str,
    starts_at: str,
    ends_at: str,
    source_reference: str,
    confirmation_note: str,
) -> int:
    _feature_gate(write=True)
    source, note = _validate_confirmation(source_reference, confirmation_note)
    purpose = assignment_purpose.strip()
    if len(purpose) < 6:
        raise ValueError("技术管理用途至少填写 6 个字符")
    _validate_term(starts_at, ends_at)
    status = "PLANNED" if _as_datetime(starts_at, "开始时间") > datetime.now(UTC) else "ACTIVE"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        person_id = _person_for_user(connection, user_id)
        cursor = execute(
            connection,
            "INSERT INTO technical_admin_assignments"
            "(person_id, assignment_purpose, starts_at, ends_at, status, "
            "source_reference, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (person_id, purpose, starts_at, ends_at, status, source, now, now),
        )
        assignment_id = cursor.lastrowid
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="identity.technical_assignment.create",
            resource_type="technical_admin_assignment",
            resource_id=str(assignment_id),
            purpose=note,
            after={
                "user_id": user_id,
                "assignment_purpose": purpose,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "source_reference": source,
            },
        )
    return assignment_id


def change_assignment_status(
    actor_user_id: int,
    *,
    assignment_type: str,
    assignment_id: int,
    status: str,
    reason: str,
) -> None:
    _feature_gate(write=True)
    status = status.upper()
    reason = reason.strip()
    if assignment_type not in ASSIGNMENT_TABLES:
        raise ValueError("未知任职记录类型")
    if status not in TERMINAL_STATUSES:
        raise ValueError("仅允许停用、结束或撤销任职")
    if len(reason) < 6:
        raise ValueError("状态变更原因至少填写 6 个字符")
    table, status_column, resource_type = ASSIGNMENT_TABLES[assignment_type]
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        row = execute(
            connection,
            f"SELECT id, {status_column} AS status FROM {table} WHERE id=?",
            (assignment_id,),
        ).fetchone()
        if not row:
            raise ValueError("任职记录不存在")
        if row["status"] in {"ENDED", "REVOKED"}:
            raise ValueError("已结束或撤销的记录不能再次变更")
        execute(
            connection,
            f"UPDATE {table} SET {status_column}=?, updated_at=? WHERE id=?",
            (status, now, assignment_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action=f"identity.{assignment_type}.status_change",
            resource_type=resource_type,
            resource_id=str(assignment_id),
            purpose=reason,
            before={"status": row["status"]},
            after={"status": status},
        )
