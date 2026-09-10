from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from app.core.privacy import mask_login_identifier
from app.core.security import (
    create_token,
    hash_password,
    token_hash,
    verify_password,
)
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit


PERMISSIONS = {
    "iam:manage": ("账户、角色与范围管理", "SENSITIVE"),
    "staff:manage": ("管理普通专职人员", "SENSITIVE"),
    "org:read": ("查看组织", "INTERNAL"),
    "org:manage": ("维护组织", "SENSITIVE"),
    "plans:read": ("查看年度MP", "INTERNAL"),
    "plans:credit_rules_manage": ("维护学习计划课程积分标准", "SENSITIVE"),
    "plans:credit_settlement_preview": ("预览学分结算与对账", "INTERNAL"),
    "plans:credit_settlement_manage": ("正式结算与学分冲销", "SENSITIVE"),
    "plans:business_calendar_manage": ("维护年度工作日日历", "SENSITIVE"),
    "plans:credit_activity_fact_manage": ("维护每日读书与优秀分享事实", "SENSITIVE"),
    "plans:hq_reading_import_manage": ("导入总部每日读书并进行身份核验", "SENSITIVE"),
    "study_meetings:courses_edit": ("修正已提交学习会课程", "SENSITIVE"),
    "study_meetings:attendees_edit": ("修正已提交学习会参加人员", "SENSITIVE"),
    "plans:period_write": ("维护本区域年度MP", "SENSITIVE"),
    "plans:import_global": ("全局导入年度MP", "RESTRICTED"),
    "plans:publish": ("发布年度方案", "SENSITIVE"),
    "members:read": ("查看学长", "INTERNAL"),
    "members:manage": ("维护学长主数据", "SENSITIVE"),
    "members:detail_view": ("查看学长基本资料(脱敏)", "INTERNAL"),
    "members:enterprise_view": ("按用途查看完整企业敏感资料", "RESTRICTED"),
    "followups:manage": ("管理关怀任务", "SENSITIVE"),
    "renewals:read": ("查看续费运营", "INTERNAL"),
    "renewals:manage": ("管理续费周期与导入", "SENSITIVE"),
    "contact:reveal": ("按任务逐人查看联系方式", "SENSITIVE"),
    "exports:normal": ("普通脱敏导出", "INTERNAL"),
    "exports:sensitive": ("敏感导出", "RESTRICTED"),
    "audit:read": ("查看审计", "SENSITIVE"),
    "integrations:manage": ("管理数据集成", "SENSITIVE"),
    "attendance:sync": ("同步签到出勤数据", "SENSITIVE"),
    "attendance:adjudicate": ("出勤裁定", "SENSITIVE"),
    "enrollment:read": ("查看新学长入塾申请", "INTERNAL"),
    "enrollment:unassigned_review": ("查看未分配组织的入塾申请", "SENSITIVE"),
    "enrollment:review": ("审核新学长入塾申请", "SENSITIVE"),
    "enrollment:payment_confirm": ("确认入塾申请收款", "SENSITIVE"),
    "enrollment:enroll": ("将已完成申请正式入塾", "SENSITIVE"),
    "enrollment:manage_link": ("管理公开入塾申请二维码", "SENSITIVE"),
}
PASSWORD_MIN_LENGTH = 6
ROLE_PERMISSIONS = {
    "system_admin": set(PERMISSIONS) - {"exports:sensitive"},
    "technical_admin": {
        "iam:manage", "org:read", "org:manage", "audit:read", "integrations:manage",
    },
    "data_security_admin": {"org:read", "members:read", "exports:sensitive", "audit:read"},
    "operations_admin": {
        "staff:manage",
        "study_meetings:courses_edit",
        "study_meetings:attendees_edit",
        "org:read", "org:manage", "plans:read", "plans:credit_rules_manage", "plans:credit_settlement_preview", "plans:credit_settlement_manage", "plans:business_calendar_manage", "plans:credit_activity_fact_manage", "plans:hq_reading_import_manage", "plans:period_write", "plans:import_global", "plans:publish",
        "members:read", "members:manage", "members:detail_view", "members:enterprise_view",
        "followups:manage", "exports:normal", "audit:read",
        "integrations:manage", "renewals:read", "renewals:manage",
        "attendance:sync", "attendance:adjudicate",
        "enrollment:read", "enrollment:review", "enrollment:payment_confirm",
        "enrollment:enroll", "enrollment:manage_link", "enrollment:unassigned_review",
    },
    "regional_manager": {
        "org:read", "plans:read", "plans:period_write", "members:read",
        "members:manage", "members:detail_view", "followups:manage", "contact:reveal",
        "exports:normal", "renewals:read", "renewals:manage",
        "enrollment:read", "enrollment:review", "enrollment:payment_confirm",
        "enrollment:enroll",
    },
    "class_counselor": {
        "org:read", "plans:read", "members:read", "members:detail_view",
        "followups:manage", "contact:reveal", "exports:normal", "renewals:read",
    },
    "group_leader": {
        "org:read", "plans:read", "members:read", "members:detail_view",
        "followups:manage", "contact:reveal", "renewals:read",
    },
    "read_only": {"org:read", "plans:read", "members:read", "renewals:read"},
    # IAM 2.0 employee roles deliberately use business capabilities rather
    # than a job title. Positions remain in operations_position_assignments.
    # None of these templates receives a C7 settlement permission: C7 keeps
    # its independent role and release gates.
    "employee_operations_lead": {
        "org:read", "org:manage", "plans:read", "plans:period_write",
        "members:read", "members:manage", "members:detail_view",
        "followups:manage", "renewals:read", "renewals:manage",
        "exports:normal", "attendance:sync", "attendance:adjudicate",
        "enrollment:read", "enrollment:review", "enrollment:payment_confirm",
        "enrollment:enroll", "enrollment:manage_link",
    },
    "employee_operations_management": {
        "org:read", "plans:read", "plans:period_write", "members:read",
        "members:detail_view", "followups:manage", "renewals:read",
    },
    "employee_member_management": {
        "org:read", "members:read", "members:manage", "members:detail_view",
        "followups:manage", "exports:normal",
    },
    "employee_learning_management": {
        "org:read", "plans:read", "members:read", "members:detail_view",
        "attendance:adjudicate",
    },
    "employee_development_management": {
        "org:read", "members:read", "members:manage", "members:detail_view",
        "followups:manage", "enrollment:read", "enrollment:review",
        "enrollment:enroll",
    },
    "employee_renewal_management": {
        "org:read", "members:read", "members:detail_view", "renewals:read",
        "renewals:manage",
    },
    "employee_finance_management": {
        "org:read", "renewals:read", "renewals:manage", "enrollment:read",
        "enrollment:payment_confirm",
    },
    "employee_data_management": {
        "org:read", "members:read", "members:detail_view", "exports:normal",
    },
    "employee_administration_management": {
        "org:read", "members:read", "members:detail_view", "followups:manage",
    },
    "ops_center_director": {
        "org:read", "org:manage", "plans:read", "plans:period_write",
        "plans:import_global", "plans:publish", "members:read", "members:manage",
        "members:detail_view", "members:enterprise_view", "followups:manage",
        "renewals:read", "renewals:manage", "exports:normal", "audit:read",
        "integrations:manage", "attendance:sync", "attendance:adjudicate",
        "enrollment:read", "enrollment:review", "enrollment:payment_confirm",
        "enrollment:enroll", "enrollment:manage_link",
    },
    "ops_center_operations": {
        "org:read", "plans:read", "plans:period_write", "members:read",
        "members:manage", "members:detail_view", "followups:manage",
        "renewals:read", "renewals:manage", "exports:normal",
        "enrollment:read", "enrollment:review", "enrollment:payment_confirm",
        "enrollment:enroll",
    },
    "ops_center_learning": {
        "org:read", "plans:read", "plans:credit_rules_manage", "plans:credit_settlement_preview", "plans:credit_settlement_manage", "plans:business_calendar_manage", "plans:credit_activity_fact_manage", "plans:hq_reading_import_manage", "members:read", "members:detail_view",
        "followups:manage", "attendance:adjudicate",
    },
    "ops_center_development": {
        "org:read", "members:read", "members:manage", "members:detail_view",
        "followups:manage", "renewals:read", "renewals:manage", "exports:normal",
        "enrollment:read", "enrollment:review", "enrollment:enroll",
    },
    "ops_center_management": {
        "org:read", "plans:read", "plans:credit_rules_manage", "plans:credit_settlement_preview", "plans:credit_settlement_manage", "plans:business_calendar_manage", "plans:credit_activity_fact_manage", "plans:hq_reading_import_manage", "plans:period_write", "plans:publish",
        "members:read", "audit:read",
    },
    "ops_center_data": {
        "org:read", "org:manage", "plans:read", "plans:import_global",
        "members:read", "members:manage", "members:detail_view", "exports:normal",
        "integrations:manage", "renewals:read", "attendance:sync",
    },
    "ops_center_finance": {
        "org:read", "plans:read", "renewals:read", "renewals:manage",
        "enrollment:read", "enrollment:payment_confirm",
    },
    "ops_center_administration": {
        "org:read", "members:read", "members:detail_view", "followups:manage",
    },
    "volunteer_director": {
        "org:read", "plans:read", "members:read", "members:detail_view",
        "followups:manage", "contact:reveal", "renewals:read",
    },
    "volunteer_regional_lead": {
        "org:read", "plans:read", "plans:period_write", "members:read",
        "members:detail_view", "followups:manage", "contact:reveal",
        "exports:normal", "renewals:read",
    },
    "volunteer_regional_service": {
        "org:read", "members:read", "members:detail_view", "followups:manage",
        "contact:reveal", "renewals:read",
    },
    "volunteer_class_counselor": {
        "org:read", "plans:read", "members:read", "members:detail_view",
        "followups:manage", "contact:reveal", "renewals:read",
    },
    "volunteer_class_committee": {
        "org:read", "members:read", "members:detail_view", "followups:manage",
        "contact:reveal", "renewals:read",
    },
    "volunteer_group_leader": {
        "org:read", "members:read", "members:detail_view", "followups:manage",
        "contact:reveal", "renewals:read",
    },
    "volunteer_group_committee": {
        "org:read", "members:read", "members:detail_view", "followups:manage",
        "contact:reveal",
    },
    "volunteer_activity": {
        "org:read", "members:read", "members:detail_view",
    },
}
# The lead position is a business administrator, not a technical or security
# administrator. Keep its role key independent for audit and scope assignment,
# while giving it the same business capability template as operations_admin.
ROLE_PERMISSIONS["employee_operations_lead"] = set(
    ROLE_PERMISSIONS["operations_admin"]
)
ROLE_NAMES = {
    "system_admin": "系统管理员",
    "technical_admin": "系统技术管理员",
    "data_security_admin": "数据安全管理员（最高权限）",
    "operations_admin": "苏州塾运营管理员",
    "regional_manager": "区域分中心负责人/理事",
    "class_counselor": "班主任/辅导员/班委",
    "group_leader": "组长/组委",
    "read_only": "只读观察员",
    "employee_operations_lead": "运营中心负责人",
    "employee_operations_management": "运营管理",
    "employee_member_management": "学员管理",
    "employee_learning_management": "学习践行管理",
    "employee_development_management": "发展建设管理",
    "employee_renewal_management": "续费管理",
    "employee_finance_management": "财务管理",
    "employee_data_management": "数据管理",
    "employee_administration_management": "行政管理",
    "ops_center_director": "运营中心负责人",
    "ops_center_operations": "分中心运营专员",
    "ops_center_learning": "学习践行专员",
    "ops_center_development": "发展建设专员",
    "ops_center_management": "运营管理专员",
    "ops_center_data": "数据中心专员",
    "ops_center_finance": "财务专员",
    "ops_center_administration": "行政专员",
    "volunteer_director": "理事志工",
    "volunteer_regional_lead": "三级分中心负责人志工",
    "volunteer_regional_service": "三级分中心志工",
    "volunteer_class_counselor": "班主任志工",
    "volunteer_class_committee": "班委志工",
    "volunteer_group_leader": "组长志工",
    "volunteer_group_committee": "组委志工",
    "volunteer_activity": "专项活动志工",
}

