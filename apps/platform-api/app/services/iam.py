from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

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
    "org:read": ("查看组织", "INTERNAL"),
    "org:manage": ("维护组织", "SENSITIVE"),
    "plans:read": ("查看年度MP", "INTERNAL"),
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
}
ROLE_PERMISSIONS = {
    "system_admin": set(PERMISSIONS) - {"exports:sensitive"},
    "data_security_admin": {"org:read", "members:read", "exports:sensitive", "audit:read"},
    "operations_admin": {
        "org:read", "org:manage", "plans:read", "plans:period_write", "plans:import_global", "plans:publish",
        "members:read", "members:manage", "members:detail_view", "members:enterprise_view",
        "followups:manage", "exports:normal", "audit:read",
        "integrations:manage", "renewals:read", "renewals:manage",
        "attendance:sync", "attendance:adjudicate",
    },
    "regional_manager": {
        "org:read", "plans:read", "plans:period_write", "members:read",
        "members:manage", "members:detail_view", "followups:manage", "contact:reveal",
        "exports:normal", "renewals:read", "renewals:manage",
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
}
ROLE_NAMES = {
    "system_admin": "系统管理员",
    "data_security_admin": "数据安全管理员（最高权限）",
    "operations_admin": "苏州塾运营管理员",
    "regional_manager": "区域分中心负责人/理事",
    "class_counselor": "班主任/辅导员/班委",
    "group_leader": "组长/组委",
    "read_only": "只读观察员",
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
    user = fetch_one(
        "SELECT id, username, display_name, token_version, is_active FROM app_users WHERE id=?",
        (user_id,),
    )
    if not user or not user["is_active"]:
        return None
    now = datetime.now(UTC).isoformat()
    user["roles"] = [
        row["role_key"]
        for row in fetch_all(
            "SELECT ur.role_key FROM user_roles ur JOIN roles r ON r.role_key=ur.role_key "
            "WHERE ur.user_id=? AND r.is_active=1 "
            "AND (ur.valid_from IS NULL OR ur.valid_from<=?) "
            "AND (ur.valid_until IS NULL OR ur.valid_until>=?)",
            (user_id, now, now),
        )
    ]
    user["permissions"] = [
        row["permission_key"]
        for row in fetch_all(
            "SELECT DISTINCT rp.permission_key FROM user_roles ur "
            "JOIN roles r ON r.role_key=ur.role_key AND r.is_active=1 "
            "JOIN role_permissions rp ON rp.role_key=ur.role_key "
            "WHERE ur.user_id=? AND (ur.valid_from IS NULL OR ur.valid_from<=?) "
            "AND (ur.valid_until IS NULL OR ur.valid_until>=?)",
            (user_id, now, now),
        )
    ]
    user["scopes"] = fetch_all(
        "SELECT scope_type, org_unit_id, valid_from, valid_until FROM data_scope_grants "
        "WHERE user_id=? AND (valid_from IS NULL OR valid_from<=?) "
        "AND (valid_until IS NULL OR valid_until>=?)",
        (user_id, now, now),
    )
    return user


def accessible_org_ids(user_id: int) -> set[str] | None:
    scopes = user_context(user_id)
    if not scopes:
        return set()
    if any(item["scope_type"] == "ALL" for item in scopes["scopes"]):
        return None
    allowed: set[str] = set()
    for grant in scopes["scopes"]:
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


def create_user(
    actor_user_id: int,
    *,
    username: str,
    display_name: str,
    password: str,
    roles: list[str],
    scopes: list[dict],
) -> int:
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
            after={"username": username, "roles": roles, "scopes": scopes},
        )
        return user_id
