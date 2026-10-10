import sqlite3
from unittest.mock import patch

import pytest

from app.services import credit_opening_balances as service


@pytest.fixture
def setup_db(tmp_path, monkeypatch):
    path = tmp_path / "opening-setup.db"
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    connection = connect()
    connection.executescript("""
        CREATE TABLE permissions(permission_key TEXT PRIMARY KEY,permission_name TEXT,sensitive_level TEXT,created_at TEXT);
        CREATE TABLE roles(role_key TEXT PRIMARY KEY);
        INSERT INTO roles VALUES('employee_learning_management'),('ops_center_learning'),('employee_finance_management');
        CREATE TABLE role_permissions(role_key TEXT,permission_key TEXT,PRIMARY KEY(role_key,permission_key));
        CREATE TABLE app_users(id INTEGER PRIMARY KEY);
        INSERT INTO app_users VALUES(1);
        CREATE TABLE members(id INTEGER PRIMARY KEY);
        CREATE TABLE org_units(id TEXT PRIMARY KEY);
        CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT);
        CREATE TABLE audit_logs(id INTEGER PRIMARY KEY,actor_user_id INTEGER,action TEXT,resource_type TEXT,resource_id TEXT,org_unit_id TEXT,purpose TEXT,result TEXT,before_json TEXT,after_json TEXT,request_id TEXT,created_at TEXT);
    """)
    connection.close()
    monkeypatch.setenv("CREDIT_OPENING_SETUP_ENABLED", "true")
    monkeypatch.setenv("RUN_BOOTSTRAP_ON_STARTUP", "false")
    monkeypatch.setenv("APP_GIT_SHA", "a" * 40)
    user = {"id": 1, "roles": ["system_admin"], "permissions": ["plans:historical_credit_import_manage", "members:read", "plans:production_rule_reconciliation_apply"]}
    with patch.object(service, "connect", side_effect=connect), patch.object(service, "user_context", return_value=user), patch.object(service, "accessible_org_ids", return_value=None):
        yield connect, user


def test_fixed_setup_records_only_0069_and_replay_is_read_only(setup_db):
    connect, _ = setup_db
    result = service.setup(1, "a" * 40, service.MIGRATION_HASHES["sqlite"])
    assert result == {"status": "READY", "idempotent": False}
    assert service.setup(1, "a" * 40, service.MIGRATION_HASHES["sqlite"])["idempotent"]
    connection = connect()
    assert service.storage_available(connection)
    assert {r[0] for r in connection.execute("SELECT role_key FROM role_permissions WHERE permission_key= ?", (service.OPENING_PERMISSION,))} == {"employee_learning_management", "ops_center_learning"}
    assert [r["version"] for r in connection.execute("SELECT version FROM schema_migrations")] == [service.MIGRATION]
    assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action='production.credit_opening.setup'").fetchone()[0] == 1
    connection.close()


def test_reservation_survives_failure_and_repeated_request_cannot_rerun(setup_db):
    connect, _ = setup_db
    original = service.execute
    def fail(connection, sql, params=()):
        if sql.startswith("CREATE TABLE") or "CREATE TABLE IF NOT EXISTS learning_credit_opening_imports" in sql:
            original(connection, sql, params)
            raise TimeoutError("isolated DDL interruption")
        return original(connection, sql, params)
    with patch.object(service, "execute", side_effect=fail), pytest.raises(TimeoutError):
        service.setup(1, "a" * 40, service.MIGRATION_HASHES["sqlite"])
    with pytest.raises(ValueError, match="不会自动重试"):
        service.setup(1, "a" * 40, service.MIGRATION_HASHES["sqlite"])
    connection = connect()
    assert connection.execute("SELECT COUNT(*) FROM audit_logs WHERE result='STARTED'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize("case", ["permission", "role", "closed", "bootstrap", "commit", "hash"])
def test_setup_fails_before_reservation_when_scope_or_runtime_changes(setup_db, monkeypatch, case):
    connect, user = setup_db
    commit, digest = "a" * 40, service.MIGRATION_HASHES["sqlite"]
    if case == "permission": user["permissions"].remove("plans:production_rule_reconciliation_apply")
    if case == "role": user["roles"] = []
    if case == "closed": monkeypatch.setenv("CREDIT_OPENING_SETUP_ENABLED", "false")
    if case == "bootstrap": monkeypatch.setenv("RUN_BOOTSTRAP_ON_STARTUP", "true")
    if case == "commit": commit = "b" * 40
    if case == "hash": digest = "b" * 64
    with pytest.raises((ValueError, PermissionError)):
        service.setup(1, commit, digest)
    connection = connect()
    assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    assert service._tables(connection) == set()
    connection.close()


def test_setup_refuses_unknown_partial_schema(setup_db):
    connect, _ = setup_db
    connection = connect()
    connection.execute("CREATE TABLE learning_credit_opening_imports(id INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="中断记录"):
        service.setup(1, "a" * 40, service.MIGRATION_HASHES["sqlite"])