# These keys are the only business roles selectable through the ordinary
# staff-management drawer. Legacy position templates remain available only for
# compatibility previews, while SYSTEM/RESTRICTED roles stay outside it.
EMPLOYEE_ASSIGNABLE_ROLE_KEYS = frozenset(
    {
        "employee_operations_lead",
        "employee_operations_management",
        "employee_member_management",
        "employee_learning_management",
        "employee_development_management",
        "employee_renewal_management",
        "employee_finance_management",
        "employee_data_management",
        "employee_administration_management",
        "read_only",
    }
)

# A named volunteer capability is never retained merely because an old account
# role still exists. In the identity-authorized model it must come from a
# current appointment that is directly linked to an in-roster member.
# ``regional_manager`` / ``class_counselor`` / ``group_leader`` intentionally
# remain legacy general-purpose role templates: older staff and scoped access
# accounts use them, while every new volunteer source uses a ``volunteer_*``
# key and the formal member-post path below.
VOLUNTEER_ROLE_KEYS = frozenset(
    {
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
)

POSITION_NAMES = {
    "operations_admin": "运营管理员（兼容岗位）",
    "ops_center_director": "运营中心负责人",
    "ops_center_operations": "运营专员",
    "ops_center_learning": "学习践行专员",
    "ops_center_development": "发展建设专员",
    "ops_center_management": "运营管理专员",
    "ops_center_data": "数据专员",
    "ops_center_finance": "财务专员",
    "ops_center_administration": "行政专员",
}

# Direct legacy role assignment remains available for expert workflows, but
# system-level and RESTRICTED-capability templates must never be delegated by
# an ordinary IAM operator. The regular staff drawer does not expose these
# keys at all; this guard also protects the lower-level legacy endpoint.
HIGHEST_ADMIN_ROLE_KEYS = frozenset({"system_admin"})
SYSTEM_OR_RESTRICTED_ROLE_KEYS = frozenset(
    {
        "system_admin",
        "technical_admin",
        "data_security_admin",
        "operations_admin",
        *(
            role_key
            for role_key, permissions in ROLE_PERMISSIONS.items()
            if any(PERMISSIONS[permission][1] == "RESTRICTED" for permission in permissions)
        ),
    }
)


# A FastAPI endpoint already knows the requested permission before its service
# calls resolve an organization scope. Keeping it in a ContextVar lets the
# established service layer become role-scope-aware without a broad and risky
# signature change. Direct service calls retain the legacy aggregate behavior
# unless they explicitly pass a permission.
_request_permission: ContextVar[str | None] = ContextVar(
    "iam_request_permission", default=None
)

# A mini-program worker is authenticated by a person-level WeChat binding,
# not by issuing the browser's backend JWT into WeChat.  The temporary context
# below lets existing business services evaluate the exact IAM2 grant selected
# for the mobile action without creating a second mobile role/scope model.
_mobile_iam_principal: ContextVar[dict | None] = ContextVar(
    "iam_mobile_principal", default=None
)


def set_request_permission(permission: str) -> None:
    _request_permission.set(permission)


def clear_request_permission() -> None:
    _request_permission.set(None)


def current_request_permission() -> str | None:
    return _request_permission.get()


def current_mobile_iam_principal() -> dict | None:
    return _mobile_iam_principal.get()


def clear_mobile_iam_principal() -> None:
    _mobile_iam_principal.set(None)


@contextmanager
def mobile_iam_context(principal: dict, permission: str | None = None):
    """Apply one verified employee's IAM2 grants to an existing service call."""

    principal_token = _mobile_iam_principal.set(principal)
    permission_token = _request_permission.set(permission)
    try:
        yield
    finally:
        _request_permission.reset(permission_token)
        _mobile_iam_principal.reset(principal_token)


def resolve_employee_mobile_principal(
    person_id: str,
    *,
    verified_user_id: int | None,
) -> dict | None:
    """Resolve a staff mobile identity strictly from live IAM2 facts.

    A verified backend account is mandatory.  Employment dates and grant dates
    remain archive fields here, matching the current IAM2 staff semantics;
    account state, employment state and explicit grant state are the gates.
    """

    if not person_id or not verified_user_id:
        return None
    user = fetch_one(
        "SELECT u.id, u.display_name, u.token_version, apl.person_id "
        "FROM app_users u JOIN account_person_links apl ON apl.user_id=u.id "
        "JOIN person_profiles p ON p.id=apl.person_id "
        "WHERE u.id=? AND apl.person_id=? AND u.is_active=1 AND p.status='ACTIVE'",
        (verified_user_id, person_id),
    )
    if not user:
        return None
    employments = fetch_all(
        "SELECT oe.id, oi.name AS institution_name, oe.department_name "
        "FROM operations_employments oe "
        "JOIN operating_institutions oi ON oi.id=oe.institution_id "
        "WHERE oe.person_id=? AND oe.employment_status='ACTIVE' ORDER BY oe.id DESC",
        (person_id,),
    )
    if not employments:
        return None
    employment_ids = [int(row["id"]) for row in employments]
    placeholders = ",".join("?" for _ in employment_ids)
    grants = fetch_all(
        "SELECT eag.id, eag.employment_id, eag.role_key, eag.org_unit_id, eag.scope_type, "
        "eag.valid_from, eag.valid_until, eag.status "
        "FROM employee_authorization_grants eag "
        "JOIN roles r ON r.role_key=eag.role_key AND r.is_active=1 "
        f"WHERE eag.employment_id IN ({placeholders}) AND eag.status='ACTIVE'",
        tuple(employment_ids),
    )
    roles = sorted({row["role_key"] for row in grants})
    permission_rows = (
        fetch_all(
            "SELECT DISTINCT rp.role_key, rp.permission_key FROM role_permissions rp "
            "JOIN roles r ON r.role_key=rp.role_key AND r.is_active=1 "
            f"WHERE rp.role_key IN ({','.join('?' for _ in roles)})",
            tuple(roles),
        )
        if roles
        else []
    )
    permissions_by_role: dict[str, set[str]] = {role: set() for role in roles}
    for item in permission_rows:
        permissions_by_role[item["role_key"]].add(item["permission_key"])
    grants_with_permissions = [
        {**dict(grant), "permissions": sorted(permissions_by_role[grant["role_key"]])}
        for grant in grants
    ]
    return {
        "id": int(user["id"]),
        "user_id": int(user["id"]),
        "person_id": person_id,
        "display_name": user["display_name"],
        "token_version": int(user["token_version"]),
        "roles": roles,
        "permissions": sorted({item["permission_key"] for item in permission_rows}),
        "scopes": grants_with_permissions,
        "authorization_sources": {
            "legacy_roles": [],
            "employment_positions": [],
            "explicit_employee_grants": grants_with_permissions,
            "volunteer_appointments": [],
            "technical_assignments": [],
            "legacy_direct_scopes": [],
            "legacy_employee_scopes": [],
            "volunteer_grants": [],
        },
        "subject_contexts": ["OPERATIONS_EMPLOYEE"],
        "language_context": "OPERATIONS",
        "employments": [dict(item) for item in employments],
    }


def seed_iam() -> None:
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        sqlite = isinstance(connection, sqlite3.Connection)
        for key, (name, level) in PERMISSIONS.items():
            execute(
                connection,
                "INSERT OR IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at) "
                "VALUES (?, ?, ?, ?)" if sqlite else
                "INSERT IGNORE INTO permissions(permission_key, permission_name, sensitive_level, created_at) VALUES (?, ?, ?, ?)",
                (key, name, level, now),
            )
            execute(
                connection,
                "UPDATE permissions SET permission_name=?, sensitive_level=? "
                "WHERE permission_key=?",
                (name, level, key),
            )
        for role_key, role_name in ROLE_NAMES.items():
            execute(
                connection,
                "INSERT OR IGNORE INTO roles(role_key, role_name, is_system, is_active, created_at, updated_at) "
                "VALUES (?, ?, 1, 1, ?, ?)" if sqlite else
                "INSERT IGNORE INTO roles(role_key, role_name, is_system, is_active, created_at, updated_at) VALUES (?, ?, 1, 1, ?, ?)",
                (role_key, role_name, now, now),
            )
            execute(
                connection,
                "UPDATE roles SET role_name=?, is_system=1, is_active=1, updated_at=? "
                "WHERE role_key=?",
                (role_name, now, role_key),
            )
            # System role definitions are authoritative: remove stale grants
            # before applying the current least-privilege mapping.
            execute(
                connection,
                "DELETE FROM role_permissions WHERE role_key=?",
                (role_key,),
            )
            for permission in ROLE_PERMISSIONS[role_key]:
                execute(
                    connection,
                    "INSERT OR IGNORE INTO role_permissions(role_key, permission_key) VALUES (?, ?)"
                    if sqlite else
                    "INSERT IGNORE INTO role_permissions(role_key, permission_key) VALUES (?, ?)",
                    (role_key, permission),
                )

        root = execute(connection, "SELECT id FROM org_units WHERE unit_code='SZ_ROOT'").fetchone()
        if not root:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES ('org-suzhou', 'SZ_ROOT', '苏州塾', 'ROOT', NULL, 1, ?, ?)",
                (now, now),
            )
        # A fresh test/bootstrap database can create the stable Suzhou root
        # after migration 0056 has run.  When the Jiangnan root is already
        # present, restore the canonical parentage by stable ID; never infer
        # this relationship from a display name or replace the existing row.
        execute(
            connection,
            "UPDATE org_units SET parent_id='org-jiangnan', updated_at=? "
            "WHERE id='org-suzhou' AND unit_code='SZ_ROOT' AND is_active=1 "
            "AND parent_id IS NULL AND EXISTS "
            "(SELECT 1 FROM org_units WHERE id='org-jiangnan' AND is_active=1)",
            (now,),
        )
        # 0011 may have run before the baseline root was imported. Reassert
        # the formal stable-code mapping at bootstrap without matching names.
        execute(
            connection,
            "INSERT OR IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at) "
            "SELECT oi.id, ou.id, 'LEGACY_REPRESENTATION', ? "
            "FROM operating_institutions oi JOIN org_units ou ON ou.unit_code='SZ_ROOT' "
            "WHERE oi.institution_code='SUZHOU_CENTER' AND oi.is_active=1 AND ou.is_active=1"
            if sqlite else
            "INSERT IGNORE INTO institution_org_links(institution_id, org_unit_id, link_type, created_at) "
            "SELECT oi.id, ou.id, 'LEGACY_REPRESENTATION', ? "
            "FROM operating_institutions oi JOIN org_units ou ON ou.unit_code='SZ_ROOT' "
            "WHERE oi.institution_code='SUZHOU_CENTER' AND oi.is_active=1 AND ou.is_active=1",
            (now,),
        )
        settings = get_settings()
        existing = execute(
            connection, "SELECT id FROM app_users WHERE username=?", (settings.bootstrap_admin_username,)
        ).fetchone()
        if not existing and settings.bootstrap_admin_password:
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
                "VALUES (?, '系统管理员', ?, 1, ?, ?)",
                (
                    settings.bootstrap_admin_username,
                    hash_password(settings.bootstrap_admin_password),
                    now,
                    now,
                ),
            )
            user_id = cursor.lastrowid
            execute(
                connection,
                "INSERT INTO user_roles(user_id, role_key, created_at) VALUES (?, 'system_admin', ?)",
                (user_id, now),
            )
            execute(
                connection,
                "INSERT INTO data_scope_grants(user_id, scope_type, org_unit_id, created_at) "
                "VALUES (?, 'ALL', NULL, ?)",
                (user_id, now),
            )


