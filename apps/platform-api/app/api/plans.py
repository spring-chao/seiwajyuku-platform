from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.plans import (
    enable_plan_write,
    list_plans,
    mp_dashboard,
    period_values,
    target_variances,
    update_period_values,
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
    user: dict = Depends(require_permission("plans:write")),
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


@router.get("/analytics/target-variances")
def variances(
    plan_id: int,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    return {"success": True, "data": target_variances(plan_id, user["id"])}

