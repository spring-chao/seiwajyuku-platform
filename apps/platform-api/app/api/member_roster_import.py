from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.auth import require_permission
from app.services.member_roster_import import apply_member_roster, preview_member_roster


router = APIRouter(
    prefix="/api/v1/member-roster-import",
    tags=["member-roster-import"],
)


def _read_workbook_name(workbook: UploadFile) -> str:
    if not (workbook.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "只接受 .xlsx 工作簿")
    return workbook.filename or "member-roster.xlsx"


@router.post("/preview")
async def preview(
    workbook: UploadFile = File(...),
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    source_name = _read_workbook_name(workbook)
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过20MB限制")
    try:
        data = preview_member_roster(content, source_name, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/apply")
async def apply(
    workbook: UploadFile = File(...),
    confirmation_text: str = Form(...),
    user: dict = Depends(require_permission("members:manage")),
) -> dict:
    source_name = _read_workbook_name(workbook)
    content = await workbook.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "工作簿超过20MB限制")
    try:
        data = apply_member_roster(
            content,
            source_name,
            user["id"],
            confirmation_text,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}
