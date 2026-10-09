"""Admin ledger reads before 0066: no migration and unchanged scope/gates."""
from __future__ import annotations

import os
import sqlite3
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import execute, fetch_one
from app.main import app
from app.services import learning_credits as credits


def seed(connection, runner=execute):
    runner(connection, "CREATE TABLE members(id INTEGER PRIMARY KEY,name VARCHAR(32))")
    runner(connection, "INSERT INTO members VALUES(1,'测试学员'),(2,'范围外学员')")
    runner(connection, "CREATE TABLE learning_credit_entries("
            "id INTEGER PRIMARY KEY,member_id INTEGER,class_org_unit_id VARCHAR(32),"
            "credit_category VARCHAR(32),credit_type VARCHAR(64),source_type VARCHAR(64),"
            "source_id VARCHAR(64),points DECIMAL(10,2),occurred_at DATETIME,"
            "reversal_of_entry_id INTEGER,status VARCHAR(32))")
    for entry_id, member, points, occurred, reversal, org in [
        (1, 1, 2, '2025-12-31', None, 'class-a'),
        (2, 1, 3, '2026-02-28', None, 'class-a'),
        (3, 1, -3, '2026-02-28', 2, 'class-a'),
        (4, 2, 99, '2026-02-28', None, 'class-b'),
    ]:
        runner(connection, "INSERT INTO learning_credit_entries VALUES(?,?,?,'STANDARD_LEARNING',"
                "'COURSE_COMPLETION','GROUP_MEETING','test',?,?,?,'POSTED')",
                (entry_id, member, org, points, occurred, reversal))


@pytest.fixture
def legacy_database(tmp_path, monkeypatch):
    database = tmp_path / 'admin-legacy.db'
    with sqlite3.connect(database) as connection:
        seed(connection)
    def connect():
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection
    monkeypatch.setattr(credits, 'connect', connect)
    monkeypatch.setattr(credits, 'user_context', lambda _: {'permissions': ['plans:credit_settlement_preview']})
    monkeypatch.setattr(credits, 'accessible_org_ids', lambda _: {'class-a'})
    yield database


def assert_legacy_reads():
    entries = credits.list_credit_entries(actor_user_id=1)
    assert [row['id'] for row in entries] == [3, 2, 1]
    assert entries[0]['occurred_precision'] == 'EXACT_DATE'
    assert entries[0]['occurred_year'] == 2026 and entries[0]['occurred_month'] == 2
    assert entries[0]['period_display'] == '2026-02-28'
    assert entries[1]['reversal_entry_id'] == 3
    filtered = credits.list_credit_entries(actor_user_id=1, occurred_from='2026-02-01', occurred_to='2026-02-28', member_id=1)
    assert [row['id'] for row in filtered] == [3, 2]
    assert credits.list_credit_entries(actor_user_id=1, member_id=2) == []


def test_original_schema_reads_dates_filters_reversals_and_scope_without_writes(legacy_database):
    assert_legacy_reads()
    with sqlite3.connect(legacy_database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM learning_credit_entries').fetchone()[0] == 4
        assert 'occurred_year' not in {row[1] for row in connection.execute('PRAGMA table_info(learning_credit_entries)')}


def test_partial_upgrade_returns_explicit_503_over_http(legacy_database):
    with sqlite3.connect(legacy_database) as connection:
        connection.execute('ALTER TABLE learning_credit_entries ADD COLUMN occurred_precision TEXT')
    admin = fetch_one("SELECT id,token_version FROM app_users WHERE username='admin'")
    response = TestClient(app).get('/api/v1/learning-credits/entries', headers={
        'Authorization': 'Bearer ' + create_token(admin['id'], admin['token_version'], 'access', timedelta(minutes=5))
    })
    assert response.status_code == 503
    assert response.json() == {'detail': {'code': 'CREDIT_LEDGER_SCHEMA_UNAVAILABLE'}}


def test_permissions_and_empty_scope_are_checked_before_ledger_access(legacy_database):
    with patch.object(credits, 'connect', side_effect=AssertionError('must not read')):
        with patch.object(credits, 'user_context', return_value={'permissions': []}):
            with pytest.raises(PermissionError):
                credits.list_credit_entries(actor_user_id=1)
        with patch.object(credits, 'accessible_org_ids', return_value=set()):
            assert credits.list_credit_entries(actor_user_id=1) == []


@pytest.mark.skipif(not os.getenv('DATABASE_URL', '').startswith('mysql+pymysql://'),
                    reason='Requires isolated CI MySQL')
def test_mysql_original_schema_reads_isolated_fixture_tables():
    from app.db import connect
    from app.core.settings import get_settings
    assert get_settings().app_env == 'test'
    connection = connect()
    # MySQL cannot self-join a temporary table. Use uniquely named disposable
    # fixture tables, leaving the CI baseline tables intact.
    suffix = uuid4().hex
    ledger_table = 'test_admin_ledger_' + suffix
    member_table = 'test_admin_member_' + suffix
    def fixture_execute(connection, statement, params=()):
        return execute(connection, statement.replace('learning_credit_entries', ledger_table)
                       .replace('members', member_table), params)
    class SharedConnection:
        def cursor(self):
            return connection.cursor()
        def close(self):
            pass
    try:
        seed(connection, runner=fixture_execute)
        with patch.object(credits, 'connect', return_value=SharedConnection()), \
             patch.object(credits, 'execute', side_effect=fixture_execute), \
             patch.object(credits, 'user_context', return_value={'permissions': ['plans:credit_settlement_preview']}), \
             patch.object(credits, 'accessible_org_ids', return_value={'class-a'}):
            assert_legacy_reads()
            fixture_execute(connection, 'ALTER TABLE learning_credit_entries ADD COLUMN occurred_precision VARCHAR(32)')
            with pytest.raises(credits.LearningCreditSchemaUnavailable):
                credits.list_credit_entries(actor_user_id=1)
        assert fixture_execute(connection, 'SELECT COUNT(*) AS n FROM learning_credit_entries').fetchone()['n'] == 4
    finally:
        execute(connection, 'DROP TABLE IF EXISTS ' + ledger_table)
        execute(connection, 'DROP TABLE IF EXISTS ' + member_table)
        connection.close()
