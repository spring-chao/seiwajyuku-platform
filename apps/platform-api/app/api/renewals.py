from __future__ import annotations

import tempfile
from pathlib import Path
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.core.settings import get_settings
from app.services.renewals import (
    add_followup,
    apply_preview,
    list_assignees,
    list_cycles,
    list_followups,
    list_overview,
    preview_result_view,
    preview_workbook,
    rollback_import,
    save_preview,
    update_cycle,
)

router = APIRouter(prefix="/api/v1/renewals", tags=["renewals"])


class RenewalApplyPayload(BaseModel):
    renewal_year: int = Field(ge=2020, le=2100)
    confirmation: str


class RenewalRollbackPayload(BaseModel):
    confirmation: str


class RenewalCycleUpdatePayload(BaseModel):
    status: str | None = None
    phase: str | None = None
    result: str | None = None
    assigned_user_id: int | None = None


class RenewalFollowupPayload(BaseModel):
    channel: str
    summary: str = Field(min_length=4, max_length=4000)
    intention: str | None = Field(default=None, max_length=64)
    needs_support: bool = False
    next_action: str | None = Field(default=None, max_length=4000)
    next_followup_at: str | None = None

@router.get("/overview")
def overview(year: int = 2026, user: dict = Depends(require_permission("renewals:read"))) -> dict:
    return {"success": True, "data": list_overview(user["id"], year)}


@router.get("/cycles")
def cycles(
    year: int = 2026,
    status: str | None = None,
    user: dict = Depends(require_permission("renewals:read")),
) -> dict:
    return {"success": True, "data": list_cycles(user["id"], year, status)}


@router.get("/assignees")
def assignees(
    org_unit_id: str | None = None,
    user: dict = Depends(require_permission("renewals:manage")),
) -> dict:
    try:
        data = list_assignees(user["id"], org_unit_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"success": True, "data": data}

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
        try:
            preview = preview_workbook(path, master_path)
        except (ValueError, InvalidFileException, BadZipFile) as exc:
            raise HTTPException(400, str(exc)) from exc
    read_only = get_settings().deployment_read_only
    batch_id = None if read_only else save_preview(preview, user["id"])
    result = preview_result_view(preview)
    return {
        "success": True,
        "data": {
            "batch_id": batch_id,
            "persisted": not read_only,
            **result,
        },
    }


@router.post("/imports/{batch_id}/apply")
def apply_import(
    batch_id: int,
    payload: RenewalApplyPayload,
    user: dict = Depends(require_permission("renewals:manage")),
) -> dict:
    try:
        data = apply_preview(batch_id, user["id"], payload.renewal_year, payload.confirmation)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/imports/{batch_id}/rollback")
def rollback_import_batch(
    batch_id: int,
    payload: RenewalRollbackPayload,
    user: dict = Depends(require_permission("renewals:manage")),
) -> dict:
    try:
        data = rollback_import(batch_id, user["id"], payload.confirmation)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.patch("/cycles/{cycle_id}")
def edit_cycle(
    cycle_id: int,
    payload: RenewalCycleUpdatePayload,
    user: dict = Depends(require_permission("renewals:manage")),
) -> dict:
    try:
        update_cycle(cycle_id, user["id"], **payload.model_dump(exclude_unset=True))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": cycle_id}}


@router.get("/cycles/{cycle_id}/followups")
def cycle_followups(
    cycle_id: int,
    user: dict = Depends(require_permission("renewals:read")),
) -> dict:
    try:
        data = list_followups(cycle_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": data}


@router.post("/cycles/{cycle_id}/followups")
def create_cycle_followup(
    cycle_id: int,
    payload: RenewalFollowupPayload,
    user: dict = Depends(require_permission("renewals:manage")),
) -> dict:
    try:
        followup_id = add_followup(cycle_id, user["id"], **payload.model_dump())
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": {"id": followup_id}}
