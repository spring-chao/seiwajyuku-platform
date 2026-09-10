from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT


FORWARD = MIGRATION_ROOT / "sqlite" / "0058_wuxi_org_master_data.sql"
ROLLBACK = MIGRATION_ROOT / "rollback" / "sqlite" / "0058_wuxi_org_master_data.down.sql"


def _connection_before_0058() -> sqlite3.Connection:
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
        "INSERT OR IGNORE INTO org_units "
        "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        "VALUES ('org-jiangnan', 'JN_ROOT', '江南塾', 'ROOT', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT OR IGNORE INTO org_units "
        "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        "VALUES ('org-wuxi', 'WX_ROOT', '无锡塾', 'ROOT', 'org-jiangnan', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    connection.commit()
    return connection


def test_0058_lands_guidance_units_classes_and_direct_special_cohort() -> None:
    connection = _connection_before_0058()
    try:
        before_fk = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
        connection.executescript(FORWARD.read_text(encoding="utf-8"))

        guidance = connection.execute(
            "SELECT unit_code, unit_type, parent_id FROM org_units "
            "WHERE parent_id='org-wuxi' ORDER BY unit_code"
        ).fetchall()
        assert [(row["unit_code"], row["unit_type"], row["parent_id"]) for row in guidance] == [
            ("WX_GUIDANCE_1", "OPERATING_UNIT", "org-wuxi"),
            ("WX_GUIDANCE_2", "OPERATING_UNIT", "org-wuxi"),
            ("WX_JING_JIN", "SPECIAL_COHORT", "org-wuxi"),
        ]

        classes = connection.execute(
            "SELECT unit_code, parent_id FROM org_units WHERE unit_type='CLASS' "
            "AND parent_id IN ('org-wuxi-guidance-1','org-wuxi-guidance-2') ORDER BY unit_code"
        ).fetchall()
        assert len(classes) == 16
        assert sum(row["parent_id"] == "org-wuxi-guidance-1" for row in classes) == 8
        assert sum(row["parent_id"] == "org-wuxi-guidance-2" for row in classes) == 8

        assert tuple(
            connection.execute(
                "SELECT unit_type, parent_id FROM org_units WHERE id='org-wuxi-jing-jin'"
            ).fetchone()
        ) == ("SPECIAL_COHORT", "org-wuxi")
        assert [tuple(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()] == before_fk
    finally:
        connection.close()


def test_0058_rollback_refuses_to_remove_a_used_wuxi_tree() -> None:
    connection = _connection_before_0058()
    try:
        connection.executescript(FORWARD.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO org_units "
            "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES ('used-wuxi-group', 'USED_WX_GROUP', '已使用无锡小组', 'GROUP', "
            "'org-wuxi-ling-hang-1', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(ROLLBACK.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT parent_id FROM org_units WHERE id='used-wuxi-group'"
        ).fetchone()[0] == "org-wuxi-ling-hang-1"
    finally:
        connection.close()
