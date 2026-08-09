from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.auth import require_permission
from app.services.legacy_operations_merge import apply_bundle, preview_bundle


router = APIRouter(prefix="/api/v1/legacy-operations", tags=["legacy-operations"])
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


async def _read_json(file: UploadFile) -> tuple[bytes, str]:
    filename = file.filename or "legacy-operations.json"
    if not filename.lower().endswith(".json"):
        raise HTTPException(400, "只接受 .json 合并包")
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "合并包超过20MB限制")
    if not content:
        raise HTTPException(400, "合并包不能为空")
    return content, filename


@router.post("/preview")
async def preview_legacy_operations(
    file: UploadFile = File(...),
    _user: dict = Depends(require_permission("integrations:manage")),
) -> dict:
    content, filename = await _read_json(file)
    try:
        result = preview_bundle(content, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": result}


@router.post("/apply")
async def apply_legacy_operations(
    file: UploadFile = File(...),
    confirmation_reason: str = Form(..., min_length=8, max_length=1000),
    second_confirmed: bool = Form(...),
    user: dict = Depends(require_permission("integrations:manage")),
) -> dict:
    content, filename = await _read_json(file)
    try:
        result = apply_bundle(
            content,
            filename,
            user["id"],
            confirmation_reason,
            second_confirmed,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": result}
