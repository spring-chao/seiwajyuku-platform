from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth import require_permission
from app.services.member_care_management import build_member_care_management_overview


router = APIRouter(
    prefix="/api/v1/operations/member-care",
    tags=["member-care-management"],
)


@router.get("/management-overview")
def management_overview(
    as_of: date | None = Query(default=None),
    org_unit_id: str | None = Query(default=None, max_length=128),
    user: dict = Depends(require_permission("org:read")),
) -> dict:
    try:
        data = build_member_care_management_overview(
            user["id"], as_of=as_of, org_unit_id=org_unit_id
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"success": True, "data": data}
