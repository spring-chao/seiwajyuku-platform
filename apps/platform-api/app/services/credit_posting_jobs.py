"""Durable user-confirmed work, executed only by bounded maintenance requests.

No browser, process-local task, cached permission or automatic stale POSTING
recovery is required. The ledger's existing locks and idempotency remain final.
"""
import sqlite3
import time
import hashlib
from datetime import datetime, timedelta, UTC
from uuid import uuid4

from app.db import connect, execute, transaction
from app.services import credit_source_imports as sources
from app.services import learning_credits as credits
from app.services.audit import write_audit

TABLE = 'learning_credit_posting_jobs'
MIGRATION = '0074_credit_posting_jobs.sql'
ACTIVE = {'PENDING', 'RUNNING'}
HASHES = {'mysql': '081762928159757c25af065e0d8c7e8ef55356e5b2fd32aca3341a4a1c152de5', 'sqlite': '82b7b086236745ecb14ce4223d54e2f022353310f8455d67a4942349c75c0688'}


def available(c):
    query = "SELECT name FROM sqlite_master WHERE type='table' AND name=?" if isinstance(c, sqlite3.Connection) else "SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?"
    return bool(execute(c, query, (TABLE,)).fetchone() and execute(c, 'SELECT version FROM schema_migrations WHERE version=?', (MIGRATION,)).fetchone())


def setup(actor, commit, digest):
    """Explicit, audited fixed-table preparation. Never runs on application startup."""
    from app.core.build_info import get_build_info
    from app.migrations import MIGRATION_ROOT, _split_mysql
    from app.services import credit_opening_balances as opening
    user = sources._principal(actor)
    if 'system_admin' not in user.get('roles', []) or 'plans:production_rule_reconciliation_apply' not in user['permissions']:
        raise PermissionError('仅有存储准备权限的管理员可以执行')
    credits._write_allowed()
    settings = credits.get_settings()
    if not settings.credit_opening_setup_enabled or settings.run_bootstrap_on_startup or commit != get_build_info()['commit_sha'] or len(commit) != 40:
        raise credits.LearningCreditError('发布版本或设置已变化，请刷新核对')
    c = connect()
    try:
        with opening._operation(c):
            dialect = 'sqlite' if isinstance(c, sqlite3.Connection) else 'mysql'
            content = (MIGRATION_ROOT/dialect/MIGRATION).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest or digest != HASHES[dialect]:
                raise credits.LearningCreditError('固定迁移校验失败')
            if available(c):
                return {'status': 'READY', 'idempotent': True}
            query = "SELECT name FROM sqlite_master WHERE type='table' AND name=?" if isinstance(c, sqlite3.Connection) else "SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=?"
            if execute(c, query, (TABLE,)).fetchone() or execute(c, 'SELECT version FROM schema_migrations WHERE version=?', (MIGRATION,)).fetchone() or execute(c, "SELECT id FROM audit_logs WHERE action='production.credit_posting_jobs.setup' LIMIT 1").fetchone():
                raise credits.LearningCreditError('存在中断或已执行记录，请核对结构，不自动重试')
            if not sources.available(c):
                raise credits.LearningCreditError('请先准备学分来源存储')
            write_audit(c, actor_user_id=actor, action='production.credit_posting_jobs.setup', resource_type='schema_migration', resource_id=MIGRATION, result='STARTED', after={'commit': commit, 'sha256': digest, 'tables': [TABLE], 'ledger_write': False})
            c.commit()
            for sql in _split_mysql(content.decode('utf-8')):
                execute(c, sql)
            execute(c, 'INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)', (MIGRATION, credits._db_timestamp(c)))
            write_audit(c, actor_user_id=actor, action='production.credit_posting_jobs.setup.complete', resource_type='schema_migration', resource_id=MIGRATION, after={'commit': commit, 'sha256': digest, 'ledger_write': False})
            c.commit()
            return {'status': 'READY', 'idempotent': False}
    finally:
        c.close()


