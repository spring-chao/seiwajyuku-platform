from __future__ import annotations

from fastapi import APIRouter

from app.services.enrollment import get_public_portal


router = APIRouter(prefix="/api/v1/public", tags=["public-portal"])


@router.get("/portal")
def public_portal() -> dict:
    return {"success": True, "data": get_public_portal()}
