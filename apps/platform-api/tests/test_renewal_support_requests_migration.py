from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0059_renewal_support_requests.sql"
ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0059_renewal_support_requests.down.sql"
MYSQL_FORWARD = MIGRATION_ROOT / "mysql" / "0059_renewal_support_requests.sql"
MYSQL_ROLLBACK = MIGRATION_ROOT / "rollback" / "mysql" / "0059_renewal_support_requests.down.sql"
BIRTHDAY_FORWARD = MIGRATION_ROOT / "sqlite" / "0057_birthday_care_completions.sql"
BIRTHDAY_ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0057_birthday_care_completions.down.sql"
BIRTHDAY_MYSQL_FORWARD = MIGRATION_ROOT / "mysql" / "0057_birthday_care_completions.sql"
BIRTHDAY_MYSQL_ROLLBACK = MIGRATION_ROOT / "rollback" / "mysql" / "0057_birthday_care_completions.down.sql"


def _connection_before(migration_filename: str) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
        if path.name < migration_filename:
            connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def _seed_referenced_member_and_user(connection: sqlite3.Connection) -> tuple[sqlite3.Row, sqlite3.Row]:
    member = connection.execute(
        "SELECT id, org_unit_id FROM members ORDER BY id LIMIT 1"
    ).fetchone()
    user = connection.execute(
        "SELECT id FROM app_users ORDER BY id LIMIT 1"
    ).fetchone()
    if member is None or user is None:
        connection.execute(
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('migration-test-root', 'MIGRATION_TEST_ROOT', '迁移测试根组织', 'ROOT', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO app_users(username, display_name, password_hash, is_active, created_at, updated_at) "
            "VALUES ('migration-test-admin', '迁移测试管理员', 'local-test-hash', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
            "VALUES ('MIGRATION-TEST-MEMBER', '迁移测试学长', 'migration-test-root', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        member = connection.execute(
            "SELECT id, org_unit_id FROM members ORDER BY id LIMIT 1"
        ).fetchone()
        user = connection.execute(
            "SELECT id FROM app_users ORDER BY id LIMIT 1"
        ).fetchone()
    assert member is not None
    assert user is not None
    return member, user


def test_0057_birthday_completion_schema_and_empty_rollback_are_lossless() -> None:
    connection = _connection_before(BIRTHDAY_FORWARD.name)
    try:
        connection.executescript(BIRTHDAY_FORWARD.read_text(encoding="utf-8"))
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(birthday_care_completions)")
        }
        assert {
            "member_id",
            "birthday_year",
            "due_date",
            "channel",
            "completed_at",
            "completed_by",
        }.issubset(columns)
        connection.executescript(BIRTHDAY_ROLLBACK.read_text(encoding="utf-8"))
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='birthday_care_completions'"
        ).fetchone()
    finally:
        connection.close()


def test_0057_rollback_refuses_to_discard_recorded_completion() -> None:
    connection = _connection_before(BIRTHDAY_FORWARD.name)
    try:
        connection.executescript(BIRTHDAY_FORWARD.read_text(encoding="utf-8"))
        member, user = _seed_referenced_member_and_user(connection)
        connection.execute(
            "INSERT INTO birthday_care_completions(member_id, birthday_year, due_date, channel, completed_at, completed_by, created_at, updated_at) "
            "VALUES (?, 2099, '2099-08-21', 'WECHAT', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (member["id"], user["id"]),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(BIRTHDAY_ROLLBACK.read_text(encoding="utf-8"))
    finally:
        connection.close()


def test_0057_mysql_migration_contract_matches_the_sqlite_business_fields() -> None:
    mysql_forward = BIRTHDAY_MYSQL_FORWARD.read_text(encoding="utf-8")
    mysql_rollback = BIRTHDAY_MYSQL_ROLLBACK.read_text(encoding="utf-8")
    for field in (
        "member_id",
        "birthday_year",
        "due_date",
        "channel",
        "completed_at",
        "completed_by",
    ):
        assert field in mysql_forward
    assert "UNIQUE(member_id, birthday_year)" in mysql_forward
    assert "COUNT(*) FROM birthday_care_completions" in mysql_rollback


def test_0059_support_requests_schema_and_empty_rollback_are_lossless() -> None:
    connection = _connection_before(FORWARD.name)
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(renewal_support_requests)")
        }
        assert {
            "renewal_cycle_id",
            "supporter_member_id",
            "supporter_person_id",
            "supporter_name_snapshot",
            "supporter_role",
            "status",
            "requested_by",
            "feedback_summary",
            "next_action",
        }.issubset(columns)
        connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='renewal_support_requests'"
        ).fetchone()
    finally:
        connection.close()


def test_0059_rollback_refuses_to_discard_recorded_coordination() -> None:
    connection = _connection_before(FORWARD.name)
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        member, user = _seed_referenced_member_and_user(connection)
        cycle = connection.execute(
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
            "VALUES (?, 2099, ?, 9, 'IN_COMMUNICATION', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (member["id"], member["org_unit_id"]),
        )
        connection.execute(
            "INSERT INTO renewal_support_requests(renewal_cycle_id, supporter_name_snapshot, supporter_role, status, requested_by, requested_at, created_at, updated_at) "
            "VALUES (?, '本地测试助力人', 'REFERRER', 'REQUESTED', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (cycle.lastrowid, user["id"]),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
    finally:
        connection.close()


def test_0059_mysql_migration_contract_matches_the_sqlite_business_fields() -> None:
    mysql_forward = MYSQL_FORWARD.read_text(encoding="utf-8")
    mysql_rollback = MYSQL_ROLLBACK.read_text(encoding="utf-8")
    for field in (
        "renewal_cycle_id",
        "supporter_member_id",
        "supporter_person_id",
        "supporter_name_snapshot",
        "supporter_role",
        "feedback_summary",
        "next_action",
    ):
        assert field in mysql_forward
    assert "FOREIGN KEY(renewal_cycle_id)" in mysql_forward
    assert "RENAME" not in mysql_forward
    assert "COUNT(*) FROM renewal_support_requests" in mysql_rollback
