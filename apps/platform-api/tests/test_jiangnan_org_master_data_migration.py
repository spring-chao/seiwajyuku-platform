from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0056_jiangnan_org_master_data.sql"
ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0056_jiangnan_org_master_data.down.sql"


def _connection_before_0056() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
        if path.name < FORWARD.name:
            connection.executescript(path.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO org_units "
        "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        "VALUES ('org-suzhou', 'SZ_ROOT', '苏州塾', 'ROOT', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    connection.commit()
    return connection


def test_0056_lands_confirmed_shuku_tree_and_preserves_suzhou_identity() -> None:
    connection = _connection_before_0056()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))

        suzhou = connection.execute(
            "SELECT id, unit_code, parent_id FROM org_units WHERE unit_code='SZ_ROOT'"
        ).fetchone()
        assert dict(suzhou) == {
            "id": "org-suzhou",
            "unit_code": "SZ_ROOT",
            "parent_id": "org-jiangnan",
        }

        centers = connection.execute(
            "SELECT unit_code, name, parent_id FROM org_units "
            "WHERE parent_id='org-changzhou' ORDER BY unit_code"
        ).fetchall()
        assert [(row["unit_code"], row["name"]) for row in centers] == [
            ("CZ_JIAN_XING", "健行分中心"),
            ("CZ_JING_KAI", "经开分中心"),
            ("CZ_LONG_CHENG", "龙城分中心"),
            ("CZ_TIAN_NING", "天宁分中心"),
            ("CZ_WU_JIN", "武进分中心"),
            ("CZ_XIN_BEI", "新北分中心"),
            ("CZ_ZHONG_LOU", "钟楼分中心"),
        ]

        institutions = connection.execute(
            "SELECT institution_code, name FROM operating_institutions "
            "WHERE institution_code IN ('JIANGNAN','SUZHOU_CENTER','CHANGZHOU_CENTER','WUXI_CENTER') "
            "ORDER BY institution_code"
        ).fetchall()
        assert [(row["institution_code"], row["name"]) for row in institutions] == [
            ("CHANGZHOU_CENTER", "常州塾"),
            ("JIANGNAN", "江南塾"),
            ("SUZHOU_CENTER", "苏州分中心"),
            ("WUXI_CENTER", "无锡塾"),
        ]

        links = connection.execute(
            "SELECT oi.institution_code, ou.unit_code, l.link_type "
            "FROM institution_org_links l "
            "JOIN operating_institutions oi ON oi.id=l.institution_id "
            "JOIN org_units ou ON ou.id=l.org_unit_id "
            "WHERE l.link_type='SERVICE_BOUNDARY' "
            "ORDER BY oi.institution_code"
        ).fetchall()
        assert [(row["institution_code"], row["unit_code"]) for row in links] == [
            ("CHANGZHOU_CENTER", "CZ_ROOT"),
            ("JIANGNAN", "JN_ROOT"),
            ("SUZHOU_CENTER", "SZ_ROOT"),
            ("WUXI_CENTER", "WX_ROOT"),
        ]

        assert connection.execute(
            "SELECT COUNT(*) FROM org_units WHERE parent_id='org-wuxi'"
        ).fetchone()[0] == 0

        connection.executescript(ROLLBACK.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT COUNT(*) FROM org_units WHERE id IN "
            "('org-jiangnan','org-changzhou','org-wuxi')"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT parent_id FROM org_units WHERE id='org-suzhou'"
        ).fetchone()[0] is None
    finally:
        connection.close()


def test_0056_rollback_refuses_to_remove_a_used_changzhou_tree() -> None:
    connection = _connection_before_0056()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO org_units "
            "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('used-cz-class', 'USED_CZ_CLASS', '已使用常州班级', 'CLASS', "
            "'org-changzhou-tian-ning', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT parent_id FROM org_units WHERE id='used-cz-class'"
        ).fetchone()[0] == "org-changzhou-tian-ning"
        assert connection.execute(
            "SELECT parent_id FROM org_units WHERE id='org-suzhou'"
        ).fetchone()[0] == "org-jiangnan"
    finally:
        connection.close()
