from __future__ import annotations

import json
import os
import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.db import execute, fetch_all, fetch_one, transaction
from app.migrations import run_migrations
from app.services.iam import create_user, seed_iam
from app.services.members import (
    create_member,
    get_member_timeline,
    record_member_service_signal_feedback,
)
from app.services.member_service_signals import RULE_VERSION, build_member_service_signals


class MemberServiceSignalFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for org_id, code, name in (
                ("signal-feedback-center", "SIGNAL_FEEDBACK", "提示反馈测试中心"),
                ("signal-outside-center", "SIGNAL_OUTSIDE", "提示反馈范围外中心"),
            ):
                if not execute(
                    connection, "SELECT id FROM org_units WHERE id=?", (org_id,)
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                        "is_active, created_at, updated_at) VALUES (?, ?, ?, "
                        "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                        (org_id, code, name, now, now),
                    )
        cls.member_id = create_member(
            cls.admin["id"],
            member_code="SIGNAL-FEEDBACK-001",
            name="服务提示反馈测试学长",
            org_unit_id="signal-feedback-center",
            development_org_unit_id=None,
            phone=None,
        )
        cls.read_only_user_id = create_user(
            cls.admin["id"],
            username="signal-feedback-read-only",
            display_name="提示反馈只读账号",
            password="signal-feedback-read-only-password",
            roles=["read_only"],
            scopes=[{"scope_type": "SUBTREE", "org_unit_id": "signal-feedback-center"}],
        )
        cls.outside_manager_id = create_user(
            cls.admin["id"],
            username="signal-feedback-outside",
            display_name="提示反馈范围外负责人",
            password="signal-feedback-outside-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "SUBTREE", "org_unit_id": "signal-outside-center"}],
        )

    def test_feedback_is_append_only_and_attached_to_active_rule(self) -> None:
        first = record_member_service_signal_feedback(
            self.member_id,
            self.admin["id"],
            signal_code="CONTACT_INFO_REVIEW",
            rule_version=RULE_VERSION,
            feedback_status="NOT_APPLICABLE",
        )
        self.assertEqual(first["status"], "NOT_APPLICABLE")
        second = record_member_service_signal_feedback(
            self.member_id,
            self.admin["id"],
            signal_code="CONTACT_INFO_REVIEW",
            rule_version=RULE_VERSION,
            feedback_status="CONFIRMED_VALID",
        )
        self.assertNotEqual(first["id"], second["id"])

        rows = fetch_all(
            "SELECT feedback_status, evidence_json FROM member_service_signal_feedback "
            "WHERE member_id=? AND signal_code='CONTACT_INFO_REVIEW' ORDER BY id",
            (self.member_id,),
        )
        self.assertEqual(
            [row["feedback_status"] for row in rows[-2:]],
            ["NOT_APPLICABLE", "CONFIRMED_VALID"],
        )
        evidence = json.loads(rows[-1]["evidence_json"])
        self.assertEqual(evidence, {"masked_phone_present": False})
        self.assertNotIn("phone", rows[-1]["evidence_json"].replace("masked_phone", ""))

        timeline = get_member_timeline(self.member_id, self.admin["id"])
        signal = next(
            item
            for item in timeline["service_signals"]
            if item["code"] == "CONTACT_INFO_REVIEW"
        )
        self.assertEqual(signal["latest_feedback"]["status"], "CONFIRMED_VALID")
        self.assertTrue(timeline["service_signal_feedback_enabled"])

        audit = fetch_one(
            "SELECT action, before_json, after_json FROM audit_logs "
            "WHERE resource_type='member_service_signal_feedback' "
            "AND resource_id=? ORDER BY id DESC LIMIT 1",
            (str(second["id"]),),
        )
        self.assertEqual(audit["action"], "members.service_signal.feedback")
        self.assertEqual(json.loads(audit["before_json"])["status"], "NOT_APPLICABLE")
        audit_after = json.loads(audit["after_json"])
        self.assertEqual(audit_after["signal_code"], "CONTACT_INFO_REVIEW")
        self.assertNotIn("服务提示反馈测试学长", audit["after_json"])

    def test_feedback_rejects_invalid_rule_permission_scope_and_disabled_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "不支持的服务提示反馈状态"):
            record_member_service_signal_feedback(
                self.member_id,
                self.admin["id"],
                signal_code="CONTACT_INFO_REVIEW",
            rule_version=RULE_VERSION,
                feedback_status="IGNORED",
            )
        with self.assertRaisesRegex(ValueError, "规则版本已变化"):
            record_member_service_signal_feedback(
                self.member_id,
                self.admin["id"],
                signal_code="CONTACT_INFO_REVIEW",
                rule_version="member-service-signals/0.9",
                feedback_status="CONFIRMED_VALID",
            )
        with self.assertRaisesRegex(PermissionError, "当前角色不能提交"):
            record_member_service_signal_feedback(
                self.member_id,
                self.read_only_user_id,
                signal_code="CONTACT_INFO_REVIEW",
            rule_version=RULE_VERSION,
                feedback_status="CONFIRMED_VALID",
            )
        with self.assertRaisesRegex(PermissionError, "不在组织授权范围"):
            record_member_service_signal_feedback(
                self.member_id,
                self.outside_manager_id,
                signal_code="CONTACT_INFO_REVIEW",
            rule_version=RULE_VERSION,
                feedback_status="CONFIRMED_VALID",
            )
        with patch.dict(
            os.environ, {"MEMBER_SERVICE_SIGNAL_FEEDBACK_ENABLED": "false"}
        ):
            with self.assertRaisesRegex(PermissionError, "尚未启用"):
                record_member_service_signal_feedback(
                    self.member_id,
                    self.admin["id"],
                    signal_code="CONTACT_INFO_REVIEW",
                    rule_version=RULE_VERSION,
                    feedback_status="CONFIRMED_VALID",
                )

    def test_read_only_signals_do_not_query_feedback_history_when_gate_is_closed(self) -> None:
        with patch.dict(
            os.environ, {"MEMBER_SERVICE_SIGNAL_FEEDBACK_ENABLED": "false"}
        ), patch(
            "app.services.member_service_signals.attach_latest_feedback",
            side_effect=AssertionError("feedback history must stay unopened"),
        ):
            timeline = get_member_timeline(self.member_id, self.admin["id"])
        self.assertFalse(timeline["service_signal_feedback_enabled"])
        self.assertTrue(timeline["service_signals"])

    def test_closed_renewal_statuses_do_not_trigger_due_signal(self) -> None:
        member_id = create_member(
            self.admin["id"],
            member_code="SIGNAL-RENEWAL-CLOSED-001",
            name="续费提示状态测试学员",
            org_unit_id="signal-feedback-center",
            development_org_unit_id=None,
            phone="13800000000",
        )
        now = datetime(2026, 8, 11, tzinfo=UTC)
        with transaction() as connection:
            for index, status in enumerate(("RENEWED", "NOT_RENEWING", "EXITED"), 1):
                execute(
                    connection,
                    "INSERT INTO renewal_cycles "
                    "(member_id, renewal_year, org_unit_id, due_month, status, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        member_id,
                        2020 + index,
                        "signal-feedback-center",
                        1,
                        status,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
        member = fetch_one("SELECT * FROM members WHERE id=?", (member_id,))
        signals = build_member_service_signals(
            member,
            self.admin["id"],
            {"renewals:read"},
            {"signal-feedback-center"},
            now=now,
        )
        self.assertNotIn("RENEWAL_DUE", {item["code"] for item in signals})

    def test_sqlite_feedback_schema_has_a_clean_rollback(self) -> None:
        root = Path(__file__).resolve().parents[3]
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE org_units(id TEXT PRIMARY KEY);"
            "CREATE TABLE members(id INTEGER PRIMARY KEY);"
            "CREATE TABLE app_users(id INTEGER PRIMARY KEY);"
        )
        connection.executescript(
            (
                root / "migrations/sqlite/0019_member_service_signal_feedback.sql"
            ).read_text(encoding="utf-8")
        )
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='member_service_signal_feedback'"
        ).fetchone()
        self.assertIsNotNone(table)
        connection.executescript(
            (
                root
                / "migrations/rollback/sqlite/0019_member_service_signal_feedback.down.sql"
            ).read_text(encoding="utf-8")
        )
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='member_service_signal_feedback'"
        ).fetchone()
        self.assertIsNone(table)
        connection.close()


if __name__ == "__main__":
    unittest.main()