def authenticate(username: str, password: str) -> dict | None:
    user = fetch_one(
        "SELECT id, username, display_name, password_hash, token_version, is_active "
        "FROM app_users WHERE username=?",
        (username.strip(),),
    )
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        return None
    settings = get_settings()
    access = create_token(
        user["id"], user["token_version"], "access", timedelta(minutes=settings.access_token_minutes)
    )
    refresh = create_token(
        user["id"], user["token_version"], "refresh", timedelta(days=settings.refresh_token_days)
    )
    now = datetime.now(UTC)
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO refresh_tokens(user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (
                user["id"],
                token_hash(refresh),
                (now + timedelta(days=settings.refresh_token_days)).isoformat(),
                now.isoformat(),
            ),
        )
        execute(
            connection,
            "UPDATE app_users SET last_login_at=?, updated_at=? WHERE id=?",
            (now.isoformat(), now.isoformat(), user["id"]),
        )
        write_audit(
            connection,
            actor_user_id=user["id"],
            action="auth.login",
            resource_type="app_user",
            resource_id=str(user["id"]),
        )
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": settings.access_token_minutes * 60,
    }


def user_context(user_id: int) -> dict | None:
    mobile_principal = current_mobile_iam_principal()
    if mobile_principal and int(mobile_principal["user_id"]) == int(user_id):
        # Deliberately return only the live IAM2 principal while handling a
        # worker mobile request.  Browser/direct roles and legacy employment
        # templates must never be added to a person-bound mobile session.
        return mobile_principal
    user = fetch_one(
        "SELECT id, username, display_name, token_version, is_active FROM app_users WHERE id=?",
        (user_id,),
    )
    if not user or not user["is_active"]:
        return None
    now = datetime.now(UTC).isoformat()
    identity_enabled = get_settings().identity_authorization_enabled
    raw_direct_roles = [
        row["role_key"]
        for row in fetch_all(
            "SELECT ur.role_key FROM user_roles ur JOIN roles r ON r.role_key=ur.role_key "
            "WHERE ur.user_id=? AND r.is_active=1 "
            "AND (ur.valid_from IS NULL OR ur.valid_from<=?) "
            "AND (ur.valid_until IS NULL OR ur.valid_until>=?)",
            (user_id, now, now),
        )
    ]
    # Legacy volunteer roles remain visible in the database for audit and
    # migration preview, but cannot bypass the member-status + current-post
    # rule once the identity model is enabled.
    direct_roles = (
        [role_key for role_key in raw_direct_roles if role_key not in VOLUNTEER_ROLE_KEYS]
        if identity_enabled
        else raw_direct_roles
    )
    # An employment switches to explicit authorization as soon as it has any
    # grant record. The EXISTS check intentionally does not filter by dates or
    # status: an expired/revoked explicit grant must not silently fall back to
    # a job-title permission template.
    legacy_position_rows = fetch_all(
        "SELECT DISTINCT oe.id AS employment_id, pa.position_key FROM account_person_links apl "
        "JOIN operations_employments oe ON oe.person_id=apl.person_id "
        "JOIN operations_position_assignments pa ON pa.employment_id=oe.id "
        "WHERE apl.user_id=? AND oe.employment_status='ACTIVE' "
        "AND pa.status='ACTIVE' "
        "AND NOT EXISTS (SELECT 1 FROM employee_authorization_grants eag "
        "WHERE eag.employment_id=oe.id)",
        (user_id,),
    ) if identity_enabled else []
    position_roles = [row["position_key"] for row in legacy_position_rows]
    explicit_employee_grants = fetch_all(
        "SELECT eag.id, eag.employment_id, eag.role_key, eag.org_unit_id, "
        "eag.scope_type, eag.valid_from, eag.valid_until, eag.status "
        "FROM account_person_links apl "
        "JOIN operations_employments oe ON oe.person_id=apl.person_id "
        "JOIN employee_authorization_grants eag ON eag.employment_id=oe.id "
        "JOIN roles r ON r.role_key=eag.role_key AND r.is_active=1 "
        "WHERE apl.user_id=? AND oe.employment_status='ACTIVE' "
        "AND eag.status='ACTIVE'",
        (user_id,),
    ) if identity_enabled else []
    explicit_roles = [row["role_key"] for row in explicit_employee_grants]
    volunteer_grants = fetch_all(
        "SELECT DISTINCT va.id, va.appointment_key AS role_key, va.org_unit_id, "
        "va.scope_type, va.created_at AS valid_from, NULL AS valid_until, va.status "
        "FROM account_person_links apl "
        "JOIN volunteer_appointments va ON va.person_id=apl.person_id "
        "JOIN members m ON m.id=va.member_id "
        "JOIN member_identities mi ON mi.member_id=va.member_id "
        "AND mi.person_id=va.person_id "
        "WHERE apl.user_id=? AND m.status='ACTIVE' AND va.status='ACTIVE' "
        "AND va.volunteer_service_unit_id IS NULL",
        (user_id,),
    ) if identity_enabled else []
    volunteer_roles = [row["role_key"] for row in volunteer_grants]
    technical_roles = [
        "technical_admin"
        for row in fetch_all(
            "SELECT ta.id FROM account_person_links apl "
            "JOIN technical_admin_assignments ta ON ta.person_id=apl.person_id "
            "WHERE apl.user_id=? AND ta.status IN ('PLANNED','ACTIVE') "
            "AND ta.starts_at<=? AND ta.ends_at>=?",
            (user_id, now, now),
        )
    ] if identity_enabled else []
    roles = sorted(
        set(direct_roles + position_roles + explicit_roles + volunteer_roles + technical_roles)
    )
    user["roles"] = roles
    if roles:
        placeholders = ",".join("?" for _ in roles)
        user["permissions"] = [
            row["permission_key"]
            for row in fetch_all(
                "SELECT DISTINCT rp.permission_key FROM role_permissions rp "
                "JOIN roles r ON r.role_key=rp.role_key AND r.is_active=1 "
                f"WHERE rp.role_key IN ({placeholders})",
                tuple(roles),
            )
        ]
    else:
        user["permissions"] = []
    direct_scopes = fetch_all(
        "SELECT scope_type, org_unit_id, valid_from, valid_until FROM data_scope_grants "
        "WHERE user_id=? AND (valid_from IS NULL OR valid_from<=?) "
        "AND (valid_until IS NULL OR valid_until>=?)",
        (user_id, now, now),
    )
    legacy_employment_scopes = fetch_all(
        "SELECT esr.scope_type, esr.org_unit_id, esr.valid_from, esr.valid_until "
        "FROM account_person_links apl "
        "JOIN operations_employments oe ON oe.person_id=apl.person_id "
        "JOIN employee_service_responsibilities esr ON esr.employment_id=oe.id "
        "WHERE apl.user_id=? AND oe.employment_status='ACTIVE' "
        "AND esr.status='ACTIVE' "
        "AND NOT EXISTS (SELECT 1 FROM employee_authorization_grants eag "
        "WHERE eag.employment_id=oe.id)",
        (user_id,),
    ) if identity_enabled else []
    unique_scopes: dict[tuple, dict] = {}
    for scope in (
        direct_scopes
        + legacy_employment_scopes
        + volunteer_grants
        + explicit_employee_grants
    ):
        key = (
            scope["scope_type"], scope.get("org_unit_id"),
            str(scope.get("valid_from")), str(scope.get("valid_until")),
        )
        unique_scopes[key] = scope
    user["scopes"] = list(unique_scopes.values())
    subjects: list[str] = []
    if position_roles or explicit_employee_grants:
        subjects.append("OPERATIONS_EMPLOYEE")
    if volunteer_roles:
        subjects.append("VOLUNTEER")
    if technical_roles:
        subjects.append("TECHNICAL_ADMIN")
    if identity_enabled and fetch_one(
        "SELECT m.id FROM account_person_links apl "
        "JOIN member_identities mi ON mi.person_id=apl.person_id "
        "JOIN members m ON m.id=mi.member_id "
        "WHERE apl.user_id=? AND m.status='ACTIVE' LIMIT 1",
        (user_id,),
    ):
        subjects.append("MEMBER")
    user["subject_contexts"] = subjects
    user["language_context"] = (
        "OPERATIONS" if position_roles or explicit_employee_grants else
        "VOLUNTEER" if volunteer_roles else
        "TECHNICAL" if technical_roles else
        "LEGACY"
    )
    user["authorization_sources"] = {
        "legacy_roles": direct_roles,
        "suppressed_legacy_volunteer_roles": sorted(
            set(raw_direct_roles).intersection(VOLUNTEER_ROLE_KEYS)
        ) if identity_enabled else [],
        "employment_positions": position_roles,
        "explicit_employee_grants": explicit_employee_grants,
        "volunteer_appointments": volunteer_roles,
        "technical_assignments": technical_roles,
        # Kept as named source sets so role+scope evaluation can be performed
        # without treating every current role as valid in every current scope.
        "legacy_direct_scopes": direct_scopes,
        "legacy_employee_scopes": legacy_employment_scopes,
        "volunteer_grants": volunteer_grants,
    }
    return user


