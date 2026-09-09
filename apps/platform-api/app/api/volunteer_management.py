"""HTTP surface for the dedicated Volunteer 2.0 appointment workspace."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.api.auth import require_permission
from app.services import volunteer_management as service


router = APIRouter(prefix="/api/v1/volunteer-management", tags=["volunteer-management"])


class ServiceUnitPayload(BaseModel):
    unit_code: str = Field(min_length=2, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    system_type: Literal["CLASS_TEAM", "GOVERNANCE", "COMMITTEE_LINE", "ACTIVITY"]
    line_type: Literal["LEARNING", "OPERATIONS", "DEVELOPMENT", "GENERAL", "SUPERVISION"]
    parent_id: str | None = Field(default=None, max_length=64)
    home_shuku_org_unit_id: str | None = Field(default=None, max_length=64)
    service_target_org_unit_id: str = Field(min_length=1, max_length=64)
    sort_order: int = Field(default=0, ge=-100000, le=100000)


class AppointmentPayload(BaseModel):
    member_id: int = Field(gt=0)
    service_unit_id: str | None = Field(default=None, min_length=1, max_length=64)
    service_target_org_unit_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    position_key: str = Field(min_length=3, max_length=64)
    confirmation_note: str = Field(default="学员管理页添加志工任职", max_length=1000)

    @model_validator(mode="after")
    def require_service_choice(self):
        if not self.service_unit_id and not self.service_target_org_unit_id:
            raise ValueError("请选择服务组织")
        return self


class AppointmentStatusPayload(BaseModel):
    status: Literal["ACTIVE", "SUSPENDED", "ENDED", "REVOKED"]
    reason: str = Field(default="学员管理页确认结束志工任职", max_length=1000)


class RecommendationRulePayload(BaseModel):
    source_service_unit_id: str = Field(min_length=1, max_length=64)
    source_position_key: str = Field(min_length=3, max_length=64)
    target_service_unit_id: str = Field(min_length=1, max_length=64)
    target_position_key: str = Field(min_length=3, max_length=64)


class RecommendationAcceptPayload(BaseModel):
    confirmation_note: str = Field(min_length=8, max_length=1000)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/service-units")
def get_service_units(
    system_type: str | None = Query(default=None),
    line_type: str | None = Query(default=None),
    parent_id: str | None = Query(default=None, max_length=64),
    active_only: bool = Query(default=True),
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    return {
        "success": True,
        "data": _call(
            service.list_service_units,
            user["id"],
            system_type=system_type,
            line_type=line_type,
            parent_id=parent_id,
            active_only=active_only,
        ),
    }


@router.post("/service-units")
def add_service_unit(
    payload: ServiceUnitPayload,
    user: dict = Depends(require_permission("org:manage")),
) -> dict:
    return {"success": True, "data": _call(service.create_service_unit, user["id"], **payload.model_dump())}


@router.get("/position-options")
def get_position_options(
    service_unit_id: str = Query(min_length=1, max_length=64),
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    return {"success": True, "data": _call(service.list_position_options, user["id"], service_unit_id=service_unit_id)}


@router.get("/member-editor-catalog")
def get_member_editor_catalog(
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    return {"success": True, "data": _call(service.member_editor_catalog, user["id"])}


@router.get("/appointments")
def get_appointments(
    member_id: int | None = Query(default=None, gt=0),
    system_type: str | None = Query(default=None),
    line_type: str | None = Query(default=None),
    service_unit_id: str | None = Query(default=None, max_length=64),
    position_key: str | None = Query(default=None, max_length=64),
    home_shuku_org_unit_id: str | None = Query(default=None, max_length=64),
    service_target_org_unit_id: str | None = Query(default=None, max_length=64),
    member_name: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None),
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    return {
        "success": True,
        "data": _call(
            service.list_appointments,
            user["id"],
            member_id=member_id,
            system_type=system_type,
            line_type=line_type,
            service_unit_id=service_unit_id,
            position_key=position_key,
            home_shuku_org_unit_id=home_shuku_org_unit_id,
            service_target_org_unit_id=service_target_org_unit_id,
            member_name=member_name,
            status=status,
        ),
    }


@router.post("/appointments")
def add_appointment(
    payload: AppointmentPayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    return {"success": True, "data": _call(service.create_appointment, user["id"], **payload.model_dump())}


@router.post("/appointments/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusPayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    return {"success": True, "data": _call(service.change_appointment_status, user["id"], appointment_id, **payload.model_dump())}


@router.get("/recommendation-rules")
def get_recommendation_rules(
    source_service_unit_id: str | None = Query(default=None, max_length=64),
    source_position_key: str | None = Query(default=None, max_length=64),
    active_only: bool = Query(default=False),
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    return {
        "success": True,
        "data": _call(
            service.list_recommendation_rules,
            user["id"],
            source_service_unit_id=source_service_unit_id,
            source_position_key=source_position_key,
            active_only=active_only,
        ),
    }


@router.post("/recommendation-rules")
def add_recommendation_rule(
    payload: RecommendationRulePayload,
    user: dict = Depends(require_permission("org:manage")),
) -> dict:
    return {"success": True, "data": _call(service.create_recommendation_rule, user["id"], **payload.model_dump())}


@router.post("/appointments/{appointment_id}/recommendations/{rule_id}/accept")
def accept_recommended_appointment(
    appointment_id: int,
    rule_id: int,
    payload: RecommendationAcceptPayload,
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    return {
        "success": True,
        "data": _call(
            service.accept_recommendation,
            user["id"],
            source_appointment_id=appointment_id,
            recommendation_rule_id=rule_id,
            **payload.model_dump(),
        ),
    }


@router.get("/migration-preview")
def get_migration_preview(
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    return {"success": True, "data": _call(service.migration_preview, user["id"])}
