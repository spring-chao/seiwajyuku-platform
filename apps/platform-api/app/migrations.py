from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.db import connect, execute


REPO_ROOT = Path(__file__).resolve().parents[3]


def _split_mysql(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def run_migrations() -> list[str]:
    settings = get_settings()
    dialect = "sqlite" if settings.database_url.startswith("sqlite") else "mysql"
    migration_dir = REPO_ROOT / "migrations" / dialect
    connection = connect()
    applied: list[str] = []
    try:
        if dialect == "sqlite":
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        else:
            execute(
                connection,
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version VARCHAR(64) PRIMARY KEY, applied_at DATETIME NOT NULL)",
            )
        existing = {
            row[0] if not isinstance(row, dict) else row["version"]
            for row in execute(connection, "SELECT version FROM schema_migrations").fetchall()
        }
        for path in sorted(migration_dir.glob("*.sql")):
            if path.name in existing:
                continue
            script = path.read_text(encoding="utf-8")
            if dialect == "sqlite":
                connection.executescript(script)
            else:
                for statement in _split_mysql(script):
                    execute(connection, statement)
            execute(
                connection,
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (path.name, datetime.now(UTC).isoformat()),
            )
            applied.append(path.name)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return applied

