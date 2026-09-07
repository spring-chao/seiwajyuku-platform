from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.db import execute, fetch_one, transaction
from app.main import app
from app.migrations import MIGRATION_ROOT
from app.services.iam import accessible_org_ids, create_user, user_context
from app.services.members import create_member
from app.services.staff_management import authorization_migration_preview


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
            "phone": None,
            "gender": "UNSPECIFIED",
            "institution_id": "institution-suzhou-operations",
            "department_name": "运营支持",
            "supervisor_user_id": None,
            "position_keys": ["ops_center_learning"],
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

    def test_expired_explicit_grant_does_not_fall_back_to_position_template(self) -> None:
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
        self.assertNotIn("employee_learning_management", context["roles"])
        self.assertNotIn("ops_center_learning", context["roles"])
        self.assertEqual(accessible_org_ids(user_id, "attendance:adjudicate"), set())

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
        self.assertIsNone(row["phone_masked"])
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

    def test_sensitive_grant_validity_extension_requires_business_reason(self) -> None:
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
        extended_until = (
            datetime.fromisoformat(ended_on) + timedelta(days=14)
        ).isoformat()
        update_payload = {
            **payload,
            "temporary_password": None,
            "ended_on": extended_until,
            "grants": [
                {
                    **payload["grants"][0],
                    "valid_until": extended_until,
                }
            ],
            "authorization_reason": "",
        }
        preview = self.client.post(
            f"/api/v1/staff-management/staff/{user_id}/change-preview",
            headers=self.admin_headers,
            json=update_payload,
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["data"]["requires_business_reason"])
        self.assertEqual(len(preview.json()["data"]["diff"]["changed_grants"]), 1)
        rejected = self.client.put(
            f"/api/v1/staff-management/staff/{user_id}",
            headers=self.admin_headers,
            json=update_payload,
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertIn("业务原因", rejected.text)

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


if __name__ == "__main__":
    unittest.main()
