from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

from app.core.settings import get_settings


settings = get_settings()
settings.assert_safe_startup()


def _sqlite_path(url: str) -> str:
    if url == "sqlite:///:memory:":
        return ":memory:"
    return str(Path(url.removeprefix("sqlite:///")).resolve())


def connect():
    url = settings.database_url
    if url.startswith("sqlite:///"):
        path = _sqlite_path(url)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    if url.startswith("mysql+pymysql://"):
        import pymysql

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        return pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=parsed.path.lstrip("/"),
            charset=query.get("charset", ["utf8mb4"])[0],
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
    raise RuntimeError("仅支持 sqlite:/// 或 mysql+pymysql:// 数据库地址")


def _sql(connection, statement: str) -> str:
    return statement if isinstance(connection, sqlite3.Connection) else statement.replace("?", "%s")


def execute(connection, statement: str, params: tuple[Any, ...] = ()):
    cursor = connection.cursor()
    cursor.execute(_sql(connection, statement), params)
    return cursor


@contextmanager
def transaction() -> Iterator[Any]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_one(statement: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    connection = connect()
    try:
        row = execute(connection, statement, params).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def fetch_all(statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection = connect()
    try:
        return [dict(row) for row in execute(connection, statement, params).fetchall()]
    finally:
        connection.close()