def _roles_with_permission(role_keys: list[str], permission: str) -> set[str]:
    if not role_keys:
        return set()
    placeholders = ",".join("?" for _ in role_keys)
    return {
        row["role_key"]
        for row in fetch_all(
            "SELECT DISTINCT rp.role_key FROM role_permissions rp "
            "JOIN roles r ON r.role_key=rp.role_key AND r.is_active=1 "
            f"WHERE rp.permission_key=? AND rp.role_key IN ({placeholders})",
            (permission, *role_keys),
        )
    }


def _expand_scope_rows(scope_rows: list[dict]) -> set[str] | None:
    if any(item.get("scope_type") == "ALL" for item in scope_rows):
        return None
    allowed: set[str] = set()
    for grant in scope_rows:
        org_id = grant.get("org_unit_id")
        if not org_id:
            continue
        allowed.add(org_id)
        if grant["scope_type"] == "SUBTREE":
            rows = fetch_all(
                "WITH RECURSIVE descendants(id) AS ("
                " SELECT id FROM org_units WHERE id=? "
                " UNION ALL SELECT o.id FROM org_units o JOIN descendants d ON o.parent_id=d.id"
                ") SELECT id FROM descendants",
                (org_id,),
            )
            allowed.update(row["id"] for row in rows)
    return allowed


