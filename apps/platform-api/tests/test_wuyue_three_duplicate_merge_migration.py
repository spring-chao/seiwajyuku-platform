from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[3] / "migrations" / "sqlite" / "0024_merge_duplicate_wuyue_three.sql"
CANONICAL = "3efcbf8d-c992-4f57-a09d-8b8cfa4cd134"


class WuyueThreeDuplicateMergeMigrationTests(unittest.TestCase):
    def _connection(self, *, active_source_members: int = 29) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE org_units (id TEXT PRIMARY KEY, name TEXT NOT NULL, unit_type TEXT NOT NULL, parent_id TEXT, is_active INTEGER NOT NULL, active_until TEXT, updated_at TEXT);
            CREATE TABLE members (id INTEGER PRIMARY KEY, org_unit_id TEXT, development_org_unit_id TEXT, status TEXT NOT NULL);
            CREATE TABLE member_org_relations (id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL, org_unit_id TEXT NOT NULL, relation_type TEXT NOT NULL, valid_from TEXT, valid_until TEXT, updated_at TEXT, UNIQUE(member_id, org_unit_id, relation_type));
            CREATE TABLE attendance_event_groups (id INTEGER PRIMARY KEY, study_org_unit_id TEXT, status TEXT NOT NULL, updated_at TEXT);
            CREATE TABLE app_users (id INTEGER PRIMARY KEY, username TEXT NOT NULL);
            CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, actor_user_id INTEGER, action TEXT, resource_type TEXT, resource_id TEXT, org_unit_id TEXT, purpose TEXT, result TEXT, before_json TEXT, after_json TEXT, created_at TEXT);
            INSERT INTO app_users VALUES (1, 'admin');
            INSERT INTO org_units(id,name,unit_type,parent_id,is_active) VALUES
                ('org-wujiang','吴江分中心','REGIONAL_CENTER','org-suzhou',1),
                ('org-wuyue-3','吴越三班','CLASS','org-wujiang',1),
                ('3efcbf8d-c992-4f57-a09d-8b8cfa4cd134','吴越三班','CLASS','org-wujiang',1),
                ('group-1','一组','GROUP','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',1),
                ('group-2','二组','GROUP','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',1),
                ('group-3','三组','GROUP','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',1),
                ('group-4','四组','GROUP','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',1),
                ('group-5','五组','GROUP','3efcbf8d-c992-4f57-a09d-8b8cfa4cd134',1);
            INSERT INTO attendance_event_groups VALUES (1,'org-wuyue-3','ACTIVE',NULL);
            """
        )
        for member_id in range(1, active_source_members + 1):
            connection.execute("INSERT INTO members VALUES (?, 'org-wujiang', NULL, 'ACTIVE')", (member_id,))
            connection.execute("INSERT INTO member_org_relations(id,member_id,org_unit_id,relation_type) VALUES (?,?, 'org-wuyue-3','STUDY_CLASS')", (member_id, member_id))
            if member_id <= 19:
                connection.execute("INSERT INTO member_org_relations(id,member_id,org_unit_id,relation_type) VALUES (?,?,?,'STUDY_CLASS')", (100 + member_id, member_id, CANONICAL))
        connection.execute("INSERT INTO members VALUES (30, 'org-wujiang', NULL, 'INACTIVE')")
        connection.execute("INSERT INTO member_org_relations(id,member_id,org_unit_id,relation_type) VALUES (30,30,'org-wuyue-3','STUDY_CLASS')")
        return connection

    def test_merges_ten_unique_active_relations_and_preserves_history(self) -> None:
        connection = self._connection()
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.assertEqual(connection.execute("SELECT is_active FROM org_units WHERE id='org-wuyue-3'").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT study_org_unit_id FROM attendance_event_groups WHERE id=1").fetchone()[0], CANONICAL)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM member_org_relations WHERE org_unit_id=?", (CANONICAL,)).fetchone()[0], 29)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM member_org_relations WHERE org_unit_id='org-wuyue-3' AND valid_until IS NOT NULL").fetchone()[0], 19)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM member_org_relations WHERE org_unit_id='org-wuyue-3' AND member_id=30").fetchone()[0], 1)
        audit = connection.execute("SELECT resource_id,after_json FROM audit_logs WHERE action='org.class_name_duplicate.merge'").fetchone()
        self.assertEqual(audit[0], 'org-wuyue-3')
        self.assertIn(CANONICAL, audit[1])

    def test_fails_closed_when_active_preview_count_changes(self) -> None:
        connection = self._connection(active_source_members=28)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.assertEqual(connection.execute("SELECT is_active FROM org_units WHERE id='org-wuyue-3'").fetchone()[0], 1)

    def test_retries_after_an_interrupted_run_left_the_guard_table(self) -> None:
        connection = self._connection()
        connection.execute(
            "CREATE TABLE migration_guard_0024_merge_duplicate_wuyue_three "
            "(ok INTEGER NOT NULL CHECK (ok = 1))"
        )

        connection.executescript(MIGRATION.read_text(encoding="utf-8"))

        self.assertEqual(
            connection.execute(
                "SELECT is_active FROM org_units WHERE id='org-wuyue-3'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='migration_guard_0024_merge_duplicate_wuyue_three'"
            ).fetchone()[0],
            0,
        )


if __name__ == '__main__':
    unittest.main()
