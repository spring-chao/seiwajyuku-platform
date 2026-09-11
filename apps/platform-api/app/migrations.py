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
    """Split MySQL statements without treating comment/string semicolons as delimiters."""

    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0

    while index < len(script):
        char = script[index]
        next_char = script[index + 1] if index + 1 < len(script) else ""

        if line_comment:
            current.append(char)
            if char in "\r\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                index += 2
                block_comment = False
            else:
                index += 1
            continue

        if quote is not None:
            current.append(char)
            if char == "\\" and index + 1 < len(script):
                current.append(next_char)
                index += 2
            elif char == quote:
                if next_char == quote:
                    current.append(next_char)
                    index += 2
                else:
                    quote = None
                    index += 1
            else:
                index += 1
            continue

        if char == "-" and next_char == "-" and (
            index + 2 >= len(script) or script[index + 2].isspace()
        ):
            current.extend((char, next_char))
            index += 2
            line_comment = True
            continue
        if char == "#":
            current.append(char)
            index += 1
            line_comment = True
            continue
        if char == "/" and next_char == "*":
            current.extend((char, next_char))
            index += 2
            block_comment = True
            continue
        if char in "'\"`":
            current.append(char)
            index += 1
            quote = char
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


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