def public(row):
    if not row:
        return None
    return {**{k: row[k] for k in ('id','status','error_code','created_at','updated_at')}, 'pause_requested': bool(row['pause_requested'])}


def for_source(c, import_id):
    if not available(c):
        return None
    return public(execute(c, 'SELECT * FROM '+TABLE+' WHERE import_id=?', (import_id,)).fetchone())


def _authorize(actor, source):
    sources._principal(actor, source['source_kind'], write=True)
    sources._write_scope(actor, source['class_org_unit_id'], source['source_kind'])
    if not sources._has_scope(actor, source['class_org_unit_id'], ('plans:credit_settlement_post',)):
        raise PermissionError('这份来源不在当前入账授权范围')
    if not credits.get_settings().learning_credit_batch_post_enabled:
        raise credits.LearningCreditError('学分结算批次正式入账尚未开启')


def submit(import_id, actor, fingerprint, resume=False):
    source = sources.get_import(import_id, actor)
    _authorize(actor, source)
    if not fingerprint or fingerprint != source['settlement_fingerprint']:
        raise credits.LearningCreditError('结算预览已变化，请重新核对后确认')
    if not source['batches']:
        raise credits.LearningCreditError('请先生成结算预览')
    with transaction() as c:
        if not available(c):
            raise credits.LearningCreditError('后台入账任务尚未准备，请联系管理员')
        # Serialize submissions for this source, including concurrent first inserts.
        lock = '' if isinstance(c, sqlite3.Connection) else ' FOR UPDATE'
        execute(c, 'SELECT id FROM learning_credit_source_imports WHERE id=?'+lock, (import_id,)).fetchone()
        existing = execute(c, 'SELECT * FROM '+TABLE+' WHERE import_id=?'+lock, (import_id,)).fetchone()
        if existing and existing['status'] in ACTIVE:
            if existing['confirmed_fingerprint'] != fingerprint:
                raise credits.LearningCreditError('已有不同版本的后台任务，请核对原任务')
            return {'job': public(existing), 'idempotent': True}
        if source['posting_progress']['state'] == 'POSTING':
            raise credits.LearningCreditError('批次仍在处理中，请核对中断状态后再提交任务')
        if source['posting_progress']['state'] == 'PARTIAL_FAILED' and not resume:
            raise credits.LearningCreditError('部分失败条目需要明确确认继续入账')
        status = 'SUCCEEDED' if source['posting_progress']['completed'] else 'PENDING'
        now = credits._db_timestamp(c)
        if existing:
            execute(c, 'UPDATE '+TABLE+' SET actor_user_id=?,confirmed_fingerprint=?,resume_partial_failure=?,status=?,pause_requested=0,lease_token=NULL,lease_until=NULL,error_code=NULL,updated_at=? WHERE id=?',
                (actor, fingerprint, bool(resume), status, now, existing['id']))
            job_id = existing['id']
        else:
            job_id = execute(c, 'INSERT INTO '+TABLE+' (import_id,actor_user_id,confirmed_fingerprint,resume_partial_failure,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                (import_id, actor, fingerprint, bool(resume), status, now, now)).lastrowid
        write_audit(c, actor_user_id=actor, action='learning.credit_posting_job.confirm', resource_type='credit_posting_job', resource_id=str(job_id), org_unit_id=source['class_org_unit_id'],
            after={'import_id': import_id, 'confirmed_fingerprint': fingerprint, 'resume_partial_failure': bool(resume), 'status': status})
        return {'job': public(execute(c, 'SELECT * FROM '+TABLE+' WHERE id=?', (job_id,)).fetchone()), 'idempotent': False}


def pause(import_id, actor):
    source = sources.get_import(import_id, actor)
    _authorize(actor, source)
    with transaction() as c:
        if not available(c):
            raise credits.LearningCreditError('后台任务存储尚未准备')
        execute(c, "UPDATE "+TABLE+" SET pause_requested=1,status=CASE WHEN status='PENDING' THEN 'PAUSED' ELSE status END,updated_at=? WHERE import_id=? AND status IN ('PENDING','RUNNING')",
            (credits._db_timestamp(c), import_id))
        write_audit(c, actor_user_id=actor, action='learning.credit_posting_job.pause', resource_type='credit_source_import', resource_id=str(import_id), org_unit_id=source['class_org_unit_id'])
        return {'job': for_source(c, import_id)}


def _claim():
    with transaction() as c:
        if not available(c):
            return None
        now = credits._db_timestamp(c)
        # Do not infer whether an expired owner committed its last ledger step.
        execute(c, "UPDATE "+TABLE+" SET status='NEEDS_REVIEW',error_code='WORKER_INTERRUPTED',lease_token=NULL,lease_until=NULL,updated_at=? WHERE status='RUNNING' AND lease_until<?", (now, now))
        row = execute(c, "SELECT * FROM "+TABLE+" WHERE status='PENDING' AND pause_requested=0 ORDER BY updated_at,id LIMIT 1").fetchone()
        if not row:
            return None
        token = uuid4().hex
        until = credits._db_timestamp(c, datetime.now(UTC)+timedelta(minutes=10))
        changed = execute(c, "UPDATE "+TABLE+" SET status='RUNNING',lease_token=?,lease_until=?,updated_at=? WHERE id=? AND status='PENDING' AND pause_requested=0", (token, until, now, row['id']))
        return {**dict(row), 'lease_token': token} if changed.rowcount == 1 else None


def _finish(job, status, code=None):
    with transaction() as c:
        row = execute(c, 'SELECT pause_requested FROM '+TABLE+' WHERE id=?', (job['id'],)).fetchone()
        if status == 'PENDING' and row and row['pause_requested']:
            status = 'PAUSED'
        changed = execute(c, "UPDATE "+TABLE+" SET status=?,error_code=?,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND status='RUNNING' AND lease_token=?",
            (status, code, credits._db_timestamp(c), job['id'], job['lease_token']))
        if changed.rowcount == 1:
            write_audit(c, actor_user_id=job['actor_user_id'], action='learning.credit_posting_job.step', resource_type='credit_posting_job', resource_id=str(job['id']), after={'status': status, 'error_code': code})


def run_sweep(*, budget_seconds=20):
    # A scheduler request keeps CloudRun CPU active. Bound the number of steps
    # as well as wall time; never accept actor/import identifiers from it.
    report = {'processed': 0, 'completed': 0, 'needs_review': 0}
    credits._write_allowed()
    deadline = time.monotonic()+min(max(budget_seconds, 0), 20)
    while report['processed'] < 2 and time.monotonic() < deadline:
        job = _claim()
        if not job:
            break
        status, code = 'NEEDS_REVIEW', 'STEP_FAILED'
        try:
            source = sources.get_import(job['import_id'], job['actor_user_id'])
            _authorize(job['actor_user_id'], source)
            if source['posting_job']['pause_requested']:
                _finish(job, 'PAUSED')
                report['processed'] += 1
                continue
            if source['settlement_fingerprint'] != job['confirmed_fingerprint']:
                raise credits.LearningCreditError('确认版本已变化')
            # process revalidates membership, calendar/rules, source snapshots,
            # actor permissions and the ledger under its existing transaction locks.
            result = sources.process(job['import_id'], job['actor_user_id'], 'confirm', job['confirmed_fingerprint'], bool(job['resume_partial_failure']), True, _job_id=job['id'])
            progress = result['posting_progress']
            if progress['completed']:
                status, code = 'SUCCEEDED', None
            elif progress['state'] == 'READY':
                status, code = 'PENDING', None
            else:
                code = 'BATCH_REVIEW_REQUIRED'
        except PermissionError:
            code = 'PERMISSION_CHANGED'
        except Exception:
            # Do not persist raw exception bodies, DB errors or person data.
            code = 'STEP_REVIEW_REQUIRED'
        _finish(job, status, code)
        report['processed'] += 1
        report['completed'] += status == 'SUCCEEDED'
        report['needs_review'] += status == 'NEEDS_REVIEW'
    return report
