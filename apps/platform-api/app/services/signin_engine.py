"""Authenticated adapter to the single authoritative signin engine.

This module owns authorization and transport only. Registration, attendance
windows, team slots, cross-class participation and check-in rules stay in signin.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.settings import get_settings
from app.db import fetch_one
from app.services.iam import accessible_org_ids


OPERATIONS = {
    "admin_events": "attendance:view",
    "event_detail": "attendance:view",
    "create_event": "attendance:create",
    "import_preview": "attendance:import",
    "import_apply": "attendance:import",
    "stats": "attendance:view",
    "class_roster_reconciliation": "attendance:view",
    "ops_roster_options": "attendance:view",
    "ops_roster_members": "attendance:import",
    "event_update": "attendance:update",
    "event_lifecycle_update": "attendance:manage",
    "create_class_meeting_sessions": "attendance:create",
    "sync_class_roster": "attendance:import",
    "upload_preview": "attendance:import",
    "upload": "attendance:import",
    "registration": "attendance:manage",
    "registration_delete": "attendance:manage",
    "attendance_status": "attendance:status",
    "manual_checkin": "attendance:manage",
    "export": "attendance:export",
}
READ_OPERATIONS = frozenset({
    "admin_events", "event_detail", "stats", "class_roster_reconciliation",
    "ops_roster_options", "ops_roster_members", "upload_preview", "import_preview", "export",
})
PRIVATE_FIELDS = frozenset({
    "phone", "phone_plain", "phone_hash", "password", "password_hash",
    "admin_token", "token", "api_key", "secret", "notes", "note", "attendance_note",
})


class SigninEngineError(ValueError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def enabled(*, member: bool = False, write: bool = False) -> None:
    settings = get_settings()
    flag = settings.signin_member_checkin_enabled if member else settings.signin_management_enabled
    if not flag:
        raise SigninEngineError("活动签到服务尚未开放", 404)
    if write and (settings.deployment_read_only or (
        settings.is_production and not settings.allow_production_mutations
    )):
        raise SigninEngineError("当前环境禁止签到管理写入", 403)
    if not settings.signin_api_base_url or not settings.signin_platform_api_key:
        raise SigninEngineError("签到引擎连接尚未配置", 503)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()
                if key.lower() not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def engine_request(path: str, payload: dict, *, timeout: float = 20) -> dict:
    settings = get_settings()
    base = settings.signin_api_base_url.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in {"https", "http"}
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise SigninEngineError("签到引擎地址无效", 503)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(base + path, json=payload,
                                   headers={"X-API-Key": settings.signin_platform_api_key})
            if response.status_code >= 400:
                # Do not expose upstream bodies, URLs or credentials.
                raise SigninEngineError("签到引擎暂时不可用，请重试", 502)
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, SigninEngineError):
            raise
        raise SigninEngineError("签到引擎暂时不可用，请重试", 502) from exc
    if not isinstance(data, dict):
        raise SigninEngineError("签到引擎返回格式无效", 502)
    if data.get("ok") is False:
        message = str(data.get("msg") or "签到操作未完成")[:300]
        raise SigninEngineError(message, 409)
    return data


def allowed_scope(user_id: int, permission: str) -> list[str] | None:
    allowed = accessible_org_ids(user_id, permission)
    if allowed is not None and not allowed:
        raise SigninEngineError("没有可操作的组织范围", 403)
    return None if allowed is None else sorted(allowed)


def required_permissions(operation: str, payload: dict) -> list[str]:
    base = OPERATIONS.get(operation)
    if not base:
        raise SigninEngineError("不支持的签到管理操作", 404)
    if operation == "event_lifecycle_update":
        return ["attendance:update" if payload.get("lifecycle_status") in {"CONFIRMED", "DRAFT"} else "attendance:manage"]
    if operation == "event_update" and ({"status", "select"} & set(payload)):
        metadata = set(payload) - {"event_id", "status", "select"}
        return ["attendance:manage", "attendance:update"] if metadata else ["attendance:manage"]
    if operation == "upload":
        return ["attendance:import", "attendance:create"]
    return [base]


def combined_scope(user_id: int, permissions: list[str]) -> list[str] | None:
    scopes = [allowed_scope(user_id, permission) for permission in permissions]
    narrowed = [set(scope) for scope in scopes if scope is not None]
    if not narrowed:
        return None
    allowed = set.intersection(*narrowed)
    if not allowed:
        raise SigninEngineError("各操作权限没有共同的组织范围", 403)
    return sorted(allowed)


def validate_organizations(payload: dict, allowed: list[str] | None, *, required: bool = False) -> None:
    ids = [str(payload.get(key) or "").strip() for key in (
        "org_unit_id", "class_org_unit_id", "group_org_unit_id", "study_org_unit_id",
    )]
    ids = [item for item in ids if item]
    if required and not ids:
        raise SigninEngineError("活动必须关联已确认的组织编号", 400)
    for org_id in ids:
        org = fetch_one("SELECT id, parent_id FROM org_units WHERE id=? AND is_active=1", (org_id,))
        if not org:
            raise SigninEngineError("活动组织不存在或已停用", 400)
    owner = str(payload.get("group_org_unit_id") or payload.get("study_org_unit_id")
                or payload.get("class_org_unit_id") or payload.get("org_unit_id") or "")
    if owner and allowed is not None and owner not in allowed:
        raise SigninEngineError("活动不在可操作的组织范围内", 403)
    class_id = payload.get("class_org_unit_id")
    group_id = payload.get("group_org_unit_id") or payload.get("study_org_unit_id")
    if class_id and group_id:
        group = fetch_one("SELECT parent_id FROM org_units WHERE id=?", (str(group_id),))
        if not group or group["parent_id"] != class_id:
            raise SigninEngineError("小组与所选班级的组织关系不一致", 400)
    # Parent IDs are descriptive context, not extra scope grants. Every supplied
    # parent must be a verified ancestor of the most specific activity owner.
    ancestor_ids = {owner} if owner else set()
    cursor = owner
    for _ in range(32):
        if not cursor:
            break
        current = fetch_one("SELECT parent_id FROM org_units WHERE id=?", (cursor,))
        parent = str((current or {}).get("parent_id") or "")
        if parent in ancestor_ids:
            raise SigninEngineError("活动组织层级异常", 400)
        if parent:
            ancestor_ids.add(parent)
        cursor = parent
    if any(org_id not in ancestor_ids for org_id in ids):
        raise SigninEngineError("活动组织上下级关系不一致", 400)


def management_request(operation: str, payload: dict, *, user: dict) -> dict:
    permissions = required_permissions(operation, payload)
    if not set(permissions).issubset(user.get("permissions", [])):
        raise SigninEngineError("无此操作权限", 403)
    enabled(write=operation not in READ_OPERATIONS)
    allowed = combined_scope(user["id"], permissions)
    validate_organizations(payload, allowed,
                           required=operation in {"create_class_meeting_sessions", "create_event"}
                           or operation == "upload" and not payload.get("event_id"))
    if any(key in payload for key in ("actor", "allowed_org_unit_ids", "admin_token", "password", "api_key")):
        raise SigninEngineError("请求包含不可提交的授权字段", 400)
    data = engine_request("/ops/v1/manage/" + operation, {
        "payload": payload,
        "actor": {"id": user["id"], "permissions": permissions},
        "allowed_org_unit_ids": allowed,
    })
    return redact(data)


def event_detail(event_id: str, *, user: dict, permission: str) -> dict:
    allowed = allowed_scope(user["id"], permission)
    result = engine_request("/ops/v1/manage/event_detail", {
        "payload": {"event_id": event_id},
        "actor": {"id": user["id"], "permissions": ["attendance:view"]},
        "allowed_org_unit_ids": allowed,
    })
    event = result.get("event") or result.get("data", {}).get("event")
    if not isinstance(event, dict):
        raise SigninEngineError("活动不存在", 404)
    validate_organizations(event, allowed, required=True)
    return redact(event)
