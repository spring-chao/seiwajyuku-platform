from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.operation_rhythm import (
    generate_rhythm_cycles,
    list_rhythm_templates,
    rhythm_snapshot,
    update_rhythm_item,
    update_rhythm_template_node,
)


router = APIRouter(prefix="/api/v1/operations/rhythm", tags=["operation-rhythm"])


class RhythmItemUpdate(BaseModel):
    status: Literal[
        "PENDING",
        "PLANNED",
        "IN_PROGRESS",
        "WAITING_EXTERNAL",
        "COMPLETED",
        "ATTENTION",
        "CANCELLED",
    ] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=2000)
    start_date: str | None = Field(default=None, max_length=10)
    due_date: str | None = Field(default=None, max_length=10)


class RhythmTemplateNodeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    rule_type: str | None = Field(default=None, min_length=1, max_length=64)
    rule_config: dict[str, Any] | None = None
    start_offset_days: int | None = Field(default=None, ge=-366, le=366)
    due_offset_days: int | None = Field(default=None, ge=-366, le=366)
    responsibility_role: str | None = Field(default=None, max_length=128)
    external_responsibility_role: str | None = Field(default=None, max_length=128)


@router.get("/snapshot")
def snapshot(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    organization_id: str | None = Query(default=None, max_length=64),
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    status: Literal[
        "PENDING",
        "PLANNED",
        "IN_PROGRESS",
        "WAITING_EXTERNAL",
        "COMPLETED",
        "ATTENTION",
        "CANCELLED",
    ] | None = None,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = rhythm_snapshot(
            user["id"],
            year,
            month,
            organization_id=organization_id,
            class_org_unit_id=class_org_unit_id,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/templates")
def templates(user: dict = Depends(require_permission("plans:read"))) -> dict:
    return {"success": True, "data": list_rhythm_templates(user["id"])}


@router.post("/generate")
def generate(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = generate_rhythm_cycles(user["id"], year, month)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.patch("/items/{item_id}")
def update_item(
    item_id: int,
    payload: RhythmItemUpdate,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        updates = payload.model_dump(exclude_unset=True)
        data = update_rhythm_item(
            user["id"],
            item_id,
            **updates,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    return {"success": True, "data": data}


@router.patch("/templates/{template_id}/nodes/{node_id}")
def update_template_node(
    template_id: int,
    node_id: int,
    payload: RhythmTemplateNodeUpdate,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = update_rhythm_template_node(
            user["id"], template_id, node_id, payload.model_dump(exclude_none=True)
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    return {"success": True, "data": data}
