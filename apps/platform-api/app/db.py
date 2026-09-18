from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

from app.core.settings import get_settings


settings = get_settings()
settings.assert_safe_startup()


@dataclass
class _AtomicState:
    connection: Any
    rollback_only: bool = False


_atomic_state: ContextVar[_AtomicState | None] = ContextVar("db_atomic_state", default=None)


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
    if isinstance(connection, sqlite3.Connection):
        return statement

    # PyMySQL uses ``%`` interpolation for its qmark-adapted parameters.  A
    # literal percent in SQL (for example a ``LIKE '%目前不读书%'`` predicate)
    # must therefore be escaped as ``%%`` or it is interpreted as another
    # format placeholder and the request fails before reaching MySQL.
    # Protect qmark placeholders while escaping literal percent signs.
    placeholder = "\x00"
    return statement.replace("?", placeholder).replace("%", "%%").replace(
        placeholder, "%s"
    )


def execute(connection, statement: str, params: tuple[Any, ...] = ()):
    cursor = connection.cursor()
    cursor.execute(_sql(connection, statement), params)
    return cursor


@contextmanager
def transaction() -> Iterator[Any]:
    state = _atomic_state.get()
    if state is not None:
        try:
            yield state.connection
        except Exception:
            state.rollback_only = True
            raise
        return
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def atomic_transaction() -> Iterator[Any]:
    """Opt-in unit of work for existing services; nested writes commit together."""
    if _atomic_state.get() is not None:
        with transaction() as connection:
            yield connection
        return
    with transaction() as connection:
        state = _AtomicState(connection)
        token = _atomic_state.set(state)
        try:
            yield connection
            if state.rollback_only:
                raise RuntimeError("事务中的操作失败，已取消本次保存")
        finally:
            _atomic_state.reset(token)


@contextmanager
def _read_connection() -> Iterator[Any]:
    state = _atomic_state.get()
    if state is not None:
        yield state.connection
        return
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def fetch_one(statement: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with _read_connection() as connection:
        row = execute(connection, statement, params).fetchone()
        return dict(row) if row else None


def fetch_all(statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _read_connection() as connection:
        return [dict(row) for row in execute(connection, statement, params).fetchall()]
