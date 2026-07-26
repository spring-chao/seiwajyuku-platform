from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.core.settings import get_settings
from app.db import connect, execute


def _find_migration_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "migrations"
        if candidate.is_dir():
            return candidate
    raise RuntimeError("Unable to locate the database migrations directory")


MIGRATION_ROOT = _find_migration_root()


def _split_mysql(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def run_migrations() -> list[str]:
    settings = get_settings()
    dialect = "sqlite" if settings.database_url.startswith("sqlite") else "mysql"
    migration_dir = MIGRATION_ROOT / dialect
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
