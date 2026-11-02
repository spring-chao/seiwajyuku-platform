"""Signed machine-only reading endpoint. Never accepts an arbitrary actor/kind."""
import base64
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from app.services import reading_sync_automation as service

router=APIRouter(prefix='/api/v1/internal/reading-sync',tags=['internal-reading-sync'])


async def authenticate(request,require_enabled=True):
    key=os.getenv('READING_SYNC_SIGNING_KEY','')
    timestamp=request.headers.get('X-Reading-Sync-Time','')
    signature=request.headers.get('X-Reading-Sync-Signature','')
    try:
        valid_time=timestamp.isdecimal() and abs(time.time()-int(timestamp))<=120
    except (ValueError,OverflowError):
        valid_time=False
    if len(key)<32 or not valid_time or len(signature)!=64:
        raise HTTPException(401,'同步服务身份无效')
    chunks=[]
    size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>7_100_000:
            raise HTTPException(413,'原表过大')
        chunks.append(chunk)
    body=b''.join(chunks)
    material='\n'.join([request.method,request.url.path,timestamp,hashlib.sha256(body).hexdigest()])
    expected=hmac.new(key.encode(),material.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature):
        raise HTTPException(401,'同步服务身份无效')
    if require_enabled and os.getenv('READING_SYNC_ENABLED','false').lower()!='true':
        raise HTTPException(503,'云端读书同步尚未启用')
    return body


@router.post('/configuration-candidates')
async def configuration_candidates(request:Request):
    body=await authenticate(request,require_enabled=False)
    if body!=b'{}':raise HTTPException(400,'配置查询格式无效')
    try:
        data=await run_in_threadpool(service.configuration_candidates)
    except Exception:
        raise HTTPException(409,'班级映射需要核对') from None
    return {'success':True,'data':data}


class Source(BaseModel):
    model_config=ConfigDict(extra='forbid')
    class_id:str=Field(min_length=1,max_length=128)
    source_meeting_id:str=Field(min_length=1,max_length=128)
    start:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')


class Import(Source):
    workbook_base64:str=Field(min_length=1,max_length=7_000_000)


class Status(Source):
    import_id:int=Field(gt=0)


class Lookup(Source):
    file_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


class Outcome(Source):
    error_code:str=Field(pattern=r'^[A-Z_]{1,64}$')


def extra_operation(path,model,operation):
    async def endpoint(request:Request):
        raw=await authenticate(request)
        try:
            payload=model.model_validate_json(raw)
            result=await run_in_threadpool(operation,**payload.model_dump())
        except PermissionError:
            raise HTTPException(403,'同步来源超出授权范围') from None
        except Exception:
            raise HTTPException(409,'同步状态待核验') from None
        return {'success':True,'data':result}
    router.add_api_route(path,endpoint,methods=['POST'],name='reading_sync_'+path.strip('/'))


extra_operation('/lookup',Lookup,lambda class_id,source_meeting_id,start,end,file_sha256:service.lookup(class_id,source_meeting_id,start,end,file_sha256))
extra_operation('/resume',Status,service.resume)
extra_operation('/outcome',Outcome,service.outcome)


class Interval(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')


class Plan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    intervals:list[Interval]=Field(min_length=1,max_length=2)


@router.post('/plan')
async def plan(request:Request):
    raw=await authenticate(request)
    try:
        payload=Plan.model_validate_json(raw)
        data=await run_in_threadpool(service.plan,[p.model_dump() for p in payload.intervals])
    except PermissionError:
        raise HTTPException(403,'同步班级范围待核对') from None
    except Exception:
        raise HTTPException(409,'同步配置或日期待核对') from None
    return {'success':True,'data':data}


@router.post('/imports')
async def ingest(request:Request):
    raw=await authenticate(request)
    try:
        payload=Import.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(400,'同步请求格式无效') from None
    try:
        content=base64.b64decode(payload.workbook_base64,validate=True)
        if len(content)>5*1024*1024:raise ValueError
    except (ValueError,TypeError):
        raise HTTPException(400,'原表编码无效') from None
    try:
        data=await run_in_threadpool(service.ingest,content,payload.class_id,payload.source_meeting_id,payload.start,payload.end)
    except PermissionError:
        raise HTTPException(403,'同步来源超出授权范围') from None
    except Exception:
        # A save/dispatch may already have committed. Do not echo member data
        # or invite the worker to repeat a ledger write without reconciliation.
        raise HTTPException(409,'同步结果待核验，请查询实际来源及入账状态') from None
    return {'success':True,'data':data}


@router.post('/status')
async def status(request:Request):
    raw=await authenticate(request)
    try:
        payload=Status.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(400,'同步请求格式无效') from None
    try:
        data=await run_in_threadpool(service.status,payload.class_id,payload.source_meeting_id,payload.start,payload.end,payload.import_id)
    except PermissionError:
        raise HTTPException(403,'同步来源超出授权范围') from None
    except Exception:
        raise HTTPException(409,'同步状态待核验') from None
    return {'success':True,'data':data}
