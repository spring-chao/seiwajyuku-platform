import base64
import hashlib
import hmac
import io
import json
import time
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
from app.api.reading_sync_automation import router
from app.services import reading_sync_automation as sync
from app.services import credit_source_imports as sources
from app.services import credit_posting_jobs as jobs
from app.db import transaction, execute, fetch_one
from test_credit_source_excel import source_fixture


def raw_report(name='合成学员', extra=False, bad_date=False):
    wb=Workbook();sheet=wb.active;sheet.title='导出数据'
    sheet.append(['姓名','账户','小组','日期','内容标题','完成率','打卡状态','读书','分享','回应','录音','准时'])
    sheet.append([name,'138****1234','合成组','2026-10-09','合成任务','100%','已完成','-','-','-','按时完成','按时完成'])
    if extra:sheet.append(['合成待核对','139****9999','合成组','2026-10-01' if bad_date else '2026-10-09','合成任务','100%','已完成','-','-','-','按时完成','按时完成'])
    stream=io.BytesIO();wb.save(stream);return stream.getvalue()


def configure(monkeypatch, f):
    mappings=[{'class_id':f['class_id'],'source_title':'合成一班','source_meeting_id':'synthetic-meeting'}]
    mappings += [{'class_id':f'synthetic-{i}','source_title':f'合成{i}班','source_meeting_id':f'synthetic-meeting-{i}'} for i in range(21)]
    monkeypatch.setenv('READING_SYNC_ACTOR_ID',str(f['actor']))
    monkeypatch.setenv('READING_SYNC_CLASS_MAP',json.dumps(mappings))
    monkeypatch.setattr(jobs,'_trigger',lambda *args:None)
    with transaction() as c:
        execute(c,"UPDATE members SET phone_last4='1234',phone_masked='138****1234' WHERE id=?",(f['member_id'],))


