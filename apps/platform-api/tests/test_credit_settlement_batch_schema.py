"""0067 schema contract on a disposable SQLite database.

The CI MySQL staging job additionally executes the real MySQL migration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
FORWARD = ROOT / "migrations/sqlite/0067_learning_credit_settlement_batches.sql"
ROLLBACK = ROOT / "migrations/rollback/sqlite/0067_learning_credit_settlement_batches.down.sql"


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL);"
        "CREATE TABLE org_units (id TEXT PRIMARY KEY);"
        "CREATE TABLE app_users (id INTEGER PRIMARY KEY);"
        "CREATE TABLE members (id INTEGER PRIMARY KEY);"
        "CREATE TABLE learning_credit_entries (id INTEGER PRIMARY KEY);"
    )
    connection.executescript(FORWARD.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO schema_migrations VALUES (?, ?)",
        ("0067_learning_credit_settlement_batches.sql", "2026-09-29"),
    )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def _insert_year_batch(connection: sqlite3.Connection, *, number: str = "LC-TEST-1"):
    connection.execute(
        "INSERT INTO learning_credit_settlement_batches "
        "(batch_no,batch_type,source_type,period_precision,period_year,"
        "source_snapshot_json,rule_snapshot_json,result_snapshot_json,"
        "source_fingerprint,rule_fingerprint,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            number, "HISTORICAL_IMPORT", "LEGACY_SUZHOU_2026_V1", "YEAR", 2026,
            "{}", "{}", "{}", "a" * 64, "b" * 64, "2026-09-29", "2026-09-29",
        ),
    )


def test_year_batch_keeps_unknown_month_and_tables_start_empty(database):
    for table in (
        "learning_credit_settlement_batches",
        "learning_credit_settlement_batch_items",
        "learning_credit_entries",
    ):
        assert database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    _insert_year_batch(database)
    row = database.execute(
        "SELECT period_precision,period_start,period_end,period_year,period_month "
        "FROM learning_credit_settlement_batches"
    ).fetchone()
    assert row == ("YEAR", None, None, 2026, None)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_year_batch(database, number="LC-TEST-DUPLICATE-SOURCE")
    assert database.execute("SELECT COUNT(*) FROM learning_credit_entries").fetchone()[0] == 0


def test_year_batch_cannot_fabricate_exact_date(database):
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO learning_credit_settlement_batches "
            "(batch_no,batch_type,source_type,period_precision,period_start,period_end,"
            "period_year,source_snapshot_json,rule_snapshot_json,result_snapshot_json,"
            "source_fingerprint,rule_fingerprint,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "LC-INVALID", "HISTORICAL_IMPORT", "LEGACY_SUZHOU_2026_V1",
                "YEAR", "2026-01-01", "2026-12-31", 2026, "{}", "{}", "{}",
                "a" * 64, "b" * 64, "2026-09-29", "2026-09-29",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(
            "INSERT INTO learning_credit_settlement_batches "
            "(batch_no,batch_type,source_type,period_precision,source_snapshot_json,"
            "rule_snapshot_json,result_snapshot_json,source_fingerprint,rule_fingerprint,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "LC-MISSING-YEAR", "HISTORICAL_IMPORT", "LEGACY_SUZHOU_2026_V1",
                "YEAR", "{}", "{}", "{}", "c" * 64, "d" * 64,
                "2026-09-29", "2026-09-29",
            ),
        )


def test_rollback_refuses_facts_then_succeeds_when_empty(database):
    _insert_year_batch(database)
    database.commit()
    with pytest.raises(sqlite3.IntegrityError):
        database.executescript(ROLLBACK.read_text(encoding="utf-8"))
    database.rollback()
    assert database.execute(
        "SELECT COUNT(*) FROM learning_credit_settlement_batches"
    ).fetchone()[0] == 1
    database.execute("DELETE FROM learning_credit_settlement_batches")
    database.commit()
    database.executescript(ROLLBACK.read_text(encoding="utf-8"))
    assert database.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'learning_credit_settlement_batch%'"
    ).fetchone()[0] == 0
    assert database.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
        ("0067_learning_credit_settlement_batches.sql",),
    ).fetchone()[0] == 0
