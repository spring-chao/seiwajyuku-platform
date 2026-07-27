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
async def import_preview(
    renewal_file: UploadFile = File(...), master_file: UploadFile = File(...),
    user: dict = Depends(require_permission("renewals:manage")),
) -> dict:
    if not all((item.filename or "").lower().endswith(".xlsx") for item in [renewal_file, master_file]):
        raise HTTPException(400, "续费名单和主档案都必须为 .xlsx 工作簿")
    content, master_content = await renewal_file.read(), await master_file.read()
    if max(len(content), len(master_content)) > 25 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过 25MB 限制")
    with tempfile.TemporaryDirectory(prefix="seiwajyuku-renewal-") as directory:
        path = Path(directory) / "renewals.xlsx"; master_path = Path(directory) / "master.xlsx"
        path.write_bytes(content); master_path.write_bytes(master_content)
        try: preview = preview_workbook(path, master_path)
        except ValueError as exc: raise HTTPException(400, str(exc)) from exc
    batch_id = save_preview(preview, user["id"])
    return {"success": True, "data": {"batch_id": batch_id, "summary": preview["summary"], "samples": preview["rows"][:50]}}
