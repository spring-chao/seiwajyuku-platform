"""Fixed 0072 setup and recovery in a fresh disposable CI MySQL schema."""
import os
from contextlib import contextmanager
from unittest.mock import patch
import pytest
from app.db import execute
from app.migrations import MIGRATION_ROOT, _split_mysql
from app.services import credit_source_imports as service
from app.services import learning_credits as credits
from app.services import learning_activity_credits as activity
from test_credit_single_migration_mysql import isolated_schema

pytestmark = pytest.mark.skipif(os.getenv('CREDIT_MIGRATION_ISOLATED_MYSQL') != '1', reason='explicit disposable CI MySQL only')

@pytest.fixture
def fresh_source_db(isolated_schema,monkeypatch):
    _,raw=isolated_schema
    def connect():
        c=raw();c.autocommit(False);return c
    @contextmanager
    def transaction():
        c=connect()
        try:yield c;c.commit()
        except Exception:c.rollback();raise
        finally:c.close()
    c=connect()
    for path in sorted((MIGRATION_ROOT/'mysql').glob('*.sql')):
        if '0064'<=path.name[:4]<='0067':
            for sql in _split_mysql(path.read_text()):execute(c,sql)
            execute(c,'INSERT INTO schema_migrations(version,applied_at) VALUES (?,UTC_TIMESTAMP())',(path.name,))
    execute(c,"INSERT INTO app_users(id,username,display_name,password_hash,created_at,updated_at) VALUES (1,'source-test','隔离来源测试','test-only',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    c.commit();c.close()
    for key,value in {'CREDIT_OPENING_SETUP_ENABLED':'true','RUN_BOOTSTRAP_ON_STARTUP':'false','ALLOW_PRODUCTION_MUTATIONS':'true','DEPLOYMENT_READ_ONLY':'false','APP_GIT_SHA':'a'*40}.items():monkeypatch.setenv(key,value)
    user={'id':1,'roles':['system_admin'],'permissions':['members:read','plans:credit_settlement_manage','plans:credit_settlement_preview','plans:production_rule_reconciliation_apply','plans:business_calendar_manage']}
    with patch.object(service,'connect',side_effect=connect),patch.object(service,'transaction',side_effect=transaction),patch.object(activity,'user_context',return_value=user),patch.object(credits,'user_context',return_value=user),patch.object(credits,'accessible_org_ids',return_value=None):yield connect

def rollback(c,name):
    for sql in _split_mysql((MIGRATION_ROOT/'rollback/mysql'/name).read_text()):execute(c,sql)

def test_fixed_setup_hash_idempotency_and_empty_reversal(fresh_source_db):
    with pytest.raises(ValueError,match='校验'):service.setup(1,'a'*40,'b'*64)
    assert service.workbench(1)['setup_allowed']
    assert service.setup(1,'a'*40,service.HASHES['mysql'])['status']=='READY'
    assert service.setup(1,'a'*40,service.HASHES['mysql'])['idempotent']
    c=fresh_source_db()
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_entries').fetchone()['n']==0
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_activity_facts').fetchone()['n']==0
    assert execute(c,"SELECT COUNT(*) AS n FROM audit_logs WHERE action='production.credit_source.setup'").fetchone()['n']==1
    # Child tables block parent rollback before permissions or tables change.
    with pytest.raises(Exception,match='constraint|CHECK'):rollback(c,'0067_learning_credit_settlement_batches.down.sql')
    permissions=execute(c,'SELECT * FROM role_permissions ORDER BY role_key,permission_key').fetchall()
    with pytest.raises(Exception,match='constraint|CHECK'):rollback(c,'0049_c7_learning_activity_and_business_calendar.down.sql')
    assert execute(c,'SELECT * FROM role_permissions ORDER BY role_key,permission_key').fetchall()==permissions
    rollback(c,'0072_learning_credit_source_imports.down.sql');c.commit()
    assert not service.present_tables(c)
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_settlement_batches').fetchone()['n']==0
    c.close()

def test_interrupted_ddl_is_durable_and_never_replayed(fresh_source_db):
    original=service.execute
    def interrupted(c,sql,params=()):
        if sql.startswith('CREATE TABLE learning_credit_source_rows'):raise TimeoutError('isolated response lost')
        return original(c,sql,params)
    with patch.object(service,'execute',side_effect=interrupted),pytest.raises(TimeoutError):service.setup(1,'a'*40,service.HASHES['mysql'])
    state=service.workbench(1)
    assert state['setup_incomplete'] and not state['setup_allowed'] and not state['storage_available']
    with pytest.raises(ValueError,match='不自动重试'):service.setup(1,'a'*40,service.HASHES['mysql'])
    c=fresh_source_db()
    assert service.present_tables(c)=={'learning_credit_source_imports'}
    assert not execute(c,'SELECT version FROM schema_migrations WHERE version=?',(service.MIGRATION,)).fetchone()
    c.close()

def test_retained_source_refuses_destructive_reversal(fresh_source_db):
    service.setup(1,'a'*40,service.HASHES['mysql']);c=fresh_source_db()
    execute(c,"INSERT INTO org_units(id,unit_code,name,unit_type,is_active,created_at,updated_at) VALUES ('source-rollback-test','source-rollback-test','隔离班级','CLASS',1,UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    plan=execute(c,"INSERT INTO learning_plan_versions(plan_key,plan_name,version_label,duration_cycles,status,created_at,updated_at) VALUES ('source-rollback-test','隔离计划','test',36,'PUBLISHED',UTC_TIMESTAMP(),UTC_TIMESTAMP())").lastrowid
    binding=execute(c,"INSERT INTO class_learning_bindings(class_org_unit_id,plan_version_id,started_at,status,created_at,updated_at) VALUES ('source-rollback-test',?,UTC_TIMESTAMP(),'ACTIVE',UTC_TIMESTAMP(),UTC_TIMESTAMP())",(plan,)).lastrowid
    execute(c,"INSERT INTO learning_credit_source_imports(fingerprint,file_sha256,original_filename,source_kind,class_org_unit_id,binding_id,options_json,source_snapshot_json,source_note,created_by,created_at) VALUES (?,?,'synthetic.xlsx','DAILY_READING','source-rollback-test',?,'{}','{}','隔离保留来源',1,UTC_TIMESTAMP())",('b'*64,'c'*64,binding));c.commit()
    with pytest.raises(Exception,match='constraint|CHECK'):rollback(c,'0072_learning_credit_source_imports.down.sql')
    assert service.available(c) and execute(c,'SELECT COUNT(*) AS n FROM learning_credit_source_imports').fetchone()['n']==1
    c.close()


def test_missing_reading_calendar_preparation_is_complete_and_never_replaces_policy(fresh_source_db):
    before=service.workbench(1)
    assert before['reading_calendar']['status']=='MISSING'
    result=service.prepare_reading_calendar(1)
    assert not result['idempotent'] and result['reading_calendar']['day_count']==365
    assert service.prepare_reading_calendar(1)['idempotent']
    c=fresh_source_db()
    assert execute(c,"SELECT COUNT(*) AS n FROM learning_business_calendar_versions WHERE calendar_year=2026 AND status='PUBLISHED'").fetchone()['n']==1
    assert execute(c,"SELECT day_type FROM learning_business_calendar_days WHERE business_date='2026-10-10'").fetchone()['day_type']=='ADJUSTED_WORKDAY'
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_entries').fetchone()['n']==0
    c.close()
