from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "sqlite"
    / "0023_reparent_wuyue_three.sql"
)


class WuyueThreeReparentMigrationTests(unittest.TestCase):
    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE org_units (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, unit_type TEXT NOT NULL,
                parent_id TEXT, is_active INTEGER NOT NULL, updated_at TEXT
            );
            CREATE TABLE app_users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL
            );
            CREATE TABLE audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER,
                action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT,
                org_unit_id TEXT, purpose TEXT, result TEXT NOT NULL,
                before_json TEXT, after_json TEXT, created_at TEXT NOT NULL
            );
            INSERT INTO app_users(id, username) VALUES (1, 'admin');
            INSERT INTO org_units VALUES
                ('org-suzhou', '苏州塾', 'ROOT', NULL, 1, CURRENT_TIMESTAMP),
                ('org-wujiang', '吴江分中心', 'REGIONAL_CENTER', 'org-suzhou', 1, CURRENT_TIMESTAMP),
                ('org-wuyue-3', '吴越三班', 'CLASS', 'org-suzhou', 1, CURRENT_TIMESTAMP),
                ('org-wuyue-3-group-4', '四组', 'GROUP', 'org-wuyue-3', 1, CURRENT_TIMESTAMP);
            """
        )
        return connection

    def test_reparents_class_preserves_group_and_writes_rollback_audit(self) -> None:
        connection = self._connection()
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))

        parent = connection.execute(
            "SELECT parent_id FROM org_units WHERE id='org-wuyue-3'"
        ).fetchone()[0]
        group_parent = connection.execute(
            "SELECT parent_id FROM org_units WHERE id='org-wuyue-3-group-4'"
        ).fetchone()[0]
        audit = connection.execute(
            "SELECT actor_user_id, before_json, after_json FROM audit_logs "
            "WHERE resource_id='org-wuyue-3'"
        ).fetchone()

        self.assertEqual(parent, "org-wujiang")
        self.assertEqual(group_parent, "org-wuyue-3")
        self.assertEqual(audit[0], 1)
        self.assertIn("org-suzhou", audit[1])
        self.assertIn("org-wujiang", audit[2])

    def test_fails_closed_for_unexpected_existing_source(self) -> None:
        connection = self._connection()
        connection.execute(
            "UPDATE org_units SET parent_id='org-unexpected' WHERE id='org-wuyue-3'"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.executescript(MIGRATION.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
