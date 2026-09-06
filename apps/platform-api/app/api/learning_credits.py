from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.learning_credits import (
    LearningCreditError,
    dry_run_study_meeting_settlement,
    dry_run_study_meetings,
    list_credit_entries,
    member_credit_summary,
    reverse_credit_entry,
    settle_study_meeting,
)
from app.services.class_meeting_credits import (
    dry_run_class_meeting_settlement,
    dry_run_class_meetings,
)


router = APIRouter(prefix="/api/v1/learning-credits", tags=["learning-credits"])


class ReversalPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


@router.post("/dry-run/class-meetings/{event_group_id}")
def dry_run_class_meeting(
    event_group_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    """Project one attendance event group into the learning-credit model."""
    try:
        return {
            "success": True,
            "data": dry_run_class_meeting_settlement(
                actor_user_id=user["id"], event_group_id=event_group_id
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/class-meetings")
def dry_run_class_meeting_batch(
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    event_date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    event_date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=30, ge=1, le=500),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    """Project class meetings in a class/date window without writing credits."""
    try:
        return {
            "success": True,
            "data": dry_run_class_meetings(
                actor_user_id=user["id"],
                class_org_unit_id=class_org_unit_id,
                event_date_from=event_date_from,
                event_date_to=event_date_to,
                limit=limit,
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/study-meetings/{session_id}")
def dry_run_study_meeting(
    session_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": dry_run_study_meeting_settlement(
                actor_user_id=user["id"], session_id=session_id
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/dry-run/study-meetings")
def dry_run_study_meeting_batch(
    class_org_unit_id: str | None = Query(default=None, max_length=64),
    learning_cycle_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=30, ge=1, le=500),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": dry_run_study_meetings(
                actor_user_id=user["id"], class_org_unit_id=class_org_unit_id,
                learning_cycle_id=learning_cycle_id, limit=limit,
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/entries")
def credit_entries(
    member_id: int | None = Query(default=None, ge=1),
    occurred_from: str | None = Query(default=None, max_length=32),
    occurred_to: str | None = Query(default=None, max_length=32),
    credit_category: str | None = Query(default=None, max_length=32),
    source_type: str | None = Query(default=None, max_length=64),
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        data = list_credit_entries(
            actor_user_id=user["id"], member_id=member_id,
            occurred_from=occurred_from, occurred_to=occurred_to,
            credit_category=credit_category, source_type=source_type,
        )
        return {"success": True, "data": {"entries": data}}
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.get("/members/{member_id}/summary")
def credit_summary(
    member_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_preview")),
) -> dict:
    try:
        return {
            "success": True,
            "data": member_credit_summary(actor_user_id=user["id"], member_id=member_id),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/study-meetings/{session_id}/settle")
def settle_meeting(
    session_id: int,
    user: dict = Depends(require_permission("plans:credit_settlement_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": settle_study_meeting(
                actor_user_id=user["id"], session_id=session_id
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc


@router.post("/entries/{entry_id}/reverse")
def reverse_entry(
    entry_id: int,
    payload: ReversalPayload,
    user: dict = Depends(require_permission("plans:credit_settlement_manage")),
) -> dict:
    try:
        return {
            "success": True,
            "data": reverse_credit_entry(
                actor_user_id=user["id"], entry_id=entry_id, reason=payload.reason
            ),
        }
    except (LearningCreditError, PermissionError) as exc:
        raise _error(exc) from exc
