import hashlib
import json
from threading import RLock
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import current_user
from app.services import monthly_core_reports as reports
from app.services import monthly_core_export as export
from app.services import credit_source_imports as sources
from app.services import learning_credits as credits
from app.services.study_evidence_bridge import BridgeObjectError
from test_reading_weekly_export import reading_fixture
from test_credit_source_excel import source_fixture


class Store:
    def __init__(self):self.rows={};self.lock=RLock()
    def request(self,method,key,body=None,content_type=None,**options):
        assert options['operation']=='monthly-core-object'
        with self.lock:
            existing=self.rows.get(key)
            if method=='GET':
                if existing is None:raise BridgeObjectError(404)
                return existing
            assert method=='PUT'
            expected=options.get('if_match')
            if expected and (not existing or existing[1]!=expected) or not expected and existing:raise BridgeObjectError(412)
            etag='"'+hashlib.md5(body).hexdigest()+'"'
            self.rows[key]=(body,etag)
            return b'',etag


@pytest.fixture
def cache(monkeypatch):
    store=Store();monkeypatch.setattr(reports,'_store',lambda:store)
    return store


def test_prepare_returns_without_generation_and_ready_download_never_recalculates(reading_fixture,cache):
    f=reading_fixture
    with patch.object(reports,'_dispatch') as dispatch,patch.object(export,'export_monthly',return_value=b'PK-workbook') as generate:
        assert reports.status(f['actor'],f['center_id'],'2026-10')['status']=='MISSING'
        assert reports.prepare(f['actor'],f['center_id'],'2026-10')['status']=='PENDING'
        reports.prepare(f['actor'],f['center_id'],'2026-10')
        assert dispatch.call_count==1 and generate.call_count==0
        context,key=reports._context(f['actor'],f['center_id'],'2026-10')
        assert reports.run('2026-10',key.rsplit('/',1)[1])['completed']==1
        assert reports.run('2026-10',key.rsplit('/',1)[1])['completed']==0
        assert reports.status(f['actor'],f['center_id'],'2026-10')['status']=='READY'
        assert reports.download(f['actor'],f['center_id'],'2026-10')==b'PK-workbook'
        assert reports.download(f['actor'],f['center_id'],'2026-10')==b'PK-workbook'
        assert generate.call_count==1
        with patch.object(sources,'effective_scope',return_value=set()),pytest.raises(PermissionError):reports.download(f['actor'],f['center_id'],'2026-10')


def test_concurrent_prepare_claims_only_one_job(cache):
    context={'month':'2026-10','center_id':'synthetic','cutoff':'2026-10-29'}
    key='monthly-core/production/2026-10/'+'a'*64
    with patch.object(reports,'_context',return_value=(context,key)),patch.object(reports,'_dispatch') as dispatch:
        with ThreadPoolExecutor(max_workers=8) as pool:
            result=list(pool.map(lambda _:reports.prepare(1,'synthetic','2026-10'),range(20)))
        assert all(r['status']=='PENDING' for r in result)
        assert dispatch.call_count==1


def test_failed_and_expired_jobs_retry_with_new_attempt_and_scope_changes_do_not_publish(reading_fixture,cache):
    f=reading_fixture
    with patch.object(reports,'_dispatch'),patch.object(export,'export_monthly',side_effect=ValueError('private source failure')):
        reports.prepare(f['actor'],f['center_id'],'2026-10')
        context,key=reports._context(f['actor'],f['center_id'],'2026-10')
        assert reports.run('2026-10',key.rsplit('/',1)[1])['failed']==1
        first,_=reports._state(cache,key)
        assert 'private' not in json.dumps(first)
        reports.prepare(f['actor'],f['center_id'],'2026-10')
        second,_=reports._state(cache,key)
        assert first['attempt']!=second['attempt']
    with patch.object(export,'export_monthly',return_value=b'PK-workbook'),patch.object(reports,'_context',side_effect=[(context,key),PermissionError('revoked')]):
        assert reports.run('2026-10',key.rsplit('/',1)[1])['failed']==1
    assert not any(name.endswith('.xlsx') for name in cache.rows)


def test_partial_scope_uses_separate_snapshot_and_cache_integrity_is_checked(reading_fixture,cache):
    f=reading_fixture
    full,key=reports._context(f['actor'],f['center_id'],'2026-10')
    with patch.object(sources,'effective_scope',return_value={f['class_id']}):
        partial,partial_key=reports._context(f['actor'],f['center_id'],'2026-10')
        assert partial_key!=key and full['center_activity'] and not partial['center_activity']
    with patch.object(reports,'_dispatch'),patch.object(export,'export_monthly',return_value=b'PK-workbook'):
        reports.prepare(f['actor'],f['center_id'],'2026-10');reports.run('2026-10',key.rsplit('/',1)[1])
    ready,_=reports._state(cache,key);cache.rows[key+'/'+ready['attempt']+'.xlsx']=(b'corrupt','"'+32*'a'+'"')
    with pytest.raises(ValueError,match='校验'):reports.download(f['actor'],f['center_id'],'2026-10')


def test_api_requires_auth_and_download_missing_never_generates(reading_fixture,cache):
    f=reading_fixture;client=TestClient(app)
    assert client.post('/api/v1/internal/monthly-core-reports/sweep',json={}).status_code==401
    assert client.post('/api/v1/internal/monthly-core-reports/verify',json={}).status_code==401
    payload={'center_id':f['center_id'],'month':'2026-10'}
    assert client.post('/api/v1/learning-credits/excel/monthly-core-prepare',json=payload).status_code==401
    app.dependency_overrides[current_user]=lambda:{'id':f['actor'],'permissions':credits.user_context(f['actor'])['permissions']}
    try:
        with patch.object(export,'export_monthly') as generate:
            response=client.get('/api/v1/learning-credits/excel/monthly-core-export',params=payload)
            assert response.status_code==400 and generate.call_count==0
        with patch.object(reports,'_dispatch'):
            assert client.post('/api/v1/learning-credits/excel/monthly-core-prepare',json=payload).json()['data']['status']=='PENDING'
    finally:app.dependency_overrides.pop(current_user,None)


def test_first_day_scheduler_targets_previous_month_and_never_regenerates_ready(cache):
    from datetime import datetime
    context={'month':'2026-10','center_id':'synthetic','cutoff':'2026-10-31'}
    key='monthly-core/production/2026-10/'+'b'*64
    with patch.object(reports,'datetime') as clock,patch.object(reports,'_scheduler_context',return_value=(1,{'synthetic':{}})),patch.object(reports,'_context',return_value=(context,key)),patch.object(export,'export_monthly',return_value=b'PK-snapshot') as generate:
        clock.now.return_value=datetime(2026,11,1,2)
        assert reports.sweep()['completed']==1
        result=reports.sweep()
        assert result['month']=='2026-10' and result['more'] is False
        assert generate.call_count==1


def test_worker_reports_failure_without_leaking_errors_and_retries_are_bounded(cache):
    from datetime import datetime
    context={'month':'2026-10','center_id':'synthetic','cutoff':'2026-10-31'}
    key='monthly-core/production/2026-10/'+'c'*64
    with patch.object(reports,'datetime') as clock,patch.object(reports,'_scheduler_context',return_value=(1,{'synthetic':{}})),patch.object(reports,'_context',return_value=(context,key)),patch.object(export,'export_monthly',side_effect=ValueError('private detail')) as generate:
        clock.now.return_value=datetime(2026,11,1,2)
        for _ in range(3):assert reports.sweep()['failed']==1
        assert reports.sweep()['more'] is False and generate.call_count==3