def accessible_org_ids(user_id: int, permission: str | None = None) -> set[str] | None:
    """Resolve organization access, optionally bound to one permission.

    No explicit permission preserves the pre-IAM2 aggregate result for legacy
    direct callers. HTTP request paths obtain their required permission from
    ``require_permission`` automatically, so an explicit employee grant never
    turns into a role-union × scope-union authorization in normal requests.
    """
    mobile_principal = current_mobile_iam_principal()
    if mobile_principal and int(mobile_principal["user_id"]) == int(user_id):
        permission = permission or current_request_permission()
        if not permission or permission not in mobile_principal["permissions"]:
            return set()
        scope_rows = [
            grant
            for grant in mobile_principal["authorization_sources"]["explicit_employee_grants"]
            if permission in grant["permissions"]
        ]
        return _expand_scope_rows(scope_rows)
    context = user_context(user_id)
    if not context:
        return set()
    permission = permission or current_request_permission()
    if not permission:
        return _expand_scope_rows(context["scopes"])
    if permission not in context["permissions"]:
        return set()

    sources = context["authorization_sources"]
    scope_rows: list[dict] = []
    if _roles_with_permission(sources["legacy_roles"], permission):
        scope_rows.extend(sources["legacy_direct_scopes"])
    if _roles_with_permission(sources["employment_positions"], permission):
        scope_rows.extend(sources["legacy_employee_scopes"])
    for grant in sources["explicit_employee_grants"]:
        if _roles_with_permission([grant["role_key"]], permission):
            scope_rows.append(grant)
    for grant in sources["volunteer_grants"]:
        if _roles_with_permission([grant["role_key"]], permission):
            scope_rows.append(grant)
    return _expand_scope_rows(scope_rows)


