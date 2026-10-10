"""Real MySQL DDL/rollback/no-replay proofs in a disposable pre-0064 schema."""
import os
from unittest.mock import patch

import pytest

from app.db import execute
from app.migrations import MIGRATION_ROOT, _split_mysql
from app.services import credit_opening_balances as service
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
    execute(connection, "INSERT INTO app_users(id,username,display_name,password_hash,created_at,updated_at) VALUES (1,'isolated-setup','隔离测试','test-only-not-a-password',UTC_TIMESTAMP(),UTC_TIMESTAMP())")
    for role in ("employee_learning_management", "ops_center_learning", "employee_finance_management"):
        execute(connection, "INSERT IGNORE INTO roles(role_key,role_name,is_active,created_at,updated_at) VALUES (?, ?,1,UTC_TIMESTAMP(),UTC_TIMESTAMP())", (role, role))
    connection.commit()
    connection.close()
    monkeypatch.setenv("CREDIT_OPENING_SETUP_ENABLED", "true")
    monkeypatch.setenv("RUN_BOOTSTRAP_ON_STARTUP", "false")
    monkeypatch.setenv("APP_GIT_SHA", "a" * 40)
    user = {"id": 1, "roles": ["system_admin"], "permissions": ["plans:historical_credit_import_manage", "members:read", "plans:production_rule_reconciliation_apply"]}
    with patch.object(service, "connect", side_effect=connect), patch.object(service, "user_context", return_value=user), patch.object(service, "accessible_org_ids", return_value=None):
        yield connect


def test_mysql_setup_only_creates_opening_schema_and_empty_rollback_is_safe(setup_mysql):
    connect = setup_mysql
    connection = connect()
    before = execute(connection, "SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    legacy = execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"]
    rules = execute(connection, "SELECT * FROM learning_plan_credit_rules ORDER BY id").fetchall()
    connection.close()
    assert service.setup(1, "a" * 40, service.MIGRATION_HASHES["mysql"])["status"] == "READY"
    assert service.setup(1, "a" * 40, service.MIGRATION_HASHES["mysql"])["idempotent"]
    connection = connect()
    assert execute(connection, "SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == before + 1
    assert execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"] == legacy
    assert execute(connection, "SELECT * FROM learning_plan_credit_rules ORDER BY id").fetchall() == rules
    assert {r["role_key"] for r in execute(connection, "SELECT role_key FROM role_permissions WHERE permission_key=?", (service.OPENING_PERMISSION,)).fetchall()} == {"employee_learning_management", "ops_center_learning"}
    assert not execute(connection, "SELECT version FROM schema_migrations WHERE version LIKE '0064%' OR version LIKE '0068%'").fetchall()
    for sql in _split_mysql((MIGRATION_ROOT / "rollback/mysql/0069_learning_credit_opening_balances.down.sql").read_text(encoding="utf8")):
        execute(connection, sql)
    connection.commit()
    assert service._tables(connection) == set()
    connection.close()


def test_mysql_ddl_persists_on_timeout_but_second_attempt_never_replays(setup_mysql):
    connect = setup_mysql
    original = service.execute
    def fail(connection, sql, params=()):
        if "CREATE TABLE IF NOT EXISTS learning_credit_opening_imports" in sql:
            original(connection, sql, params)
            raise TimeoutError("isolated loss after DDL")
        return original(connection, sql, params)
    with patch.object(service, "execute", side_effect=fail), pytest.raises(TimeoutError):
        service.setup(1, "a" * 40, service.MIGRATION_HASHES["mysql"])
    connection = connect()
    assert service._tables(connection) == {"learning_credit_opening_imports"}
    assert execute(connection, "SELECT COUNT(*) AS n FROM audit_logs WHERE action='production.credit_opening.setup' AND result='STARTED'").fetchone()["n"] == 1
    assert not execute(connection, "SELECT version FROM schema_migrations WHERE version=?", (service.MIGRATION,)).fetchone()
    connection.close()
    with pytest.raises(ValueError, match="不会自动重试"):
        service.setup(1, "a" * 40, service.MIGRATION_HASHES["mysql"])
    connection = connect()
    assert service._tables(connection) == {"learning_credit_opening_imports"}
    connection.close()


def test_mysql_rollback_refuses_retained_source_and_keeps_all_tables(setup_mysql):
    connect = setup_mysql
    service.setup(1, "a" * 40, service.MIGRATION_HASHES["mysql"])
    connection = connect()
    execute(connection, "INSERT INTO learning_credit_opening_imports(content_fingerprint,file_sha256,original_filename,cutoff_date,created_by,created_at) VALUES (?,?,?,'2001-01-01',1,UTC_TIMESTAMP())", ("b" * 64, "c" * 64, "isolated.xlsx"))
    connection.commit()
    with pytest.raises(Exception, match="constraint|CHECK"):
        for sql in _split_mysql((MIGRATION_ROOT / "rollback/mysql/0069_learning_credit_opening_balances.down.sql").read_text(encoding="utf8")):
            execute(connection, sql)
    assert service.storage_available(connection)
    assert execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_opening_imports").fetchone()["n"] == 1
    connection.close()
