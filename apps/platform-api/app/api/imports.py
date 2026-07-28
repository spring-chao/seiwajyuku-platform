from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.auth import require_permission
from app.services.mp_import import apply_preview, preview_workbook, save_preview


router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@router.post("/mp/preview")
async def preview_mp(
    file: UploadFile = File(...),
    user: dict = Depends(require_permission("plans:import_global")),
) -> dict:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 工作簿")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过20MB限制")
    with tempfile.TemporaryDirectory(prefix="seiwajyuku-mp-") as temp_dir:
        path = Path(temp_dir) / "upload.xlsx"
        path.write_bytes(content)
        try:
            preview = preview_workbook(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    preview["source_name"] = file.filename or "upload.xlsx"
    batch_id = save_preview(preview, user["id"])
    return {
        "success": True,
        "data": {
            "batch_id": batch_id,
            "summary": preview["summary"],
            "issues": preview["issues"],
            "reconciliation": preview["reconciliation"],
        },
    }


@router.post("/{batch_id}/apply")
def apply_mp(
    batch_id: int,
    user: dict = Depends(require_permission("plans:import_global")),
) -> dict:
    try:
        result = apply_preview(batch_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": result}

