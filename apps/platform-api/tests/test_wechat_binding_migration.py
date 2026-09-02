from __future__ import annotations

import sqlite3

from app.migrations import MIGRATION_ROOT


def test_0044_wechat_binding_token_version_forward_and_rollback() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT);
            CREATE TABLE wechat_member_bindings(
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );
            INSERT INTO schema_migrations(version, applied_at)
            VALUES ('0044_wechat_binding_token_version.sql', '2026-09-02');
            INSERT INTO wechat_member_bindings(id, status) VALUES (1, 'REVOKED');
            """
        )

        connection.executescript(
            (
                MIGRATION_ROOT / "sqlite/0044_wechat_binding_token_version.sql"
            ).read_text(encoding="utf-8")
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(wechat_member_bindings)")
        }
        assert "token_version" in columns
        assert connection.execute(
            "SELECT token_version FROM wechat_member_bindings WHERE id=1"
        ).fetchone()[0] == 1

        connection.executescript(
            (
                MIGRATION_ROOT
                / "rollback/sqlite/0044_wechat_binding_token_version.down.sql"
            ).read_text(encoding="utf-8")
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(wechat_member_bindings)")
        }
        assert "token_version" not in columns
        assert connection.execute("SELECT * FROM schema_migrations").fetchall() == []
    finally:
        connection.close()
