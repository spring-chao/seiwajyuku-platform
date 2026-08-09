from __future__ import annotations

import json
import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from uuid import uuid4
from pathlib import Path

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.legacy_operations_merge import apply_bundle, preview_bundle
from app.services.members import get_member_timeline


class LegacyOperationsMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        self.member_code = f"LEGACY-{uuid4().hex[:10].upper()}"
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            self.member_id = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
                "VALUES (?, '旧系统合并测试学员', 'org-suzhou', 'ACTIVE', ?, ?)",
                (self.member_code, now, now),
            ).lastrowid

    def _bundle(self, facts: list[dict] | None = None) -> bytes:
        payload = {
            "bundle_version": 1,
            "source_system": "seiwajyuku_system",
            "generated_at": "2032-01-02T00:00:00+00:00",
            "facts": facts
            if facts is not None
            else [
                {
                    "source_table": "reading_checkins",
                    "external_id": f"reading-{self.member_code}",
                    "member_code": self.member_code,
                    "occurred_on": "2032-01-01",
                    "participation_status": "COMPLETED",
                    "title": "活法",
                    "duration_minutes": 30,
                    "source_updated_at": "2032-01-01T09:00:00+08:00",
                },
                {
                    "source_table": "class_sessions",
                    "external_id": f"class-{self.member_code}",
                    "member_code": "NOT-IN-PLATFORM",
                    "occurred_on": "2032-01-01",
                    "participation_status": "PRESENT",
                    "title": "一月班会",
                },
            ],
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def test_preview_is_ephemeral_and_matches_only_by_member_code(self) -> None:
        content = self._bundle()
        result = preview_bundle(content, "legacy.json")
        self.assertEqual(result["summary"]["total"], 2)
        self.assertEqual(result["summary"]["importable"], 1)
        self.assertEqual(result["summary"]["unmatched_member"], 1)
        self.assertEqual(result["privacy"]["matching_key"], "member_code")
        self.assertFalse(result["privacy"]["contains_phones"])
        self.assertEqual(
            fetch_all(
                "SELECT * FROM member_activity_facts WHERE member_id=?",
                (self.member_id,),
            ),
            [],
        )

    def test_apply_is_audited_idempotent_and_visible_in_timeline(self) -> None:
        content = self._bundle()
        result = apply_bundle(
            content,
            "legacy.json",
            self.admin["id"],
            "合并旧系统学习活动事实",
            True,
        )
        self.assertEqual(result["importable"], 1)
        row = fetch_one(
            "SELECT * FROM member_activity_facts WHERE member_id=?",
            (self.member_id,),
        )
        self.assertEqual(row["activity_type"], "READING_CHECKIN")
        self.assertEqual(row["title"], "活法")
        self.assertNotIn("旧系统合并测试学员", str(row))
        audit = fetch_one(
            "SELECT action, purpose FROM audit_logs WHERE resource_type='import_batch' "
            "AND resource_id=? ORDER BY id DESC LIMIT 1",
            (str(result["batch_id"]),),
        )
        self.assertEqual(audit["action"], "legacy_operations.merge.apply")
        timeline = get_member_timeline(self.member_id, self.admin["id"])
        legacy = [
            event for event in timeline["events"] if event["event_type"] == "LEARNING_ACTIVITY"
        ]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["channel"], "READING_CHECKIN")
        with self.assertRaisesRegex(ValueError, "已经执行"):
            apply_bundle(
                content,
                "legacy.json",
                self.admin["id"],
                "再次执行相同合并包",
                True,
            )

    def test_sensitive_narratives_are_rejected(self) -> None:
        content = self._bundle(
            [
                {
                    "source_table": "reading_shares",
                    "external_id": "sensitive-1",
                    "member_code": self.member_code,
                    "occurred_on": "2032-01-01",
                    "participation_status": "COMPLETED",
                    "content": "不应进入统一平台的分享原文",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "禁止迁移字段"):
            preview_bundle(content, "unsafe.json")

    def test_sqlite_schema_has_a_clean_rollback(self) -> None:
        root = Path(__file__).resolve().parents[3]
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE org_units(id TEXT PRIMARY KEY);"
            "CREATE TABLE members(id INTEGER PRIMARY KEY);"
            "CREATE TABLE import_batches(id INTEGER PRIMARY KEY);"
        )
        connection.executescript(
            (root / "migrations/sqlite/0018_legacy_member_activity_facts.sql").read_text(
                encoding="utf-8"
            )
        )
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='member_activity_facts'"
        ).fetchone()
        self.assertIsNotNone(table)
        connection.executescript(
            (
                root
                / "migrations/rollback/sqlite/0018_legacy_member_activity_facts.down.sql"
            ).read_text(encoding="utf-8")
        )
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='member_activity_facts'"
        ).fetchone()
        self.assertIsNone(table)
        connection.close()

    def test_legacy_export_omits_personal_and_narrative_fields(self) -> None:
        root = Path(__file__).resolve().parents[3]
        module_path = root / "scripts/export_legacy_operations_bundle.py"
        spec = importlib.util.spec_from_file_location("legacy_bundle_export", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            database_path = Path(handle.name)
        try:
            connection = sqlite3.connect(database_path)
            connection.executescript(
                "CREATE TABLE member_identifiers(member_id INTEGER, identifier_type TEXT, "
                "identifier_value TEXT);"
                "CREATE TABLE reading_checkins(id INTEGER PRIMARY KEY, member_id INTEGER, "
                "checkin_date TEXT, book_name TEXT, duration_minutes INTEGER, "
                "audio_completed INTEGER, source_updated_at TEXT, content_summary TEXT);"
                "INSERT INTO member_identifiers VALUES (1, 'member_code', 'SAFE-001');"
                "INSERT INTO reading_checkins VALUES "
                "(9, 1, '2032-01-03', '活法', 20, 1, '2032-01-03T08:00:00', '私密心得');"
            )
            connection.commit()
            connection.close()
            bundle = module.export_bundle(f"sqlite:///{database_path.as_posix()}")
            encoded = json.dumps(bundle, ensure_ascii=False)
            self.assertEqual(len(bundle["facts"]), 1)
            self.assertEqual(bundle["facts"][0]["member_code"], "SAFE-001")
            self.assertNotIn("私密心得", encoded)
            self.assertNotIn("content_summary", encoded)
            self.assertNotIn("phone", bundle["facts"][0])
            self.assertFalse(bundle["privacy_contract"]["contains_phones"])
        finally:
            database_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
