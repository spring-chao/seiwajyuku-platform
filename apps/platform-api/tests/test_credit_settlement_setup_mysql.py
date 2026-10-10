"""Fixed authenticated setup and persistent DDL interruption in disposable MySQL."""
import os
from unittest.mock import patch

import pytest

from app.db import execute
from app.services import credit_settlement_setup as service
from app.services.production_operations import ProductionOperationError
from test_credit_single_migration_mysql import isolated_schema

pytestmark = pytest.mark.skipif(os.getenv("CREDIT_MIGRATION_ISOLATED_MYSQL") != "1", reason="disposable MySQL CI only")


@pytest.fixture
def setup_mysql(isolated_schema, monkeypatch):
    _, raw_connect = isolated_schema
    def connect():
        connection = raw_connect()
        connection.autocommit(False)
        return connection
    connection = connect()
    execute(connection, "INSERT INTO app_users(id,username,display_name,password_hash,created_at,updated_at) VALUES (1,'credit-setup-test','隔离测试','test-only',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    execute(connection, "INSERT IGNORE INTO roles(role_key,role_name,is_active,created_at,updated_at) VALUES ('system_admin','隔离管理员',1,UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    execute(connection, "INSERT INTO user_roles(user_id,role_key,created_at) VALUES (1,'system_admin',UTC_TIMESTAMP())")
    plan = execute(connection, "SELECT id FROM learning_plan_versions WHERE plan_key='standard-3y' AND version_label='2026'").fetchone()
    if not plan:
        plan_id = execute(connection, "INSERT INTO learning_plan_versions(plan_key,plan_name,version_label,duration_cycles,status,created_at,updated_at) VALUES ('standard-3y','隔离计划','2026',36,'PUBLISHED',UTC_TIMESTAMP(),UTC_TIMESTAMP())").lastrowid
    else:
        plan_id = plan['id']
    for index in range(20):
        org = f'credit-setup-test-{index}'
        execute(connection, "INSERT INTO org_units(id,unit_code,name,unit_type,is_active,created_at,updated_at) VALUES (?,?,?,'CLASS',1,UTC_TIMESTAMP(),UTC_TIMESTAMP())", (org, org, '隔离班级'))
        execute(connection, "INSERT INTO class_learning_bindings(class_org_unit_id,plan_version_id,started_at,status,created_at,updated_at) VALUES (?,?,UTC_TIMESTAMP(),?,UTC_TIMESTAMP(),UTC_TIMESTAMP())", (org, plan_id, 'ACTIVE' if index < 19 else 'ENDED'))
    connection.commit()
    connection.close()
    for key, value in {"CREDIT_SETTLEMENT_SETUP_ENABLED": "true", "RUN_BOOTSTRAP_ON_STARTUP": "false",
                       "LEARNING_CREDIT_SETTLEMENT_ENABLED": "false", "ALLOW_PRODUCTION_MUTATIONS": "true",
                       "DEPLOYMENT_READ_ONLY": "false", "APP_GIT_SHA": "a" * 40}.items():
        monkeypatch.setenv(key, value)
    with patch.object(service, "connect", side_effect=connect):
        yield connect


def request(state, **changes):
    stage = next(s for s in state['stages'] if s['version'] == state['next_migration'])
    return {"actor_user_id": 1, "migration_version": stage['version'],
            "expected_release_commit": state['release_commit'], "expected_baseline_fingerprint": state['baseline_fingerprint'],
            "expected_migration_sha256": stage['sha256'], "execution_reason": '隔离验证固定结算准备', **changes}


def test_mysql_sequence_freezes_all_bindings_retains_ledger_and_rejects_replay(setup_mysql, monkeypatch):
    for version in service.MIGRATIONS:
        state = service.preview(1)
        assert state['next_migration'] == version and state['can_prepare']
        assert service.prepare(**request(state))['status'] == 'RECORDED'
        with pytest.raises(ProductionOperationError):
            service.prepare(**request(state))
    result = service.preview(1)
    assert result['storage_ready'] and not result['formal_ready']
    assert result['bindings'] == {'total': 20, 'generic_frozen': 20, 'course_frozen': 20}
    assert result['ledger']['total'] == 0
    for key in ('LEARNING_CREDIT_SETTLEMENT_ENABLED', 'LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED',
                'LEARNING_CREDIT_BATCH_APPROVAL_ENABLED', 'LEARNING_CREDIT_BATCH_POST_ENABLED'):
        monkeypatch.setenv(key, 'true')
    assert service.preview(1)['formal_ready']
    connection = setup_mysql()
    assert execute(connection, "SELECT COUNT(*) AS n FROM audit_logs WHERE action=? AND result='STARTED'", (service.ACTION,)).fetchone()['n'] == 4
    assert execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_settlement_batches").fetchone()['n'] == 0
    connection.close()


def test_mysql_first_ddl_persists_and_unknown_result_never_replays(setup_mysql):
    for _ in range(3):
        service.prepare(**request(service.preview(1)))
    payload = request(service.preview(1))
    original = service.execute
    def interrupted(connection, sql, params=()):
        if 'CREATE TABLE IF NOT EXISTS learning_credit_settlement_batches' in sql:
            original(connection, sql, params)
            raise TimeoutError('isolated DDL response lost')
        return original(connection, sql, params)
    with patch.object(service, 'execute', side_effect=interrupted), pytest.raises(ProductionOperationError, match='不会自动重试'):
        service.prepare(**payload)
    result = service.preview(1)
    assert not result['can_prepare'] and not result['storage_ready']
    with pytest.raises(ProductionOperationError):
        service.prepare(**payload)
    connection = setup_mysql()
    assert execute(connection, "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='learning_credit_settlement_batches'").fetchone()['n'] == 1
    assert not execute(connection, "SELECT version FROM schema_migrations WHERE version=?", (service.MIGRATIONS['0067'][0],)).fetchone()
    connection.close()


def test_mysql_scope_fingerprint_and_runtime_drift_are_zero_write(setup_mysql):
    state = service.preview(1)
    for changes in ({'actor_user_id': 999}, {'expected_baseline_fingerprint': 'b' * 64},
                    {'expected_release_commit': 'b' * 40}, {'expected_migration_sha256': 'b' * 64},
                    {'migration_version': '0068'}):
        with pytest.raises(ProductionOperationError):
            service.prepare(**request(state, **changes))
    connection = setup_mysql()
    assert execute(connection, "SELECT COUNT(*) AS n FROM audit_logs WHERE action=?", (service.ACTION,)).fetchone()['n'] == 0
    connection.close()


def test_mysql_closed_gate_preflight_is_select_only_and_does_not_start_setup(setup_mysql, monkeypatch):
    monkeypatch.setenv('CREDIT_SETTLEMENT_SETUP_ENABLED', 'false')
    statements = []
    original = service.execute
    def capture(connection, sql, params=()):
        statements.append(sql)
        return original(connection, sql, params)
    with patch.object(service, 'execute', side_effect=capture):
        state = service.preview(1)
    assert not state['can_prepare']
    assert all(sql.startswith('SELECT') and 'FOR UPDATE' not in sql for sql in statements)
    with pytest.raises(ProductionOperationError):
        service.prepare(**request(state))
