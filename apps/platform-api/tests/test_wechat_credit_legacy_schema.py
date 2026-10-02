from __future__ import annotations

import sqlite3
import os
from datetime import datetime
from unittest.mock import patch

import pytest

from app.services import wechat_credit_summary as credits


@pytest.fixture
def legacy_database(tmp_path):
    database = tmp_path / "exact-date-ledger.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
      CREATE TABLE members(id INTEGER PRIMARY KEY,status TEXT);
      INSERT INTO members VALUES(1,'ACTIVE'),(2,'ACTIVE'),(3,'INACTIVE');
      CREATE TABLE learning_credit_entries(
        id INTEGER PRIMARY KEY, member_id INTEGER, credit_category TEXT,
        credit_type TEXT, source_type TEXT, points REAL, occurred_at TEXT,
        rule_key TEXT, rule_version TEXT, rule_snapshot_json TEXT,
        posted_at TEXT, reversal_of_entry_id INTEGER, status TEXT);
    """)
    year = datetime.now(credits.BUSINESS_TIMEZONE).year
    rows = [
        (1, 1, "STANDARD_LEARNING", "COURSE_COMPLETION", "GROUP_MEETING", 2,
         f"{year}-02-28", "COURSE_COMPLETION", "2026.1", "{}", f"{year}-03-01", None, "POSTED"),
        (2, 1, "STANDARD_LEARNING", "COURSE_COMPLETION", "REVERSAL", -2,
         f"{year}-02-28", "COURSE_COMPLETION", "2026.1", "{}", f"{year}-03-02", 1, "POSTED"),
        (3, 1, "EXTENSION_ACTIVITY", "DAILY_READING", "DAILY_READING", 5,
         f"{year - 1}-12-31", "DAILY_READING", "2026.1", "{}", f"{year}-01-01", None, "POSTED"),
        (4, 2, "STANDARD_LEARNING", "COURSE_COMPLETION", "GROUP_MEETING", 99,
         f"{year}-09-01", "COURSE_COMPLETION", "2026.1", "{}", f"{year}-09-02", None, "POSTED"),
        (5, 1, "STANDARD_LEARNING", "COURSE_COMPLETION", "GROUP_MEETING", 10,
         f"{year}-09-01", "COURSE_COMPLETION", "2026.1", "{}", None, None, "PENDING"),
    ]
    connection.executemany("INSERT INTO learning_credit_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()
    def connect():
        value = sqlite3.connect(database)
        value.row_factory = sqlite3.Row
        return value
    with patch.object(credits, "connect", connect):
        yield database


def test_original_schema_totals_pagination_details_and_ownership(legacy_database):
    summary = credits.get_member_credit_summary(1)
    assert summary["total_points"] == "5.00"
    assert summary["current_year_points"] == "0.00"
    assert summary["standard_learning_points"] == "0.00"
    assert summary["extension_activity_points"] == "5.00"
    first = credits.get_member_credit_entries(1, limit=1)
    assert first["entries"][0]["entry_ref"] == "2" and first["has_more"]
    assert first["entries"][0]["period_display"].endswith("/02/28")
    second = credits.get_member_credit_entries(1, limit=2, offset=1, snapshot_id=int(first["snapshot_id"]))
    assert [row["entry_ref"] for row in second["entries"]] == ["1", "3"]
    assert not second["has_more"]
    assert credits.get_member_credit_entry(1, 1)["is_reversed"]
    assert credits.get_member_credit_entry(1, 2)["original_entry_ref"] == "1"
    for foreign_or_unposted in [4, 5, 99]:
        with pytest.raises(credits.MemberCreditEntryNotFound):
            credits.get_member_credit_entry(1, foreign_or_unposted)
    with pytest.raises(ValueError):
        credits.get_member_credit_summary(3)
    with sqlite3.connect(legacy_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM learning_credit_entries").fetchone()[0] == 5
        assert "occurred_precision" not in {r[1] for r in connection.execute("PRAGMA table_info(learning_credit_entries)")}


def test_partial_precision_upgrade_does_not_invent_exact_dates(legacy_database):
    with sqlite3.connect(legacy_database) as connection:
        connection.execute("ALTER TABLE learning_credit_entries ADD COLUMN occurred_precision TEXT")
    with pytest.raises(RuntimeError, match="账本结构不完整"):
        credits.get_member_credit_summary(1)


def test_mysql_legacy_projection_uses_native_year_and_month():
    class Cursor:
        description = [(name,) for name in ["id", "member_id", "occurred_at"]]
    with patch.object(credits, "execute", return_value=Cursor()):
        columns, order, year = credits._entry_sql(object())
    assert "YEAR(occurred_at) AS occurred_year" in columns
    assert "MONTH(occurred_at) AS occurred_month" in columns
    assert order == "occurred_at DESC,id DESC" and year == "YEAR(occurred_at)"


@pytest.mark.skipif(not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
                    reason="Requires the isolated CI MySQL database")
def test_mysql_original_schema_reads_temporary_ledger_without_migration():
    from app.core.settings import get_settings
    from app.db import connect, execute
    assert get_settings().app_env == "test"
    connection = connect()
    class SharedConnection:
        def cursor(self):
            return connection.cursor()
        def close(self):
            pass
    year = datetime.now(credits.BUSINESS_TIMEZONE).year
    try:
        # Connection-local tables shadow the migrated CI schema. No permanent
        # table, production credential, migration or shared ledger is modified.
        execute(connection, "CREATE TEMPORARY TABLE members(id BIGINT PRIMARY KEY,status VARCHAR(32))")
        execute(connection, "INSERT INTO members VALUES(1,'ACTIVE'),(2,'ACTIVE')")
        execute(connection, "CREATE TEMPORARY TABLE learning_credit_entries("
                "id BIGINT PRIMARY KEY,member_id BIGINT,credit_category VARCHAR(32),credit_type VARCHAR(64),"
                "source_type VARCHAR(64),points DECIMAL(10,2),occurred_at DATETIME,rule_key VARCHAR(128),"
                "rule_version VARCHAR(64),rule_snapshot_json TEXT,posted_at DATETIME,reversal_of_entry_id BIGINT,status VARCHAR(32))")
        for member, points, status, entry_id in [(1, 2, "POSTED", 1), (1, 7, "PENDING", 2), (2, 99, "POSTED", 3)]:
            execute(connection, "INSERT INTO learning_credit_entries VALUES(?,?,?,'COURSE_COMPLETION','GROUP_MEETING',"
                    "?,?,'COURSE_COMPLETION','2026.1','{}',?,NULL,?)",
                    (entry_id, member, "STANDARD_LEARNING", points, f"{year}-02-28", f"{year}-03-01", status))
        with patch.object(credits, "connect", return_value=SharedConnection()):
            summary = credits.get_member_credit_summary(1)
            assert summary["total_points"] == "2.00" and summary["current_year_points"] == "2.00"
            history = credits.get_member_credit_entries(1)
            assert [row["entry_ref"] for row in history["entries"]] == ["1"]
            assert history["entries"][0]["period_display"] == f"{year}/02/28"
            assert credits.get_member_credit_entry(1, 1)["points"] == "2.00"
            with pytest.raises(credits.MemberCreditEntryNotFound):
                credits.get_member_credit_entry(1, 3)
    finally:
        connection.close()
