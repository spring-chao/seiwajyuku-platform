from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.auth import require_permission
from app.core.settings import get_settings
from app.services.direct_class_import import apply_confirmed_import

router = APIRouter(prefix="/api/v1/direct-class-import", tags=["direct-class-import"])

@router.post("/apply")
async def apply(workbook: UploadFile = File(...), user: dict = Depends(require_permission("members:manage"))) -> dict:
    settings = get_settings()
    if settings.is_production and not settings.allow_production_mutations:
        raise HTTPException(403, "生产写入开关未开启")
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 工作簿")
    try:
        data = apply_confirmed_import(await workbook.read(), workbook.filename or "upload.xlsx", user["id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
