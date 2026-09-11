from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0061_enrollment_shuku_profiles.sql"
ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0061_enrollment_shuku_profiles.down.sql"


def _connection_before_0061() -> sqlite3.Connection:
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


def test_0061_adds_empty_business_owned_profile_table_and_lossless_rollback() -> None:
    connection = _connection_before_0061()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(enrollment_shuku_profiles)"
            ).fetchall()
        }
        assert {
            "shuku_org_unit_id",
            "display_name",
            "joining_notice",
            "payment_instructions",
            "payee_name",
            "bank_name",
            "bank_account",
            "contact_name",
            "contact_phone",
            "contact_address",
            "is_active",
        }.issubset(columns)
        assert connection.execute(
            "SELECT COUNT(*) FROM enrollment_shuku_profiles"
        ).fetchone()[0] == 0
        connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='enrollment_shuku_profiles'"
        ).fetchone() is None
    finally:
        connection.close()


def test_0061_rollback_refuses_to_discard_business_profile() -> None:
    connection = _connection_before_0061()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO enrollment_shuku_profiles"
            "(shuku_org_unit_id, display_name, joining_notice, created_at, updated_at) "
            "VALUES ('org-wuxi', '无锡塾', '测试说明', '2026-09-11', '2026-09-11')"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT display_name FROM enrollment_shuku_profiles WHERE shuku_org_unit_id='org-wuxi'"
        ).fetchone()[0] == "无锡塾"
    finally:
        connection.close()
