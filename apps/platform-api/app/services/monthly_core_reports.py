"""Private, scope-bound monthly workbooks generated outside download requests."""
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta
from uuid import uuid4
from urllib.request import Request, build_opener

from app.db import connect, execute
from app.core.settings import get_settings
from app.services import monthly_core_export as report
from app.services.audit import write_audit
from app.services.study_evidence_bridge import StorageBridge, BridgeObjectError, NoRedirect

XLSX='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
LIMIT=20*1024*1024
LEASE=600


def _store():
    return StorageBridge(os.environ.get('CLOUDBASE_STORAGE_BRIDGE_URL',''),get_settings().study_evidence_cleanup_token,
                         os.environ.get('CLOUDBASE_STORAGE_BUCKET',''),os.environ.get('CLOUDBASE_STORAGE_REGION',''))


def _object(store, method, key, body=None, content_type=None, etag=None):
    return store.request(method,key,body,content_type,operation='monthly-core-object',if_match=etag,with_metadata=True)


def _state(store, key):
    try:
        raw,etag=_object(store,'GET',key+'/state.json')
        if len(raw)>65536: raise ValueError('报表任务状态无效')
        return json.loads(raw),etag
    except BridgeObjectError as exc:
        if exc.status_code==404:return None,None
        raise


def _save(store,key,state,etag=None):
    return _object(store,'PUT',key+'/state.json',json.dumps(state,sort_keys=True).encode(),'application/json',etag)


def _context(actor,center_id,month):
    first,cutoff=report.period(month)
    if cutoff<first:raise ValueError('本月尚无已完成统计日期，请选择上月')
    c=connect()
    try:
        centers,scope=report._selection(c,actor)
        center=centers.get(center_id)
        if not center:raise PermissionError('分中心不在核心数据导出授权范围')
        context={'layout':'wujiang-v3','month':month,'cutoff':str(cutoff),'center_id':center_id,
                 'classes':sorted({cid for item in center['classes'] for cid in item['related_class_ids']}),
                 'center_activity':scope is None or center_id in scope}
        digest=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
        return context,f'monthly-core/production/{month}/{digest}'
    finally:c.close()


def _public(state,context):
    if not state:return {'status':'MISSING','cutoff':context['cutoff']}
    if state.get('context')!=context:raise PermissionError('报表范围不匹配，请重新生成')
    status=state['status']
    if status in ('PENDING','RUNNING') and time.time()-state['updated_at']>LEASE:status='EXPIRED'
    return {'status':status,'cutoff':context['cutoff'],'generated_at':state.get('generated_at')}


def status(actor,center_id,month):
    context,key=_context(actor,center_id,month)
    state,_=_state(_store(),key)
    return _public(state,context)


def _dispatch(key):
    store=_store()
    parts=key.split('/')
    payload={'operation':'monthly-core-dispatch','month':parts[2],'report_id':parts[3]}
    req=Request(store.url,data=json.dumps(payload).encode(),method='POST',headers={
        'Content-Type':'application/json','X-Study-Evidence-Cleanup-Token':store.token})
    with build_opener(NoRedirect()).open(req,timeout=10) as response:
        if response.status!=202:raise ValueError('报表后台任务启动待确认，请稍后查看状态')


def prepare(actor,center_id,month):
    context,key=_context(actor,center_id,month);store=_store()
    state,etag=_state(store,key); current=_public(state,context)
    if current['status'] in ('READY','PENDING','RUNNING'):return current
    pending={'context':context,'actor':actor,'status':'PENDING','attempt':uuid4().hex,'updated_at':time.time(),
             'attempt_count':(state or {}).get('attempt_count',0)+1,'trigger':'manual'}
    try:_save(store,key,pending,etag)
    except BridgeObjectError as exc:
        if exc.status_code in (409,412):return status(actor,center_id,month)
        raise
    try:_dispatch(key)
    except Exception:
        # Keep an uncertain dispatch pending: a worker may already be running.
        return {'status':'PENDING','cutoff':context['cutoff'],'dispatch_uncertain':True}
    return _public(pending,context)


def download(actor,center_id,month):
    context,key=_context(actor,center_id,month); store=_store(); state,_=_state(store,key)
    if _public(state,context)['status']!='READY':raise ValueError('报表尚未生成，请先启动后台生成')
    raw,_=_object(store,'GET',key+'/'+state['attempt']+'.xlsx')
    if len(raw)>LIMIT or hashlib.sha256(raw).hexdigest()!=state['sha256']:raise ValueError('报表文件校验失败，请联系管理员')
    c=connect()
    try:
        write_audit(c,actor_user_id=actor,action='learning.monthly_core.download',resource_type='monthly_core_report',
                    org_unit_id=center_id,after={'month':month,'cutoff':context['cutoff'],'cached':True,'contact_fields':False})
        c.commit()
    finally:c.close()
    return raw


