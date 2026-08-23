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
                (
                    "identity-admin-center-two",
                    "IDENTITY_ADMIN_CENTER_TWO",
                    "任职管理第二测试中心",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    "identity-admin-class-two",
                    "IDENTITY_ADMIN_CLASS_TWO",
                    "任职管理第二测试班级",
                    "CLASS",
                    "identity-admin-center-two",
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

    def test_catalog_exposes_read_only_permission_matrix(self) -> None:
        response = self.client.get(
            "/api/v1/identity-admin/catalog", headers=self.admin_headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        matrix = response.json()["data"]["permission_matrix"]
        by_role = {item["role_key"]: item for item in matrix}
        self.assertIn("system_admin", by_role)
        self.assertIn("technical_admin", by_role)
        technical_permissions = {
            item["permission_key"]
            for item in by_role["technical_admin"]["permissions"]
        }
        self.assertIn("iam:manage", technical_permissions)
        self.assertNotIn("members:read", technical_permissions)
        self.assertNotIn("exports:sensitive", technical_permissions)

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

    def test_01b_platform_admin_is_not_a_person_link_candidate(self) -> None:
        admin_id = fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"]
        response = self.client.post(
            f"/api/v1/identity-admin/accounts/{admin_id}/initialize",
            headers=self.admin_headers,
            json={
                "source_reference": "platform-admin-boundary",
                "confirmation_note": "平台最高管理账号不能作为真实身份试点对象",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("不作为自然人", response.text)
        self.assertIsNone(
            fetch_one(
                "SELECT person_id FROM account_person_links WHERE user_id=?", (admin_id,)
            )
        )

    def test_01c_onboarding_gate_rolls_back_without_creating_account(self) -> None:
        now = datetime.now(UTC)
        with patch.dict(os.environ, {"IDENTITY_ADMIN_WRITES_ENABLED": "false"}):
            response = self.client.post(
                "/api/v1/identity-admin/employees/onboard",
                headers=self.admin_headers,
                json={
                    "new_account": {
                        "username": "identity-onboarding-gate",
                        "display_name": "一站式门禁测试账号",
                        "password": "identity-onboarding-gate-password",
                    },
                    "position_keys": ["ops_center_operations"],
                    "started_on": now.isoformat(),
                    "ended_on": (now + timedelta(days=30)).isoformat(),
                    "service_responsibilities": [
                        {
                            "org_unit_id": "identity-admin-center",
                            "scope_type": "SUBTREE",
                        }
                    ],
                    "source_reference": "onboarding-gate-check",
                    "confirmation_note": "验证门禁关闭时整单不创建任何账号和任职记录",
                },
            )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIsNone(
            fetch_one(
                "SELECT id FROM app_users WHERE username='identity-onboarding-gate'"
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

    def test_02b_supports_multiple_positions_under_one_employment(self) -> None:
        now = datetime.now(UTC)
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES "
                "('identity-multi-position-account', '盛和塾', ?, 1, ?, ?)",
                (hash_password("identity-multi-position-password"), now.isoformat(), now.isoformat()),
            )
            user_id = cursor.lastrowid
        self._initialize(user_id, "approved-multi-position-link-001")
        response = self.client.post(
            f"/api/v1/identity-admin/accounts/{user_id}/employments",
            headers=self.admin_headers,
            json={
                "position_keys": [
                    "operations_admin",
                    "ops_center_operations",
                    "ops_center_data",
                    "ops_center_administration",
                ],
                "started_on": (now - timedelta(minutes=1)).isoformat(),
                "ended_on": (now + timedelta(days=1)).isoformat(),
                "service_responsibilities": [
                    {"org_unit_id": "identity-admin-center", "scope_type": "SUBTREE"}
                ],
                "source_reference": "approved-multi-position-employment-001",
                "confirmation_note": "已确认四个岗位属于同一运营中心雇佣并受统一期限约束",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        employment_id = response.json()["data"]["id"]
        positions = fetch_all(
            "SELECT position_key FROM operations_position_assignments "
            "WHERE employment_id=? ORDER BY position_key",
            (employment_id,),
        )
        self.assertEqual(
            [row["position_key"] for row in positions],
            [
                "operations_admin",
                "ops_center_administration",
                "ops_center_data",
                "ops_center_operations",
            ],
        )
        context = user_context(user_id)
        self.assertEqual(
            set(context["roles"]),
            {
                "operations_admin",
                "ops_center_administration",
                "ops_center_data",
                "ops_center_operations",
            },
        )

    def test_02c_account_suspension_revokes_sessions_and_protects_admin(self) -> None:
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES "
                "('identity-account-status', '账号停用测试', ?, 1, ?, ?)",
                (hash_password("identity-account-status-password"), now, now),
            )
            user_id = cursor.lastrowid
        login = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": "identity-account-status",
                "password": "identity-account-status-password",
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        token = login.json()["data"]["access_token"]
        suspended = self.client.post(
            f"/api/v1/identity-admin/accounts/{user_id}/status",
            headers=self.admin_headers,
            json={"status": "SUSPENDED", "reason": "测试结束后回收临时账号"},
        )
        self.assertEqual(suspended.status_code, 200, suspended.text)
        self.assertEqual(
            fetch_one("SELECT is_active FROM app_users WHERE id=?", (user_id,))["is_active"],
            0,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code,
            401,
        )
        audit = fetch_one(
            "SELECT action FROM audit_logs WHERE resource_type='app_user' "
            "AND resource_id=? ORDER BY id DESC LIMIT 1",
            (str(user_id),),
        )
        self.assertEqual(audit["action"], "identity.account.status_change")
        protected = self.client.post(
            "/api/v1/identity-admin/accounts/1/status",
            headers=self.admin_headers,
            json={"status": "SUSPENDED", "reason": "不应停用平台最高管理账号"},
        )
        self.assertEqual(protected.status_code, 400, protected.text)

    def test_02d_atomic_employee_onboarding_supports_two_centers(self) -> None:
        now = datetime.now(UTC)
        username = "13800001234"
        password = "unique-onboarding-password"
        response = self.client.post(
            "/api/v1/identity-admin/employees/onboard",
            headers=self.admin_headers,
            json={
                "new_account": {
                    "username": username,
                    "display_name": "双中心一站式测试账号",
                    "password": password,
                },
                "position_keys": [
                    "ops_center_operations",
                    "ops_center_management",
                ],
                "started_on": (now - timedelta(minutes=1)).isoformat(),
                "ended_on": (now + timedelta(days=365)).isoformat(),
                "service_responsibilities": [
                    {
                        "org_unit_id": "identity-admin-center",
                        "scope_type": "SUBTREE",
                    },
                    {
                        "org_unit_id": "identity-admin-center-two",
                        "scope_type": "SUBTREE",
                    },
                ],
                "source_reference": "approved-two-center-onboarding",
                "confirmation_note": "已逐项确认两个中心、两个岗位、任职期限和回滚责任",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertTrue(result["account_created"])
        self.assertTrue(result["person_link_created"])

        user_id = result["user_id"]
        self.assertEqual(
            fetch_one("SELECT username FROM app_users WHERE id=?", (user_id,))[
                "username"
            ],
            username,
        )
        positions = fetch_all(
            "SELECT position_key FROM operations_position_assignments "
            "WHERE employment_id=? ORDER BY position_key",
            (result["employment_id"],),
        )
        self.assertEqual(
            [row["position_key"] for row in positions],
            ["ops_center_management", "ops_center_operations"],
        )
        responsibilities = fetch_all(
            "SELECT org_unit_id, scope_type FROM employee_service_responsibilities "
            "WHERE employment_id=? ORDER BY org_unit_id",
            (result["employment_id"],),
        )
        self.assertEqual(
            responsibilities,
            [
                {
                    "org_unit_id": "identity-admin-center",
                    "scope_type": "SUBTREE",
                },
                {
                    "org_unit_id": "identity-admin-center-two",
                    "scope_type": "SUBTREE",
                },
            ],
        )
        self.assertEqual(
            accessible_org_ids(user_id),
            {
                "identity-admin-center",
                "identity-admin-class",
                "identity-admin-center-two",
                "identity-admin-class-two",
            },
        )
        context = user_context(user_id)
        self.assertEqual(
            set(context["roles"]),
            {"ops_center_operations", "ops_center_management"},
        )

        audit_rows = fetch_all(
            "SELECT action, after_json FROM audit_logs WHERE "
            "(resource_type='app_user' AND resource_id=?) OR "
            "(resource_type='operations_employment' AND resource_id=?) "
            "ORDER BY id",
            (str(user_id), str(result["employment_id"])),
        )
        self.assertEqual(
            {row["action"] for row in audit_rows},
            {"iam.user.create", "identity.employment.create"},
        )
        serialized_audit = " ".join(row["after_json"] or "" for row in audit_rows)
        self.assertNotIn(username, serialized_audit)
        self.assertNotIn(password, serialized_audit)
        self.assertIn("138****1234", serialized_audit)

        accounts = self.client.get(
            "/api/v1/identity-admin/accounts", headers=self.admin_headers
        )
        self.assertEqual(accounts.status_code, 200, accounts.text)
        listed = next(item for item in accounts.json()["data"] if item["id"] == user_id)
        self.assertEqual(listed["username"], "138****1234")
        self.assertFalse(listed["is_platform_admin"])

    def test_02e_onboarding_is_atomic_when_scope_is_invalid(self) -> None:
        now = datetime.now(UTC)
        response = self.client.post(
            "/api/v1/identity-admin/employees/onboard",
            headers=self.admin_headers,
            json={
                "new_account": {
                    "username": "identity-onboarding-rollback",
                    "display_name": "整单回滚测试账号",
                    "password": "identity-onboarding-rollback-password",
                },
                "position_keys": ["ops_center_operations"],
                "started_on": now.isoformat(),
                "ended_on": (now + timedelta(days=30)).isoformat(),
                "service_responsibilities": [
                    {"org_unit_id": "missing-org", "scope_type": "SUBTREE"}
                ],
                "source_reference": "atomic-rollback-check",
                "confirmation_note": "验证组织范围无效时账号、人员和任职全部回滚",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("组织不存在", response.text)
        self.assertIsNone(
            fetch_one(
                "SELECT id FROM app_users WHERE username='identity-onboarding-rollback'"
            )
        )

    def test_02f_onboarding_can_use_an_existing_unlinked_account(self) -> None:
        now = datetime.now(UTC)
        response = self.client.post(
            "/api/v1/identity-admin/employees/onboard",
            headers=self.admin_headers,
            json={
                "user_id": self.gate_user_id,
                "position_keys": ["ops_center_operations"],
                "started_on": now.isoformat(),
                "ended_on": (now + timedelta(days=30)).isoformat(),
                "service_responsibilities": [
                    {
                        "org_unit_id": "identity-admin-center",
                        "scope_type": "SUBTREE",
                    }
                ],
                "source_reference": "existing-account-onboarding",
                "confirmation_note": "已确认使用现有账号建立自然人、岗位和单中心服务范围",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertFalse(result["account_created"])
        self.assertTrue(result["person_link_created"])
        self.assertEqual(
            fetch_one(
                "SELECT person_id FROM account_person_links WHERE user_id=?",
                (self.gate_user_id,),
            )["person_id"],
            result["person_id"],
        )

    def test_02g_onboarding_rejects_legacy_role_stacking(self) -> None:
        now = datetime.now(UTC)
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (
                    "identity-legacy-role-account",
                    "旧角色叠加测试账号",
                    hash_password("identity-legacy-role-password"),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            user_id = cursor.lastrowid
            execute(
                connection,
                "INSERT INTO user_roles(user_id, role_key, created_at) VALUES (?, 'read_only', ?)",
                (user_id, now.isoformat()),
            )
        response = self.client.post(
            "/api/v1/identity-admin/employees/onboard",
            headers=self.admin_headers,
            json={
                "user_id": user_id,
                "position_keys": ["ops_center_operations"],
                "started_on": now.isoformat(),
                "ended_on": (now + timedelta(days=30)).isoformat(),
                "service_responsibilities": [
                    {
                        "org_unit_id": "identity-admin-center",
                        "scope_type": "SUBTREE",
                    }
                ],
                "source_reference": "legacy-role-stacking-check",
                "confirmation_note": "验证现有旧角色不能与新任职权限直接叠加",
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("旧角色", response.text)
        self.assertIsNone(
            fetch_one(
                "SELECT person_id FROM account_person_links WHERE user_id=?", (user_id,)
            )
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
