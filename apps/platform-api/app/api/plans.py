from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.plans import (
    enable_plan_write,
    list_plans,
    mp_dashboard,
    operations_snapshot,
    period_values,
    target_variances,
    update_period_values,
)
from app.services.class_operations import (
    class_operations_detail,
    update_class_operations,
)


router = APIRouter(prefix="/api/v1", tags=["annual-mp"])


class EnableWritePayload(BaseModel):
    business_approval_reference: str = Field(min_length=6, max_length=500)


class PeriodValueUpdate(BaseModel):
    id: int
    numeric_value: float | None = None
    value_state: str | None = None


class PeriodValuesPayload(BaseModel):
    updates: list[PeriodValueUpdate] = Field(min_length=1, max_length=500)


class GroupOperationsUpdate(BaseModel):
    group_org_unit_id: str = Field(min_length=1, max_length=64)
    planned_meeting_at: str | None = Field(default=None, max_length=40)


class ClassOperationsUpdate(BaseModel):
    weekly_meeting_at: str | None = Field(default=None, max_length=40)
    planned_class_meeting_at: str | None = Field(default=None, max_length=40)
    learning_month: int | None = Field(default=None, ge=1, le=240)
    learning_progress: str | None = Field(default=None, max_length=2000)
    revenue_growing_member_count: int | None = Field(default=None, ge=0, le=100000)
    revenue_comparable_member_count: int | None = Field(default=None, ge=0, le=100000)
    groups: list[GroupOperationsUpdate] = Field(default_factory=list, max_length=100)


@router.get("/annual-plans")
def annual_plans(user: dict = Depends(require_permission("plans:read"))) -> dict:
    return {"success": True, "data": list_plans()}


@router.post("/annual-plans/{plan_id}/enable-write")
def enable_write(
    plan_id: int,
    payload: EnableWritePayload,
    user: dict = Depends(require_permission("plans:publish")),
) -> dict:
    try:
        plan = enable_plan_write(plan_id, user["id"], payload.business_approval_reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": plan}


@router.get("/metric-period-values")
def get_period_values(
    plan_id: int,
    month: int = Query(..., ge=1, le=12),
    org_unit_id: str | None = None,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    return {
        "success": True,
        "data": period_values(
            plan_id=plan_id, user_id=user["id"], month=month, org_unit_id=org_unit_id
        ),
    }


@router.put("/metric-period-values")
def put_period_values(
    plan_id: int,
    payload: PeriodValuesPayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        changed = update_period_values(
            plan_id=plan_id,
            user_id=user["id"],
            updates=[item.model_dump() for item in payload.updates],
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"updated_count": changed}}


@router.get("/analytics/mp-dashboard")
def dashboard(
    plan_id: int,
    month: int = Query(..., ge=1, le=12),
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    return {"success": True, "data": mp_dashboard(plan_id=plan_id, user_id=user["id"], month=month)}


@router.get("/analytics/operations-snapshot")
def monthly_operations_snapshot(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    return {
        "success": True,
        "data": operations_snapshot(user_id=user["id"], year=year, month=month),
    }


@router.get("/analytics/class-operations/{class_org_unit_id}")
def get_class_operations(
    class_org_unit_id: str,
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = class_operations_detail(
            user_id=user["id"], class_org_unit_id=class_org_unit_id,
            year=year, month=month,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.put("/analytics/class-operations/{class_org_unit_id}")
def put_class_operations(
    class_org_unit_id: str,
    payload: ClassOperationsUpdate,
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = update_class_operations(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            year=year, month=month, updates=payload.model_dump(),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/analytics/target-variances")
def variances(
    plan_id: int,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    return {"success": True, "data": target_variances(plan_id, user["id"])}
