from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.identity_admin import (
    catalogs,
    change_assignment_status,
    create_employment,
    create_technical_assignment,
    create_volunteer_appointment,
    initialize_person_link,
    list_identity_accounts,
    list_org_options,
)


router = APIRouter(prefix="/api/v1/identity-admin", tags=["identity-admin"])


class ConfirmationPayload(BaseModel):
    source_reference: str = Field(min_length=4, max_length=500)
    confirmation_note: str = Field(min_length=8, max_length=1000)


class ServiceResponsibilityPayload(BaseModel):
    org_unit_id: str = Field(min_length=1, max_length=64)
    scope_type: str = Field(pattern="^(UNIT|SUBTREE)$")


class EmploymentPayload(ConfirmationPayload):
    # ``position_key`` remains for backwards compatibility.  New identity
    # accounts can hold several position templates under one employment.
    position_key: str | None = Field(default=None, min_length=3, max_length=64)
    position_keys: list[str] = Field(default_factory=list, max_length=16)
    started_on: str
    ended_on: str | None = None
    service_responsibilities: list[ServiceResponsibilityPayload] = Field(
        default_factory=list, max_length=32
    )


class VolunteerAppointmentPayload(ConfirmationPayload):
    appointment_key: str = Field(min_length=3, max_length=64)
    org_unit_id: str = Field(min_length=1, max_length=64)
    scope_type: str = Field(pattern="^(UNIT|SUBTREE)$")
    starts_at: str
    ends_at: str


class TechnicalAssignmentPayload(ConfirmationPayload):
    assignment_purpose: str = Field(min_length=6, max_length=500)
    starts_at: str
    ends_at: str


class StatusChangePayload(BaseModel):
    status: str = Field(pattern="^(SUSPENDED|ENDED|REVOKED)$")
    reason: str = Field(min_length=6, max_length=1000)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog")
def get_catalog(
    _: dict = Depends(require_permission("iam:manage")),
) -> dict:
    return {"success": True, "data": _call(catalogs)}


@router.get("/accounts")
def get_accounts(
    _: dict = Depends(require_permission("iam:manage")),
) -> dict:
    return {"success": True, "data": _call(list_identity_accounts)}


@router.get("/org-options")
def get_org_options(
    _: dict = Depends(require_permission("iam:manage")),
) -> dict:
    return {"success": True, "data": _call(list_org_options)}


@router.post("/accounts/{user_id}/initialize")
def initialize_account_identity(
    user_id: int,
    payload: ConfirmationPayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    person_id = _call(
        initialize_person_link,
        actor["id"],
        user_id,
        **payload.model_dump(),
    )
    return {"success": True, "data": {"person_id": person_id}}


@router.post("/accounts/{user_id}/employments")
def add_employment(
    user_id: int,
    payload: EmploymentPayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    values = payload.model_dump()
    if payload.position_key:
        values["position_keys"] = [payload.position_key, *payload.position_keys]
    values.pop("position_key", None)
    values["service_responsibilities"] = [
        item.model_dump() for item in payload.service_responsibilities
    ]
    employment_id = _call(
        create_employment,
        actor["id"],
        user_id,
        **values,
    )
    return {"success": True, "data": {"id": employment_id}}


@router.post("/accounts/{user_id}/volunteer-appointments")
def add_volunteer_appointment(
    user_id: int,
    payload: VolunteerAppointmentPayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    appointment_id = _call(
        create_volunteer_appointment,
        actor["id"],
        user_id,
        **payload.model_dump(),
    )
    return {"success": True, "data": {"id": appointment_id}}


@router.post("/accounts/{user_id}/technical-assignments")
def add_technical_assignment(
    user_id: int,
    payload: TechnicalAssignmentPayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    assignment_id = _call(
        create_technical_assignment,
        actor["id"],
        user_id,
        **payload.model_dump(),
    )
    return {"success": True, "data": {"id": assignment_id}}


@router.post("/assignments/{assignment_type}/{assignment_id}/status")
def update_assignment_status(
    assignment_type: str,
    assignment_id: int,
    payload: StatusChangePayload,
    actor: dict = Depends(require_permission("iam:manage")),
) -> dict:
    _call(
        change_assignment_status,
        actor["id"],
        assignment_type=assignment_type,
        assignment_id=assignment_id,
        **payload.model_dump(),
    )
    return {
        "success": True,
        "data": {"id": assignment_id, "status": payload.status},
    }
