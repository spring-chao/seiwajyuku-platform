from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import require_permission
from app.services.member_care_actions import build_member_care_actions


router = APIRouter(
    prefix="/api/v1/operations/member-actions",
    tags=["member-care-actions"],
)


@router.get("/today")
def today_member_care_actions(
    user: dict = Depends(require_permission("org:read")),
) -> dict:
    try:
        data = build_member_care_actions(user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"success": True, "data": data}