def permission_allows_org(user_id: int, permission: str, org_unit_id: str) -> bool:
    allowed = accessible_org_ids(user_id, permission)
    return allowed is None or org_unit_id in allowed


def create_user(
    actor_user_id: int,
    *,
    username: str,
    display_name: str,
    password: str,
    roles: list[str],
    scopes: list[dict],
    actor_roles: list[str] | None = None,
) -> int:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 位")
    requested_roles = set(roles)
    # ``None`` is retained for established trusted service callers. Every
    # HTTP call supplies actor roles and therefore receives the hard gate.
    if (
        actor_roles is not None
        and requested_roles.intersection(SYSTEM_OR_RESTRICTED_ROLE_KEYS)
        and not HIGHEST_ADMIN_ROLE_KEYS.intersection(actor_roles)
    ):
        raise PermissionError("只有平台系统管理员可以授予系统或受限角色")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (username.strip(), display_name.strip(), hash_password(password), now, now),
        )
        user_id = cursor.lastrowid
        for role in sorted(set(roles)):
            execute(
                connection,
                "INSERT INTO user_roles(user_id, role_key, created_at) VALUES (?, ?, ?)",
                (user_id, role, now),
            )
        for scope in scopes:
            scope_type = scope["scope_type"]
            org_unit_id = scope.get("org_unit_id")
            if scope_type == "ALL" and "data_security_admin" not in roles and "system_admin" not in roles:
                raise ValueError("只有系统级角色可授予全部组织范围")
            if scope_type != "ALL" and not org_unit_id:
                raise ValueError("UNIT/SUBTREE 范围必须指定组织")
            execute(
                connection,
                "INSERT INTO data_scope_grants(user_id, scope_type, org_unit_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, scope_type, org_unit_id, now),
            )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="iam.user.create",
            resource_type="app_user",
            resource_id=str(user_id),
            after={
                "username_masked": mask_login_identifier(username),
                "roles": roles,
                "scopes": scopes,
            },
        )
        return user_id


