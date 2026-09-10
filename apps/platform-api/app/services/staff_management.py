from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.privacy import mask_login_identifier, phone_hash, protected_phone
from app.core.security import hash_password
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import (
    EMPLOYEE_ASSIGNABLE_ROLE_KEYS,
    PERMISSIONS,
    POSITION_NAMES,
    ROLE_NAMES,
    ROLE_PERMISSIONS,
    accessible_org_ids,
    current_request_permission,
    user_context,
)
from app.services.identity_admin import assert_identity_write_enabled


VALID_GENDERS = {"MALE", "FEMALE"}
EMPLOYMENT_STATUSES = {"ACTIVE", "LEAVE"}
PASSWORD_MIN_LENGTH = 6

# Business positions remain distinct from IAM2 roles.  This is the reviewed
# one-to-one mapping used by the ordinary staff workflow; the compatibility
# position deliberately remains unmapped and therefore cannot silently gain a
# guessed permission set.
POSITION_ROLE_MAPPING = {
    "ops_center_director": "employee_operations_lead",
    "ops_center_operations": "employee_operations_management",
    "ops_center_learning": "employee_learning_management",
    "ops_center_development": "employee_development_management",
    "ops_center_management": "employee_operations_management",
    "ops_center_data": "employee_data_management",
    "ops_center_finance": "employee_finance_management",
    "ops_center_administration": "employee_administration_management",
}

# Stable institution keys are the business contract for the ordinary staff
# drawer. Existing production rows use SUZHOU_CENTER and the historical
# SUZHOU_OPERATIONS_CENTER names, so those are aliases of the formal SUZHOU
# institution rather than new choices. Missing future institutions are
# reported by the catalog until their source rows and org roots are landed.
STAFF_INSTITUTION_CATALOG = {
    "JIANGNAN": {
        "name": "江南塾",
        "source_codes": ("JIANGNAN",),
    },
    "SUZHOU": {
        "name": "苏州塾",
        "source_codes": ("SUZHOU", "SUZHOU_CENTER"),
    },
    "CHANGZHOU": {
        "name": "常州塾",
        "source_codes": ("CHANGZHOU", "CHANGZHOU_CENTER"),
    },
    "WUXI": {
        "name": "无锡塾",
        "source_codes": ("WUXI", "WUXI_CENTER"),
    },
}
STAFF_INSTITUTION_CODE_ALIASES = {
    source_code: business_code
    for business_code, item in STAFF_INSTITUTION_CATALOG.items()
    for source_code in item["source_codes"]
}
# Historical operating-center rows remain readable for existing records and
# root inheritance, but are never returned or accepted as ordinary staff
# choices.
STAFF_INSTITUTION_CODE_ALIASES["SUZHOU_OPERATIONS_CENTER"] = "SUZHOU"
STAFF_INSTITUTION_VISIBLE_SOURCE_CODES = frozenset(
    source_code
    for item in STAFF_INSTITUTION_CATALOG.values()
    for source_code in item["source_codes"]
)

POSITION_DUTY_DESCRIPTIONS = {
    "operations_admin": "兼容岗位，仅供历史记录和迁移预览",
    "ops_center_director": "负责范围内全部运营业务管理",
    "ops_center_operations": "学员、关爱、续费及日常运营",
    "ops_center_learning": "学习计划、课程、学习会、出勤等",
    "ops_center_development": "新学长、入塾审核、发展跟进",
    "ops_center_management": "年度计划、规则、运营组织与相关统计",
    "ops_center_data": "数据、导入导出、签到同步与数据维护",
    "ops_center_finance": "续费、收款确认等财务业务",
    "ops_center_administration": "基础资料、行政支持及相关关爱",
}


def _read_gate() -> None:
    if not get_settings().identity_authorization_enabled:
        raise PermissionError("身份与任职功能尚未启用")


def _write_gate() -> None:
    _read_gate()
    assert_identity_write_enabled()


def _as_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}格式无效") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _normalize_date(value: str | None, label: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _as_utc(str(value).strip(), label).isoformat()


def _normalize_employment_status(value: str | None) -> str:
    """Return the only two ordinary staff lifecycle states.

    Dates remain optional archive metadata, but are never used to turn a
    staff identity or authorization on/off.  A person is authorized as staff
    only while the employment itself is explicitly ACTIVE.
    """

    status = str(value or "ACTIVE").strip().upper()
    if status not in EMPLOYMENT_STATUSES:
        raise ValueError("专职在职状态只能是 ACTIVE（在职）或 LEAVE（离职）")
    return status


def _validate_interval(
    valid_from: str | None,
    valid_until: str | None,
    *,
    start_label: str = "开始时间",
    end_label: str = "结束时间",
) -> tuple[str | None, str | None]:
    start = _normalize_date(valid_from, start_label)
    end = _normalize_date(valid_until, end_label)
    if start and end and _as_utc(end, end_label) <= _as_utc(start, start_label):
        raise ValueError(f"{end_label}必须晚于{start_label}")
    return start, end


def _clean_text(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _role_permissions(role_key: str) -> list[dict[str, str]]:
    if role_key not in ROLE_PERMISSIONS:
        raise ValueError("未知角色")
    return [
        {
            "permission_key": key,
            "permission_name": PERMISSIONS[key][0],
            "sensitive_level": PERMISSIONS[key][1],
        }
        for key in sorted(ROLE_PERMISSIONS[role_key])
    ]


def _role_risk(role_key: str) -> set[str]:
    return {item["sensitive_level"] for item in _role_permissions(role_key)} - {"INTERNAL"}


def _organization(connection: Any, org_unit_id: str) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT id, name, unit_type, parent_id FROM org_units WHERE id=? AND is_active=1",
        (org_unit_id,),
    ).fetchone()
    if not row:
        raise ValueError("授权组织不存在或已停用")
    return dict(row)


def _institution_root(connection: Any, institution_id: str) -> dict[str, Any] | None:
    """Resolve an institution's formal org root without name matching.

    A legacy operating-center row may inherit the root mapping from its
    mapped parent. This keeps historical API records readable while the
    ordinary selector uses the formal institution row.
    """

    visited: set[str] = set()
    current_id = institution_id
    while current_id and current_id not in visited:
        visited.add(current_id)
        link = execute(
            connection,
            "SELECT l.org_unit_id, l.link_type, o.unit_code, o.name "
            "FROM institution_org_links l "
            "JOIN org_units o ON o.id=l.org_unit_id AND o.is_active=1 "
            "WHERE l.institution_id=? "
            "ORDER BY CASE WHEN l.link_type='SERVICE_BOUNDARY' THEN 0 ELSE 1 END, l.created_at, l.org_unit_id "
            "LIMIT 1",
            (current_id,),
        ).fetchone()
        if link:
            result = dict(link)
            result["source_institution_id"] = current_id
            return result
        parent = execute(
            connection,
            "SELECT parent_id FROM operating_institutions WHERE id=? AND is_active=1",
            (current_id,),
        ).fetchone()
        current_id = str(parent["parent_id"] or "") if parent else ""
    return None


