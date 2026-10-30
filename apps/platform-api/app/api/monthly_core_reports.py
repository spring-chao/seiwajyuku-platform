from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.api.study_evidence_cleanup import require_maintenance_token
from app.services import monthly_core_reports as reports

router=APIRouter(prefix='/api/v1/internal/monthly-core-reports',tags=['internal-maintenance'])

class Empty(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Job(Empty):
    month:str=Field(pattern=r'^20\d{2}-(0[1-9]|1[0-2])$')
    report_id:str=Field(pattern=r'^[a-f0-9]{64}$')

@router.post('/run')
def run(payload:Job,x_study_evidence_cleanup_token:str|None=Header(None)):
    require_maintenance_token(x_study_evidence_cleanup_token)
    try:return {'success':True,'data':reports.run(payload.month,payload.report_id)}
    except Exception:raise HTTPException(503,'报表后台任务暂不可用') from None

@router.post('/sweep')
def sweep(payload:Empty,x_study_evidence_cleanup_token:str|None=Header(None)):
    require_maintenance_token(x_study_evidence_cleanup_token)
    try:return {'success':True,'data':reports.sweep()}
    except Exception:raise HTTPException(503,'定时报表生成暂不可用') from None

@router.post('/verify')
def verify(payload:Empty,x_study_evidence_cleanup_token:str|None=Header(None)):
    require_maintenance_token(x_study_evidence_cleanup_token)
    try:return {'success':True,'data':reports.verify_ready()}
    except Exception:raise HTTPException(503,'报表下载核验暂不可用') from None
