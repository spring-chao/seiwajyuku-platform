from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.auth import require_permission
from app.services.direct_class_preflight import preview_production_workbook


router = APIRouter(prefix="/api/v1/direct-class-preflight", tags=["direct-class-preflight"])


@router.post("/preview")
async def preview(
    workbook: UploadFile = File(...),
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    """Read-only matching preview; the workbook is processed in memory and not persisted."""
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 工作簿")
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过20MB限制")
    try:
        data = preview_production_workbook(content, workbook.filename or "upload.xlsx")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
