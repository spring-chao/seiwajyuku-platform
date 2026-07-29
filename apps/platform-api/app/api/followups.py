from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.followups import (
    add_followup_record,
    add_visit_record,
    close_task,
    create_task,
    list_assignees,
    list_tasks,
)
from app.services.followup_invitations import (
    accept_invitation,
    create_invitation,
    invitation_capabilities,
    list_my_invitations,
    mark_unavailable,
    request_adjustment,
    respond_to_adjustment,
)


router = APIRouter(prefix="/api/v1/followups", tags=["followups"])


class TaskPayload(BaseModel):
    member_id: int
    task_type: str = Field(min_length=2, max_length=64)
    service_purpose: str = Field(min_length=4, max_length=1000)
    assigned_user_id: int
    due_at: str | None = None
    confidentiality_level: str = "ASSIGNEE"
    invitation_mode: bool = False
    invitation_message: str | None = Field(default=None, max_length=1000)
    invitation_valid_until: str | None = None


class RecordPayload(BaseModel):
    channel: str
    contacted_at: str
    outcome_code: str
    subject_statement: str | None = None
    objective_facts: str | None = None
    staff_judgment: str | None = None
    next_action: str | None = None
    next_followup_at: str | None = None


class VisitPayload(BaseModel):
    appointment_at: str | None = None
    visited_at: str
    purpose: str
    participants: list[str] = Field(default_factory=list)
    location_type: str
    objective_facts: str
    expressed_needs: str | None = None
    support_provided: str | None = None
    staff_judgment: str | None = None
    next_action: str | None = None
    next_followup_at: str | None = None


class ClosePayload(BaseModel):
    closure_note: str = Field(min_length=4, max_length=1000)


class InvitationPayload(BaseModel):
    invited_user_id: int
    invitation_type: str
    invitation_message: str | None = Field(default=None, max_length=1000)
    proposed_due_at: str | None = None
    valid_until: str


class InvitationResponsePayload(BaseModel):
    response_note: str | None = Field(default=None, max_length=1000)


class AdjustmentRequestPayload(BaseModel):
    requested_due_at: str
    response_note: str = Field(min_length=2, max_length=1000)


class AdjustmentResponsePayload(BaseModel):
    proposed_due_at: str
    response_note: str | None = Field(default=None, max_length=1000)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/tasks")
def task_create(
    payload: TaskPayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    task_id = _call(create_task, user["id"], **payload.model_dump())
    return {"success": True, "data": {"id": task_id}}


@router.get("/tasks")
def task_list(
    status: str | None = None,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    return {"success": True, "data": list_tasks(user["id"], status)}


@router.get("/assignees")
def assignee_list(
    org_unit_id: str | None = None,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    return {"success": True, "data": _call(list_assignees, user["id"], org_unit_id)}


@router.get("/capabilities")
def capabilities(
    _: dict = Depends(require_permission("followups:manage")),
) -> dict:
    return {"success": True, "data": invitation_capabilities()}


@router.get("/invitations/mine")
def invitation_list(
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    return {"success": True, "data": list_my_invitations(user["id"])}


@router.post("/tasks/{task_id}/invitations")
def invitation_create(
    task_id: int,
    payload: InvitationPayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    invitation_id = _call(
        create_invitation, task_id, user["id"], **payload.model_dump()
    )
    return {"success": True, "data": {"id": invitation_id}}


@router.post("/invitations/{invitation_id}/accept")
def invitation_accept(
    invitation_id: int,
    payload: InvitationResponsePayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    _call(accept_invitation, invitation_id, user["id"], payload.response_note)
    return {"success": True, "data": {"id": invitation_id, "status": "ACCEPTED"}}


@router.post("/invitations/{invitation_id}/unavailable")
def invitation_unavailable(
    invitation_id: int,
    payload: InvitationResponsePayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    _call(mark_unavailable, invitation_id, user["id"], payload.response_note or "")
    return {"success": True, "data": {"id": invitation_id, "status": "UNAVAILABLE"}}


@router.post("/invitations/{invitation_id}/adjustment-request")
def invitation_adjustment_request(
    invitation_id: int,
    payload: AdjustmentRequestPayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    _call(request_adjustment, invitation_id, user["id"], **payload.model_dump())
    return {
        "success": True,
        "data": {"id": invitation_id, "status": "ADJUSTMENT_REQUESTED"},
    }


@router.post("/invitations/{invitation_id}/adjustment-response")
def invitation_adjustment_response(
    invitation_id: int,
    payload: AdjustmentResponsePayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    _call(respond_to_adjustment, invitation_id, user["id"], **payload.model_dump())
    return {"success": True, "data": {"id": invitation_id, "status": "PENDING"}}


@router.post("/tasks/{task_id}/records")
def record_create(
    task_id: int,
    payload: RecordPayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    record_id = _call(add_followup_record, task_id, user["id"], **payload.model_dump())
    return {"success": True, "data": {"id": record_id}}


@router.post("/tasks/{task_id}/visits")
def visit_create(
    task_id: int,
    payload: VisitPayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    visit_id = _call(add_visit_record, task_id, user["id"], **payload.model_dump())
    return {"success": True, "data": {"id": visit_id}}


@router.post("/tasks/{task_id}/close")
def task_close(
    task_id: int,
    payload: ClosePayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    _call(close_task, task_id, user["id"], payload.closure_note)
    return {"success": True, "data": {"id": task_id, "status": "CLOSED"}}
