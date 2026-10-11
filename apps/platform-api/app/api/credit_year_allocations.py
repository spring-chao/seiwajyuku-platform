from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from app.api.auth import require_permission
from app.api.credit_opening_balances import _call, _content, FingerprintPayload, SetupPayload
from app.services import credit_year_allocations as service
from app.services.credit_opening_balances import OPENING_PERMISSION

router = APIRouter(prefix='/api/v1/learning-credits/year-allocations',tags=['学习年度归属'])

@router.get('')
def workbench(user=Depends(require_permission(OPENING_PERMISSION))):
    return _call(service.workbench,user['id'])

@router.post('/preview')
async def preview(workbook: UploadFile=File(...), binding_id:int=Form(...,gt=0),year_index:int=Form(...,ge=1,le=3),cutoff_date:str=Form(...),source_note:str=Form(...,min_length=8,max_length=1000),user=Depends(require_permission(OPENING_PERMISSION))):
    return _call(service.preview,await _content(workbook),binding_id,year_index,cutoff_date,source_note,user['id'])

@router.post('/imports')
async def register(workbook:UploadFile=File(...),binding_id:int=Form(...,gt=0),year_index:int=Form(...,ge=1,le=3),cutoff_date:str=Form(...),source_note:str=Form(...,min_length=8,max_length=1000),expected_fingerprint:str=Form(...,pattern=r'^[a-f0-9]{64}$'),user=Depends(require_permission(OPENING_PERMISSION))):
    return _call(service.register,await _content(workbook),binding_id,year_index,cutoff_date,source_note,workbook.filename or '年度学分.xlsx',expected_fingerprint,user['id'])

@router.post('/imports/{import_id}/approve')
def approve(import_id:int,payload:FingerprintPayload,user=Depends(require_permission(OPENING_PERMISSION))):
    return _call(service.action,import_id,user['id'],'approve',payload.expected_fingerprint)

@router.post('/imports/{import_id}/post')
def post(import_id:int,payload:FingerprintPayload,user=Depends(require_permission(OPENING_PERMISSION))):
    return _call(service.action,import_id,user['id'],'post',payload.expected_fingerprint)

@router.post('/imports/{import_id}/cancel')
def cancel(import_id:int,payload:FingerprintPayload,user=Depends(require_permission(OPENING_PERMISSION))):
    return _call(service.action,import_id,user['id'],'cancel',payload.expected_fingerprint)

@router.post('/setup')
def setup(payload:SetupPayload,user=Depends(require_permission('plans:production_rule_reconciliation_apply'))):
    try:
        return _call(service.setup,user['id'],payload.expected_release_commit,payload.expected_migration_sha256)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,'存储准备结果待核验，请刷新实际状态，不要重复执行') from exc