def list_managed_users() -> list[dict]:
    """Return account-management fields only; password material never leaves storage."""
    rows = fetch_all(
        "SELECT u.id, u.username, u.display_name, u.is_active, u.last_login_at, "
        "u.created_at, GROUP_CONCAT(ur.role_key) AS role_keys "
        "FROM app_users u LEFT JOIN user_roles ur ON ur.user_id=u.id "
        "GROUP BY u.id, u.username, u.display_name, u.is_active, u.last_login_at, u.created_at "
        "ORDER BY u.is_active DESC, u.display_name, u.id"
    )
    for row in rows:
        row["username"] = mask_login_identifier(row["username"])
        row["roles"] = sorted(
            role for role in str(row.pop("role_keys") or "").split(",") if role
        )
    return rows


def reset_user_password(
    actor_user_id: int,
    actor_roles: list[str],
    user_id: int,
    *,
    password: str,
    reason: str,
) -> None:
    """Administrator password reset with full session revocation and an audit trail."""
    reason = reason.strip()
    if len(reason) < 6:
        raise ValueError("重置原因至少填写 6 个字符")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 位")
    # Hash before opening a transaction so no plaintext password is ever logged
    # or included in an audit payload.
    password_hash = hash_password(password)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        user = execute(
            connection,
            "SELECT id, username, display_name, is_active FROM app_users WHERE id=?",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("账号不存在")
        if not user["is_active"]:
            raise ValueError("账号已停用，请先重新启用后再重置密码")
        settings = get_settings()
        if (
            user["username"] == settings.bootstrap_admin_username
            and "system_admin" not in actor_roles
        ):
            raise PermissionError("只有平台系统管理员可以重置最高管理账号的密码")
        execute(
            connection,
            "UPDATE app_users SET password_hash=?, token_version=token_version+1, updated_at=? "
            "WHERE id=?",
            (password_hash, now, user_id),
        )
        execute(
            connection,
            "UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
            (now, user_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="iam.user.password_reset",
            resource_type="app_user",
            resource_id=str(user_id),
            purpose=reason,
            before={"is_active": int(user["is_active"])},
            after={"sessions_revoked": True},
        )
