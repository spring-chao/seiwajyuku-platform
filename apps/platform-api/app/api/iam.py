from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_permission
from app.db import fetch_all
from app.services.iam import accessible_org_ids, create_user


router = APIRouter(prefix="/api/v1", tags=["iam"])


class ScopePayload(BaseModel):
    scope_type: str = Field(pattern="^(ALL|SUBTREE|UNIT)$")
    org_unit_id: str | None = None


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    roles: list[str] = Field(min_length=1)
    scopes: list[ScopePayload] = Field(min_length=1)


@router.post("/iam/users")
def add_user(
    payload: UserCreatePayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    try:
        user_id = create_user(
            actor["id"],
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            roles=payload.roles,
            scopes=[scope.model_dump() for scope in payload.scopes],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": user_id}}


@router.get("/org-units/tree")
def org_tree(user: dict = Depends(require_permission("org:read"))) -> dict:
    allowed = accessible_org_ids(user["id"])
    rows = fetch_all(
        "SELECT id, unit_code, name, unit_type, parent_id FROM org_units "
        "WHERE is_active=1 ORDER BY unit_type, name"
    )
    if allowed is not None:
        rows = [row for row in rows if row["id"] in allowed]
    return {"success": True, "data": rows}


@router.get("/audit-logs")
def audit_logs(
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(require_permission("audit:read")),
) -> dict:
    rows = fetch_all(
        "SELECT id, actor_user_id, action, resource_type, resource_id, org_unit_id, "
        "purpose, result, created_at FROM audit_logs ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return {"success": True, "data": rows}

