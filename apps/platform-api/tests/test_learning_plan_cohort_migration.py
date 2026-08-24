from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SQLITE_MIGRATIONS = ROOT / "migrations" / "sqlite"
SQLITE_ROLLBACK = ROOT / "migrations" / "rollback" / "sqlite"


def _database_through_0030() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for filename in (
        "0001_iam.sql",
        "0003_privacy.sql",
        "0009_attendance_scoring.sql",
        "0030_learning_plan_cycles.sql",
    ):
        connection.executescript((SQLITE_MIGRATIONS / filename).read_text(encoding="utf-8"))
    now = "2026-08-25T00:00:00+00:00"
    connection.executescript(
        "INSERT INTO org_units(id, unit_code, name, unit_type, is_active, created_at, updated_at) "
        f"VALUES ('root', 'ROOT', '根组织', 'ROOT', 1, '{now}', '{now}');"
        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        f"VALUES ('class', 'CLASS', '测试班', 'CLASS', 'root', 1, '{now}', '{now}');"
        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
        f"VALUES ('group', 'GROUP', '测试组', 'GROUP', 'class', 1, '{now}', '{now}');"
        "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
        f"VALUES ('plan', '测试计划', '2026', 36, 'PUBLISHED', '{now}', '{now}');"
        "INSERT INTO learning_plan_cycles(plan_version_id, cycle_index, year_index, cycle_label, created_at, updated_at) "
        f"VALUES (1, 1, 1, '第1周期', '{now}', '{now}');"
        "INSERT INTO learning_plan_tasks(plan_cycle_id, task_type, title, is_required, sort_order, created_at, updated_at) "
        f"VALUES (1, 'GROUP_MEETING', '小组会', 1, 1, '{now}', '{now}');"
        "INSERT INTO class_learning_bindings(class_org_unit_id, plan_version_id, cohort_month, started_at, status, created_at, updated_at) "
        f"VALUES ('class', 1, 4, '{now}', 'ACTIVE', '{now}', '{now}');"
        "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, plan_cycle_id, opened_at, created_at, updated_at) "
        f"VALUES (1, 'class', 1, 1, '{now}', '{now}', '{now}');"
        "INSERT INTO group_learning_cycle_tasks(class_learning_cycle_id, group_org_unit_id, plan_task_id, task_type, task_title, status, created_at, updated_at) "
        f"VALUES (1, 'group', 1, 'GROUP_MEETING', '小组会', 'PENDING', '{now}', '{now}');"
    )
    return connection


def test_0031_forward_and_rollback_preserve_generic_l1_records() -> None:
    connection = _database_through_0030()
    try:
        connection.executescript(
            (SQLITE_MIGRATIONS / "0031_learning_plan_cohort_tracks.sql").read_text(
                encoding="utf-8"
            )
        )
        assert connection.execute(
            "SELECT cohort_month FROM learning_plan_cycles WHERE id=1"
        ).fetchone()[0] is None
        assert connection.execute("SELECT COUNT(*) FROM learning_plan_tasks").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM class_learning_cycles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM group_learning_cycle_tasks").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        connection.executescript(
            (SQLITE_ROLLBACK / "0031_learning_plan_cohort_tracks.down.sql").read_text(
                encoding="utf-8"
            )
        )
        column_names = {
            row[1] for row in connection.execute("PRAGMA table_info(learning_plan_cycles)")
        }
        assert "cohort_month" not in column_names
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM group_learning_cycle_tasks").fetchone()[0] == 1
    finally:
        connection.close()


def test_0031_rollback_fails_closed_after_cohort_tracks_exist() -> None:
    connection = _database_through_0030()
    try:
        connection.executescript(
            (SQLITE_MIGRATIONS / "0031_learning_plan_cohort_tracks.sql").read_text(
                encoding="utf-8"
            )
        )
        connection.execute(
            "INSERT INTO learning_plan_cycles(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
            "VALUES (1, 4, 1, 1, '4月轨道第1周期', '2026-08-25T00:00:00+00:00', '2026-08-25T00:00:00+00:00')"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(
                (SQLITE_ROLLBACK / "0031_learning_plan_cohort_tracks.down.sql").read_text(
                    encoding="utf-8"
                )
            )
        connection.rollback()
        assert connection.execute(
            "SELECT COUNT(*) FROM learning_plan_cycles WHERE cohort_month=4"
        ).fetchone()[0] == 1
        assert "cohort_month" in {
            row[1] for row in connection.execute("PRAGMA table_info(learning_plan_cycles)")
        }
    finally:
        connection.close()
