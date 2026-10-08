"""RBAC-scoped management facade; all attendance rules execute in signin."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import current_user, require_permission
from app.api.attendance import _verify_signin_service_key
from app.core.settings import get_settings
from app.db import fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services import signin_engine
from app.services.attendance_entry_codes import generate_entry
from app.services.attendance_sync import sync_from_signin


router = APIRouter(prefix="/api/v1/attendance", tags=["signin-management"])


class CodePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    env_version: Literal["develop", "trial", "release"] = "develop"


class ImmediateSyncPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    registration_id: str | None = Field(default=None, max_length=128)


def _error(exc: signin_engine.SigninEngineError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def perform(operation: str, payload: dict, user: dict) -> dict:
    permission = signin_engine.OPERATIONS.get(operation)
    if not permission:
        raise HTTPException(404, "不支持的签到管理操作")
    permissions = signin_engine.required_permissions(operation, payload)
    if not set(permissions).issubset(user["permissions"]):
        raise HTTPException(403, "无此操作权限")
    request_id = str(uuid.uuid4())
    before = None
    event_id = str(payload.get("event_id") or payload.get("batch_id") or "")
    try:
        signin_engine.enabled(write=operation not in signin_engine.READ_OPERATIONS)
        if event_id and operation not in signin_engine.READ_OPERATIONS:
            before = signin_engine.event_detail(event_id, user=user, permission=permissions[0])
        # An intent is durable before the remote write, including uncertain outcomes.
        with transaction() as connection:
            write_audit(connection, actor_user_id=user["id"],
                        action="attendance.manage." + operation, resource_type="signin_event",
                        resource_id=event_id or None, request_id=request_id, result="PENDING",
                        before=before, after={"requested_fields": sorted(payload)})
        data = signin_engine.management_request(operation, payload, user=user)
        if operation == "ops_roster_options":
            allowed = signin_engine.combined_scope(user["id"], permissions)
            organizations = fetch_all("SELECT id,name,parent_id,unit_type FROM org_units WHERE is_active=1 ORDER BY name")
            data["org_units"] = [org for org in organizations if allowed is None or org["id"] in allowed]
            data["centers"] = [org for org in data["org_units"] if org["unit_type"] in {"ROOT", "REGIONAL_CENTER", "REGIONAL_SHUKU", "SHUKU"}]
    except signin_engine.SigninEngineError as exc:
        with transaction() as connection:
            write_audit(connection, actor_user_id=user["id"],
                        action="attendance.manage." + operation, resource_type="signin_event",
                        resource_id=event_id or None, request_id=request_id, result="FAILED",
                        before=before, after={"status_code": exc.status_code})
        raise _error(exc) from exc
    with transaction() as connection:
        audit = data.get("audit") or {}
        write_audit(connection, actor_user_id=user["id"],
                    action="attendance.manage." + operation, resource_type="signin_event",
                    resource_id=str(audit.get("target") or event_id or "") or None,
                    request_id=request_id, before=audit.get("before", before),
                    after=audit.get("after") or {"ok": data.get("ok", True), "operation": operation})
    data.pop("audit", None)
    return data


@router.get("/manage/events")
def events(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50),
           keyword: str = Query(default="", max_length=100),
           status: str = Query(default="", max_length=32),
           user: dict = Depends(require_permission("attendance:view"))) -> dict:
    return perform("admin_events", {"page": page, "page_size": page_size, "keyword": keyword, "status": status}, user)


@router.post("/manage/events/{event_id}/code")
def entry_code(event_id: str, payload: CodePayload,
               user: dict = Depends(require_permission("attendance:code"))) -> dict:
    try:
        return {"success": True, "data": generate_entry(event_id, user=user, env_version=payload.env_version)}
    except signin_engine.SigninEngineError as exc:
        raise _error(exc) from exc


@router.post("/manage/{operation}")
def operation(operation: str, payload: dict = Body(default_factory=dict),
              user: dict = Depends(current_user)) -> dict:
    # Exact allowlist and a permission per operation. This is never a generic
    # proxy and never forwards credentials, client actor or organization grants.
    return perform(operation, payload, user)


@router.post("/sync/immediate")
def immediate_sync(payload: ImmediateSyncPayload,
                   x_api_key: str | None = Header(default=None)) -> dict:
    _verify_signin_service_key(x_api_key)
    settings = get_settings()
    if not (settings.signin_management_enabled or settings.signin_member_checkin_enabled):
        raise HTTPException(404, "及时签到同步尚未开放")
    if settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations):
        raise HTTPException(403, "当前环境禁止新的签到同步写入")
    try:
        result = sync_from_signin(event_id=payload.event_id)
    except Exception as exc:
        raise HTTPException(503, "签到已保存在签到引擎，参与记录等待重试同步") from exc
    if result["status"] != "SUCCESS" or not result["received_sessions"]:
        raise HTTPException(503, "签到增量尚未完整同步，请保留重试任务")
    if payload.registration_id:
        saved = fetch_one(
            "SELECT r.id FROM attendance_records r "
            "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
            "JOIN attendance_event_groups g ON g.id=s.event_group_id "
            "WHERE g.source_key='signin' AND s.external_session_id=? "
            "AND r.external_record_id=? AND r.checked_at IS NOT NULL "
            "AND r.attendance_status IN ('PRESENT','MANUAL_PRESENT')",
            (payload.event_id, payload.registration_id),
        )
        if not saved:
            raise HTTPException(503, "本次签到事实尚未到达平台，请保留重试任务")
    with transaction() as connection:
        write_audit(connection, actor_user_id=None, action="attendance.sync.immediate",
                    resource_type="signin_event", resource_id=payload.event_id,
                    after={"run_id": result["run_id"], "status": result["status"]})
    return {"success": True, "data": result}
