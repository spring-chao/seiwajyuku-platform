"""Durable confirmation/worker tests run in SQLite and disposable MySQL CI."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth import current_user
from app.db import connect, execute, fetch_one, transaction
from app.main import app
from app.services import credit_posting_jobs as jobs
from app.services import credit_source_imports as sources
from app.services import learning_credits as credits
from app.migrations import MIGRATION_ROOT, _split_mysql
from test_credit_source_excel import source_fixture, registered
from test_credit_source_setup_mysql import fresh_source_db
from test_credit_single_migration_mysql import isolated_schema
import os


@pytest.fixture(autouse=True)
def no_external_dispatch(monkeypatch):
    monkeypatch.setattr(jobs, "_trigger", lambda *args: None)


def ready(f,kind='DAILY_READING'):
    iid,_,_=registered(f,kind)
    source=sources.create_batches(iid,f['actor'])
    return iid, source['settlement_fingerprint']


def state(iid):
    return fetch_one('SELECT * FROM learning_credit_posting_jobs WHERE import_id=?',(iid,))


def test_submit_is_durable_idempotent_and_closing_client_does_not_stop_worker(source_fixture):
    f=source_fixture;iid,fp=ready(f)
    before=fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']
    a=jobs.submit(iid,f['actor'],fp);b=jobs.submit(iid,f['actor'],fp)
    assert a['job']['id']==b['job']['id'] and b['idempotent']
    assert state(iid)['status']=='PENDING'
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before
    assert sources.get_import(iid,f['actor'])['posting_job']['status']=='PENDING'
    with pytest.raises(ValueError,match='后台'):sources.process(iid,f['actor'],'confirm',fp)
    # No browser or originating request exists when this scheduler invocation runs.
    assert jobs.run_job(state(iid)["id"],fp)['completed']==1
    assert state(iid)['status']=='SUCCEEDED'
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before+1
    assert jobs.run_job(state(iid)["id"],fp)['processed']==0
    assert jobs.submit(iid,f['actor'],fp)['job']['status']=='SUCCEEDED'
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before+1


def test_pause_and_explicit_resume_preserve_job_identity(source_fixture):
    f=source_fixture;iid,fp=ready(f)
    job=jobs.submit(iid,f['actor'],fp)['job']
    assert jobs.pause(iid,f['actor'])['job']['status']=='PAUSED'
    assert jobs.run_job(state(iid)["id"],fp)['processed']==0
    assert jobs.submit(iid,f['actor'],fp)['job']['id']==job['id']
    assert jobs.run_job(state(iid)["id"],fp)['completed']==1


def test_event_never_scans_other_pending_jobs_and_old_fingerprint_is_inert(source_fixture):
    f=source_fixture;iid,fp=ready(f);other,other_fp=ready(f,'EXCELLENT_SHARE')
    jobs.submit(iid,f['actor'],fp);jobs.submit(other,f['actor'],other_fp)
    assert jobs.run_job(state(iid)['id'],'f'*64)['processed']==0
    assert state(iid)['status']=='PENDING'
    assert jobs.run_job(state(iid)['id'],fp)['completed']==1
    assert state(other)['status']=='PENDING'
    assert sources.get_import(other,f['actor'])['posting_progress']['posted_entries']==0


def test_click_only_triggers_one_event_and_uncertain_ack_is_not_success(source_fixture):
    f=source_fixture;iid,fp=ready(f)
    with patch.object(jobs,'_trigger') as trigger:
        result=jobs.submit_and_dispatch(iid,f['actor'],fp)
        trigger.assert_called_once_with(result['job']['id'],fp)
    assert sources.get_import(iid,f['actor'])['posting_progress']['posted_entries']==0
    with patch.object(jobs,'_trigger',side_effect=TimeoutError('do not persist upstream headers')):
        result=jobs.submit_and_dispatch(iid,f['actor'],fp)
    assert result['job']['status']=='NEEDS_REVIEW' and result['job']['error_code']=='DISPATCH_UNCERTAIN'
    assert jobs.run_job(state(iid)['id'],fp)['processed']==0
    assert 'headers' not in str(state(iid))


def test_pause_during_a_claim_finishes_current_step_then_waits_for_explicit_resume(source_fixture):
    f=source_fixture;iid,fp=ready(f);jobs.submit(iid,f['actor'],fp)
    original=sources.process
    def step(*args,**kwargs):
        jobs.pause(iid,f['actor'])
        result=original(*args,**kwargs)
        # Simulate another day remaining, without driving further ledger writes.
        result['posting_progress']={**result['posting_progress'],'completed':False,'state':'READY'}
        return result
    with patch.object(sources,'process',side_effect=step):jobs.run_job(state(iid)["id"],fp)
    assert state(iid)['status']=='PAUSED'
    assert jobs.run_job(state(iid)["id"],fp)['processed']==0
    assert sources.get_import(iid,f['actor'])['posting_progress']['posted_entries']==1


@pytest.mark.skipif(os.getenv('CREDIT_MIGRATION_ISOLATED_MYSQL')!='1',reason='explicit disposable MySQL schema only')
def test_fixed_mysql_job_setup_empty_reversal_and_idempotency(fresh_source_db):
    sources.setup(1,'a'*40,sources.HASHES['mysql'])
    with patch.object(jobs,'connect',side_effect=fresh_source_db):
        with pytest.raises(ValueError,match='校验'):jobs.setup(1,'a'*40,'b'*64)
        assert jobs.setup(1,'a'*40,jobs.HASHES['mysql'])['status']=='READY'
        assert jobs.setup(1,'a'*40,jobs.HASHES['mysql'])['idempotent']
        c=fresh_source_db()
        try:
            assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_entries').fetchone()['n']==0
            script=(MIGRATION_ROOT/'rollback/mysql/0074_credit_posting_jobs.down.sql').read_text(encoding='utf-8')
            for sql in _split_mysql(script):execute(c,sql)
            c.commit()
            assert not jobs.available(c)
        finally:c.close()


def test_worker_claim_excludes_another_dispatcher_and_expiry_requires_review(source_fixture):
    f=source_fixture;iid,fp=ready(f);jobs.submit(iid,f['actor'],fp)
    claim=jobs._claim(state(iid)["id"],fp);assert claim
    assert jobs._claim(state(iid)["id"],fp) is None
    with transaction() as c:
        execute(c,'UPDATE learning_credit_posting_jobs SET lease_until=? WHERE import_id=?',
            (credits._db_timestamp(c,datetime.now(UTC)-timedelta(minutes=1)),iid))
    assert jobs.run_job(state(iid)["id"],fp)['processed']==0
    assert state(iid)['status']=='NEEDS_REVIEW' and state(iid)['error_code']=='WORKER_INTERRUPTED'
    jobs._finish(claim,'SUCCEEDED')
    assert state(iid)['status']=='NEEDS_REVIEW'


def test_failure_and_permission_revocation_stop_without_replay(source_fixture):
    f=source_fixture;iid,fp=ready(f);jobs.submit(iid,f['actor'],fp)
    with patch.object(jobs,'_authorize',side_effect=PermissionError('revoked')):
        assert jobs.run_job(state(iid)["id"],fp)['needs_review']==1
    assert state(iid)['error_code']=='PERMISSION_CHANGED'
    assert jobs.run_job(state(iid)["id"],fp)['processed']==0
    jobs.submit(iid,f['actor'],fp)
    with patch.object(sources,'process',side_effect=RuntimeError('sensitive raw DB failure')):
        assert jobs.run_job(state(iid)["id"],fp)['needs_review']==1
    assert 'sensitive' not in str(state(iid))


def test_changed_confirmed_items_and_foreign_scope_are_rejected(source_fixture):
    f=source_fixture;iid,fp=ready(f)
    with pytest.raises(ValueError,match='预览'):jobs.submit(iid,f['actor'],'a'*64)
    with patch.object(sources,'_has_scope',return_value=False),pytest.raises(PermissionError):jobs.submit(iid,f['actor'],fp)
    jobs.submit(iid,f['actor'],fp)
    with transaction() as c:execute(c,"UPDATE learning_credit_posting_jobs SET confirmed_fingerprint=? WHERE import_id=?",('b'*64,iid))
    assert jobs.run_job(state(iid)["id"],'b'*64)['needs_review']==1
    assert sources.get_import(iid,f['actor'])['posting_progress']['posted_entries']==0


def test_http_confirmation_and_scheduler_do_not_accept_unauthorized_work(source_fixture,monkeypatch):
    f=source_fixture;iid,fp=ready(f);url=f'/api/v1/learning-credits/excel/imports/{iid}/posting-job'
    client=TestClient(app)
    assert client.post(url,json={'expected_fingerprint':fp}).status_code==401
    assert client.post('/api/v1/internal/credit-posting-jobs',json={'job_id':1,'fingerprint':fp}).status_code==401
    key='synthetic-maintenance-token-for-posting-tests'
    monkeypatch.setenv('STUDY_EVIDENCE_CLEANUP_TOKEN',key)
    headers={'X-Study-Evidence-Cleanup-Token':key}
    assert client.post('/api/v1/internal/credit-posting-jobs',json={'actor':1},headers=headers).status_code==422
    app.dependency_overrides[current_user]=lambda:{'id':f['actor'],'permissions':credits.user_context(f['actor'])['permissions']}
    try:
        assert client.post(url,json={'expected_fingerprint':fp}).status_code==200
    finally:app.dependency_overrides.pop(current_user,None)
    result=client.post('/api/v1/internal/credit-posting-jobs',json={'job_id':state(iid)['id'],'fingerprint':fp},headers=headers)
    assert result.status_code==200 and result.json()['data']['completed']==1
    assert 'member' not in result.text and 'fingerprint' not in result.text


def test_job_migration_guard_refuses_confirmed_jobs_without_touching_ledger(source_fixture):
    f=source_fixture;iid,fp=ready(f);jobs.submit(iid,f['actor'],fp)
    c=connect()
    try:
        dialect='sqlite' if type(c).__module__=='sqlite3' else 'mysql'
        script=(MIGRATION_ROOT/'rollback'/dialect/'0074_credit_posting_jobs.down.sql').read_text(encoding='utf-8')
        with pytest.raises(Exception):
            for sql in _split_mysql(script):execute(c,sql)
        c.rollback()
        assert jobs.available(c)
        assert execute(c,'SELECT status FROM learning_credit_posting_jobs WHERE import_id=?',(iid,)).fetchone()['status']=='PENDING'
    finally:c.close()
