from urllib.parse import quote
from fastapi import APIRouter,Depends,File,Form,UploadFile,HTTPException,Query
from fastapi.responses import Response
from app.api.auth import require_permission
from app.api.credit_opening_balances import _call,_content,SetupPayload
from app.services import credit_source_imports as service
from app.services.credit_source_exports import export_credits,export_options

router=APIRouter(prefix='/api/v1/learning-credits/excel',tags=['学分来源表格'])

def _xlsx(raw,name):
    return Response(raw,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name)})

@router.get('')
def workbench(user=Depends(require_permission('plans:credit_settlement_manage'))):
    return _call(service.workbench,user['id'])

@router.get('/template')
def template(kind:str,class_id:str,user=Depends(require_permission('plans:credit_settlement_manage'))):
    try:return _xlsx(service.template(kind,class_id,user['id']),service.SHEETS.get(kind,'学分')+'模板.xlsx')
    except (ValueError,PermissionError) as exc:raise HTTPException(403 if isinstance(exc,PermissionError) else 400,str(exc)) from exc

@router.post('/preview')
async def preview(workbook:UploadFile=File(...),kind:str=Form(...),class_id:str=Form(...),options_json:str=Form('{}',max_length=5000),source_note:str=Form(...,min_length=4,max_length=1000),user=Depends(require_permission('plans:credit_settlement_manage'))):
    return _call(service.preview,await _content(workbook),kind,class_id,options_json,source_note,user['id'])

@router.post('/imports')
async def register(workbook:UploadFile=File(...),kind:str=Form(...),class_id:str=Form(...),options_json:str=Form('{}',max_length=5000),source_note:str=Form(...,min_length=4,max_length=1000),expected_fingerprint:str=Form(...,pattern=r'^[a-f0-9]{64}$'),user=Depends(require_permission('plans:credit_settlement_manage'))):
    return _call(service.register,await _content(workbook),kind,class_id,options_json,source_note,workbook.filename or '学分来源.xlsx',expected_fingerprint,user['id'])

@router.get('/imports/{import_id}')
def get_import(import_id:int,user=Depends(require_permission('plans:credit_settlement_manage'))):return _call(service.get_import,import_id,user['id'])

@router.post('/imports/{import_id}/batches')
def create_batches(import_id:int,user=Depends(require_permission('plans:credit_settlement_manage'))):return _call(service.create_batches,import_id,user['id'])

@router.post('/imports/{import_id}/actions/{operation}')
def process(import_id:int,operation:str,user=Depends(require_permission('plans:credit_settlement_manage'))):return _call(service.process,import_id,user['id'],operation)

@router.get('/export')
def export(class_id:str|None=None,occurred_from:str='2026-10-01',occurred_to:str|None=None,user=Depends(require_permission('exports:normal'))):
    try:return _xlsx(export_credits(user['id'],class_id,occurred_from,occurred_to),'学分汇总及新增来源.xlsx')
    except (ValueError,PermissionError) as exc:raise HTTPException(403 if isinstance(exc,PermissionError) else 400,str(exc)) from exc

@router.get('/export-options')
def options(user=Depends(require_permission('exports:normal'))):return _call(export_options,user['id'])

@router.post('/setup')
def setup(payload:SetupPayload,user=Depends(require_permission('plans:production_rule_reconciliation_apply'))):
    try:return _call(service.setup,user['id'],payload.expected_release_commit,payload.expected_migration_sha256)
    except HTTPException:raise
    except Exception as exc:raise HTTPException(503,'存储准备结果待核验，请刷新实际状态，不要重复执行') from exc
