from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = (
    MIGRATION_ROOT / "sqlite" / "0062_seed_confirmed_shuku_business_configs.sql"
)
ROLLBACK = (
    MIGRATION_ROOT
    / "rollback"
    / "sqlite"
    / "0062_seed_confirmed_shuku_business_configs.down.sql"
)


def _connection_before_0062() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
        if path.name < FORWARD.name:
            connection.executescript(path.read_text(encoding="utf-8"))
    # Production already owns the legacy Suzhou root; add the same stable
    # prerequisite here because the clean migration fixture intentionally has
    # no historical data.
    connection.execute(
        "INSERT INTO org_units"
        "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        "VALUES ('org-suzhou', 'SZ_ROOT', '苏州塾', 'ROOT', 'org-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    return connection


def test_0062_seeds_confirmed_profiles_terms_and_multiple_contacts() -> None:
    connection = _connection_before_0062()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        profiles = connection.execute(
            "SELECT shuku_org_unit_id, payee_name, bank_name, bank_account "
            "FROM enrollment_shuku_profiles ORDER BY shuku_org_unit_id"
        ).fetchall()
        assert [row["shuku_org_unit_id"] for row in profiles] == [
            "org-changzhou",
            "org-suzhou",
            "org-wuxi",
        ]
        assert tuple(
            connection.execute(
                "SELECT fee_amount, fee_unit, service_address "
                "FROM enrollment_shuku_profile_terms WHERE shuku_org_unit_id='org-suzhou'"
            ).fetchone()
        ) == (4800, "元/人/年", "苏州市高新区竹园路189号2幢102室2楼")
        contacts = connection.execute(
            "SELECT shuku_org_unit_id, contact_name, contact_phone, sort_order "
            "FROM enrollment_shuku_contacts ORDER BY shuku_org_unit_id, sort_order"
        ).fetchall()
        assert len(contacts) == 6
        assert [(row["shuku_org_unit_id"], row["contact_name"]) for row in contacts] == [
            ("org-changzhou", "常夏"),
            ("org-changzhou", "常德"),
            ("org-suzhou", "张玲嫒"),
            ("org-suzhou", "胡延辉"),
            ("org-wuxi", "春晴"),
            ("org-wuxi", "尹琦"),
        ]
    finally:
        connection.close()


def test_0062_is_idempotent_and_rollback_refuses_to_discard_confirmed_data() -> None:
    connection = _connection_before_0062()
    try:
        script = FORWARD.read_text(encoding="utf-8")
        connection.executescript(script)
        connection.executescript(script)
        assert connection.execute(
            "SELECT COUNT(*) FROM enrollment_shuku_profiles"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM enrollment_shuku_contacts"
        ).fetchone()[0] == 6
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT COUNT(*) FROM enrollment_shuku_profiles"
        ).fetchone()[0] == 3
    finally:
        connection.close()
