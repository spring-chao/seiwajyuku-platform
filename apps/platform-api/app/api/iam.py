from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_permission
from app.db import fetch_all
from app.services.iam import (
    accessible_org_ids,
    create_user,
    list_managed_users,
    reset_user_password,
)
from app.services.class_name_cleanup import (
    apply_duplicate_class_cleanup,
    preview_duplicate_class_cleanup,
)
from app.services.organization_management import (
    create_learning_org_unit,
    deactivate_learning_org_unit,
    group_member_transfer_options,
    list_learning_org_units,
    move_learning_org_unit,
    preview_learning_org_move,
    transfer_group_member_relation,
)


router = APIRouter(prefix="/api/v1", tags=["iam"])


class ScopePayload(BaseModel):
    scope_type: str = Field(pattern="^(ALL|SUBTREE|UNIT)$")
    org_unit_id: str | None = None


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    # Identity-first accounts may intentionally start without a legacy role.
    # Their permissions come from a dated employment/appointment assignment.
    roles: list[str] = Field(default_factory=list)
    # Identity-first accounts also start with no legacy data-scope grant; the
    # dated employment service-responsibility rows provide their scope.
    scopes: list[ScopePayload] = Field(default_factory=list)


class PasswordResetPayload(BaseModel):
    password: str = Field(min_length=10, max_length=256)
    reason: str = Field(min_length=6, max_length=1000)


class ClassCleanupPayload(BaseModel):
    confirmation: str = Field(min_length=4, max_length=100)


class LearningOrgCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    unit_type: str = Field(pattern="^(CLASS|GROUP)$")
    parent_id: str = Field(min_length=1, max_length=64)
    confirmation: str = Field(min_length=4, max_length=300)


class LearningOrgMovePayload(BaseModel):
    target_parent_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=6, max_length=1000)
    confirmation: str = Field(min_length=4, max_length=300)


class LearningOrgDeactivatePayload(BaseModel):
    reason: str = Field(min_length=6, max_length=1000)
    confirmation: str = Field(min_length=4, max_length=300)


class LearningGroupMemberTransferPayload(BaseModel):
    member_id: int = Field(gt=0)
    target_group_org_unit_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=6, max_length=1000)
    confirmation: str = Field(min_length=4, max_length=300)


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


@router.get("/iam/users")
def managed_users(
    _: dict = Depends(require_permission("iam:manage")),
) -> dict:
    return {"success": True, "data": list_managed_users()}


@router.post("/iam/users/{user_id}/password")
def reset_password(
    user_id: int,
    payload: PasswordResetPayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    try:
        reset_user_password(
            actor["id"], actor.get("roles", []), user_id,
            password=payload.password, reason=payload.reason,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": user_id, "sessions_revoked": True}}


@router.get("/iam/org-units/class-name-cleanup")
def preview_class_name_cleanup(
    _: dict = Depends(require_permission("org:manage")),
) -> dict:
    return {"success": True, "data": preview_duplicate_class_cleanup()}


@router.post("/iam/org-units/class-name-cleanup")
def apply_class_name_cleanup(
    payload: ClassCleanupPayload,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = apply_duplicate_class_cleanup(
            actor["id"], confirmation=payload.confirmation
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/iam/org-units/learning-management")
def learning_org_management(
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    return {"success": True, "data": list_learning_org_units(actor["id"])}


@router.post("/iam/org-units/learning-management")
def add_learning_org_unit(
    payload: LearningOrgCreatePayload,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = create_learning_org_unit(actor["id"], **payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/iam/org-units/{unit_id}/move-preview")
def learning_org_move_preview(
    unit_id: str,
    target_parent_id: str,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = preview_learning_org_move(
            actor["id"], unit_id, target_parent_id=target_parent_id
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/iam/org-units/{unit_id}/move")
def move_learning_org(
    unit_id: str,
    payload: LearningOrgMovePayload,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = move_learning_org_unit(actor["id"], unit_id, **payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/iam/org-units/{unit_id}/deactivate")
def deactivate_learning_org(
    unit_id: str,
    payload: LearningOrgDeactivatePayload,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = deactivate_learning_org_unit(actor["id"], unit_id, **payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/iam/org-units/{unit_id}/group-member-transfer-options")
def group_member_transfer_preview(
    unit_id: str,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = group_member_transfer_options(actor["id"], unit_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/iam/org-units/{unit_id}/group-member-transfer")
def transfer_group_member(
    unit_id: str,
    payload: LearningGroupMemberTransferPayload,
    actor: dict = Depends(require_permission("org:manage")),
) -> dict:
    try:
        data = transfer_group_member_relation(actor["id"], unit_id, **payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/org-units/tree")
def org_tree(user: dict = Depends(require_permission("org:read"))) -> dict:
    allowed = accessible_org_ids(user["id"])
    rows = fetch_all(
        "SELECT o.id, o.unit_code, o.name, o.unit_type, o.parent_id, "
        "p.name AS parent_name FROM org_units o "
        "LEFT JOIN org_units p ON p.id=o.parent_id "
        "WHERE o.is_active=1 ORDER BY o.unit_type, p.name, o.name, o.id"
    )
    if allowed is not None:
        rows = [row for row in rows if row["id"] in allowed]
    duplicate_names = {
        row["name"]
        for row in rows
        if row["unit_type"] in {"CLASS", "SPECIAL_COHORT"}
        and sum(
            1
            for candidate in rows
            if candidate["unit_type"] in {"CLASS", "SPECIAL_COHORT"}
            and candidate["name"] == row["name"]
        ) > 1
    }
    for row in rows:
        row["duplicate_name"] = (
            row["unit_type"] in {"CLASS", "SPECIAL_COHORT"}
            and row["name"] in duplicate_names
        )
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
