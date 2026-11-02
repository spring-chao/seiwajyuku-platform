"""Narrow machine delegation for approved reading imports; reuse all ledger gates."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
from decimal import Decimal

from app.db import connect, execute, transaction
from app.services import credit_source_imports as sources
from app.services import credit_posting_jobs as jobs
from app.services.audit import write_audit


class SyncError(ValueError):
    pass


# Explicit compatibility map approved for this 22-class integration. Resolve
# names once to organization IDs for deployment; ingestion uses only pinned IDs.
CLASS_TARGETS = [
    ('吴越一班','吴江分中心','吴江分中心吴越一班'),
    ('吴越二班','吴江分中心','吴江分中心吴越二班'),
    ('吴越三班','吴江分中心','吴江分中心吴越三班'),
    ('圆融一班','园区分中心','园区圆融一班'),
    ('圆融三班','园区分中心','园区圆融三班'),
    ('圆融四班','园区分中心','园区圆融四班'),
    ('圆融五班','园区分中心','园区圆融五班'),
    ('真干班','姑苏相城分中心','姑苏相城分中心真干班'),
    ('真干二班','姑苏相城分中心','姑苏相城真干二班'),
    ('真干三班','姑苏相城分中心','姑苏相城真干三班'),
    ('苏州不一班','姑苏相城分中心','姑苏相城分中心不一班'),
    ('振华一班','张家港分中心','张家港振华一班'),
    ('振华二班','张家港分中心','张家港振华二班'),
    ('新吴一班','新吴分中心','新吴一班'),
    ('新吴二班','新吴分中心','新吴二班'),
    ('新吴三班','新吴分中心','新吴三班'),
    ('炎武一班','昆山分中心','昆山炎武一班'),
    ('炎武二班','昆山分中心','昆山炎武二班'),
    ('炎武三班','昆山分中心','昆山炎武三班'),
    ('炎武四班','昆山分中心','昆山炎武四班'),
    ('黄埔一班','苏州塾','黄埔一班'),
    ('黄埔二班','苏州塾','黄埔二班'),
]


def configured_actor():
    actor=int(os.getenv('READING_SYNC_ACTOR_ID','0'))
    if actor>0:return actor
    # Explicit deployment-only subject binding, never an implicit administrator
    # fallback or a caller-supplied identity. Replace with the verified numeric
    # ID after configuration discovery; this performs no IAM writes.
    username=os.getenv('READING_SYNC_ACTOR_USERNAME','').strip()
    if not username:raise SyncError('SYNC_ACTOR_NOT_CONFIGURED')
    c=connect()
    try:
        rows=execute(c,'SELECT id FROM app_users WHERE username=? AND is_active=1',(username,)).fetchall()
    finally:c.close()
    if len(rows)!=1:raise SyncError('SYNC_ACTOR_NOT_UNIQUE')
    return int(rows[0]['id'])


def configuration_candidates():
    from app.services.learning_org_catalog import class_catalog
    actor=configured_actor()
    sources._principal(actor)
    c=connect()
    try:
        catalog=class_catalog(c,sources.effective_scope(actor))
    finally:
        c.close()
    result=[]
    for name,parent,title in CLASS_TARGETS:
        matches=[r for r in catalog if r['name']==name and r['parent_name']==parent]
        if len(matches)!=1:raise SyncError('CLASS_MAPPING_REQUIRES_REVIEW')
        result.append({'class_id':matches[0]['id'],'class_name':name,'center_name':parent,'source_title':title,'search_term':name})
    return {'actor_id':actor,'classes':result}


def configuration():
    # No implicit administrator fallback. Deployment must explicitly bind the
    # accountable operator and all exact organization/source IDs.
    actor = int(os.getenv('READING_SYNC_ACTOR_ID', '0'))
    raw = json.loads(os.getenv('READING_SYNC_CLASS_MAP') or Path(__file__).with_name('reading_sync_classes.json').read_text(encoding='utf-8'))
    if actor <= 0 or not isinstance(raw, list) or len(raw) != 22:
        raise SyncError('SYNC_CONFIGURATION_INCOMPLETE')
    required = {'class_id', 'source_title', 'source_meeting_id'}
    if any(not isinstance(r, dict) or not required <= r.keys() or
           any(not isinstance(r[k], str) or not r[k].strip() for k in required) or
           any(word in r['source_title'] for word in ('补课', '补卡', '塾友之家', '工作人员')) for r in raw):
        raise SyncError('SYNC_CONFIGURATION_INVALID')
    if len({r['class_id'] for r in raw}) != 22 or len({r['source_meeting_id'] for r in raw}) != 22:
        raise SyncError('SYNC_CONFIGURATION_AMBIGUOUS')
    return actor, {r['class_id']: r for r in raw}


def validate_interval(start, end, today=None):
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or last >= today or first < date(2026, 10, 1):
        raise SyncError('SYNC_INTERVAL_INVALID')
    if first.replace(day=1) != last.replace(day=1) or last-first > timedelta(days=6):
        raise SyncError('SYNC_INTERVAL_NOT_MONTH_SEGMENT')
    if first-timedelta(days=first.weekday()) != last-timedelta(days=last.weekday()):
        raise SyncError('SYNC_INTERVAL_NOT_WEEK_SEGMENT')
    return first, last


def validate_workbook(content, start, end):
    """Check every raw date BEFORE identity errors can exclude any source row."""
    first, last = validate_interval(start, end)
    try:
        rows = sources._parse(content, 'DAILY_READING')
    except sources.credits.LearningCreditError as exc:
        # The original parser has already checked ZIP limits, sheet name and
        # exact headers before raising this specific empty-report result.
        if str(exc)=='原表没有读书打卡记录':return 0
        raise
    for row in rows:
        if not row.get('original_report'):
            raise SyncError('SYNC_REQUIRES_ORIGINAL_REPORT')
        try:
            value = row['raw']['日期']
            occurred = value.date() if isinstance(value, datetime) else value if isinstance(value, date) else date.fromisoformat(str(value))
        except (KeyError, TypeError, ValueError):
            raise SyncError('SYNC_WORKBOOK_DATE_INVALID') from None
        if not first <= occurred <= last:
            raise SyncError('SYNC_WORKBOOK_DATE_OUT_OF_RANGE')
    return len(rows)


def scope(class_id, source_meeting_id):
    actor, classes = configuration()
    if class_id not in classes or classes[class_id]['source_meeting_id'] != source_meeting_id:
        raise PermissionError('SYNC_SOURCE_OUT_OF_SCOPE')
    # Delegation never grants the actor permissions it does not currently hold.
    sources._principal(actor, 'DAILY_READING', write=True)
    sources._write_scope(actor, class_id, 'DAILY_READING')
    if not sources._has_scope(actor, class_id, ('plans:credit_settlement_post',)):
        raise PermissionError('SYNC_POST_OUT_OF_SCOPE')
    return actor


def receipt_key(class_id, start, end):
    return hashlib.sha256(json.dumps([class_id,start,end],separators=(',',':')).encode()).hexdigest()


def audit(actor, class_id, start, end, report):
    with transaction() as c:
        write_audit(c, actor_user_id=actor, action='learning.reading_sync.machine',
                    resource_type='reading_sync_interval', resource_id=receipt_key(class_id,start,end),
                    org_unit_id=class_id, purpose='用户授权的22班读书来源自动保存及自动入账',
                    after={**report, 'period_start':start,'period_end':end,'caller':'reading-sync-worker'})


def ingest(content, class_id, source_meeting_id, start, end):
    actor = scope(class_id, source_meeting_id)
    count = validate_workbook(content, start, end)
    if count==0:
        report={'status':'SUCCESS','export_rows':0,'saved_rows':0,'unmatched_rows':0,
                'posting_completed':True,'source_only':True,'empty_report':True,
                'file_sha256':hashlib.sha256(content).hexdigest()}
        audit(actor,class_id,start,end,report)
        return report
    preview = sources.preview(content,'DAILY_READING',class_id,'{}','',actor)
    if any(Decimal(str(r.get('proposed_points') or 0)) not in {Decimal(0),Decimal(1)}
           for r in preview['rows'] if not r['errors']):
        raise SyncError('SYNC_UNEXPECTED_CREDIT_VALUE')
    if not preview['fingerprint']:
        raise SyncError('SYNC_NO_VALID_IDENTITY_ROWS')
    # Register's frozen fingerprint, scope, locks and business-key dedup remain
    # authoritative, including retries after an uncertain HTTP response.
    saved = sources.register(content,'DAILY_READING',class_id,'{}','',
        f'reading_{source_meeting_id}_{start}_{end}.xlsx',preview['fingerprint'],actor)
    source = sources.get_import(saved['id'], actor)
    active = source.get('posting_job') or {}
    if active.get('status') not in {'PENDING','RUNNING','NEEDS_REVIEW','PAUSED'}:
        source = sources.create_batches(saved['id'], actor)
        if source['batches'] and any(int(b['blocked_count']) for b in source['batches']):
            report = {'import_id':saved['id'],'status':'NEEDS_REVIEW','export_rows':count,
                      'saved_rows':source['row_count'],'unmatched_rows':preview['error_count'],
                      'file_sha256':hashlib.sha256(content).hexdigest()}
            audit(actor,class_id,start,end,report)
            return report
        if source['batches']:
            jobs.submit_and_dispatch(saved['id'],actor,source['settlement_fingerprint'])
    report = status(class_id, source_meeting_id, start, end, saved['id'])
    report.update(export_rows=count, unmatched_rows=preview['error_count'], file_sha256=hashlib.sha256(content).hexdigest(),
                  source_reused=bool(saved.get('idempotent')))
    if preview['error_count']:
        report['status']='PARTIAL_PENDING_IDENTITY'
    audit(actor,class_id,start,end,report)
    return report


def status(class_id, source_meeting_id, start, end, import_id):
    actor = scope(class_id, source_meeting_id)
    validate_interval(start,end)
    source = sources.get_import(import_id, actor)
    if source['source_kind'] != 'DAILY_READING' or source['class_org_unit_id'] != class_id:
        raise PermissionError('SYNC_IMPORT_OUT_OF_SCOPE')
    if any(not start <= str(r['occurred_on'])[:10] <= end for r in source['rows']):
        raise PermissionError('SYNC_IMPORT_INTERVAL_CHANGED')
    state = source['posting_progress']
    job = source.get('posting_job') or {}
    if job.get('status') in {'NEEDS_REVIEW','PAUSED','FAILED'}:
        result='NEEDS_REVIEW'
    elif source.get('ignored_rows'):
        result='PARTIAL_PENDING_IDENTITY'
    elif not source['batches'] and not source['source_only']:
        result='SOURCE_SAVED_POSTING_NOT_CREATED'
    elif state['completed'] or source['source_only']:
        result='SUCCESS'
    else:
        result='POSTING_PENDING'
    return {'import_id':import_id,'status':result,'saved_rows':source['row_count'],
            'job_status':job.get('status'),'posting_completed':bool(state['completed'] and (source['batches'] or source['source_only'])),
            'source_only':bool(source['source_only']),'unmatched_rows':len(source.get('ignored_rows',[]))}


def plan(intervals):
    actor, classes = configuration()
    pending=[]
    for interval in intervals:
        start,end=interval['start'],interval['end']
        validate_interval(start,end)
        for class_id,mapping in classes.items():
            scope(class_id,mapping['source_meeting_id'])
            c=connect()
            try:
                row=execute(c,"SELECT after_json FROM audit_logs WHERE action='learning.reading_sync.machine' AND resource_type='reading_sync_interval' AND resource_id=? AND org_unit_id=? ORDER BY id DESC LIMIT 1",(receipt_key(class_id,start,end),class_id)).fetchone()
            finally:
                c.close()
            previous=json.loads(row['after_json']) if row else {}
            live=status(class_id,mapping['source_meeting_id'],start,end,previous['import_id']) if previous.get('import_id') else previous if previous.get('empty_report') and previous.get('status')=='SUCCESS' else None
            pending.append({**mapping,'start':start,'end':end,'previous':live,
                            'skip':bool(live and live['status']=='SUCCESS')})
    return pending


def lookup(class_id, source_meeting_id, start, end, digest):
    actor=scope(class_id,source_meeting_id)
    validate_interval(start,end)
    c=connect()
    try:
        rows=execute(c,"SELECT id FROM learning_credit_source_imports WHERE file_sha256=? AND class_org_unit_id=? AND source_kind='DAILY_READING' ORDER BY id DESC",(digest,class_id)).fetchall()
    finally:
        c.close()
    for row in rows:
        result=status(class_id,source_meeting_id,start,end,int(row['id']))
        return result
    return {'import_id':None,'status':'NOT_SAVED'}


def resume(class_id, source_meeting_id, start, end, import_id):
    existing=status(class_id,source_meeting_id,start,end,import_id)
    if existing['status']!='SOURCE_SAVED_POSTING_NOT_CREATED':return existing
    actor=scope(class_id,source_meeting_id)
    source=sources.create_batches(import_id,actor)
    if any(int(b['blocked_count']) for b in source['batches']):
        return {**existing,'status':'NEEDS_REVIEW'}
    if source['batches']:jobs.submit_and_dispatch(import_id,actor,source['settlement_fingerprint'])
    result=status(class_id,source_meeting_id,start,end,import_id)
    audit(actor,class_id,start,end,result)
    return result


def outcome(class_id, source_meeting_id, start, end, error_code):
    actor=scope(class_id,source_meeting_id)
    validate_interval(start,end)
    report={'status':'FAILED','error_code':error_code}
    audit(actor,class_id,start,end,report)
    return report
