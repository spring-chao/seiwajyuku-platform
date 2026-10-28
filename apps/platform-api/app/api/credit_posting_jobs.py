"""User-confirmed durable jobs and a private, data-free scheduler entry."""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_permission
from app.api.credit_opening_balances import _call
from app.api.credit_opening_balances import SetupPayload
from app.api.study_evidence_cleanup import require_maintenance_token
from app.services import credit_posting_jobs as jobs

router=APIRouter(tags=['后台学分入账任务'])


@router.post('/api/v1/learning-credits/excel/posting-jobs/setup')
def setup(payload:SetupPayload,user=Depends(require_permission('plans:production_rule_reconciliation_apply'))):
    try:
        return _call(jobs.setup,user['id'],payload.expected_release_commit,payload.expected_migration_sha256)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503,'任务存储准备结果待核验，请刷新实际状态，不要重复执行') from None


class ConfirmJob(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_fingerprint: str=Field(pattern=r'^[a-f0-9]{64}$')
    resume_partial_failure: bool=False


@router.post('/api/v1/learning-credits/excel/imports/{import_id}/posting-job')
def submit(import_id:int,payload:ConfirmJob,user=Depends(require_permission('plans:credit_settlement_post'))):
    return _call(jobs.submit,import_id,user['id'],payload.expected_fingerprint,payload.resume_partial_failure)


@router.post('/api/v1/learning-credits/excel/imports/{import_id}/posting-job/pause')
def pause(import_id:int,user=Depends(require_permission('plans:credit_settlement_post'))):
    return _call(jobs.pause,import_id,user['id'])


class SweepPayload(BaseModel):
    model_config=ConfigDict(extra='forbid')


@router.post('/api/v1/internal/credit-posting-jobs')
def sweep(payload:SweepPayload,x_token:str|None=Header(default=None,alias='X-Study-Evidence-Cleanup-Token')):
    require_maintenance_token(x_token)
    try:
        result=jobs.run_sweep()
    except Exception:
        raise HTTPException(503,'后台入账调度暂不可用，请检查任务状态') from None
    return {'success':True,'data':result}
