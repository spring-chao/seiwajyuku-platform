"""0068 migration guards on disposable SQLite; CI also exercises MySQL 8.4."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
FORWARD = (ROOT / "migrations/sqlite/0068_signin_management.sql").read_text(encoding="utf-8")
ROLLBACK = (ROOT / "migrations/rollback/sqlite/0068_signin_management.down.sql").read_text(encoding="utf-8")


def database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("CREATE TABLE app_users(id INTEGER PRIMARY KEY);"
                     "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT);"
                     "INSERT INTO app_users VALUES(1);")
    db.executescript(FORWARD)
    db.execute("INSERT INTO schema_migrations VALUES('0068_signin_management.sql','2026-10-07')")
    db.commit()
    return db


def test_forward_replay_does_not_create_codes_or_assign_authority_and_empty_down_is_safe():
    with database() as db:
        db.executescript(FORWARD)
        assert db.execute("SELECT COUNT(*) FROM attendance_entry_tokens").fetchone()[0] == 0
        db.executescript(ROLLBACK)
        assert db.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 1
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='attendance_entry_tokens'").fetchone()
        assert not db.execute("SELECT version FROM schema_migrations").fetchone()
        db.executescript(FORWARD)
        assert db.execute("SELECT COUNT(*) FROM attendance_entry_tokens").fetchone()[0] == 0


def test_issued_code_refuses_down_and_backup_restore_preserves_every_field():
    db = database()
    restored = sqlite3.connect(":memory:")
    try:
        row = ("a" * 64, "synthetic-event", 1, "2026-10-07", "2026-10-09", None)
        db.execute("INSERT INTO attendance_entry_tokens VALUES(?,?,?,?,?,?)", row)
        db.commit()
        db.backup(restored)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            db.executescript(ROLLBACK)
        assert db.execute("SELECT * FROM attendance_entry_tokens").fetchone() == row
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == "0068_signin_management.sql"
        assert restored.execute("SELECT * FROM attendance_entry_tokens").fetchone() == row
        assert restored.execute("SELECT version FROM schema_migrations").fetchone()[0] == "0068_signin_management.sql"
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            restored.execute("PRAGMA foreign_keys=ON")
            restored.execute("INSERT INTO attendance_entry_tokens VALUES(?,?,?,?,?,?)", ("b" * 64, "invalid-actor", 999, "2026-10-07", "2026-10-09", None))
    finally:
        db.close()
        restored.close()
