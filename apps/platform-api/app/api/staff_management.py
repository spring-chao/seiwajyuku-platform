from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal

from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.staff_management import (
    authorization_migration_preview,
    create_staff,
    get_staff,
    list_staff,
    preview_staff_update,
    staff_catalog,
    update_staff,
)


router = APIRouter(prefix="/api/v1/staff-management", tags=["staff-management"])


class AuthorizationGrantPayload(BaseModel):
    role_key: str = Field(min_length=3, max_length=128)
    org_unit_id: str = Field(min_length=1, max_length=64)
    scope_type: str = Field(pattern="^(UNIT|SUBTREE)$")
    valid_from: str | None = None
    valid_until: str | None = None


class StaffCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    login_account: str = Field(min_length=3, max_length=128)
    temporary_password: str | None = Field(default=None, min_length=6, max_length=256)
    is_active: bool = True
    phone: str = Field(min_length=1, max_length=32)
    gender: Literal["MALE", "FEMALE"]
    institution_id: str = Field(min_length=1, max_length=64)
    department_name: str | None = Field(default=None, max_length=255)
    supervisor_user_id: int | None = Field(default=None, gt=0)
    position_keys: list[str] = Field(min_length=1, max_length=16)
    # Ordinary staff authority is state-based.  Date fields remain optional
    # archive metadata for an API client that needs to preserve old records.
    employment_status: Literal["ACTIVE", "LEAVE"] = "ACTIVE"
    started_on: str | None = None
    ended_on: str | None = None
    # The ordinary business flow sends one responsibility. Roles and IAM2
    # grants are derived server-side from the selected position(s).
    responsibility_org_unit_id: str | None = Field(default=None, min_length=1, max_length=64)
    responsibility_scope_type: Literal["UNIT", "SUBTREE"] | None = None
    # Kept only for compatible expert clients and historical fixtures.
    grants: list[AuthorizationGrantPayload] | None = Field(default=None, max_length=64)
    authorization_basis: str = Field(default="", max_length=500)
    authorization_reason: str = Field(default="", max_length=1000)


class StaffUpdatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # A phone-like login identifier is intentionally masked on read. Keeping
    # this optional lets an operator edit another staff field without sending
    # a masked identifier back as though it were the real account name.
    login_account: str | None = Field(default=None, min_length=3, max_length=128)
    is_active: bool
    gender: Literal["MALE", "FEMALE"]
    replace_phone: bool = False
    phone: str | None = Field(default=None, max_length=32)
    institution_id: str = Field(min_length=1, max_length=64)
    department_name: str | None = Field(default=None, max_length=255)
    supervisor_user_id: int | None = Field(default=None, gt=0)
    position_keys: list[str] = Field(min_length=1, max_length=16)
    employment_status: Literal["ACTIVE", "LEAVE"] | None = None
    started_on: str | None = None
    ended_on: str | None = None
    responsibility_org_unit_id: str | None = Field(default=None, min_length=1, max_length=64)
    responsibility_scope_type: Literal["UNIT", "SUBTREE"] | None = None
    grants: list[AuthorizationGrantPayload] | None = Field(default=None, max_length=64)
    authorization_basis: str = Field(default="", max_length=500)
    authorization_reason: str = Field(default="", max_length=1000)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog")
def catalog(actor: dict = Depends(require_permission("iam:manage"))) -> dict:
    return {"success": True, "data": _call(staff_catalog, actor["id"])}


@router.get("/staff")
def staff_list(
    org_unit_id: str | None = Query(default=None),
    department_name: str | None = Query(default=None),
    position_key: str | None = Query(default=None),
    role_key: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    query: str | None = Query(default=None, max_length=128),
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    return {
        "success": True,
        "data": _call(
            list_staff,
            actor["id"],
            org_unit_id=org_unit_id,
            department_name=department_name,
            position_key=position_key,
            role_key=role_key,
            is_active=is_active,
            query=query,
        ),
    }


@router.get("/staff/{user_id}")
def staff_detail(user_id: int, actor: dict = Depends(require_permission("iam:manage"))) -> dict:
    return {"success": True, "data": _call(get_staff, actor["id"], user_id)}


@router.post("/staff")
def create(payload: StaffCreatePayload, actor: dict = Depends(require_permission("iam:manage"))) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if payload.grants is not None:
        values["grants"] = [item.model_dump(exclude_unset=True) for item in payload.grants]
    values.setdefault("temporary_password", None)
    values.setdefault("department_name", None)
    values.setdefault("supervisor_user_id", None)
    values.setdefault("is_active", True)
    values.setdefault("employment_status", "ACTIVE")
    values.setdefault("started_on", None)
    values.setdefault("ended_on", None)
    return {"success": True, "data": _call(create_staff, actor["id"], **values)}


@router.post("/staff/{user_id}/change-preview")
def change_preview(
    user_id: int,
    payload: StaffUpdatePayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if payload.grants is not None:
        values["grants"] = [item.model_dump(exclude_unset=True) for item in payload.grants]
    return {
        "success": True,
        "data": _call(preview_staff_update, actor["id"], user_id, payload=values),
    }


@router.put("/staff/{user_id}")
def update(
    user_id: int,
    payload: StaffUpdatePayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if payload.grants is not None:
        values["grants"] = [item.model_dump(exclude_unset=True) for item in payload.grants]
    return {
        "success": True,
        "data": _call(update_staff, actor["id"], user_id, payload=values),
    }


@router.get("/authorization-migration-preview")
def migration_preview(actor: dict = Depends(require_permission("iam:manage"))) -> dict:
    return {
        "success": True,
        "data": _call(authorization_migration_preview, actor["id"]),
    }
