from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app.services.participation_history_import import (
    apply_workbook_bundle,
    preview_workbook_bundle,
    verify_service_identity,
)


router = APIRouter(
    prefix="/api/v1/integrations/participation-history", tags=["participation-history-import"]
)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _service_identity(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    try:
        verify_service_identity(x_api_key)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return x_api_key or ""


async def _read_json(file: UploadFile) -> tuple[bytes, str]:
    filename = file.filename or "participation-history.json"
    if not filename.lower().endswith(".json"):
        raise HTTPException(400, "只接受 .json 活动事实包")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "活动事实包超过20MB限制")
    if not content:
        raise HTTPException(400, "活动事实包不能为空")
    return content, filename


@router.post("/preview")
async def preview_participation_history(
    file: UploadFile = File(...),
    api_key: str = Depends(_service_identity),
) -> dict:
    content, filename = await _read_json(file)
    try:
        result = preview_workbook_bundle(content, filename, api_key)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": result}


@router.post("/apply")
async def apply_participation_history(
    file: UploadFile = File(...),
    confirmation_reason: str = Form(..., min_length=8, max_length=1000),
    second_confirmed: bool = Form(...),
    api_key: str = Depends(_service_identity),
) -> dict:
    content, filename = await _read_json(file)
    try:
        result = apply_workbook_bundle(content, filename, api_key, confirmation_reason, second_confirmed)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": result}