def run(month,report_id):
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',month) or not re.fullmatch(r'[a-f0-9]{64}',report_id):raise ValueError('任务编号无效')
    key=f'monthly-core/production/{month}/{report_id}';store=_store();state,etag=_state(store,key)
    if not state or state['status'] not in ('PENDING','RUNNING'):return {'completed':0,'failed':0,'busy':False}
    if state['status']=='RUNNING' and time.time()-state['updated_at']<LEASE:return {'completed':0,'failed':0,'busy':True}
    running={**state,'status':'RUNNING','updated_at':time.time()}
    try:_,lease_etag=_save(store,key,running,etag)
    except BridgeObjectError as exc:
        if exc.status_code in (409,412):return {'completed':0,'failed':0,'busy':True}
        raise
    if not lease_etag:raise ValueError('报表任务锁校验失败')
    try:
        context,actual_key=_context(state['actor'],state['context']['center_id'],month)
        if actual_key!=key or context!=state['context']:raise PermissionError('权限或统计日期已变化')
        raw=report.export_monthly(state['actor'],context['center_id'],month)
        # Recheck authorization and period before publishing generated bytes.
        if _context(state['actor'],context['center_id'],month)!=(context,key):raise PermissionError('报表范围已变化')
        if len(raw)>LIMIT:raise ValueError('报表文件过大')
        _object(store,'PUT',key+'/'+state['attempt']+'.xlsx',raw,XLSX)
        ready={**running,'status':'READY','sha256':hashlib.sha256(raw).hexdigest(),
               'updated_at':time.time(),'generated_at':datetime.now(report.activity.SHANGHAI).isoformat()}
        _save(store,key,ready,lease_etag)
        return {'completed':1,'failed':0,'busy':False}
    except Exception:
        try:_save(store,key,{**running,'status':'FAILED','updated_at':time.time()},lease_etag)
        except Exception:pass
        return {'completed':0,'failed':1,'busy':False}


def _scheduler_context():
    c=connect()
    try:
        principal=execute(c,'SELECT id FROM app_users WHERE username=? AND is_active=1',(get_settings().bootstrap_admin_username,)).fetchone()
        if not principal:raise PermissionError('定时报表缺少有效运营账号')
        actor=int(principal['id']);centers,scope=report._selection(c,actor)
        if scope is not None:raise PermissionError('定时报表账号不具备全分中心导出范围')
        return actor,centers
    finally:c.close()


def sweep():
    """One centre per scheduler call; existing bootstrap operator grants remain authoritative."""
    local=datetime.now(report.activity.SHANGHAI)
    month=(local.date().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
    actor,centers=_scheduler_context()
    failed=0;busy=False
    for center_id in sorted(centers):
        current=status(actor,center_id,month)
        if current['status'] in ('MISSING','EXPIRED','FAILED'):
            # The scheduler already owns a durable function invocation; generate one centre here.
            context,key=_context(actor,center_id,month);store=_store();previous,etag=_state(store,key)
            attempts=(previous or {}).get('attempt_count',0)
            if attempts>=3:
                failed+=1;continue
            try:_save(store,key,{'context':context,'actor':actor,'status':'PENDING','attempt':uuid4().hex,'updated_at':time.time(),
                                'attempt_count':attempts+1,'trigger':'scheduled'},etag)
            except BridgeObjectError as exc:
                if exc.status_code not in (409,412):raise
            result=run(month,key.rsplit('/',1)[1])
            return {**result,'more':True,'month':month}
        if current['status']=='PENDING':return {**run(month,_context(actor,center_id,month)[1].rsplit('/',1)[1]),'more':True,'month':month}
        if current['status']=='RUNNING':busy=True
    return {'completed':0,'failed':failed,'busy':busy,'more':busy,'month':month}


def verify_ready():
    """Counts and timings only; never transmit the report contents through maintenance."""
    month=(datetime.now(report.activity.SHANGHAI).date().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
    actor,centers=_scheduler_context();ready=failed=slow=0;maximum=0.0
    for center_id in sorted(centers):
        try:
            if status(actor,center_id,month)['status']!='READY':continue
            start=time.perf_counter();download(actor,center_id,month);elapsed=time.perf_counter()-start
            ready+=1;maximum=max(maximum,elapsed);slow+=elapsed>=30
        except Exception:failed+=1
    return {'month':month,'total':len(centers),'ready':ready,'failed':failed,'slow':slow,'max_download_seconds':round(maximum,3)}