def _institution(
    connection: Any,
    institution_id: str,
    *,
    require_business_catalog: bool = False,
) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT id, institution_code, name, institution_type, parent_id "
        "FROM operating_institutions WHERE id=? AND is_active=1",
        (institution_id,),
    ).fetchone()
    if not row:
        raise ValueError("所属机构不存在或已停用")
    result = dict(row)
    business_code = STAFF_INSTITUTION_CODE_ALIASES.get(result["institution_code"])
    if require_business_catalog and result["institution_code"] not in STAFF_INSTITUTION_VISIBLE_SOURCE_CODES:
        raise ValueError("普通专职人员只能选择四个正式塾机构")
    if business_code:
        result["business_code"] = business_code
        result["business_name"] = STAFF_INSTITUTION_CATALOG[business_code]["name"]
    root = _institution_root(connection, institution_id)
    result["root_org_unit_id"] = root["org_unit_id"] if root else None
    result["root_org_unit_code"] = root["unit_code"] if root else None
    result["root_org_unit_name"] = root["name"] if root else None
    return result


def _organization_descendants(connection: Any, root_org_unit_id: str) -> set[str]:
    rows = execute(
        connection,
        "WITH RECURSIVE descendants(id) AS ("
        " SELECT id FROM org_units WHERE id=? AND is_active=1 "
        " UNION ALL SELECT o.id FROM org_units o JOIN descendants d ON o.parent_id=d.id "
        " WHERE o.is_active=1"
        ") SELECT id FROM descendants",
        (root_org_unit_id,),
    ).fetchall()
    return {row["id"] for row in rows}


def _validate_institution_scope(
    connection: Any,
    institution: dict[str, Any],
    org_unit_id: str,
) -> None:
    root_id = institution.get("root_org_unit_id")
    if not root_id:
        raise ValueError(
            f"{institution.get('business_name') or institution.get('name')}尚未配置正式组织根节点，暂不能设置负责范围"
        )
    if org_unit_id not in _organization_descendants(connection, str(root_id)):
        raise ValueError("负责范围必须属于所属机构对应的组织树")


def _actor_scope_ids(actor_user_id: int) -> set[str] | None:
    """Return the actor's staff-management boundary.

    Technical/system IAM administrators retain their existing unrestricted
    catalog access. Business staff managers are resolved through the exact
    ``staff:manage`` grant and therefore cannot select or edit outside scope.
    """

    actor = user_context(actor_user_id) or {}
    if {"system_admin", "technical_admin"}.intersection(actor.get("roles", [])):
        return None
    permission = current_request_permission() or "staff:manage"
    return accessible_org_ids(actor_user_id, permission)


def _validate_actor_grants(actor_user_id: int, grants: list[dict[str, Any]]) -> None:
    allowed = _actor_scope_ids(actor_user_id)
    if allowed is not None and any(grant["org_unit_id"] not in allowed for grant in grants):
        raise PermissionError("授权范围超出当前账号可管理的组织范围")


def _assert_staff_item_in_actor_scope(actor_user_id: int, item: dict[str, Any]) -> None:
    allowed = _actor_scope_ids(actor_user_id)
    if allowed is not None and not any(
        scope["org_unit_id"] in allowed for scope in item.get("scopes", [])
    ):
        raise PermissionError("当前账号不能管理该人员的负责范围")


def _supervisor(connection: Any, supervisor_user_id: int | None, user_id: int | None = None) -> int | None:
    if supervisor_user_id is None:
        return None
    if user_id is not None and int(supervisor_user_id) == int(user_id):
        raise ValueError("上级负责人不能是本人")
    row = execute(
        connection,
        "SELECT id FROM app_users WHERE id=? AND is_active=1",
        (supervisor_user_id,),
    ).fetchone()
    if not row:
        raise ValueError("上级负责人账号不存在或已停用")
    return int(supervisor_user_id)


