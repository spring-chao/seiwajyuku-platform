from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.db import execute, fetch_all, fetch_one, transaction
from app.migrations import run_migrations
from app.services.iam import create_user, seed_iam
from app.services.members import (
    _as_utc,
    create_member,
    create_sensitive_export,
    download_sensitive_export,
    get_member_detail,
    get_member_change_history,
    get_member_enterprise_detail,
    get_member_timeline,
    list_members,
    normal_export_csv,
    reveal_contact,
)


class PrivacyIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            if not execute(
                connection, "SELECT id FROM org_units WHERE id='privacy-center'"
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, "
                    "created_at, updated_at) VALUES ('privacy-center', 'PRIVACY_CENTER', ?, "
                    "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                    ("隐私测试中心", now, now),
                )
            if not execute(
                connection, "SELECT id FROM org_units WHERE id='privacy-class'"
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, "
                    "created_at, updated_at) VALUES ('privacy-class', 'PRIVACY_CLASS', ?, "
                    "'CLASS', 'privacy-center', 1, ?, ?)",
                    ("圆融一班", now, now),
                )
            if not execute(
                connection, "SELECT id FROM org_units WHERE id='privacy-group'"
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, "
                    "created_at, updated_at) VALUES ('privacy-group', 'PRIVACY_GROUP', ?, "
                    "'GROUP', 'privacy-class', 1, ?, ?)",
                    ("圆梦组", now, now),
                )
        cls.regional_user_id = create_user(
            cls.admin["id"],
            username="privacy-regional",
            display_name="隐私测试负责人",
            password="privacy-regional-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "SUBTREE", "org_unit_id": "privacy-center"}],
        )
        cls.security_user_id = create_user(
            cls.admin["id"],
            username="privacy-security",
            display_name="隐私测试安全员",
            password="privacy-security-password",
            roles=["data_security_admin"],
            scopes=[{"scope_type": "ALL", "org_unit_id": None}],
        )
        cls.member_id = create_member(
            cls.admin["id"],
            member_code="PRIVACY-001",
            name="隐私测试学长",
            org_unit_id="privacy-center",
            development_org_unit_id=None,
            phone="13800138000",
            company_name="示例企业",
        )
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO followup_tasks(member_id, org_unit_id, task_type, service_purpose, "
                "assigned_user_id, status, confidentiality_level, due_at, created_by, created_at, "
                "updated_at) VALUES (?, 'privacy-center', 'PHONE', ?, ?, 'OPEN', 'ASSIGNEE', ?, ?, ?, ?)",
                (
                    cls.member_id,
                    "确认近期经营支持需求",
                    cls.regional_user_id,
                    (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    cls.admin["id"],
                    now,
                    now,
                ),
            )
            cls.task_id = cursor.lastrowid

    def test_member_list_and_normal_export_are_masked(self) -> None:
        rows = list_members(self.regional_user_id)
        member = next(row for row in rows if row["id"] == self.member_id)
        self.assertEqual(member["phone_masked"], "138****8000")
        self.assertNotIn("phone_ciphertext", member)
        self.assertNotIn("company_name", member)
        self.assertNotIn("birthday", member)
        self.assertNotIn("notes", member)
        self.assertNotIn("sensitivity_level", member)
        content = normal_export_csv(self.regional_user_id)
        self.assertIn("138****8000", content)
        self.assertNotIn("13800138000", content)

    def test_mysql_datetime_is_accepted_for_task_deadline(self) -> None:
        value = datetime(2026, 7, 27, 9, 30)
        self.assertEqual(_as_utc(value), value.replace(tzinfo=UTC))

    def test_duplicate_phone_is_rejected_before_second_member_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "手机号已存在学员档案"):
            create_member(
                self.admin["id"],
                member_code="PRIVACY-DUPLICATE-001",
                name="重复手机号测试学长",
                org_unit_id="privacy-center",
                development_org_unit_id=None,
                phone="13800138000",
            )

    def test_regional_manager_detail_returns_masked_phone(self) -> None:
        """get_member_detail now returns masked phone, not full phone.

        Full phone access requires reveal_contact (task-based).
        """
        profile = get_member_detail(self.member_id, self.regional_user_id)
        self.assertEqual(profile["phone_masked"], "138****8000")
        self.assertNotIn("phone_ciphertext", profile)
        self.assertNotIn("phone", profile)  # No full phone in basic detail
        self.assertNotIn("annual_sales", profile)  # No financial data in basic detail
        self.assertNotIn("company_address", profile)
        self.assertNotIn("company_products", profile)
        self.assertNotIn("notes", profile)
        self.assertEqual(profile["name"], "隐私测试学长")

    def test_member_timeline_is_scoped_and_metadata_only(self) -> None:
        timeline = get_member_timeline(self.member_id, self.regional_user_id)
        self.assertEqual(timeline["member"]["phone_masked"], "138****8000")
        self.assertNotIn("phone_ciphertext", timeline["member"])
        self.assertIn("FOLLOWUP_TASK", timeline["summary"])
        self.assertTrue(any(item["event_type"] == "FOLLOWUP_TASK" for item in timeline["events"]))
        serialized = str(timeline)
        self.assertNotIn("13800138000", serialized)
        self.assertNotIn("确认近期经营支持需求", serialized)
        self.assertTrue(
            any(
                item["code"] == "STUDY_CLASS_RELATION_REVIEW"
                for item in timeline["service_signals"]
            )
        )
        self.assertTrue(
            all(item["rule_version"] == "member-service-signals/1.0" for item in timeline["service_signals"])
        )
        self.assertNotIn("score", serialized.lower())
        self.assertNotIn("rank", serialized.lower())

    def test_member_change_history_is_redacted_and_audited(self) -> None:
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO member_change_history(member_id, change_type, before_json, "
                "after_json, changed_by, changed_at) VALUES (?, 'PROFILE_UPDATE', ?, ?, ?, ?)",
                (
                    self.member_id,
                    '{"status":"ACTIVE","notes":"内部关怀备注","company_name":"敏感企业"}',
                    '{"status":"SUSPENDED","notes":"不可经普通历史接口返回","company_name":"敏感企业"}',
                    self.admin["id"],
                    now,
                ),
            )
        history = get_member_change_history(self.member_id, self.regional_user_id)
        serialized = str(history)
        self.assertIn("SUSPENDED", serialized)
        self.assertNotIn("内部关怀备注", serialized)
        self.assertNotIn("不可经普通历史接口返回", serialized)
        self.assertNotIn("敏感企业", serialized)
        audit = fetch_one(
            "SELECT action, after_json FROM audit_logs WHERE actor_user_id=? "
            "AND resource_type='member' AND resource_id=? ORDER BY id DESC LIMIT 1",
            (self.regional_user_id, str(self.member_id)),
        )
        self.assertEqual(audit["action"], "members.change_history.view")
        self.assertIn("privacy_safe_history", audit["after_json"])

    def test_enterprise_detail_requires_purpose_without_revealing_phone(self) -> None:
        """Enterprise details stay separate from task-based phone access."""
        # Regional manager cannot access enterprise detail
        with self.assertRaises(PermissionError):
            get_member_enterprise_detail(self.member_id, self.regional_user_id, "测试用途")
        # Operations admin (system_admin here) can access with purpose.
        profile = get_member_enterprise_detail(self.member_id, self.admin["id"], "运营核查用途")
        self.assertEqual(profile["phone_masked"], "138****8000")
        self.assertNotIn("phone", profile)
        self.assertNotIn("phone_ciphertext", profile)
        self.assertEqual(profile["name"], "隐私测试学长")
        # Purpose too short
        with self.assertRaises(ValueError):
            get_member_enterprise_detail(self.member_id, self.admin["id"], "短")

    def test_contact_reveal_requires_current_assignee(self) -> None:
        revealed = reveal_contact(
            member_id=self.member_id,
            task_id=self.task_id,
            actor_user_id=self.regional_user_id,
            purpose="执行已分配的电话跟进",
            client_reference="privacy-test",
        )
        self.assertEqual(revealed["phone"], "13800138000")
        with self.assertRaises(PermissionError):
            reveal_contact(
                member_id=self.member_id,
                task_id=self.task_id,
                actor_user_id=self.admin["id"],
                purpose="非责任人尝试查看联系方式",
                client_reference="privacy-test-denied",
            )
        logs = fetch_all(
            "SELECT action, before_json, after_json FROM audit_logs "
            "WHERE resource_type IN ('member', 'member_export')"
        )
        self.assertNotIn("13800138000", str(logs))
        contact_logs = fetch_all(
            "SELECT result, client_reference FROM contact_access_logs WHERE member_id=?",
            (self.member_id,),
        )
        self.assertIn(
            {"result": "SUCCESS", "client_reference": "privacy-test"},
            contact_logs,
        )
        self.assertIn(
            {"result": "DENIED_TASK_OWNER_OR_STATUS", "client_reference": "privacy-test-denied"},
            contact_logs,
        )

    def test_sensitive_export_isolated_and_watermarked(self) -> None:
        with self.assertRaises(PermissionError):
            create_sensitive_export(self.admin["id"], "系统管理员越权测试", True)
        with self.assertRaises(ValueError):
            create_sensitive_export(self.security_user_id, "安全复核导出", False)
        job_id = create_sensitive_export(
            self.security_user_id, "安全复核导出测试用途", True
        )
        content = download_sensitive_export(job_id, self.security_user_id)
        self.assertIn("敏感数据", content)
        self.assertIn("13800138000", content)
        job = fetch_one(
            "SELECT payload_ciphertext, expires_at FROM sensitive_export_jobs WHERE id=?",
            (job_id,),
        )
        self.assertNotIn("13800138000", job["payload_ciphertext"])
        self.assertIsNotNone(job["expires_at"])

    def test_extended_member_profile_and_financial_fields_are_protected(self) -> None:
        member_id = create_member(
            self.admin["id"],
            member_code=None,
            name="扩展资料测试学长",
            org_unit_id="privacy-center",
            development_org_unit_id=None,
            phone="13700137000",
            company_name="扩展资料测试企业",
            gender="MALE",
            district="吴江区",
            company_address="测试地址",
            class_org_unit_id="privacy-class",
            group_org_unit_id="privacy-group",
            birthday="1988-08-08",
            join_date="2024-01-01",
            study_start_date="2024-01-01",
            membership_years=2.5,
            renewal_month="2026-01",
            position="总经理",
            referrer="推荐人",
            referrer_center="吴江分中心",
            industry_category="制造业",
            industry="装备制造",
            company_products="测试产品",
            annual_sales="5000万元",
            company_size="100-199人",
            profit_margin="12%",
            notes="扩展字段验证",
        )
        row = next(item for item in list_members(self.admin["id"]) if item["id"] == member_id)
        self.assertEqual(row["class_name"], "圆融一班")
        self.assertEqual(row["group_name"], "圆梦组")
        self.assertNotIn("annual_sales", row)
        stored = fetch_one(
            "SELECT member_code, enterprise_financial_ciphertext FROM members WHERE id=?",
            (member_id,),
        )
        self.assertTrue(stored["member_code"].startswith("MEM-"))
        self.assertNotIn("5000万元", stored["enterprise_financial_ciphertext"])
        self.assertNotIn("12%", stored["enterprise_financial_ciphertext"])


if __name__ == "__main__":
    unittest.main()
