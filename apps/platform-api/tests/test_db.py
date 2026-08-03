import sqlite3

from app.db import _sql


def test_sqlite_keeps_qmark_and_percent_literals():
    statement = "SELECT * FROM members WHERE notes LIKE '%目前不读书%' AND id=?"

    assert _sql(sqlite3.connect(":memory:"), statement) == statement


def test_mysql_escapes_literal_percent_without_escaping_placeholders():
    statement = "SELECT * FROM members WHERE notes LIKE '%目前不读书%' AND id=?"

    assert _sql(object(), statement) == (
        "SELECT * FROM members WHERE notes LIKE '%%目前不读书%%' AND id=%s"
    )