def _normalize_positions(position_keys: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in position_keys:
        key = str(raw or "").strip()
        if key and key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("至少选择一个岗位")
    unknown = [key for key in normalized if key not in POSITION_NAMES]
    if unknown:
        raise ValueError("包含未知岗位")
    return normalized


def _normalize_grants(
    connection: Any,
    grants: list[dict[str, Any]],
    *,
    allow_empty: bool,
    historical_grants: dict[tuple[str, str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in grants:
        role_key = str(raw.get("role_key") or "").strip()
        org_unit_id = str(raw.get("org_unit_id") or "").strip()
        scope_type = str(raw.get("scope_type") or "").upper().strip()
        if role_key not in EMPLOYEE_ASSIGNABLE_ROLE_KEYS:
            raise ValueError("普通专职人员管理不允许分配该角色")
        if not org_unit_id or scope_type not in {"UNIT", "SUBTREE"}:
            raise ValueError("每条角色授权都必须指定组织和 UNIT/SUBTREE 范围")
        _organization(connection, org_unit_id)
        key = (role_key, org_unit_id, scope_type)
        prior = (historical_grants or {}).get(key, {})
        # These values are retained only when an older API client explicitly
        # supplies them or when an existing archive record already has them.
        # They are never authorization conditions.
        valid_from, valid_until = _validate_interval(
            raw.get("valid_from") if "valid_from" in raw else prior.get("valid_from"),
            raw.get("valid_until") if "valid_until" in raw else prior.get("valid_until"),
            start_label="授权开始时间",
            end_label="授权结束时间",
        )
        if key in seen:
            # The simple drawer intentionally deduplicates repeated role-scope
            # selections instead of creating indistinguishable grants.
            continue
        seen.add(key)
        normalized.append(
            {
                "role_key": role_key,
                "org_unit_id": org_unit_id,
                "scope_type": scope_type,
                "valid_from": valid_from,
                "valid_until": valid_until,
                # Normal staff management creates an active grant. Removing
                # it writes REVOKED in update_staff; there is no date-driven
                # planned or automatic-expiry state in this workflow.
                "status": "ACTIVE",
            }
        )
    if not normalized and not allow_empty:
        raise ValueError("至少配置一条角色与管辖范围授权")
    role_keys = sorted({grant["role_key"] for grant in normalized})
    placeholders = ", ".join("?" for _ in role_keys)
    available_rows = execute(
        connection,
        f"SELECT role_key FROM roles WHERE role_key IN ({placeholders}) AND is_active=1",
        tuple(role_keys),
    ).fetchall()
    available = {
        row["role_key"] if isinstance(row, dict) else row["role_key"]
        for row in available_rows
    }
    missing = [role_key for role_key in role_keys if role_key not in available]
    if missing:
        names = "、".join(ROLE_NAMES.get(role_key, role_key) for role_key in missing)
        raise ValueError(
            f"岗位权限尚未配置：{names}，请联系管理员完成 IAM2 角色初始化"
        )
    return normalized


def _validate_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 位")
    return password


def _resolve_existing_member_person(
    connection: Any, *, name: str, phone_hash_value: str
) -> tuple[int, str | None] | None:
    """Resolve an already-known member without treating staff as members.

    A staff create may reuse a person only when one ACTIVE member matches both
    the exact normalized name and the exact protected phone hash.  A phone
    match with a different name, or more than one exact match, is deliberately
    surfaced for manual review instead of guessed or merged.
    """

    exact = execute(
        connection,
        "SELECT m.id AS member_id, mi.person_id, mi.status AS identity_status FROM members m "
        "LEFT JOIN member_identities mi ON mi.member_id=m.id "
        "WHERE m.status='ACTIVE' AND m.name=? AND m.phone_hash=? "
        "ORDER BY m.id LIMIT 3",
        (name, phone_hash_value),
    ).fetchall()
    if len(exact) > 1:
        raise ValueError("姓名和手机号匹配到多个在册学员，请先人工核对")
    if exact:
        identity = exact[0]
        if identity["identity_status"] is None:
            # Older member records can predate the person-identity table.  A
            # staff create may establish that missing bridge in this same
            # transaction, without inventing a member or guessing a person.
            return int(identity["member_id"]), None
        if identity["identity_status"] != "ACTIVE" or not identity["person_id"]:
            raise ValueError("已有学员身份记录状态异常，请先人工核对")
        return int(identity["member_id"]), identity["person_id"]

    phone_matches = execute(
        connection,
        "SELECT m.id, m.name FROM members m "
        "WHERE m.status='ACTIVE' AND m.phone_hash=? LIMIT 2",
        (phone_hash_value,),
    ).fetchall()
    if phone_matches:
        raise ValueError("检测到可能已有学员档案，请核对后再关联")
    return None


def _auto_grants(
    connection: Any,
    position_keys: list[str],
    org_unit_id: str | None,
    scope_type: str | None,
    *,
    institution: dict[str, Any] | None = None,
    actor_user_id: int | None = None,
) -> list[dict[str, Any]]:
    organization_id = str(org_unit_id or "").strip()
    normalized_scope_type = str(scope_type or "").strip().upper()
    if not organization_id or normalized_scope_type not in {"UNIT", "SUBTREE"}:
        raise ValueError("请选择负责范围")
    _organization(connection, organization_id)
    if institution is not None:
        _validate_institution_scope(connection, institution, organization_id)
    if actor_user_id is not None:
        allowed = _actor_scope_ids(actor_user_id)
        if allowed is not None and organization_id not in allowed:
            raise PermissionError("负责范围超出当前账号可管理的组织范围")
    missing = [key for key in position_keys if key not in POSITION_ROLE_MAPPING]
    if missing:
        names = "、".join(POSITION_NAMES.get(key, key) for key in missing)
        raise ValueError(f"岗位权限映射待业务确认：{names}")
    return _normalize_grants(
        connection,
        [
            {
                "role_key": POSITION_ROLE_MAPPING[position_key],
                "org_unit_id": organization_id,
                "scope_type": normalized_scope_type,
            }
            for position_key in position_keys
        ],
        allow_empty=False,
    )


def _mask_grant(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": grant.get("id"),
        "role_key": grant["role_key"],
        "role_name": ROLE_NAMES.get(grant["role_key"], grant["role_key"]),
        "org_unit_id": grant["org_unit_id"],
        "org_name": grant.get("org_name"),
        "scope_type": grant["scope_type"],
        "valid_from": grant.get("valid_from"),
        "valid_until": grant.get("valid_until"),
        "status": grant.get("status"),
        "sensitive_levels": sorted(_role_risk(grant["role_key"])),
    }


def _employment_rows(*, user_id: int | None = None, current_only: bool = True) -> list[dict[str, Any]]:
    # Leave records remain visible as personnel history, but only ACTIVE is
    # considered an effective employment by user_context().  The dates below
    # are archive fields and must not hide or activate a staff record.
    conditions = ["oe.employment_status IN ('ACTIVE','LEAVE')"]
    params: list[Any] = []
    del current_only
    if user_id is not None:
        conditions.append("u.id=?")
        params.append(user_id)
    return fetch_all(
        "SELECT u.id AS user_id, u.username, u.display_name, u.is_active, "
        "u.last_login_at, apl.person_id, epd.work_phone_masked, epd.work_phone_hash, "
        "epd.gender, oe.id AS employment_id, oe.institution_id, oi.name AS institution_name, "
        "oe.department_name, oe.supervisor_user_id, supervisor.display_name AS supervisor_name, "
        "oe.employment_status, oe.started_on, oe.ended_on "
        "FROM operations_employments oe "
        "JOIN person_profiles p ON p.id=oe.person_id "
        "JOIN account_person_links apl ON apl.person_id=p.id "
        "JOIN app_users u ON u.id=apl.user_id "
        "JOIN operating_institutions oi ON oi.id=oe.institution_id "
        "LEFT JOIN employee_profile_details epd ON epd.person_id=p.id "
        "LEFT JOIN app_users supervisor ON supervisor.id=oe.supervisor_user_id "
        "WHERE " + " AND ".join(conditions) + " ORDER BY u.is_active DESC, u.display_name, oe.id DESC",
        tuple(params),
    )


def _position_rows(employment_id: int, *, current_only: bool = True) -> list[dict[str, Any]]:
    condition = ""
    params: tuple[Any, ...] = (employment_id,)
    if current_only:
        condition = " AND status='ACTIVE'"
    rows = fetch_all(
        "SELECT id, position_key, valid_from, valid_until, status "
        "FROM operations_position_assignments WHERE employment_id=?" + condition + " ORDER BY id",
        params,
    )
    for row in rows:
        row["position_name"] = POSITION_NAMES.get(row["position_key"], row["position_key"])
    return rows


def _grant_rows(employment_id: int, *, current_only: bool = True) -> list[dict[str, Any]]:
    condition = ""
    params: tuple[Any, ...] = (employment_id,)
    if current_only:
        condition = " AND eag.status='ACTIVE'"
    return fetch_all(
        "SELECT eag.id, eag.role_key, eag.org_unit_id, o.name AS org_name, eag.scope_type, "
        "eag.valid_from, eag.valid_until, eag.status "
        "FROM employee_authorization_grants eag JOIN org_units o ON o.id=eag.org_unit_id "
        "WHERE eag.employment_id=?" + condition + " ORDER BY o.name, eag.role_key, eag.id",
        params,
    )


def _service_scope_rows(employment_id: int, *, current_only: bool = True) -> list[dict[str, Any]]:
    condition = ""
    params: tuple[Any, ...] = (employment_id,)
    if current_only:
        condition = " AND esr.status='ACTIVE'"
    return fetch_all(
        "SELECT esr.id, esr.org_unit_id, o.name AS org_name, esr.scope_type, "
        "esr.valid_from, esr.valid_until, esr.status "
        "FROM employee_service_responsibilities esr JOIN org_units o ON o.id=esr.org_unit_id "
        "WHERE esr.employment_id=?" + condition + " ORDER BY o.name, esr.id",
        params,
    )


def _staff_item(row: dict[str, Any]) -> dict[str, Any]:
    employment_id = int(row["employment_id"])
    current_grants = _grant_rows(employment_id)
    all_grants = _grant_rows(employment_id, current_only=False)
    positions = _position_rows(employment_id)
    service_scopes = _service_scope_rows(employment_id)
    authorization_mode = (
        "EXPLICIT"
        if all_grants
        else "LEGACY_COMPATIBILITY"
        if positions
        else "UNCONFIGURED"
    )
    display_grants = current_grants
    if authorization_mode == "LEGACY_COMPATIBILITY":
        display_grants = [
            {
                "role_key": position["position_key"],
                "org_unit_id": scope["org_unit_id"],
                "org_name": scope["org_name"],
                "scope_type": scope["scope_type"],
                "valid_from": max(
                    str(position.get("valid_from") or ""), str(scope.get("valid_from") or "")
                )
                or None,
                "valid_until": None,
                "status": "LEGACY_COMPATIBILITY",
            }
            for position in positions
            for scope in service_scopes
        ]
    scopes: list[dict[str, Any]] = []
    seen_scopes: set[tuple[str, str]] = set()
    for grant in display_grants:
        key = (grant["org_unit_id"], grant["scope_type"])
        if key not in seen_scopes:
            seen_scopes.add(key)
            scopes.append(
                {
                    "org_unit_id": grant["org_unit_id"],
                    "org_name": grant.get("org_name"),
                    "scope_type": grant["scope_type"],
                }
            )
    return {
        "id": int(row["user_id"]),
        "name": row["display_name"],
        "login_account": mask_login_identifier(row["username"]),
        "phone_masked": row.get("work_phone_masked"),
        "gender": row.get("gender"),
        "is_active": bool(row["is_active"]),
        "last_login_at": row.get("last_login_at"),
        "institution_id": row["institution_id"],
        "institution_name": row["institution_name"],
        "department_name": row.get("department_name"),
        "supervisor_user_id": row.get("supervisor_user_id"),
        "supervisor_name": row.get("supervisor_name"),
        "employment_status": row["employment_status"],
        "started_on": row.get("started_on"),
        "ended_on": row.get("ended_on"),
        "positions": positions,
        "authorization_grants": [_mask_grant(grant) for grant in display_grants],
        "scopes": scopes,
        "authorization_mode": authorization_mode,
        # Internal-only filter material is removed before this object leaves
        # the service. The full phone is never selected or returned.
        "_work_phone_hash": row.get("work_phone_hash"),
        "_login_username": row["username"],
        "_person_id": row["person_id"],
        "_employment_id": employment_id,
    }


def _public_staff_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def staff_catalog(actor_user_id: int) -> dict[str, Any]:
    _read_gate()
    actor = user_context(actor_user_id) or {"roles": []}
    actor_allowed_org_ids = _actor_scope_ids(actor_user_id)
    roles = []
    for role_key in sorted(EMPLOYEE_ASSIGNABLE_ROLE_KEYS, key=lambda key: ROLE_NAMES[key]):
        permissions = _role_permissions(role_key)
        roles.append(
            {
                "role_key": role_key,
                "role_name": ROLE_NAMES[role_key],
                "permissions": permissions,
                "sensitive_levels": sorted(
                    {permission["sensitive_level"] for permission in permissions}
                ),
                "assignable": True,
            }
        )
    org_units = fetch_all(
        "SELECT id, unit_code, name, unit_type, parent_id FROM org_units "
        "WHERE is_active=1 ORDER BY unit_type, name, id"
    )
    if actor_allowed_org_ids is not None:
        org_units = [row for row in org_units if row["id"] in actor_allowed_org_ids]
    raw_institutions = fetch_all(
        "SELECT id, institution_code, name, institution_type, parent_id "
        "FROM operating_institutions WHERE is_active=1 ORDER BY name, id"
    )
    institutions: list[dict[str, Any]] = []
    seen_business_codes: set[str] = set()
    for raw in raw_institutions:
        source_code = raw["institution_code"]
        if source_code not in STAFF_INSTITUTION_VISIBLE_SOURCE_CODES:
            continue
        business_code = STAFF_INSTITUTION_CODE_ALIASES[source_code]
        if business_code in seen_business_codes:
            continue
        item = dict(raw)
        item["business_code"] = business_code
        item["name"] = STAFF_INSTITUTION_CATALOG[business_code]["name"]
        item["source_name"] = raw["name"]
        item["scope_root_org_unit_id"] = None
        item["scope_root_name"] = None
        # Catalog reads use the same connection-independent mapping query as
        # create/update. The service helper is intentionally name agnostic.
        mapping = fetch_one(
            "SELECT l.org_unit_id, o.name "
            "FROM institution_org_links l "
            "JOIN org_units o ON o.id=l.org_unit_id AND o.is_active=1 "
            "WHERE l.institution_id=? "
            "ORDER BY CASE WHEN l.link_type='SERVICE_BOUNDARY' THEN 0 ELSE 1 END, l.created_at, l.org_unit_id "
            "LIMIT 1",
            (raw["id"],),
        )
        if not mapping and raw.get("parent_id"):
            mapping = fetch_one(
                "SELECT l.org_unit_id, o.name "
                "FROM institution_org_links l "
                "JOIN org_units o ON o.id=l.org_unit_id AND o.is_active=1 "
                "WHERE l.institution_id=? "
                "ORDER BY CASE WHEN l.link_type='SERVICE_BOUNDARY' THEN 0 ELSE 1 END, l.created_at, l.org_unit_id "
                "LIMIT 1",
                (raw["parent_id"],),
            )
        if mapping:
            item["scope_root_org_unit_id"] = mapping["org_unit_id"]
            item["scope_root_name"] = mapping["name"]
            if actor_allowed_org_ids is not None:
                visible_scope_ids = {
                    row["id"]
                    for row in fetch_all(
                        "WITH RECURSIVE descendants(id) AS ("
                        " SELECT id FROM org_units WHERE id=? AND is_active=1 "
                        " UNION ALL SELECT o.id FROM org_units o JOIN descendants d ON o.parent_id=d.id "
                        " WHERE o.is_active=1"
                        ") SELECT id FROM descendants",
                        (mapping["org_unit_id"],),
                    )
                    if row["id"] in actor_allowed_org_ids
                }
                if mapping["org_unit_id"] not in actor_allowed_org_ids:
                    visible_roots = [
                        row
                        for row in org_units
                        if row["id"] in visible_scope_ids
                        and row.get("parent_id") not in visible_scope_ids
                    ]
                    if visible_roots:
                        visible_roots.sort(key=lambda row: (row["name"], row["id"]))
                        item["scope_root_org_unit_id"] = visible_roots[0]["id"]
                        item["scope_root_name"] = visible_roots[0]["name"]
                    else:
                        item["scope_root_org_unit_id"] = None
                        item["scope_root_name"] = None
        item["scope_available"] = bool(item["scope_root_org_unit_id"])
        institutions.append(item)
        seen_business_codes.add(business_code)
    missing_institutions = [
        {
            "business_code": business_code,
            "name": item["name"],
            "reason": "机构或组织根节点尚未落地",
        }
        for business_code, item in STAFF_INSTITUTION_CATALOG.items()
        if business_code not in seen_business_codes
    ]
    departments = [
        row["department_name"]
        for row in fetch_all(
            "SELECT DISTINCT department_name FROM operations_employments "
            "WHERE department_name IS NOT NULL AND department_name<>'' ORDER BY department_name"
        )
    ]
    supervisors = [
        {
            "id": int(row["user_id"]),
            "name": row["display_name"],
            "institution_name": row["institution_name"],
        }
        for row in _employment_rows()
        if row["is_active"] and row["employment_status"] == "ACTIVE"
    ]
    return {
        "writes_enabled": get_settings().identity_admin_writes_enabled,
        "actor_is_highest_admin": bool(
            {"system_admin", "data_security_admin"}.intersection(actor.get("roles", []))
        ),
        "positions": [
            {
                "position_key": key,
                "position_name": name,
                "duty_description": POSITION_DUTY_DESCRIPTIONS.get(key, ""),
                "role_key": POSITION_ROLE_MAPPING.get(key),
                "role_name": (
                    ROLE_NAMES.get(POSITION_ROLE_MAPPING[key])
                    if key in POSITION_ROLE_MAPPING
                    else None
                ),
                "mapping_status": (
                    "AUTO" if key in POSITION_ROLE_MAPPING else "MAPPING_REVIEW_REQUIRED"
                ),
            }
            for key, name in POSITION_NAMES.items()
        ],
        "roles": roles,
        "org_units": org_units,
        "institutions": institutions,
        "missing_institutions": missing_institutions,
        "departments": departments,
        "supervisors": supervisors,
        "scope_types": ["UNIT", "SUBTREE"],
    }


def list_staff(
    actor_user_id: int,
    *,
    org_unit_id: str | None = None,
    department_name: str | None = None,
    position_key: str | None = None,
    role_key: str | None = None,
    is_active: bool | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    _read_gate()
    actor_allowed_org_ids = _actor_scope_ids(actor_user_id)
    lowered_query = str(query or "").strip().lower()
    query_phone_hash: str | None = None
    if lowered_query:
        try:
            query_phone_hash = phone_hash(lowered_query)
        except ValueError:
            query_phone_hash = None
    rows: list[dict[str, Any]] = []
    for raw in _employment_rows():
        item = _staff_item(raw)
        if actor_allowed_org_ids is not None and not any(
            scope["org_unit_id"] in actor_allowed_org_ids for scope in item["scopes"]
        ):
            continue
        if org_unit_id and org_unit_id not in {
            scope["org_unit_id"] for scope in item["scopes"]
        }:
            continue
        if department_name and item.get("department_name") != department_name:
            continue
        if position_key and position_key not in {
            position["position_key"] for position in item["positions"]
        }:
            continue
        if role_key and role_key not in {
            grant["role_key"] for grant in item["authorization_grants"]
        }:
            continue
        if is_active is not None and bool(item["is_active"]) != bool(is_active):
            continue
        if lowered_query and not (
            lowered_query in str(item["name"]).lower()
            or lowered_query in str(item["_login_username"]).lower()
            or query_phone_hash == item.get("_work_phone_hash")
        ):
            continue
        rows.append(_public_staff_item(item))
    return rows


def get_staff(actor_user_id: int, user_id: int) -> dict[str, Any]:
    _read_gate()
    actor_allowed_org_ids = _actor_scope_ids(actor_user_id)
    rows = _employment_rows(user_id=user_id)
    if not rows:
        raise ValueError("专职人员不存在或当前没有有效任职")
    item = _staff_item(rows[0])
    if actor_allowed_org_ids is not None and not any(
        scope["org_unit_id"] in actor_allowed_org_ids for scope in item["scopes"]
    ):
        raise PermissionError("当前账号不能查看该人员的负责范围")
    return _public_staff_item(item)


def _insert_grants(
    connection: Any,
    *,
    employment_id: int,
    grants: list[dict[str, Any]],
    actor_user_id: int,
    source_reference: str,
    now: str,
) -> list[int]:
    grant_ids: list[int] = []
    for grant in grants:
        cursor = execute(
            connection,
            "INSERT INTO employee_authorization_grants"
            "(employment_id, role_key, org_unit_id, scope_type, valid_from, valid_until, "
            "status, source_reference, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                employment_id,
                grant["role_key"],
                grant["org_unit_id"],
                grant["scope_type"],
                grant["valid_from"],
                grant["valid_until"],
                grant["status"],
                source_reference,
                actor_user_id,
                now,
                now,
            ),
        )
        grant_ids.append(int(cursor.lastrowid))
    return grant_ids


def create_staff(
    actor_user_id: int,
    *,
    name: str,
    login_account: str,
    temporary_password: str | None,
    is_active: bool,
    phone: str | None,
    gender: str | None,
    institution_id: str,
    department_name: str | None,
    supervisor_user_id: int | None,
    position_keys: list[str],
    employment_status: str,
    started_on: str | None,
    ended_on: str | None,
    grants: list[dict[str, Any]] | None = None,
    responsibility_org_unit_id: str | None = None,
    responsibility_scope_type: str | None = None,
    authorization_basis: str = "",
    authorization_reason: str = "",
) -> dict[str, Any]:
    _write_gate()
    staff_name = str(name or "").strip()
    username = str(login_account or "").strip()
    if not staff_name:
        raise ValueError("姓名不能为空")
    if len(username) < 3:
        raise ValueError("登录账号至少填写 3 个字符")
    normalized_gender = _clean_text(gender)
    if normalized_gender not in VALID_GENDERS:
        raise ValueError("性别必须选择男或女")
    positions = _normalize_positions(position_keys)
    employment_start, employment_end = _validate_interval(
        started_on, ended_on, start_label="任职开始", end_label="任职结束"
    )
    normalized_employment_status = _normalize_employment_status(employment_status)
    generated_password = None
    password = str(temporary_password or "")
    if not password:
        generated_password = secrets.token_urlsafe(15)
        password = generated_password
    _validate_password(password)
    password_hash = hash_password(password)
    phone_value = _clean_text(phone)
    if not phone_value:
        raise ValueError("手机号不能为空")
    phone_fields = protected_phone(phone_value)
    now = datetime.now(UTC).isoformat()
    source_reference = "IAM2_STAFF_MANAGEMENT"

    with transaction() as connection:
        if execute(connection, "SELECT id FROM app_users WHERE username=?", (username,)).fetchone():
            raise ValueError("登录账号已经存在，请更换账号")
        institution = _institution(
            connection, institution_id, require_business_catalog=True
        )
        supervisor_id = _supervisor(connection, supervisor_user_id)
        normalized_grants = (
            _normalize_grants(connection, grants, allow_empty=False)
            if grants is not None
            else _auto_grants(
                connection,
                positions,
                responsibility_org_unit_id,
                responsibility_scope_type,
                institution=institution,
                actor_user_id=actor_user_id,
            )
        )
        _validate_actor_grants(actor_user_id, normalized_grants)
        basis = str(authorization_basis or "").strip() or "SYSTEM_AUTO:POSITION_SCOPE_MAPPING"
        reason = str(authorization_reason or "").strip()
        member_match = _resolve_existing_member_person(
            connection, name=staff_name, phone_hash_value=phone_fields["phone_hash"]
        )
        member_id: int | None = None
        person_reused = False
        if member_match:
            member_id, person_id = member_match
            person_reused = bool(person_id)
            if person_id:
                profile = execute(
                    connection,
                    "SELECT status FROM person_profiles WHERE id=?",
                    (person_id,),
                ).fetchone()
                if not profile or profile["status"] != "ACTIVE":
                    raise ValueError("已有学员身份的自然人记录不可用，请先人工核对")
            else:
                person_id = f"person-{uuid4()}"
                execute(
                    connection,
                    "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                    "VALUES (?, ?, 'ACTIVE', ?, ?)",
                    (person_id, staff_name, now, now),
                )
                execute(
                    connection,
                    "INSERT INTO member_identities(member_id, person_id, status, "
                    "source_reference, created_at, updated_at) VALUES (?, ?, 'ACTIVE', ?, ?, ?)",
                    (member_id, person_id, source_reference, now, now),
                )
        else:
            person_id = f"person-{uuid4()}"
            execute(
                connection,
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (person_id, staff_name, now, now),
            )
        if execute(
            connection,
            "SELECT person_id FROM employee_profile_details WHERE work_phone_hash=?",
            (phone_fields["phone_hash"],),
        ).fetchone():
            raise ValueError("该手机号已绑定其他工作人员，请核对")
        cursor = execute(
            connection,
            "INSERT INTO app_users(username, display_name, password_hash, is_active, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, staff_name, password_hash, 1 if is_active else 0, now, now),
        )
        user_id = int(cursor.lastrowid)
        execute(
            connection,
            "INSERT INTO account_person_links"
            "(user_id, person_id, linked_at, linked_by, source_reference) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, person_id, now, actor_user_id, source_reference),
        )
        execute(
            connection,
            "INSERT INTO employee_profile_details"
            "(person_id, work_phone_ciphertext, work_phone_hash, work_phone_last4, "
            "work_phone_masked, gender, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person_id,
                phone_fields["phone_ciphertext"] if phone_fields else None,
                phone_fields["phone_hash"] if phone_fields else None,
                phone_fields["phone_last4"] if phone_fields else None,
                phone_fields["phone_masked"] if phone_fields else None,
                normalized_gender,
                now,
                now,
            ),
        )
        cursor = execute(
            connection,
            "INSERT INTO operations_employments"
            "(person_id, institution_id, department_name, supervisor_user_id, employment_status, "
            "started_on, ended_on, source_reference, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person_id,
                institution_id,
                _clean_text(department_name),
                supervisor_id,
                normalized_employment_status,
                employment_start,
                employment_end,
                source_reference,
                now,
                now,
            ),
        )
        employment_id = int(cursor.lastrowid)
        for position_key in positions:
            execute(
                connection,
                "INSERT INTO operations_position_assignments"
                "(employment_id, position_key, valid_from, valid_until, status, "
                "source_reference, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employment_id,
                    position_key,
                    None,
                    None,
                    "ACTIVE",
                    source_reference,
                    now,
                    now,
                ),
            )
        grant_ids = _insert_grants(
            connection,
            employment_id=employment_id,
            grants=normalized_grants,
            actor_user_id=actor_user_id,
            source_reference=source_reference,
            now=now,
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="iam2.staff.create",
            resource_type="operations_employment",
            resource_id=str(employment_id),
            purpose=reason or basis,
            after={
                "user_id": user_id,
                "login_account_masked": mask_login_identifier(username),
                "institution_id": institution_id,
                "department_name": _clean_text(department_name),
                "position_keys": positions,
                "employment_status": normalized_employment_status,
                "started_on": employment_start,
                "ended_on": employment_end,
                "authorization_grants": normalized_grants,
                "authorization_basis": basis,
                "grant_ids": grant_ids,
                "account_active": bool(is_active),
                "person_reused": person_reused,
                "linked_member_id": member_id,
            },
        )
    return {
        "id": user_id,
        "employment_id": employment_id,
        "temporary_password": generated_password,
        "person_reused": person_reused,
        "linked_member_id": member_id,
    }


def _grant_identity(grant: dict[str, Any]) -> tuple[str, str, str]:
    return (grant["role_key"], grant["org_unit_id"], grant["scope_type"])


def _grant_changed(current: dict[str, Any], desired: dict[str, Any]) -> bool:
    return any(
        current.get(key) != desired.get(key)
        for key in ("valid_from", "valid_until", "status")
    )


def _safe_change_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item["name"],
        "login_account": item["login_account"],
        "is_active": item["is_active"],
        "institution_name": item["institution_name"],
        "department_name": item.get("department_name"),
        "supervisor_name": item.get("supervisor_name"),
        "employment_status": item.get("employment_status"),
        "started_on": item.get("started_on"),
        "ended_on": item.get("ended_on"),
        "positions": [
            {"position_key": row["position_key"], "position_name": row["position_name"]}
            for row in item["positions"]
        ],
        "authorization_grants": item["authorization_grants"],
        "authorization_mode": item["authorization_mode"],
    }


