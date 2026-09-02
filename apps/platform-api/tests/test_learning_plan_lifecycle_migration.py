from __future__ import annotations

import sqlite3

import pytest

from app.migrations import MIGRATION_ROOT
from test_learning_plan_cohort_migration import _database_through_0030


def _database_through_0043() -> sqlite3.Connection:
    connection = _database_through_0030()
    connection.executescript(
        (MIGRATION_ROOT / "sqlite/0031_learning_plan_cohort_tracks.sql").read_text(
            encoding="utf-8"
        )
    )
    connection.executescript(
        (MIGRATION_ROOT / "sqlite/0043_class_learning_cycle_schedule_overrides.sql").read_text(
            encoding="utf-8"
        )
    )
    return connection


def _apply_0045(connection: sqlite3.Connection) -> None:
    connection.executescript(
        (MIGRATION_ROOT / "sqlite/0045_class_learning_plan_lifecycle.sql").read_text(
            encoding="utf-8"
        )
    )


def test_0045_forward_and_rollback_preserve_existing_learning_facts() -> None:
    connection = _database_through_0043()
    try:
        _apply_0045(connection)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(class_learning_bindings)")
        }
        assert {
            "learning_round",
            "start_cycle_index",
            "ended_at",
            "ended_reason",
            "previous_binding_id",
            "transition_type",
        } <= columns
        assert connection.execute(
            "SELECT learning_round, start_cycle_index, transition_type "
            "FROM class_learning_bindings WHERE id=1"
        ).fetchone() == (1, 1, "INITIAL")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        connection.executescript(
            (MIGRATION_ROOT / "rollback/sqlite/0045_class_learning_plan_lifecycle.down.sql").read_text(
                encoding="utf-8"
            )
        )
        remaining = {
            row[1] for row in connection.execute("PRAGMA table_info(class_learning_bindings)")
        }
        assert "learning_round" not in remaining
        assert connection.execute("SELECT COUNT(*) FROM class_learning_bindings").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM class_learning_cycles").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_0045_rollback_refuses_after_a_new_learning_round_exists() -> None:
    connection = _database_through_0043()
    try:
        _apply_0045(connection)
        connection.execute(
            "UPDATE class_learning_bindings SET learning_round=2 WHERE id=1"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(
                (MIGRATION_ROOT / "rollback/sqlite/0045_class_learning_plan_lifecycle.down.sql").read_text(
                    encoding="utf-8"
                )
            )
        connection.rollback()
        assert "learning_round" in {
            row[1] for row in connection.execute("PRAGMA table_info(class_learning_bindings)")
        }
    finally:
        connection.close()
