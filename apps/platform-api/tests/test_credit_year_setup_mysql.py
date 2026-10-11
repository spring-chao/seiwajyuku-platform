"""Fixed forward setup and interrupted DDL in a fresh disposable MySQL schema."""
import os
from unittest.mock import patch
import pytest
from app.db import execute
from app.migrations import MIGRATION_ROOT,_split_mysql
from app.services import credit_year_allocations as service
from app.services import credit_opening_balances as opening
from app.services import credit_year_reconciliation as rec
from test_credit_single_migration_mysql import isolated_schema

pytestmark=pytest.mark.skipif(os.getenv('CREDIT_MIGRATION_ISOLATED_MYSQL')!='1',reason='explicit disposable CI MySQL only')

@pytest.fixture
def fresh_year_db(isolated_schema,monkeypatch):
    _,raw=isolated_schema
    def connect():
        c=raw();c.autocommit(False);return c
    c=connect()
    for sql in _split_mysql((MIGRATION_ROOT/'mysql'/opening.MIGRATION).read_text()):execute(c,sql)
    execute(c,'INSERT INTO schema_migrations(version,applied_at) VALUES (?,UTC_TIMESTAMP())',(opening.MIGRATION,))
    execute(c,"INSERT INTO app_users(id,username,display_name,password_hash,created_at,updated_at) VALUES (1,'annual-test','隔离年度测试','test-only',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    c.commit();c.close()
    for key,value in {'CREDIT_OPENING_SETUP_ENABLED':'true','RUN_BOOTSTRAP_ON_STARTUP':'false','ALLOW_PRODUCTION_MUTATIONS':'true','DEPLOYMENT_READ_ONLY':'false','APP_GIT_SHA':'a'*40}.items():monkeypatch.setenv(key,value)
    principal={'id':1,'roles':['system_admin'],'permissions':[opening.OPENING_PERMISSION,'members:read','plans:production_rule_reconciliation_apply']}
    with patch.object(service,'connect',side_effect=connect),patch.object(opening,'user_context',return_value=principal),patch.object(opening,'accessible_org_ids',return_value=None):yield connect

def test_fixed_mysql_year_setup_retains_balances_and_validates_hash(fresh_year_db):
    with pytest.raises(ValueError,match='校验'):service.setup(1,'a'*40,'b'*64)
    before=service.workbench(1)
    assert before['setup_allowed'] and not before['storage_available']
    assert service.setup(1,'a'*40,service.HASHES['mysql'])['status']=='READY'
    assert service.setup(1,'a'*40,service.HASHES['mysql'])['idempotent']
    after=service.workbench(1)
    assert after['storage_available'] and not after['setup_allowed']
    c=fresh_year_db()
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_entries').fetchone()['n']==0
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_opening_balances').fetchone()['n']==0
    assert execute(c,"SELECT COUNT(*) AS n FROM audit_logs WHERE action='production.credit_year.setup'").fetchone()['n']==1
    for sql in _split_mysql((MIGRATION_ROOT/'rollback/mysql/0070_learning_credit_year_allocations.down.sql').read_text()):execute(c,sql)
    c.commit()
    assert service.present_tables(c)==set() and opening.storage_available(c)
    c.close()

def test_mysql_interrupted_year_ddl_is_durable_and_cannot_replay(fresh_year_db):
    original=service.execute
    def interrupted(c,sql,params=()):
        if sql.startswith('CREATE TABLE learning_credit_year_rows'):
            raise TimeoutError('isolated fixed setup response lost')
        return original(c,sql,params)
    with patch.object(service,'execute',side_effect=interrupted),pytest.raises(TimeoutError):service.setup(1,'a'*40,service.HASHES['mysql'])
    state=service.workbench(1)
    assert state['setup_incomplete'] and not state['storage_available'] and not state['setup_allowed']
    with pytest.raises(ValueError,match='不自动重试'):service.setup(1,'a'*40,service.HASHES['mysql'])
    c=fresh_year_db()
    assert service.present_tables(c)=={'learning_credit_year_imports'}
    assert not execute(c,'SELECT version FROM schema_migrations WHERE version=?',(service.MIGRATION,)).fetchone()
    c.close()

def test_mysql_rollback_keeps_retained_annual_source_and_parent_permissions(fresh_year_db):
    service.setup(1,'a'*40,service.HASHES['mysql'])
    c=fresh_year_db()
    execute(c,"INSERT INTO org_units(id,unit_code,name,unit_type,is_active,created_at,updated_at) VALUES ('year-rollback-test','year-rollback-test','隔离班级','CLASS',1,UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    plan=execute(c,"INSERT INTO learning_plan_versions(plan_key,plan_name,version_label,duration_cycles,status,created_at,updated_at) VALUES ('year-rollback-test','隔离计划','test',36,'PUBLISHED',UTC_TIMESTAMP(),UTC_TIMESTAMP())").lastrowid
    binding=execute(c,"INSERT INTO class_learning_bindings(class_org_unit_id,plan_version_id,started_at,status,created_at,updated_at) VALUES ('year-rollback-test',?,UTC_TIMESTAMP(),'ACTIVE',UTC_TIMESTAMP(),UTC_TIMESTAMP())",(plan,)).lastrowid
    execute(c,"INSERT INTO learning_credit_year_imports(content_fingerprint,file_sha256,original_filename,binding_id,year_index,cutoff_date,source_note,created_by,created_at) VALUES (?,?,'synthetic.xlsx',?,2,'2001-08-31','隔离保留来源',1,UTC_TIMESTAMP())",('b'*64,'c'*64,binding))
    c.commit()
    permissions=execute(c,'SELECT * FROM role_permissions ORDER BY role_key,permission_key').fetchall()
    with pytest.raises(Exception,match='constraint|CHECK'):
        for sql in _split_mysql((MIGRATION_ROOT/'rollback/mysql/0070_learning_credit_year_allocations.down.sql').read_text()):execute(c,sql)
    assert service.available(c)
    with pytest.raises(Exception,match='constraint|CHECK'):
        for sql in _split_mysql((MIGRATION_ROOT/'rollback/mysql/0069_learning_credit_opening_balances.down.sql').read_text()):execute(c,sql)
    assert execute(c,'SELECT * FROM role_permissions ORDER BY role_key,permission_key').fetchall()==permissions
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_year_imports').fetchone()['n']==1
    c.close()


def test_mysql_fixed_deficit_setup_empty_rollback_and_parent_dependency(fresh_year_db):
    service.setup(1,'a'*40,service.HASHES['mysql'])
    with patch.object(rec,'connect',side_effect=fresh_year_db):
        with pytest.raises(ValueError,match='校验'):rec.setup(1,'a'*40,'b'*64)
        assert rec.setup(1,'a'*40,rec.HASHES['mysql'])['status']=='READY'
        assert rec.setup(1,'a'*40,rec.HASHES['mysql'])['idempotent']
    c=fresh_year_db()
    assert rec.available(c)
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_opening_balances').fetchone()['n']==0
    assert execute(c,'SELECT COUNT(*) AS n FROM learning_credit_entries').fetchone()['n']==0
    with pytest.raises(Exception,match='constraint|CHECK'):
        for sql in _split_mysql((MIGRATION_ROOT/'rollback/mysql/0070_learning_credit_year_allocations.down.sql').read_text()):execute(c,sql)
    assert service.available(c) and rec.available(c)
    for sql in _split_mysql((MIGRATION_ROOT/'rollback/mysql/0071_learning_credit_year_reconciliation.down.sql').read_text()):execute(c,sql)
    c.commit()
    assert not rec.present_tables(c) and service.available(c) and opening.storage_available(c)
    c.close()


def test_mysql_interrupted_deficit_ddl_cannot_replay(fresh_year_db):
    service.setup(1,'a'*40,service.HASHES['mysql'])
    original=rec.execute
    def interrupted(c,sql,params=()):
        if sql.startswith('CREATE TABLE learning_credit_opening_adjustments'):
            raise TimeoutError('isolated response lost')
        return original(c,sql,params)
    with patch.object(rec,'connect',side_effect=fresh_year_db):
        with patch.object(rec,'execute',side_effect=interrupted),pytest.raises(TimeoutError):rec.setup(1,'a'*40,rec.HASHES['mysql'])
        with pytest.raises(ValueError,match='不自动重试'):rec.setup(1,'a'*40,rec.HASHES['mysql'])
    c=fresh_year_db()
    assert rec.present_tables(c)=={'learning_credit_year_reconciliation_rows'}
    assert not rec.available(c)
    assert execute(c,"SELECT COUNT(*) AS n FROM audit_logs WHERE action='production.credit_year.reconcile.setup'").fetchone()['n']==1
    assert not execute(c,'SELECT version FROM schema_migrations WHERE version=?',(rec.MIGRATION,)).fetchone()
    c.close()