def test_real_source_automatically_submits_durable_posting_and_retries_do_not_duplicate(source_fixture,monkeypatch):
    f=source_fixture;configure(monkeypatch,f)
    content=raw_report(f['name'])
    before=fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']
    a=sync.ingest(content,f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09')
    assert a['status']=='POSTING_PENDING'
    source=sources.get_import(a['import_id'],f['actor'])
    assert jobs.run_job(source['posting_job']['id'],source['settlement_fingerprint'])['completed']==1
    b=sync.ingest(content,f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09')
    assert b['status']=='SUCCESS' and b['source_reused']
    assert b['import_id']==a['import_id']
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before+1


def test_machine_subject_never_defaults_to_administrator(monkeypatch):
    monkeypatch.delenv('READING_SYNC_ACTOR_ID',raising=False)
    monkeypatch.delenv('READING_SYNC_ACTOR_USERNAME',raising=False)
    with pytest.raises(sync.SyncError,match='ACTOR_NOT_CONFIGURED'):sync.configured_actor()


def test_plan_refreshes_live_posting_without_hydrating_all_member_details(source_fixture,monkeypatch):
    f=source_fixture;configure(monkeypatch,f)
    created=sync.ingest(raw_report(f['name']),f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09')
    source=sources.get_import(created['import_id'],f['actor'])
    jobs.run_job(source['posting_job']['id'],source['settlement_fingerprint'])
    monkeypatch.setattr(sources,'get_import',lambda *args:pytest.fail('planning must use bounded summaries'))
    planned=sync.plan([{'start':'2026-10-05','end':'2026-10-09'}])
    first=next(t for t in planned if t['class_id']==f['class_id'])
    assert len(planned)==22 and first['skip'] and first['previous']['status']=='SUCCESS'
    assert first['previous']['posted_entries']==1


def test_packaged_class_manifest_pins_22_unique_formal_sources(monkeypatch):
    monkeypatch.delenv('READING_SYNC_CLASS_MAP',raising=False)
    monkeypatch.setenv('READING_SYNC_ACTOR_ID','1')
    actor,classes=sync.configuration()
    assert actor==1 and len(classes)==22
    assert len({r['source_meeting_id'] for r in classes.values()})==22
    assert all(r['search_term']==r['source_title'] for r in classes.values())
    assert all(not any(word in r['source_title'] for word in ('补课','补卡','塾友之家','工作人员')) for r in classes.values())


def test_failed_interval_is_reported_and_never_treated_as_missing_or_complete(source_fixture,monkeypatch):
    f=source_fixture;configure(monkeypatch,f)
    sync.outcome(f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09','SOURCE_OR_POSTING_NOT_VERIFIED')
    task=next(t for t in sync.plan([{'start':'2026-10-05','end':'2026-10-09'}]) if t['class_id']==f['class_id'])
    assert not task['skip'] and task['previous']['status']=='FAILED'
    assert task['previous']['error_code']=='SOURCE_OR_POSTING_NOT_VERIFIED'


def test_identity_exceptions_do_not_block_valid_rows_or_mark_whole_class_success(source_fixture,monkeypatch):
    f=source_fixture;configure(monkeypatch,f)
    a=sync.ingest(raw_report(f['name'],extra=True),f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09')
    assert a['status']=='PARTIAL_PENDING_IDENTITY' and a['unmatched_rows']==1 and a['saved_rows']==1
    source=sources.get_import(a['import_id'],f['actor'])
    jobs.run_job(source['posting_job']['id'],source['settlement_fingerprint'])
    assert sync.status(f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09',a['import_id'])['status']=='PARTIAL_PENDING_IDENTITY'
    planned=sync.plan([{'start':'2026-10-05','end':'2026-10-09'}])
    selected=next(t for t in planned if t['class_id']==f['class_id'])
    assert not selected['skip'] and selected['previous']['unmatched_rows']==1


def test_invalid_date_on_unmatched_identity_row_rejects_the_entire_workbook_before_writes(monkeypatch):
    monkeypatch.setattr(sources,'preview',lambda *a:pytest.fail('must not preview/write'))
    with pytest.raises(sync.SyncError,match='OUT_OF_RANGE'):
        sync.validate_workbook(raw_report(extra=True,bad_date=True),'2026-10-05','2026-10-09')


@pytest.mark.parametrize('start,end',[('2026-10-26','2026-11-01'),('2026-10-01','2026-10-31'),('2026-10-09','2026-10-05')])
def test_broad_or_cross_month_ranges_rejected(start,end):
    with pytest.raises(sync.SyncError):sync.validate_interval(start,end,today=date(2026,11,2))


def test_exact_source_and_class_scope_are_enforced_before_import(source_fixture,monkeypatch):
    f=source_fixture;configure(monkeypatch,f)
    with pytest.raises(PermissionError):sync.scope(f['class_id'],'another-meeting')
    with pytest.raises(PermissionError):sync.scope('unconfigured','synthetic-meeting')


def test_original_empty_export_is_audited_without_creating_credit_batches(monkeypatch):
    monkeypatch.setattr(sync,'scope',lambda *args:1)
    reports=[]
    monkeypatch.setattr(sync,'audit',lambda *args:reports.append(args[-1]))
    monkeypatch.setattr(sources,'preview',lambda *args:pytest.fail('empty report must not create sources or credits'))
    wb=Workbook();sheet=wb.active;sheet.title='导出数据'
    sheet.append(['姓名','账户','小组','日期','内容标题','完成率','打卡状态','读书','分享','回应','录音','准时'])
    stream=io.BytesIO();wb.save(stream)
    result=sync.ingest(stream.getvalue(),'synthetic','synthetic','2026-10-11','2026-10-11')
    assert result['status']=='SUCCESS' and result['saved_rows']==0 and result['posting_completed']
    assert len(reports)==1


def test_same_facts_in_a_differently_encoded_workbook_never_add_points_twice(source_fixture,monkeypatch):
    from openpyxl import load_workbook
    f=source_fixture;configure(monkeypatch,f)
    data=raw_report(f['name'])
    first=sync.ingest(data,f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09')
    source=sources.get_import(first['import_id'],f['actor'])
    jobs.run_job(source['posting_job']['id'],source['settlement_fingerprint'])
    before=fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']
    wb=load_workbook(io.BytesIO(data));wb.properties.title='Different ZIP metadata, same business facts'
    output=io.BytesIO();wb.save(output)
    assert hashlib.sha256(output.getvalue()).digest()!=hashlib.sha256(data).digest()
    second=sync.ingest(output.getvalue(),f['class_id'],'synthetic-meeting','2026-10-05','2026-10-09')
    new_source=sources.get_import(second['import_id'],f['actor'])
    if new_source['posting_job']:jobs.run_job(new_source['posting_job']['id'],new_source['settlement_fingerprint'])
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before


def test_http_signature_binds_body_method_path_and_time_and_never_echoes_workbook(monkeypatch):
    app=FastAPI();app.include_router(router)
    monkeypatch.setenv('READING_SYNC_SIGNING_KEY','synthetic-key-'+'a'*40)
    monkeypatch.setenv('READING_SYNC_ENABLED','true')
    path='/api/v1/internal/reading-sync/imports'
    stamp=str(int(time.time()));body=b'{"workbook_base64":"private-workbook-data"}'
    material='\n'.join(['POST',path,stamp,hashlib.sha256(body).hexdigest()])
    signature=hmac.new(('synthetic-key-'+'a'*40).encode(),material.encode(),hashlib.sha256).hexdigest()
    headers={'X-Reading-Sync-Time':stamp,'X-Reading-Sync-Signature':signature,'Content-Type':'application/json'}
    with TestClient(app) as client:
        assert client.post(path,content=body).status_code==401
        assert client.post(path,content=body+b' ',headers=headers).status_code==401
        assert client.post('/api/v1/internal/reading-sync/status',content=body,headers=headers).status_code==401
        response=client.post(path,content=body,headers=headers)
        assert response.status_code==400 and 'private-workbook-data' not in response.text
