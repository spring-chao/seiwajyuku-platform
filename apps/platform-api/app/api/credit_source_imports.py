from urllib.parse import quote
from fastapi import APIRouter,Depends,File,Form,UploadFile,HTTPException,Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from app.api.auth import require_permission
from app.api.credit_opening_balances import _call,_content,SetupPayload
from app.services import credit_source_imports as service
from app.services.credit_source_exports import export_credits,export_options
from app.services import reading_weekly_export

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
async def preview(workbook:UploadFile=File(...),kind:str=Form(...),class_id:str=Form(...),options_json:str=Form('{}',max_length=5000),source_note:str=Form('',max_length=1000),user=Depends(require_permission('plans:credit_settlement_manage'))):
    return await run_in_threadpool(_call,service.preview,await _content(workbook),kind,class_id,options_json,source_note,user['id'])

@router.post('/imports')
async def register(workbook:UploadFile=File(...),kind:str=Form(...),class_id:str=Form(...),options_json:str=Form('{}',max_length=5000),source_note:str=Form('',max_length=1000),expected_fingerprint:str=Form(...,pattern=r'^[a-f0-9]{64}$'),user=Depends(require_permission('plans:credit_settlement_manage'))):
    return await run_in_threadpool(_call,service.register,await _content(workbook),kind,class_id,options_json,source_note,workbook.filename or '学分来源.xlsx',expected_fingerprint,user['id'])

@router.get('/saved-source')
def saved_source(fingerprint:str=Query(...,pattern=r'^[a-f0-9]{64}$'),class_id:str=Query(...),kind:str=Query(...),user=Depends(require_permission('plans:credit_settlement_manage'))):
    return _call(service.find_saved_source,fingerprint,class_id,kind,user['id'])

@router.get('/imports/{import_id}')
def get_import(import_id:int,user=Depends(require_permission('plans:credit_settlement_manage'))):return _call(service.get_import,import_id,user['id'])

@router.post('/imports/{import_id}/batches')
def create_batches(import_id:int,user=Depends(require_permission('plans:credit_settlement_manage'))):return _call(service.create_batches,import_id,user['id'])

class ConfirmationPayload(BaseModel):
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    resume_partial_failure: bool = False
    incremental: bool = False

@router.post('/imports/{import_id}/actions/{operation}')
def process(import_id:int,operation:str,payload:ConfirmationPayload | None=None,user=Depends(require_permission('plans:credit_settlement_manage'))):
    if operation=='reconcile':return _call(service.reconcile,import_id,user['id'])
    return _call(service.process,import_id,user['id'],operation,payload.expected_fingerprint if payload else None,payload.resume_partial_failure if payload else False,payload.incremental if payload else False)

@router.get('/export')
def export(class_id:str|None=None,occurred_from:str='2026-10-01',occurred_to:str|None=None,member_id:int|None=Query(None,gt=0),user=Depends(require_permission('exports:normal'))):
    try:return _xlsx(export_credits(user['id'],class_id,occurred_from,occurred_to,member_id),'学分汇总及新增来源.xlsx')
    except (ValueError,PermissionError) as exc:raise HTTPException(403 if isinstance(exc,PermissionError) else 400,str(exc)) from exc

@router.get('/export-options')
def options(user=Depends(require_permission('exports:normal'))):return _call(export_options,user['id'])

class ReadingPause(BaseModel):
    member_id: int = Field(gt=0)
    occurred_from: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    occurred_to: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    reason: str = Field(pattern=r'^(COURSE|TEMPORARY)$')

class ReadingWeeklyPayload(BaseModel):
    class_id: str = Field(min_length=1, max_length=128)
    occurred_from: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    occurred_to: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    pauses: list[ReadingPause] = Field(default_factory=list, max_length=200)

@router.get('/reading-weekly-roster')
def reading_roster(class_id: str, occurred_from: str, occurred_to: str, user=Depends(require_permission('exports:normal'))):
    return _call(reading_weekly_export.roster, user['id'], class_id, occurred_from, occurred_to)

@router.post('/reading-weekly-export')
def reading_weekly(payload: ReadingWeeklyPayload, user=Depends(require_permission('exports:normal'))):
    try:
        raw=reading_weekly_export.export_weekly(user['id'], payload.class_id, payload.occurred_from, payload.occurred_to, [r.model_dump() for r in payload.pauses])
        return _xlsx(raw, f'班级每周读书打卡统计_{payload.occurred_from}_{payload.occurred_to}.xlsx')
    except (ValueError, PermissionError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc

@router.post('/setup')
def setup(payload:SetupPayload,user=Depends(require_permission('plans:production_rule_reconciliation_apply'))):
    try:return _call(service.setup,user['id'],payload.expected_release_commit,payload.expected_migration_sha256)
    except HTTPException:raise
    except Exception as exc:raise HTTPException(503,'存储准备结果待核验，请刷新实际状态，不要重复执行') from exc

@router.post('/reading-calendar/setup')
def prepare_calendar(user=Depends(require_permission('plans:business_calendar_manage'))):
    return _call(service.prepare_reading_calendar,user['id'])
