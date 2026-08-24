from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.learning_cycles import (
    bind_class_learning_plan,
    confirm_class_meeting,
    get_class_learning_progress,
    list_learning_plans,
    update_current_learning_cycle,
)


router = APIRouter(prefix="/api/v1", tags=["learning-plans"])


class LearningPlanBindingPayload(BaseModel):
    plan_version_id: int = Field(gt=0)
    cohort_month: int | None = Field(default=None, ge=1, le=12)
    started_at: str | None = Field(default=None, max_length=64)


class GroupLearningTaskUpdate(BaseModel):
    group_org_unit_id: str = Field(min_length=1, max_length=64)
    status: Literal["PENDING", "COMPLETED", "WAIVED"]
    note: str | None = Field(default=None, max_length=2000)


class LearningCycleUpdatePayload(BaseModel):
    planned_class_meeting_at: str | None = Field(default=None, max_length=64)
    class_meeting_status: Literal["PLANNED", "POSTPONED"] | None = None
    group_meeting_policy: Literal["REQUIRED", "SUSPENDED", "WAIVED"] | None = None
    adjustment_reason: str | None = Field(default=None, max_length=1000)
    group_tasks: list[GroupLearningTaskUpdate] = Field(default_factory=list, max_length=200)


class ConfirmClassMeetingPayload(BaseModel):
    actual_class_meeting_at: str | None = Field(default=None, max_length=64)
    source_event_group_id: int | None = Field(default=None, gt=0)
    confirmation_reason: str | None = Field(default=None, max_length=1000)


@router.get("/learning-plans")
def learning_plans(user: dict = Depends(require_permission("plans:read"))) -> dict:
    return {"success": True, "data": list_learning_plans()}


@router.post("/classes/{class_org_unit_id}/learning-plan-binding")
def create_learning_plan_binding(
    class_org_unit_id: str,
    payload: LearningPlanBindingPayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = bind_class_learning_plan(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            plan_version_id=payload.plan_version_id, cohort_month=payload.cohort_month,
            started_at=payload.started_at,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.get("/classes/{class_org_unit_id}/learning-progress")
def learning_progress(
    class_org_unit_id: str,
    at: str | None = None,
    user: dict = Depends(require_permission("plans:read")),
) -> dict:
    try:
        data = get_class_learning_progress(
            user_id=user["id"], class_org_unit_id=class_org_unit_id, at=at
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"success": True, "data": data}


@router.patch("/classes/{class_org_unit_id}/learning-cycles/current")
def update_learning_cycle(
    class_org_unit_id: str,
    payload: LearningCycleUpdatePayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = update_current_learning_cycle(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            updates=payload.model_dump(exclude_unset=True),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/classes/{class_org_unit_id}/learning-cycles/current/confirm-class-meeting")
def confirm_learning_cycle_class_meeting(
    class_org_unit_id: str,
    payload: ConfirmClassMeetingPayload,
    user: dict = Depends(require_permission("plans:period_write")),
) -> dict:
    try:
        data = confirm_class_meeting(
            actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
            actual_class_meeting_at=payload.actual_class_meeting_at,
            source_event_group_id=payload.source_event_group_id,
            confirmation_reason=payload.confirmation_reason,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
