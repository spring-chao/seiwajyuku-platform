from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db import execute, fetch_one, transaction
from app.main import app
from app.migrations import MIGRATION_ROOT
from app.services.iam import accessible_org_ids, create_user, user_context
from app.services.members import create_member
from app.services.staff_management import authorization_migration_preview
from app.services.iam import ROLE_PERMISSIONS


class IAM2StaffManagementTests(unittest.TestCase):
    """Acceptance tests for the IAM 2.0 staff authorization boundary."""

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
        cls.admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
        suffix = uuid4().hex[:10]
        cls.center_a = f"iam2-{suffix}-a"
        cls.class_a = f"iam2-{suffix}-class-a"
        cls.center_b = f"iam2-{suffix}-b"
        cls.class_b = f"iam2-{suffix}-class-b"
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for values in (
                (
                    cls.center_a,
                    f"IAM2_{suffix.upper()}_A",
                    "IAM2授权测试中心甲",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    cls.class_a,
                    f"IAM2_{suffix.upper()}_CLASS_A",
                    "IAM2授权测试班级甲",
                    "CLASS",
                    cls.center_a,
                ),
                (
                    cls.center_b,
                    f"IAM2_{suffix.upper()}_B",
                    "IAM2授权测试中心乙",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    cls.class_b,
                    f"IAM2_{suffix.upper()}_CLASS_B",
                    "IAM2授权测试班级乙",
                    "CLASS",
                    cls.center_b,
                ),
            ):
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                    "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    values + (now, now),
                )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    @staticmethod
    def _active_window() -> tuple[str, str]:
        now = datetime.now(UTC)
        return (
            (now - timedelta(days=1)).isoformat(),
            (now + timedelta(days=30)).isoformat(),
        )

    def _staff_payload(self, *, account: str, grants: list[dict]) -> dict:
        started_on, ended_on = self._active_window()
        return {
            "name": "IAM2专职人员测试",
            "login_account": account,
            "temporary_password": None,
            "is_active": True,
            "phone": f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}",
            "gender": "FEMALE",
            "institution_id": "institution-suzhou",
            "department_name": "运营支持",
            "supervisor_user_id": None,
            "position_keys": ["ops_center_learning"],
            "employment_status": "ACTIVE",
            "started_on": started_on,
            "ended_on": ended_on,
            "grants": grants,
            "authorization_basis": "IAM2自动化验收授权依据",
            "authorization_reason": "IAM2敏感权限自动化验收原因",
        }

    def _create_staff(self, grants: list[dict]) -> tuple[int, str, dict]:
        account = f"iam2-staff-{uuid4().hex[:12]}"
        payload = self._staff_payload(account=account, grants=grants)
        response = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertIsInstance(data["temporary_password"], str)
        self.assertGreaterEqual(len(data["temporary_password"]), 10)
        return int(data["id"]), str(data["temporary_password"]), payload

    def test_simple_create_derives_iam2_grant_without_authorization_writing(self) -> None:
        account = f"simple-staff-{uuid4().hex[:12]}"
        phone = f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}"
        response = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={
                "name": "一分钟新增专职人员",
                "gender": "MALE",
                "phone": phone,
                "login_account": account,
                "institution_id": "institution-suzhou",
                "position_keys": ["ops_center_learning"],
                "responsibility_org_unit_id": self.center_a,
                "responsibility_scope_type": "SUBTREE",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        user_id = int(response.json()["data"]["id"])
        context = user_context(user_id)
        self.assertIn("employee_learning_management", context["roles"])
        self.assertNotIn("ops_center_learning", context["roles"])
        self.assertEqual(
            accessible_org_ids(user_id, "attendance:adjudicate"),
            {self.center_a, self.class_a},
        )
        audit = fetch_one(
            "SELECT purpose, after_json FROM audit_logs WHERE action='iam2.staff.create' "
            "AND resource_id=(SELECT CAST(id AS TEXT) FROM operations_employments "
            "WHERE person_id=(SELECT person_id FROM account_person_links WHERE user_id=?)) "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        self.assertEqual(audit["purpose"], "SYSTEM_AUTO:POSITION_SCOPE_MAPPING")
        self.assertIn("employee_learning_management", audit["after_json"])

    def test_staff_catalog_uses_formal_institutions_scope_mapping_and_position_duties(self) -> None:
        response = self.client.get(
            "/api/v1/staff-management/catalog", headers=self.admin_headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        catalog = response.json()["data"]
        institutions = {item["business_code"]: item for item in catalog["institutions"]}
        self.assertEqual(set(institutions), {"JIANGNAN", "SUZHOU"})
        self.assertNotIn("SEIWA_HQ", {item["institution_code"] for item in catalog["institutions"]})
        self.assertNotIn(
            "SUZHOU_OPERATIONS_CENTER",
            {item["institution_code"] for item in catalog["institutions"]},
        )
        self.assertEqual(institutions["SUZHOU"]["name"], "苏州塾")
        self.assertEqual(institutions["SUZHOU"]["scope_root_org_unit_id"], "org-suzhou")
        self.assertFalse(institutions["JIANGNAN"]["scope_available"])
        self.assertEqual(
            {item["business_code"] for item in catalog["missing_institutions"]},
            {"CHANGZHOU", "WUXI"},
        )
        positions = {item["position_key"]: item for item in catalog["positions"]}
        self.assertEqual(positions["ops_center_director"]["role_key"], "employee_operations_lead")
        self.assertIn("全部运营业务", positions["ops_center_director"]["duty_description"])
        self.assertEqual(
            ROLE_PERMISSIONS["employee_operations_lead"],
            ROLE_PERMISSIONS["operations_admin"],
        )

    def test_business_staff_manager_sees_and_writes_only_its_scope(self) -> None:
        account = f"scope-manager-{uuid4().hex[:12]}"
        phone = f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}"
        created = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={
                "name": "范围负责人",
                "gender": "FEMALE",
                "phone": phone,
                "login_account": account,
                "temporary_password": "scope123",
                "institution_id": "institution-suzhou",
                "position_keys": ["ops_center_director"],
                "responsibility_org_unit_id": self.center_a,
                "responsibility_scope_type": "SUBTREE",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": account, "password": "scope123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        manager_headers = {
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        }
        catalog = self.client.get(
            "/api/v1/staff-management/catalog", headers=manager_headers
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertEqual(
            {row["id"] for row in catalog.json()["data"]["org_units"]},
            {self.center_a, self.class_a},
        )
        rejected = self.client.post(
            "/api/v1/staff-management/staff",
            headers=manager_headers,
            json={
                "name": "越权范围测试",
                "gender": "MALE",
                "phone": f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}",
                "login_account": f"out-of-scope-{uuid4().hex[:12]}",
                "institution_id": "institution-suzhou",
                "position_keys": ["ops_center_operations"],
                "responsibility_org_unit_id": self.center_b,
                "responsibility_scope_type": "UNIT",
            },
        )
        self.assertEqual(rejected.status_code, 403, rejected.text)

        catalog = self.client.get(
            "/api/v1/staff-management/catalog", headers=self.admin_headers
        )
        self.assertEqual(catalog.status_code, 200, catalog.text)
        positions = {
            item["position_key"]: item
            for item in catalog.json()["data"]["positions"]
        }
        self.assertEqual(positions["ops_center_learning"]["mapping_status"], "AUTO")
        self.assertEqual(
            positions["operations_admin"]["mapping_status"],
            "MAPPING_REVIEW_REQUIRED",
        )

        unmapped = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={
                "name": "待确认岗位人员",
                "gender": "MALE",
                "phone": f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}",
                "login_account": f"mapping-review-{uuid4().hex[:12]}",
                "institution_id": "institution-suzhou",
                "position_keys": ["operations_admin"],
                "responsibility_org_unit_id": self.center_a,
                "responsibility_scope_type": "SUBTREE",
            },
        )
        self.assertEqual(unmapped.status_code, 400, unmapped.text)
        self.assertIn("岗位权限映射待业务确认", unmapped.json()["detail"])

    def test_staff_password_minimum_six_and_required_profile_fields(self) -> None:
        base = {
            "name": "密码门槛测试",
            "gender": "FEMALE",
            "phone": f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}",
            "login_account": f"six-pass-{uuid4().hex[:12]}",
            "institution_id": "institution-suzhou",
            "position_keys": ["ops_center_data"],
            "responsibility_org_unit_id": self.center_a,
            "responsibility_scope_type": "UNIT",
        }
        accepted = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={**base, "temporary_password": "abc123"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        rejected = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={
                **base,
                "login_account": f"five-pass-{uuid4().hex[:12]}",
                "phone": f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}",
                "temporary_password": "abc12",
            },
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)
        missing_phone = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={key: value for key, value in base.items() if key != "phone"},
        )
        self.assertEqual(missing_phone.status_code, 422, missing_phone.text)
        unspecified_gender = self.client.post(
            "/api/v1/staff-management/staff",
            headers=self.admin_headers,
            json={
                **base,
                "login_account": f"gender-pass-{uuid4().hex[:12]}",
                "phone": f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}",
                "gender": "UNSPECIFIED",
            },
        )
        self.assertEqual(unspecified_gender.status_code, 422, unspecified_gender.text)

    def test_staff_create_rejects_missing_role_catalog_before_any_write(self) -> None:
        role_key = "employee_learning_management"
        prior = fetch_one("SELECT is_active FROM roles WHERE role_key=?", (role_key,))
        self.assertIsNotNone(prior)
        account = f"missing-role-{uuid4().hex[:12]}"
        phone = f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}"
        try:
            with transaction() as connection:
                execute(
                    connection,
                    "UPDATE roles SET is_active=0 WHERE role_key=?",
                    (role_key,),
                )
            response = self.client.post(
                "/api/v1/staff-management/staff",
                headers=self.admin_headers,
                json={
                    "name": "角色目录缺失测试",
                    "gender": "MALE",
                    "phone": phone,
                    "login_account": account,
                    "institution_id": "institution-suzhou",
                    "position_keys": ["ops_center_learning"],
                    "responsibility_org_unit_id": self.center_a,
                    "responsibility_scope_type": "UNIT",
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("岗位权限尚未配置", response.json()["detail"])
            self.assertIsNone(
                fetch_one("SELECT id FROM app_users WHERE username=?", (account,))
            )
            self.assertEqual(
                fetch_one(
                    "SELECT COUNT(*) AS n FROM operations_employments "
                    "WHERE person_id IN (SELECT person_id FROM account_person_links "
                    "WHERE user_id IN (SELECT id FROM app_users WHERE username=?))",
                    (account,),
                )["n"],
                0,
            )
        finally:
            with transaction() as connection:
                execute(
                    connection,
                    "UPDATE roles SET is_active=? WHERE role_key=?",
                    (prior["is_active"], role_key),
                )

    def test_staff_create_integrity_failure_returns_400_and_rolls_back(self) -> None:
        account = f"integrity-rollback-{uuid4().hex[:12]}"
        phone = f"13{int(uuid4().hex[:8], 16) % 1_000_000_000:09d}"
        with patch(
            "app.services.staff_management._insert_grants",
            side_effect=sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
        ):
            response = self.client.post(
                "/api/v1/staff-management/staff",
                headers=self.admin_headers,
                json={
                    "name": "完整性回滚测试",
                    "gender": "FEMALE",
                    "phone": phone,
                    "login_account": account,
                    "institution_id": "institution-suzhou",
                    "position_keys": ["ops_center_learning"],
                    "responsibility_org_unit_id": self.center_a,
                    "responsibility_scope_type": "UNIT",
                },
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("岗位、机构或人员关联数据无效", response.json()["detail"])
        self.assertIsNone(
            fetch_one("SELECT id FROM app_users WHERE username=?", (account,))
        )
        self.assertEqual(
            fetch_one(
                "SELECT COUNT(*) AS n FROM person_profiles "
                "WHERE display_name='完整性回滚测试'"
            )["n"],
            0,
        )

    def test_password_reset_accepts_six_without_business_reason(self) -> None:
        user_id, _, _ = self._create_staff(
            [
                {
                    "role_key": "employee_data_management",
                    "org_unit_id": self.center_a,
                    "scope_type": "UNIT",
                }
            ]
        )
        reset = self.client.post(
            f"/api/v1/iam/users/{user_id}/password",
            headers=self.admin_headers,
            json={"password": "new123"},
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        too_short = self.client.post(
            f"/api/v1/iam/users/{user_id}/password",
            headers=self.admin_headers,
            json={"password": "new12"},
        )
        self.assertEqual(too_short.status_code, 422, too_short.text)

    def test_explicit_grants_bind_permission_to_its_own_scope_and_subtree(self) -> None:
        started_on, ended_on = self._active_window()
        user_id, temporary_password, _ = self._create_staff(
            [
                {
                    "role_key": "employee_member_management",
                    "org_unit_id": self.center_a,
                    "scope_type": "SUBTREE",
                    "valid_from": started_on,
                    "valid_until": ended_on,
                },
                {
                    "role_key": "employee_learning_management",
                    "org_unit_id": self.center_b,
                    "scope_type": "UNIT",
                    "valid_from": started_on,
                    "valid_until": ended_on,
                },
            ]
        )
        context = user_context(user_id)
        self.assertIn("employee_member_management", context["roles"])
        self.assertIn("employee_learning_management", context["roles"])
        # A persisted explicit grant suppresses the legacy job-template role.
        self.assertNotIn("ops_center_learning", context["roles"])
        self.assertEqual(
            accessible_org_ids(user_id, "members:manage"),
            {self.center_a, self.class_a},
        )
        self.assertEqual(
            accessible_org_ids(user_id, "attendance:adjudicate"),
            {self.center_b},
        )

        member_a = create_member(
            self.admin_id,
            member_code=f"IAM2-MEMBER-A-{uuid4().hex[:8]}",
            name="IAM2范围内学员",
            org_unit_id=self.center_a,
            development_org_unit_id=None,
            phone=None,
        )
        member_b = create_member(
            self.admin_id,
            member_code=f"IAM2-MEMBER-B-{uuid4().hex[:8]}",
            name="IAM2范围外学员",
            org_unit_id=self.center_b,
            development_org_unit_id=None,
            phone=None,
        )
        account = fetch_one("SELECT username FROM app_users WHERE id=?", (user_id,))["username"]
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": account, "password": temporary_password},
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        # This request exercises the actual FastAPI permission context rather
        # than only the service helper: a member-management grant for A must
        # not borrow the learning grant's B scope.
        denied = self.client.patch(
            f"/api/v1/members/{member_b}", headers=headers, json={"status": "SUSPENDED"}
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        allowed = self.client.patch(
            f"/api/v1/members/{member_a}", headers=headers, json={"status": "SUSPENDED"}
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_staff_authorization_ignores_legacy_date_windows(self) -> None:
        now = datetime.now(UTC)
        user_id, _, _ = self._create_staff(
            [
                {
                    "role_key": "employee_learning_management",
                    "org_unit_id": self.center_a,
                    "scope_type": "UNIT",
                    "valid_from": (now - timedelta(days=3)).isoformat(),
                    "valid_until": (now - timedelta(days=2)).isoformat(),
                }
            ]
        )
        context = user_context(user_id)
        self.assertIn("employee_learning_management", context["roles"])
        self.assertNotIn("ops_center_learning", context["roles"])
        self.assertEqual(
            accessible_org_ids(user_id, "attendance:adjudicate"),
            {self.center_a},
        )

    def test_staff_create_preview_disable_and_audit_do_not_expose_password(self) -> None:
        started_on, ended_on = self._active_window()
        user_id, temporary_password, create_payload = self._create_staff(
            [
                {
                    "role_key": "employee_member_management",
                    "org_unit_id": self.center_a,
                    "scope_type": "SUBTREE",
                    "valid_from": started_on,
                    "valid_until": ended_on,
                }
            ]
        )
        listed = self.client.get(
            "/api/v1/staff-management/staff", headers=self.admin_headers
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        row = next(item for item in listed.json()["data"] if item["id"] == user_id)
        # Named login accounts remain readable for operations; only phone-like
        # identifiers are masked by the shared privacy helper.
        self.assertEqual(row["login_account"], create_payload["login_account"])
        self.assertRegex(row["phone_masked"], r"^\d{3}\*{4}\d{4}$")
        self.assertNotEqual(row["phone_masked"], create_payload["phone"])
        self.assertNotIn("temporary_password", row)
        audit = fetch_one(
            "SELECT after_json FROM audit_logs WHERE action='iam2.staff.create' "
            "AND resource_id=(SELECT CAST(id AS TEXT) FROM operations_employments "
            "WHERE person_id=(SELECT person_id FROM account_person_links WHERE user_id=?)) "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        self.assertIsNotNone(audit)
        self.assertNotIn(temporary_password, audit["after_json"])

        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": create_payload["login_account"], "password": temporary_password},
        )
        self.assertEqual(login.status_code, 200, login.text)
        old_access = login.json()["data"]["access_token"]
        update_payload = {
            **create_payload,
            "temporary_password": None,
            "is_active": False,
            "authorization_basis": "IAM2离岗停用依据",
            "authorization_reason": "",
        }
        preview = self.client.post(
            f"/api/v1/staff-management/staff/{user_id}/change-preview",
            headers=self.admin_headers,
            json=update_payload,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["data"]["before"]["is_active"])
        self.assertFalse(preview.json()["data"]["after"]["is_active"])
        updated = self.client.put(
            f"/api/v1/staff-management/staff/{user_id}",
            headers=self.admin_headers,
            json=update_payload,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertTrue(updated.json()["data"]["sessions_revoked"])
        self.assertEqual(
            self.client.get(
                "/api/v1/me", headers={"Authorization": f"Bearer {old_access}"}
            ).status_code,
            401,
        )
        self.assertIsNotNone(
            fetch_one(
                "SELECT revoked_at FROM refresh_tokens WHERE user_id=? AND revoked_at IS NOT NULL",
                (user_id,),
            )
        )
        self.assertIsNotNone(
            fetch_one(
                "SELECT id FROM audit_logs WHERE action='iam2.staff.update' "
                "AND resource_type='operations_employment' ORDER BY id DESC LIMIT 1"
            )
        )

    def test_staff_leave_immediately_closes_employee_authorization(self) -> None:
        started_on, ended_on = self._active_window()
        user_id, _, payload = self._create_staff(
            [
                {
                    "role_key": "employee_member_management",
                    "org_unit_id": self.center_a,
                    "scope_type": "UNIT",
                    "valid_from": started_on,
                    "valid_until": ended_on,
                }
            ]
        )
        update_payload = {
            **payload,
            "temporary_password": None,
            "employment_status": "LEAVE",
            "authorization_reason": "",
        }
        preview = self.client.post(
            f"/api/v1/staff-management/staff/{user_id}/change-preview",
            headers=self.admin_headers,
            json=update_payload,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertFalse(preview.json()["data"]["requires_business_reason"])
        updated = self.client.put(
            f"/api/v1/staff-management/staff/{user_id}",
            headers=self.admin_headers,
            json=update_payload,
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        context = user_context(user_id)
        self.assertNotIn("employee_member_management", context["roles"])
        self.assertEqual(accessible_org_ids(user_id, "members:manage"), set())

        revoked_payload = {
            **update_payload,
            "employment_status": "ACTIVE",
            "grants": [],
            "authorization_basis": "IAM2撤销授权依据",
        }
        revoked = self.client.put(
            f"/api/v1/staff-management/staff/{user_id}",
            headers=self.admin_headers,
            json=revoked_payload,
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        context = user_context(user_id)
        self.assertNotIn("employee_member_management", context["roles"])
        self.assertNotIn("ops_center_learning", context["roles"])

    def test_only_system_admin_can_assign_system_or_restricted_roles(self) -> None:
        suffix = uuid4().hex[:12]
        technical_password = f"t-{uuid4().hex}"
        technical_id = create_user(
            self.admin_id,
            username=f"iam2-technical-{suffix}",
            display_name="IAM2系统角色门禁测试",
            password=technical_password,
            roles=["technical_admin"],
            scopes=[],
            actor_roles=["system_admin"],
        )
        login = self.client.post(
            "/api/v1/auth/login",
            json={
                "username": f"iam2-technical-{suffix}",
                "password": technical_password,
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        protected_username = f"iam2-protected-{uuid4().hex[:12]}"
        rejected = self.client.post(
            "/api/v1/iam/users",
            headers=headers,
            json={
                "username": protected_username,
                "display_name": "IAM2越权系统角色测试",
                "password": f"p-{uuid4().hex}",
                "roles": ["system_admin"],
                "scopes": [],
            },
        )
        self.assertEqual(rejected.status_code, 403, rejected.text)
        self.assertIn("平台系统管理员", rejected.text)
        self.assertIsNone(fetch_one("SELECT id FROM app_users WHERE username=?", (protected_username,)))
        self.assertIsNotNone(fetch_one("SELECT id FROM app_users WHERE id=?", (technical_id,)))

    def test_legacy_migration_preview_is_no_write_and_deduplicates_pairs(self) -> None:
        suffix = uuid4().hex[:10]
        now = datetime.now(UTC).isoformat()
        password_hash = hash_password(uuid4().hex)
        with transaction() as connection:
            user = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (
                    f"iam2-legacy-{suffix}",
                    "IAM2兼容迁移预览人员",
                    password_hash,
                    now,
                    now,
                ),
            )
            user_id = int(user.lastrowid)
            person_id = f"iam2-legacy-person-{suffix}"
            execute(
                connection,
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (person_id, "IAM2兼容迁移预览人员", now, now),
            )
            execute(
                connection,
                "INSERT INTO account_person_links(user_id, person_id, linked_at, source_reference) "
                "VALUES (?, ?, ?, 'IAM2_TEST')",
                (user_id, person_id, now),
            )
            employment = execute(
                connection,
                "INSERT INTO operations_employments(person_id, institution_id, employment_status, "
                "started_on, source_reference, created_at, updated_at) "
                "VALUES (?, 'institution-suzhou-operations', 'ACTIVE', ?, 'IAM2_TEST', ?, ?)",
                (person_id, now, now, now),
            )
            employment_id = int(employment.lastrowid)
            for _ in range(2):
                execute(
                    connection,
                    "INSERT INTO operations_position_assignments(employment_id, position_key, "
                    "valid_from, status, source_reference, created_at, updated_at) "
                    "VALUES (?, 'ops_center_learning', ?, 'ACTIVE', 'IAM2_TEST', ?, ?)",
                    (employment_id, now, now, now),
                )
            execute(
                connection,
                "INSERT INTO employee_service_responsibilities(employment_id, org_unit_id, scope_type, "
                "valid_from, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'SUBTREE', ?, 'ACTIVE', 'IAM2_TEST', ?, ?)",
                (employment_id, self.center_a, now, now, now),
            )
        before_count = fetch_one(
            "SELECT COUNT(*) AS n FROM employee_authorization_grants WHERE employment_id=?",
            (employment_id,),
        )["n"]
        preview = authorization_migration_preview(self.admin_id)
        item = next(row for row in preview if row["employment_id"] == employment_id)
        self.assertEqual(item["status"], "SAFE_TO_MIGRATE")
        self.assertEqual(item["write_action"], "PREVIEW_ONLY")
        self.assertEqual(len(item["proposed_authorization_grants"]), 1)
        self.assertEqual(item["deduplicated_grant_count"], 1)
        after_count = fetch_one(
            "SELECT COUNT(*) AS n FROM employee_authorization_grants WHERE employment_id=?",
            (employment_id,),
        )["n"]
        self.assertEqual(before_count, after_count)


class IAM2MigrationParityTests(unittest.TestCase):
    @staticmethod
    def _fresh_connection() -> tuple[tempfile.TemporaryDirectory, sqlite3.Connection]:
        temporary = tempfile.TemporaryDirectory()
        database = Path(temporary.name) / "iam2-migration.db"
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT)"
        )
        for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
            connection.executescript(path.read_text(encoding="utf-8"))
        return temporary, connection

    def test_sqlite_forward_and_empty_rollback_are_loss_averse(self) -> None:
        temporary, connection = self._fresh_connection()
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(operations_employments)").fetchall()
            }
            self.assertTrue({"department_name", "supervisor_user_id"}.issubset(columns))
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='employee_authorization_grants'"
                ).fetchone()
            )
            connection.executescript(
                (
                    MIGRATION_ROOT
                    / "rollback"
                    / "sqlite"
                    / "0051_iam2_staff_authorization.down.sql"
                ).read_text(encoding="utf-8")
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='employee_authorization_grants'"
                ).fetchone()
            )
        finally:
            connection.close()
            temporary.cleanup()

    def test_sqlite_rollback_refuses_used_iam2_data(self) -> None:
        temporary, connection = self._fresh_connection()
        try:
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES ('iam2-rollback-person', 'IAM2回滚保护测试', 'ACTIVE', ?, ?)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO employee_profile_details(person_id, created_at, updated_at) "
                "VALUES ('iam2-rollback-person', ?, ?)",
                (now, now),
            )
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.executescript(
                    (
                        MIGRATION_ROOT
                        / "rollback"
                        / "sqlite"
                        / "0051_iam2_staff_authorization.down.sql"
                    ).read_text(encoding="utf-8")
                )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='employee_authorization_grants'"
                ).fetchone()
            )
        finally:
            connection.close()
            temporary.cleanup()

    def test_mysql_migration_keeps_the_same_iam2_contract(self) -> None:
        sqlite_sql = (
            MIGRATION_ROOT / "sqlite" / "0051_iam2_staff_authorization.sql"
        ).read_text(encoding="utf-8")
        mysql_sql = (
            MIGRATION_ROOT / "mysql" / "0051_iam2_staff_authorization.sql"
        ).read_text(encoding="utf-8")
        mysql_rollback = (
            MIGRATION_ROOT / "rollback" / "mysql" / "0051_iam2_staff_authorization.down.sql"
        ).read_text(encoding="utf-8")
        for identifier in (
            "employee_profile_details",
            "employee_authorization_grants",
            "department_name",
            "supervisor_user_id",
            "scope_type",
            "valid_from",
            "valid_until",
        ):
            self.assertIn(identifier, sqlite_sql)
            self.assertIn(identifier, mysql_sql)
        for comment in mysql_sql.splitlines():
            if comment.lstrip().startswith("--"):
                self.assertNotIn(";", comment)
        self.assertIn("employee_authorization_grants", mysql_rollback)
        self.assertIn("operations_employments", mysql_rollback)

    def test_0054_seeds_assignable_employee_roles_and_permissions(self) -> None:
        expected_roles = {
            "employee_operations_lead",
            "employee_operations_management",
            "employee_member_management",
            "employee_learning_management",
            "employee_development_management",
            "employee_renewal_management",
            "employee_finance_management",
            "employee_data_management",
            "employee_administration_management",
            "read_only",
        }
        temporary, connection = self._fresh_connection()
        try:
            role_rows = connection.execute(
                "SELECT role_key, is_active FROM roles WHERE role_key IN (%s)"
                % ",".join("?" for _ in expected_roles),
                tuple(sorted(expected_roles)),
            ).fetchall()
            self.assertEqual({row[0] for row in role_rows}, expected_roles)
            self.assertTrue(all(row[1] == 1 for row in role_rows))
            permission_count = connection.execute(
                "SELECT COUNT(*) FROM role_permissions WHERE role_key IN (%s)"
                % ",".join("?" for _ in expected_roles),
                tuple(sorted(expected_roles)),
            ).fetchone()[0]
            self.assertGreaterEqual(permission_count, 60)
        finally:
            connection.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