def _desired_snapshot(
    connection: Any,
    current: dict[str, Any],
    payload: dict[str, Any],
    *,
    actor_user_id: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    positions = _normalize_positions(payload.get("position_keys") or [])
    institution_id = str(payload.get("institution_id") or current["institution_id"])
    institution = _institution(
        connection, institution_id, require_business_catalog=True
    )
    started_on, ended_on = _validate_interval(
        payload.get("started_on") if "started_on" in payload else current.get("started_on"),
        payload.get("ended_on") if "ended_on" in payload else current.get("ended_on"),
        start_label="任职开始",
        end_label="任职结束",
    )
    supervisor_id = _supervisor(
        connection,
        payload["supervisor_user_id"]
        if "supervisor_user_id" in payload
        else current.get("supervisor_user_id"),
        current["_id"],
    )
    existing_grants = {
        _grant_identity(grant): grant
        for grant in _grant_rows(int(current["_employment_id"]), current_only=True)
    }
    if "grants" in payload and payload.get("grants") is not None:
        normalized_grants = _normalize_grants(
            connection,
            payload.get("grants") or [],
            allow_empty=True,
            historical_grants=existing_grants,
        )
    elif (
        "responsibility_org_unit_id" in payload
        or "responsibility_scope_type" in payload
    ):
        normalized_grants = _auto_grants(
            connection,
            positions,
            payload.get("responsibility_org_unit_id"),
            payload.get("responsibility_scope_type"),
            institution=institution,
            actor_user_id=actor_user_id,
        )
    else:
        normalized_grants = list(existing_grants.values())
    _validate_actor_grants(actor_user_id, normalized_grants)
    return (
        {
            "name": str(payload.get("name") or current["name"]).strip(),
            "login_account_raw": str(
                payload.get("login_account") or current["_login_username"]
            ).strip(),
            "is_active": bool(
                payload["is_active"] if "is_active" in payload else current["is_active"]
            ),
            "institution_id": institution_id,
            "institution_name": institution["name"],
            "department_name": _clean_text(
                payload["department_name"]
                if "department_name" in payload
                else current.get("department_name")
            ),
            "supervisor_user_id": supervisor_id,
            "started_on": started_on,
            "ended_on": ended_on,
            "employment_status": _normalize_employment_status(
                payload.get("employment_status")
                if "employment_status" in payload
                else current.get("employment_status")
            ),
            "positions": positions,
        },
        normalized_grants,
    )


def preview_staff_update(
    actor_user_id: int,
    user_id: int,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _read_gate()
    rows = _employment_rows(user_id=user_id)
    if not rows:
        raise ValueError("专职人员不存在或当前没有有效任职")
    raw = rows[0]
    current = _staff_item(raw)
    current["_id"] = user_id
    _assert_staff_item_in_actor_scope(actor_user_id, current)
    with transaction() as connection:
        desired, normalized_grants = _desired_snapshot(
            connection, current, payload, actor_user_id=actor_user_id
        )
    current_grants = _grant_rows(int(current["_employment_id"]), current_only=True)
    current_grants_by_key = {
        _grant_identity(grant): grant for grant in current_grants
    }
    current_keys = set(current_grants_by_key)
    desired_keys = {_grant_identity(grant) for grant in normalized_grants}
    added = [grant for grant in normalized_grants if _grant_identity(grant) not in current_keys]
    changed = [
        (current_grants_by_key[_grant_identity(grant)], grant)
        for grant in normalized_grants
        if _grant_identity(grant) in current_grants_by_key
        and _grant_changed(current_grants_by_key[_grant_identity(grant)], grant)
    ]
    removed = [
        _mask_grant(grant)
        for grant in current_grants
        if _grant_identity(grant) not in desired_keys
    ]
    # A changed active sensitive authorization can enlarge access just as much
    # as a new role-scope pair, so it requires the same reason.
    sensitive_expansion = any(
        _role_risk(grant["role_key"]) for grant in added
    ) or any(_role_risk(after["role_key"]) for _, after in changed)
    after = {
        "name": desired["name"],
        "login_account": mask_login_identifier(desired["login_account_raw"]),
        "is_active": desired["is_active"],
        "institution_name": desired["institution_name"],
        "department_name": desired["department_name"],
        "supervisor_user_id": desired["supervisor_user_id"],
        "employment_status": desired["employment_status"],
        "started_on": desired["started_on"],
        "ended_on": desired["ended_on"],
        "positions": [
            {"position_key": key, "position_name": POSITION_NAMES[key]}
            for key in desired["positions"]
        ],
        "authorization_grants": [_mask_grant(grant) for grant in normalized_grants],
        "authorization_mode": "EXPLICIT" if normalized_grants else "UNCONFIGURED",
    }
    return {
        "before": _safe_change_snapshot(current),
        "after": after,
        "diff": {
            "added_grants": [_mask_grant(grant) for grant in added],
            "removed_grants": removed,
            "changed_grants": [
                {"before": _mask_grant(before), "after": _mask_grant(after)}
                for before, after in changed
            ],
            "added_position_keys": sorted(
                set(desired["positions"])
                - {position["position_key"] for position in current["positions"]}
            ),
            "removed_position_keys": sorted(
                {position["position_key"] for position in current["positions"]}
                - set(desired["positions"])
            ),
        },
        "sensitive_expansion": sensitive_expansion,
        "requires_business_reason": sensitive_expansion,
    }


def update_staff(
    actor_user_id: int,
    user_id: int,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _write_gate()
    preview = preview_staff_update(actor_user_id, user_id, payload=payload)
    rows = _employment_rows(user_id=user_id)
    if not rows:
        raise ValueError("专职人员不存在或当前没有有效任职")
    current = _staff_item(rows[0])
    current["_id"] = user_id
    _assert_staff_item_in_actor_scope(actor_user_id, current)
    now = datetime.now(UTC).isoformat()
    source_reference = "IAM2_STAFF_MANAGEMENT"
    with transaction() as connection:
        desired, grants = _desired_snapshot(
            connection, current, payload, actor_user_id=actor_user_id
        )
        if not desired["name"]:
            raise ValueError("姓名不能为空")
        if len(desired["login_account_raw"]) < 3:
            raise ValueError("登录账号至少填写 3 个字符")
        basis = (
            str(payload.get("authorization_basis") or "").strip()
            or "SYSTEM_AUTO:STAFF_BUSINESS_UPDATE"
        )
        reason = str(payload.get("authorization_reason") or "").strip()
        duplicate = execute(
            connection,
            "SELECT id FROM app_users WHERE username=? AND id<>?",
            (desired["login_account_raw"], user_id),
        ).fetchone()
        if duplicate:
            raise ValueError("登录账号已被使用")
        gender = _clean_text(payload.get("gender"))
        if gender not in VALID_GENDERS:
            raise ValueError("性别必须选择男或女")
        profile_exists = execute(
            connection,
            "SELECT 1 FROM employee_profile_details WHERE person_id=?",
            (current["_person_id"],),
        ).fetchone()
        if not profile_exists:
            execute(
                connection,
                "INSERT INTO employee_profile_details(person_id, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (current["_person_id"], now, now),
            )
        if payload.get("replace_phone"):
            phone_value = _clean_text(payload.get("phone"))
            if not phone_value:
                raise ValueError("手机号不能为空")
            phone_fields = protected_phone(phone_value)
            duplicate_phone = execute(
                connection,
                "SELECT person_id FROM employee_profile_details "
                "WHERE work_phone_hash=? AND person_id<>?",
                (phone_fields["phone_hash"], current["_person_id"]),
            ).fetchone()
            if duplicate_phone:
                raise ValueError("该手机号已绑定其他工作人员，请核对")
            execute(
                connection,
                "UPDATE employee_profile_details SET work_phone_ciphertext=?, work_phone_hash=?, "
                "work_phone_last4=?, work_phone_masked=?, gender=?, updated_at=? WHERE person_id=?",
                (
                    phone_fields["phone_ciphertext"],
                    phone_fields["phone_hash"],
                    phone_fields["phone_last4"],
                    phone_fields["phone_masked"],
                    gender,
                    now,
                    current["_person_id"],
                ),
            )
        else:
            execute(
                connection,
                "UPDATE employee_profile_details SET gender=?, updated_at=? WHERE person_id=?",
                (gender, now, current["_person_id"]),
            )
        account_changed = (
            bool(current["is_active"]) != desired["is_active"]
            or current["_login_username"] != desired["login_account_raw"]
        )
        execute(
            connection,
            "UPDATE app_users SET username=?, display_name=?, is_active=?, "
            "token_version=token_version+?, updated_at=? WHERE id=?",
            (
                desired["login_account_raw"],
                desired["name"],
                1 if desired["is_active"] else 0,
                1 if account_changed else 0,
                now,
                user_id,
            ),
        )
        if account_changed:
            execute(
                connection,
                "UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )
        execute(
            connection,
            "UPDATE person_profiles SET display_name=?, updated_at=? WHERE id=?",
            (desired["name"], now, current["_person_id"]),
        )
        execute(
            connection,
            "UPDATE operations_employments SET institution_id=?, department_name=?, "
            "supervisor_user_id=?, employment_status=?, started_on=?, ended_on=?, "
            "updated_at=? WHERE id=?",
            (
                desired["institution_id"],
                desired["department_name"],
                desired["supervisor_user_id"],
                desired["employment_status"],
                desired["started_on"],
                desired["ended_on"],
                now,
                current["_employment_id"],
            ),
        )
        existing_positions = _position_rows(int(current["_employment_id"]), current_only=True)
        desired_position_keys = set(desired["positions"])
        existing_position_keys = {row["position_key"] for row in existing_positions}
        for position in existing_positions:
            if position["position_key"] not in desired_position_keys:
                execute(
                    connection,
                    "UPDATE operations_position_assignments SET status='ENDED', "
                    "valid_until=?, updated_at=? WHERE id=?",
                    (now, now, position["id"]),
                )
        for position_key in desired_position_keys - existing_position_keys:
            execute(
                connection,
                "INSERT INTO operations_position_assignments"
                "(employment_id, position_key, valid_from, valid_until, status, "
                "source_reference, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    current["_employment_id"],
                    position_key,
                    None,
                    None,
                    "ACTIVE",
                    source_reference,
                    now,
                    now,
                ),
            )
        existing_grants = _grant_rows(int(current["_employment_id"]), current_only=True)
        existing_grants_by_key = {_grant_identity(grant): grant for grant in existing_grants}
        desired_grants_by_key = {_grant_identity(grant): grant for grant in grants}
        for key, grant in existing_grants_by_key.items():
            if key not in desired_grants_by_key:
                execute(
                    connection,
                    "UPDATE employee_authorization_grants SET status='REVOKED', updated_at=? "
                    "WHERE id=?",
                    (now, grant["id"]),
                )
            else:
                wanted = desired_grants_by_key[key]
                execute(
                    connection,
                    "UPDATE employee_authorization_grants SET valid_from=?, valid_until=?, "
                    "status=?, updated_at=? WHERE id=?",
                    (
                        wanted["valid_from"],
                        wanted["valid_until"],
                        wanted["status"],
                        now,
                        grant["id"],
                    ),
                )
        added_grants = [
            grant for key, grant in desired_grants_by_key.items() if key not in existing_grants_by_key
        ]
        _insert_grants(
            connection,
            employment_id=int(current["_employment_id"]),
            grants=added_grants,
            actor_user_id=actor_user_id,
            source_reference=source_reference,
            now=now,
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="iam2.staff.update",
            resource_type="operations_employment",
            resource_id=str(current["_employment_id"]),
            purpose=reason or basis,
            before=preview["before"],
            after={
                **preview["after"],
                "authorization_basis": basis,
                "sessions_revoked": account_changed,
            },
        )
    return {
        "id": user_id,
        "sessions_revoked": account_changed,
        "preview": preview,
    }


def authorization_migration_preview(actor_user_id: int) -> list[dict[str, Any]]:
    """Produce a no-write, evidence-first legacy authorization conversion preview."""
    _read_gate()
    del actor_user_id
    rows = fetch_all(
        "SELECT oe.id AS employment_id, oe.person_id, u.id AS user_id, u.display_name "
        "FROM operations_employments oe "
        "LEFT JOIN account_person_links apl ON apl.person_id=oe.person_id "
        "LEFT JOIN app_users u ON u.id=apl.user_id "
        "WHERE oe.employment_status='ACTIVE' "
        "AND NOT EXISTS (SELECT 1 FROM employee_authorization_grants eag "
        "WHERE eag.employment_id=oe.id) ORDER BY u.display_name, oe.id",
        (),
    )
    preview: list[dict[str, Any]] = []
    for row in rows:
        employment_id = int(row["employment_id"])
        positions = _position_rows(employment_id)
        scopes = _service_scope_rows(employment_id)
        candidate_grants = [
            {
                "role_key": position["position_key"],
                "org_unit_id": scope["org_unit_id"],
                "org_name": scope["org_name"],
                "scope_type": scope["scope_type"],
                "valid_from": None,
                "valid_until": None,
                "status": "ACTIVE",
            }
            for position in positions
            for scope in scopes
        ]
        raw_candidate_count = len(candidate_grants)
        candidate_grants = [
            grant
            for index, grant in enumerate(candidate_grants)
            if _grant_identity(grant) not in {
                _grant_identity(previous) for previous in candidate_grants[:index]
            }
        ]
        position_permissions = {
            permission
            for position in positions
            for permission in ROLE_PERMISSIONS.get(position["position_key"], set())
        }
        proposed_permissions = {
            permission
            for grant in candidate_grants
            for permission in ROLE_PERMISSIONS.get(grant["role_key"], set())
        }
        status = "SAFE_TO_MIGRATE"
        blockers: list[str] = []
        if row.get("user_id") is None:
            status = "REVIEW_REQUIRED"
            blockers.append("未关联可登录账号")
        if not positions:
            status = "REVIEW_REQUIRED"
            blockers.append("没有当前有效岗位")
        if not scopes:
            status = "REVIEW_REQUIRED"
            blockers.append("没有当前有效服务责任范围")
        if position_permissions != proposed_permissions:
            status = "REVIEW_REQUIRED"
            blockers.append("拟生成角色权限与当前岗位模板不等价")
        preview.append(
            {
                "employment_id": employment_id,
                "user_id": row.get("user_id"),
                "name": row.get("display_name") or "未关联账号人员",
                "current_positions": [
                    {
                        "position_key": position["position_key"],
                        "position_name": position["position_name"],
                    }
                    for position in positions
                ],
                "current_permissions": sorted(position_permissions),
                "current_service_scopes": [
                    {
                        "org_unit_id": scope["org_unit_id"],
                        "org_name": scope["org_name"],
                        "scope_type": scope["scope_type"],
                    }
                    for scope in scopes
                ],
                "proposed_authorization_grants": [_mask_grant(grant) for grant in candidate_grants],
                "proposed_permissions": sorted(proposed_permissions),
                "deduplicated_grant_count": raw_candidate_count - len(candidate_grants),
                "permission_diff": {
                    "added": sorted(proposed_permissions - position_permissions),
                    "removed": sorted(position_permissions - proposed_permissions),
                },
                "status": status,
                "blockers": blockers,
                "write_action": "PREVIEW_ONLY",
            }
        )
    return preview
