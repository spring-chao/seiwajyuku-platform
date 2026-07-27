from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.auth import require_permission
from app.services.renewals import list_overview, preview_workbook, save_preview

router = APIRouter(prefix="/api/v1/renewals", tags=["renewals"])

@router.get("/overview")
def overview(year: int = 2026, user: dict = Depends(require_permission("renewals:read"))) -> dict:
    return {"success": True, "data": list_overview(user["id"], year)}

@router.post("/imports/preview")
async def import_preview(file: UploadFile = File(...), user: dict = Depends(require_permission("renewals:manage"))) -> dict:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只支持 .xlsx 工作簿")
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过 25MB 限制")
    with tempfile.TemporaryDirectory(prefix="seiwajyuku-renewal-") as directory:
        path = Path(directory) / "upload.xlsx"; path.write_bytes(content)
        try: preview = preview_workbook(path)
        except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    batch_id = save_preview(preview, user["id"])
    return {"success": True, "data": {"batch_id": batch_id, "summary": preview["summary"], "samples": preview["rows"][:50]}}
