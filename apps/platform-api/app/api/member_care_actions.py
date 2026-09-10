from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.services.member_care_actions import (
    build_member_care_actions,
    complete_birthday_care,
)


router = APIRouter(
    prefix="/api/v1/operations/member-actions",
    tags=["member-care-actions"],
)


class BirthdayCareCompletionPayload(BaseModel):
    birthday_year: int = Field(ge=1900, le=9999)
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    channel: Literal["WECHAT", "PHONE"]
    operation_item_id: int | None = Field(default=None, ge=1)


@router.get("/today")
def today_member_care_actions(
    user: dict = Depends(require_permission("org:read")),
) -> dict:
    try:
        data = build_member_care_actions(user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/birthday-care/{member_id}/complete")
def complete_member_birthday_care(
    member_id: int,
    payload: BirthdayCareCompletionPayload,
    user: dict = Depends(require_permission("followups:manage")),
) -> dict:
    try:
        data = complete_birthday_care(
            member_id,
            user["id"],
            **payload.model_dump(),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
