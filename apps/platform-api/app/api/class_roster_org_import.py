from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.auth import require_permission
from app.core.settings import get_settings
from app.services.class_roster_org_import import (
    apply_confirmed_member_relations,
    apply_confirmed_org_import,
)


router = APIRouter(
    prefix="/api/v1/class-roster-org-import",
    tags=["class-roster-org-import"],
)


@router.post("/apply")
async def apply(
    workbook: UploadFile = File(...),
    confirmation_text: str = Form(...),
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    """Apply the separately approved organization-only phase."""
    settings = get_settings()
    if (
        not settings.allow_production_mutations
        or not settings.class_roster_org_import_enabled
    ):
        raise HTTPException(403, "全量班级组织迁移开关未开启")
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 工作簿")
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过20MB限制")
    try:
        data = apply_confirmed_org_import(
            content,
            workbook.filename or "upload.xlsx",
            confirmation_text,
            user["id"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/apply-relations")
async def apply_relations(
    workbook: UploadFile = File(...),
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    """Apply the separately approved, relation-only second phase."""
    settings = get_settings()
    if (
        not settings.allow_production_mutations
        or not settings.class_roster_org_import_enabled
    ):
        raise HTTPException(403, "全量班级组织迁移开关未开启")
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 工作簿")
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过20MB限制")
    try:
        data = apply_confirmed_member_relations(
            content, workbook.filename or "upload.xlsx", user["id"]
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
