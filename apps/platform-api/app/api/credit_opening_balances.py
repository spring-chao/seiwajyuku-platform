from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.auth import require_permission, require_any_permission
from app.services import credit_opening_balances as service

router = APIRouter(prefix="/api/v1/learning-credits/opening-balances", tags=["期初学分"])


class FingerprintPayload(BaseModel):
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SetupPayload(BaseModel):
    expected_release_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    expected_migration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _call(fn, *args):
    try:
        return {"success": True, "data": fn(*args)}
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _content(file: UploadFile) -> bytes:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "请上传下载模板填写后的.xlsx文件")
    content = await file.read(service.MAX_BYTES + 1)
    if len(content) > service.MAX_BYTES:
        raise HTTPException(400, "文件超过5MB，请分批上传")
    return content


@router.get("")
def workbench(user=Depends(require_any_permission(service.OPENING_PERMISSION, "plans:historical_credit_import_manage"))):
    return _call(service.workbench, user["id"])


@router.get("/template")
def template(user=Depends(require_any_permission(service.OPENING_PERMISSION, "plans:historical_credit_import_manage"))):
    data = _call(service.template_bytes, user["id"])["data"]
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="credit-opening-template.xlsx"', "Cache-Control": "no-store"})


@router.post("/preview")
async def preview(workbook: UploadFile = File(...), cutoff_date: str = Form(...),
                  user=Depends(require_any_permission(service.OPENING_PERMISSION, "plans:historical_credit_import_manage"))):
    return _call(service.preview, await _content(workbook), cutoff_date, user["id"])


@router.post("/imports")
async def register(workbook: UploadFile = File(...), cutoff_date: str = Form(...),
                   expected_fingerprint: str = Form(..., pattern=r"^[a-f0-9]{64}$"),
                   user=Depends(require_any_permission(service.OPENING_PERMISSION, "plans:historical_credit_import_manage"))):
    return _call(service.register, await _content(workbook), cutoff_date, workbook.filename or "credits.xlsx", expected_fingerprint, user["id"])


@router.post("/imports/{import_id}/approve")
def approve(import_id: int, payload: FingerprintPayload,
            user=Depends(require_permission(service.OPENING_PERMISSION))):
    return _call(service.action, import_id, user["id"], "approve", payload.expected_fingerprint)


@router.post("/imports/{import_id}/post")
def post(import_id: int, payload: FingerprintPayload,
         user=Depends(require_permission(service.OPENING_PERMISSION))):
    return _call(service.action, import_id, user["id"], "post", payload.expected_fingerprint)


@router.post("/setup")
def setup(payload: SetupPayload,
          user=Depends(require_permission("plans:production_rule_reconciliation_apply"))):
    try:
        return _call(service.setup, user["id"], payload.expected_release_commit, payload.expected_migration_sha256)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "存储准备返回异常，请刷新核验实际状态；不要重复执行") from exc


@router.post("/imports/{import_id}/cancel")
def cancel(import_id: int, payload: FingerprintPayload,
           user=Depends(require_any_permission(service.OPENING_PERMISSION, "plans:historical_credit_import_manage"))):
    return _call(service.action, import_id, user["id"], "cancel", payload.expected_fingerprint)
