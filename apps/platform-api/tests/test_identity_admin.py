from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db import execute, fetch_all, fetch_one, transaction
from app.main import app
from app.services.iam import accessible_org_ids, user_context


class IdentityAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()
        login = cls.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        if login.status_code != 200:
            raise AssertionError(login.text)
        cls.admin_headers = {
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        }
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for values in (
                (
                    "identity-admin-center",
                    "IDENTITY_ADMIN_CENTER",
                    "任职管理测试中心",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    "identity-admin-class",
                    "IDENTITY_ADMIN_CLASS",
                    "任职管理测试班级",
                    "CLASS",
                    "identity-admin-center",
                ),
            ):
                if not execute(
                    connection, "SELECT id FROM org_units WHERE id=?", (values[0],)
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                        "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        values + (now, now),
                    )
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES "
                "('identity-managed-account', '任职管理测试账号', ?, 1, ?, ?)",
                (hash_password("identity-managed-password"), now, now),
            )
            cls.managed_user_id = cursor.lastrowid
            tech_cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES "
                "('identity-technical-account', '技术职责测试账号', ?, 1, ?, ?)",
                (hash_password("identity-technical-password"), now, now),
            )
            cls.technical_user_id = tech_cursor.lastrowid
            gate_cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES "
                "('identity-gate-account', '写入门禁测试账号', ?, 1, ?, ?)",
                (hash_password("identity-gate-password"), now, now),
            )
            cls.gate_user_id = gate_cursor.lastrowid
            duplicate_cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES "
                "('identity-duplicate-account', '重复雇佣测试账号', ?, 1, ?, ?)",
                (hash_password("identity-duplicate-password"), now, now),
            )
            cls.duplicate_user_id = duplicate_cursor.lastrowid

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    def _initialize(self, user_id: int, reference: str) -> str:
        response = self.client.post(
            f"/api/v1/identity-admin/accounts/{user_id}/initialize",
            headers=self.admin_headers,
            json={
                "source_reference": reference,
                "confirmation_note": "业务负责人已确认该账号对应的自然人档案",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["person_id"]

    def test_01_write_gate_blocks_without_explicit_enablement(self) -> None:
        with patch.dict(
            os.environ, {"IDENTITY_ADMIN_WRITES_ENABLED": "false"}
        ):
            response = self.client.post(
                f"/api/v1/identity-admin/accounts/{self.gate_user_id}/initialize",
                headers=self.admin_headers,
                json={
                    "source_reference": "gate-test",
                    "confirmation_note": "验证写入灰度关闭时不能建立关联",
                },
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("写入尚未获准", response.text)
        self.assertIsNone(
            fetch_one(
                "SELECT person_id FROM account_person_links WHERE user_id=?",
                (self.gate_user_id,),
            )
        )

    def test_02_initialize_employment_and_volunteer_with_audit(self) -> None:
        person_id = self._initialize(
            self.managed_user_id, "approved-identity-link-001"
        )
        now = datetime.now(UTC)
        employment = self.client.post(
            f"/api/v1/identity-admin/accounts/{self.managed_user_id}/employments",
            headers=self.admin_headers,
            json={
                "position_key": "ops_center_learning",
                "started_on": (now - timedelta(days=1)).isoformat(),
                "ended_on": None,
                "service_responsibilities": [
                    {
                        "org_unit_id": "identity-admin-center",
                        "scope_type": "SUBTREE",
                    }
                ],
                "source_reference": "approved-employment-001",
                "confirmation_note": "已确认运营中心雇佣、学习践行岗位和服务责任范围",
            },
        )
        self.assertEqual(employment.status_code, 200, employment.text)
        context = user_context(self.managed_user_id)
        self.assertIn("ops_center_learning", context["roles"])
        self.assertIn("OPERATIONS_EMPLOYEE", context["subject_contexts"])
        self.assertEqual(
            accessible_org_ids(self.managed_user_id),
            {"identity-admin-center", "identity-admin-class"},
        )
        member_link = fetch_one(
            "SELECT member_id FROM member_identities WHERE person_id=?",
            (person_id,),
        )
        self.assertIsNone(member_link)

        volunteer = self.client.post(
            f"/api/v1/identity-admin/accounts/{self.managed_user_id}/volunteer-appointments",
            headers=self.admin_headers,
            json={
                "appointment_key": "volunteer_class_counselor",
                "org_unit_id": "identity-admin-class",
                "scope_type": "UNIT",
                "starts_at": (now - timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(days=30)).isoformat(),
                "source_reference": "approved-volunteer-001",
                "confirmation_note": "已确认班主任志工任职、班级范围和本次任期",
            },
        )
        self.assertEqual(volunteer.status_code, 200, volunteer.text)
        volunteer_id = volunteer.json()["data"]["id"]
        context = user_context(self.managed_user_id)
        self.assertIn("volunteer_class_counselor", context["roles"])
        self.assertIn("VOLUNTEER", context["subject_contexts"])
        self.assertEqual(context["language_context"], "OPERATIONS")

        revoke = self.client.post(
            f"/api/v1/identity-admin/assignments/volunteer/{volunteer_id}/status",
            headers=self.admin_headers,
            json={
                "status": "REVOKED",
                "reason": "业务负责人确认本次志工任命撤销",
            },
        )
        self.assertEqual(revoke.status_code, 200, revoke.text)
        context = user_context(self.managed_user_id)
        self.assertNotIn("volunteer_class_counselor", context["roles"])
        self.assertIn("ops_center_learning", context["roles"])

        actions = {
            row["action"]
            for row in fetch_all(
                "SELECT action FROM audit_logs WHERE resource_type IN "
                "('person_profile','operations_employment','volunteer_appointment')"
            )
        }
        self.assertTrue(
            {
                "identity.person.link",
                "identity.employment.create",
                "identity.volunteer_appointment.create",
                "identity.volunteer.status_change",
            }.issubset(actions)
        )

    def test_03_technical_admin_can_manage_identity_without_business_access(self) -> None:
        self._initialize(
            self.technical_user_id, "approved-technical-person-link-001"
        )
        now = datetime.now(UTC)
        response = self.client.post(
            f"/api/v1/identity-admin/accounts/{self.technical_user_id}/technical-assignments",
            headers=self.admin_headers,
            json={
                "assignment_purpose": "测试环境身份与任职配置维护",
                "starts_at": (now - timedelta(minutes=1)).isoformat(),
                "ends_at": (now + timedelta(days=7)).isoformat(),
                "source_reference": "approved-technical-assignment-001",
                "confirmation_note": "已批准限定时间内执行测试环境身份配置维护",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        context = user_context(self.technical_user_id)
        self.assertEqual(context["roles"], ["technical_admin"])
        self.assertIn("iam:manage", context["permissions"])
        self.assertNotIn("members:read", context["permissions"])
        self.assertEqual(context["scopes"], [])

        login = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": "identity-technical-account",
                "password": "identity-technical-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = {
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        }
        accounts = self.client.get(
            "/api/v1/identity-admin/accounts", headers=headers
        )
        self.assertEqual(accounts.status_code, 200, accounts.text)
        self.assertNotIn("password_hash", accounts.text)
        members = self.client.get("/api/v1/members", headers=headers)
        self.assertEqual(members.status_code, 403)

    def test_04_requires_authentication_and_rejects_duplicate_employment(self) -> None:
        unauthenticated = self.client.get("/api/v1/identity-admin/accounts")
        self.assertEqual(unauthenticated.status_code, 401)
        now = datetime.now(UTC)
        self._initialize(
            self.duplicate_user_id, "approved-duplicate-person-link-001"
        )
        first = self.client.post(
            f"/api/v1/identity-admin/accounts/{self.duplicate_user_id}/employments",
            headers=self.admin_headers,
            json={
                "position_key": "ops_center_operations",
                "started_on": now.isoformat(),
                "service_responsibilities": [],
                "source_reference": "first-employment-record",
                "confirmation_note": "建立首条有效雇佣记录用于重复校验",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        duplicate = self.client.post(
            f"/api/v1/identity-admin/accounts/{self.duplicate_user_id}/employments",
            headers=self.admin_headers,
            json={
                "position_key": "ops_center_operations",
                "started_on": now.isoformat(),
                "service_responsibilities": [],
                "source_reference": "duplicate-employment-check",
                "confirmation_note": "验证不能重复建立未结束的运营中心雇佣记录",
            },
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("已有未结束", duplicate.text)


if __name__ == "__main__":
    unittest.main()
