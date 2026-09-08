from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0053_volunteer2_service_organizations.sql"
ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0053_volunteer2_service_organizations.down.sql"


def _connection_before_0053() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
        if path.name < FORWARD.name:
            connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def test_0053_adds_person_binding_and_v2_schema_with_lossless_empty_rollback() -> None:
    connection = _connection_before_0053()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "volunteer_service_units",
            "volunteer_position_profiles",
            "volunteer_appointment_recommendation_rules",
            "volunteer_appointment_links",
        }.issubset(tables)
        binding_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(wechat_member_bindings)")
        }
        assert {"member_id", "person_id", "verified_user_id"}.issubset(binding_columns)
        appointment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(volunteer_appointments)")
        }
        assert "volunteer_service_unit_id" in appointment_columns

        connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        binding_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(wechat_member_bindings)")
        }
        appointment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(volunteer_appointments)")
        }
        assert "person_id" not in binding_columns
        assert "volunteer_service_unit_id" not in appointment_columns
    finally:
        connection.close()


def test_0053_rollback_refuses_to_discard_used_v2_service_data() -> None:
    connection = _connection_before_0053()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('used-vsu-root', 'USED_VSU_ROOT', '已使用塾', 'ROOT', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO volunteer_service_units "
            "(id, unit_code, name, system_type, line_type, service_target_org_unit_id, is_active, sort_order, created_at, updated_at) "
            "VALUES ('used-vsu', 'USED_VSU', '已使用服务组织', 'ACTIVITY', 'GENERAL', 'used-vsu-root', 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='volunteer_service_units'"
        ).fetchone()
    finally:
        connection.close()
