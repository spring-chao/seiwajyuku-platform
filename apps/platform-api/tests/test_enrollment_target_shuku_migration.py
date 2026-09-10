from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0060_enrollment_target_shuku.sql"
ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0060_enrollment_target_shuku.down.sql"


def _connection_before_0060() -> sqlite3.Connection:
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


def test_0060_adds_nullable_target_columns_and_lossless_empty_rollback() -> None:
    connection = _connection_before_0060()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        for table in ("member_enrollment_links", "member_enrollment_applications"):
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "target_shuku_org_unit_id" in columns
        connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        for table in ("member_enrollment_links", "member_enrollment_applications"):
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert "target_shuku_org_unit_id" not in columns
    finally:
        connection.close()


def test_0060_rollback_refuses_to_discard_a_recorded_target() -> None:
    connection = _connection_before_0060()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO app_users(username, display_name, password_hash, is_active, "
            "created_at, updated_at) VALUES ('migration-target-user', '迁移测试用户', 'hash', 1, ?, ?)",
            ("2026-09-11", "2026-09-11"),
        )
        connection.execute(
            "INSERT INTO member_enrollment_links "
            "(name, token_hash, status, active_slot, created_by, created_at, updated_at, "
            "target_shuku_org_unit_id) VALUES (?, ?, 'ACTIVE', 1, 1, ?, ?, ?)",
            ("迁移测试入口", "migration-target-hash", "2026-09-11", "2026-09-11", "org-wuxi"),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(member_enrollment_links)"
            ).fetchall()
        }
        assert "target_shuku_org_unit_id" in columns
        assert connection.execute(
            "SELECT target_shuku_org_unit_id FROM member_enrollment_links"
        ).fetchone()[0] == "org-wuxi"
    finally:
        connection.close()
