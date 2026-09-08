from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.security import hash_password
from app.db import execute, fetch_one, transaction
from app.migrations import MIGRATION_ROOT, run_migrations
from app.services.iam import (
    ROLE_PERMISSIONS,
    accessible_org_ids,
    seed_iam,
    user_context,
)
from app.services.volunteer_positions import get_member_volunteer_history
from app.services.wechat_identity import get_member_role_scopes


class IdentityAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.now = datetime.now(UTC)
        stamp = cls.now.isoformat()
        with transaction() as connection:
            for values in (
                ("identity-center-a", "IDENTITY_CENTER_A", "身份测试中心A", "REGIONAL_CENTER", "org-suzhou"),
                ("identity-class-a", "IDENTITY_CLASS_A", "身份测试班级A", "CLASS", "identity-center-a"),
                ("identity-center-b", "IDENTITY_CENTER_B", "身份测试中心B", "REGIONAL_CENTER", "org-suzhou"),
            ):
                if not execute(
                    connection, "SELECT id FROM org_units WHERE id=?", (values[0],)
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                        "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        values + (stamp, stamp),
                    )

    def _account_and_person(self, suffix: str) -> tuple[int, str]:
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO app_users(username, display_name, password_hash, is_active, "
                "created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                (
                    f"identity-{suffix}",
                    f"身份测试{suffix}",
                    hash_password("identity-test-password"),
                    now,
                    now,
                ),
            )
            user_id = cursor.lastrowid
            person_id = f"person-{suffix}"
            execute(
                connection,
                "INSERT INTO person_profiles(id, display_name, status, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?)",
                (person_id, f"身份测试{suffix}", now, now),
            )
            execute(
                connection,
                "INSERT INTO account_person_links(user_id, person_id, linked_at, source_reference) "
                "VALUES (?, ?, ?, 'automated-test')",
                (user_id, person_id, now),
            )
        return user_id, person_id

    def _member_for_person(self, person_id: str, suffix: str) -> int:
        """Create the formal roster record required by volunteer authority."""

        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
                "VALUES (?, ?, 'identity-center-a', 'ACTIVE', ?, ?)",
                (f"IAM-AUTH-MEMBER-{suffix}", f"志工测试{suffix}", now, now),
            )
            member_id = int(cursor.lastrowid)
            execute(
                connection,
                "INSERT INTO member_identities(member_id, person_id, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', 'automated-test', ?, ?)",
                (member_id, person_id, now, now),
            )
        return member_id

    def test_employee_position_authorizes_without_regional_membership(self) -> None:
        user_id, person_id = self._account_and_person("employee")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            cursor = execute(
                connection,
                "INSERT INTO operations_employments(person_id, institution_id, "
                "employment_status, started_on, source_reference, created_at, updated_at) "
                "VALUES (?, 'institution-suzhou-operations', 'ACTIVE', ?, "
                "'confirmed-test-employment', ?, ?)",
                (person_id, now, now, now),
            )
            employment_id = cursor.lastrowid
            execute(
                connection,
                "INSERT INTO operations_position_assignments(employment_id, position_key, "
                "valid_from, status, source_reference, created_at, updated_at) "
                "VALUES (?, 'ops_center_learning', ?, 'ACTIVE', "
                "'confirmed-test-position', ?, ?)",
                (employment_id, now, now, now),
            )
        context = user_context(user_id)
        self.assertIn("OPERATIONS_EMPLOYEE", context["subject_contexts"])
        self.assertIn("ops_center_learning", context["roles"])
        self.assertIn("attendance:adjudicate", context["permissions"])
        self.assertNotIn("members:enterprise_view", context["permissions"])
        self.assertEqual(context["scopes"], [])

    def test_leave_ends_employee_access_without_ending_active_volunteer_service(self) -> None:
        user_id, person_id = self._account_and_person("employee-volunteer")
        member_id = self._member_for_person(person_id, "employee-volunteer")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            employment = execute(
                connection,
                "INSERT INTO operations_employments(person_id, institution_id, "
                "employment_status, source_reference, created_at, updated_at) "
                "VALUES (?, 'institution-suzhou-operations', 'ACTIVE', "
                "'employee-volunteer-test', ?, ?)",
                (person_id, now, now),
            )
            execute(
                connection,
                "INSERT INTO operations_position_assignments(employment_id, position_key, "
                "status, source_reference, created_at, updated_at) "
                "VALUES (?, 'ops_center_learning', 'ACTIVE', "
                "'employee-volunteer-test', ?, ?)",
                (employment.lastrowid, now, now),
            )
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'volunteer_activity', 'identity-center-a', 'UNIT', "
                "?, NULL, 'ACTIVE', 'employee-volunteer-test', ?, ?)",
                (person_id, member_id, now, now, now),
            )
        self.assertIn("ops_center_learning", user_context(user_id)["roles"])
        self.assertIn("volunteer_activity", user_context(user_id)["roles"])

        with transaction() as connection:
            execute(
                connection,
                "UPDATE operations_employments SET employment_status='LEAVE' WHERE id=?",
                (employment.lastrowid,),
            )
        context = user_context(user_id)
        self.assertNotIn("ops_center_learning", context["roles"])
        self.assertIn("volunteer_activity", context["roles"])
        self.assertIn("MEMBER", context["subject_contexts"])
        self.assertIn("VOLUNTEER", context["subject_contexts"])

    def test_identity_authorization_requires_explicit_feature_flag(self) -> None:
        user_id, person_id = self._account_and_person("feature-flag")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            employment = execute(
                connection,
                "INSERT INTO operations_employments(person_id, institution_id, "
                "employment_status, started_on, source_reference, created_at, updated_at) "
                "VALUES (?, 'institution-suzhou-operations', 'ACTIVE', ?, "
                "'feature-flag-test', ?, ?)",
                (person_id, now, now, now),
            )
            execute(
                connection,
                "INSERT INTO operations_position_assignments(employment_id, position_key, "
                "valid_from, status, source_reference, created_at, updated_at) "
                "VALUES (?, 'ops_center_operations', ?, 'ACTIVE', "
                "'feature-flag-test', ?, ?)",
                (employment.lastrowid, now, now, now),
            )
        with patch.dict(
            "os.environ", {"IDENTITY_AUTHORIZATION_ENABLED": "false"}
        ):
            context = user_context(user_id)
        self.assertNotIn("ops_center_operations", context["roles"])
        self.assertEqual(context["subject_contexts"], [])

    def test_volunteer_appointment_uses_roster_and_active_post_not_dates(self) -> None:
        user_id, person_id = self._account_and_person("volunteer")
        member_id = self._member_for_person(person_id, "volunteer")
        now = datetime.now(UTC)
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'volunteer_regional_lead', 'identity-center-a', 'SUBTREE', "
                "?, ?, 'ACTIVE', 'confirmed-test-appointment', ?, ?)",
                (
                    person_id,
                    member_id,
                    (now + timedelta(days=365)).isoformat(),
                    (now - timedelta(days=365)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'volunteer_activity', 'identity-center-b', 'UNIT', "
                "?, ?, 'ACTIVE', 'audit-timestamp-test', ?, ?)",
                (
                    person_id,
                    member_id,
                    (now + timedelta(days=30)).isoformat(),
                    (now - timedelta(days=1)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        context = user_context(user_id)
        self.assertEqual(context["language_context"], "VOLUNTEER")
        self.assertIn("volunteer_regional_lead", context["roles"])
        self.assertIn("volunteer_activity", context["roles"])
        self.assertEqual(
            accessible_org_ids(user_id),
            {"identity-center-a", "identity-class-a", "identity-center-b"},
        )

    def test_planned_appointment_does_not_activate_from_a_time_window(self) -> None:
        user_id, person_id = self._account_and_person("planned-window")
        member_id = self._member_for_person(person_id, "planned-window")
        now = datetime.now(UTC)
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'volunteer_activity', 'identity-center-a', 'UNIT', "
                "?, ?, 'PLANNED', 'planned-window-test', ?, ?)",
                (
                    person_id,
                    member_id,
                    (now - timedelta(minutes=1)).isoformat(),
                    (now + timedelta(days=1)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        context = user_context(user_id)
        self.assertNotIn("volunteer_activity", context["roles"])
        self.assertEqual(accessible_org_ids(user_id), set())

    def test_member_status_immediately_controls_volunteer_authority_and_keeps_history(self) -> None:
        user_id, person_id = self._account_and_person("member-status")
        member_id = self._member_for_person(person_id, "member-status")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'volunteer_group_leader', 'identity-center-a', 'UNIT', "
                "?, NULL, 'ACTIVE', 'member-status-test', ?, ?)",
                (person_id, member_id, now, now, now),
            )
        self.assertIn("volunteer_group_leader", user_context(user_id)["roles"])
        self.assertTrue(get_member_role_scopes(member_id))

        with transaction() as connection:
            execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (member_id,))
        self.assertNotIn("volunteer_group_leader", user_context(user_id)["roles"])
        self.assertEqual(get_member_role_scopes(member_id), [])
        self.assertEqual(len(get_member_volunteer_history(member_id)["appointments"]), 1)

        with transaction() as connection:
            execute(connection, "UPDATE members SET status='ACTIVE' WHERE id=?", (member_id,))
        self.assertIn("volunteer_group_leader", user_context(user_id)["roles"])

        with transaction() as connection:
            execute(
                connection,
                "UPDATE volunteer_appointments SET status='ENDED', ends_at=?, updated_at=? "
                "WHERE member_id=?",
                (now, now, member_id),
            )
            execute(connection, "UPDATE members SET status='INACTIVE' WHERE id=?", (member_id,))
            execute(connection, "UPDATE members SET status='ACTIVE' WHERE id=?", (member_id,))
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, NULL, 'volunteer_activity', 'identity-center-a', 'UNIT', "
                "?, NULL, 'ACTIVE', 'unlinked-legacy-row', ?, ?)",
                (person_id, now, now, now),
            )
        self.assertNotIn("volunteer_group_leader", user_context(user_id)["roles"])
        self.assertNotIn("volunteer_activity", user_context(user_id)["roles"])
        self.assertEqual(get_member_role_scopes(member_id), [])
        self.assertEqual(len(get_member_volunteer_history(member_id)["appointments"]), 1)

    def test_disabled_account_cannot_use_an_active_member_volunteer_post(self) -> None:
        user_id, person_id = self._account_and_person("disabled-account")
        member_id = self._member_for_person(person_id, "disabled-account")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO volunteer_appointments(person_id, member_id, appointment_key, org_unit_id, "
                "scope_type, starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, ?, 'volunteer_activity', 'identity-center-a', 'UNIT', "
                "?, NULL, 'ACTIVE', 'disabled-account-test', ?, ?)",
                (person_id, member_id, now, now, now),
            )
            execute(connection, "UPDATE app_users SET is_active=0 WHERE id=?", (user_id,))
        self.assertIsNone(user_context(user_id))

    def test_technical_assignment_has_no_business_sensitive_access(self) -> None:
        user_id, person_id = self._account_and_person("technical")
        now = datetime.now(UTC)
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO technical_admin_assignments(person_id, assignment_purpose, "
                "starts_at, ends_at, status, source_reference, created_at, updated_at) "
                "VALUES (?, '测试环境系统维护', ?, ?, 'ACTIVE', "
                "'approved-test-assignment', ?, ?)",
                (
                    person_id,
                    (now - timedelta(minutes=1)).isoformat(),
                    (now + timedelta(days=1)).isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        context = user_context(user_id)
        self.assertEqual(context["roles"], ["technical_admin"])
        self.assertIn("iam:manage", context["permissions"])
        self.assertNotIn("members:read", context["permissions"])
        self.assertNotIn("members:enterprise_view", context["permissions"])
        self.assertNotIn("exports:sensitive", context["permissions"])
        self.assertEqual(accessible_org_ids(user_id), set())

    def test_position_templates_are_distinct_and_least_privilege(self) -> None:
        position_keys = {
            "ops_center_director",
            "ops_center_operations",
            "ops_center_learning",
            "ops_center_development",
            "ops_center_management",
            "ops_center_data",
            "ops_center_finance",
            "ops_center_administration",
        }
        self.assertTrue(position_keys.issubset(ROLE_PERMISSIONS))
        for key in position_keys:
            self.assertNotIn("exports:sensitive", ROLE_PERMISSIONS[key])
        self.assertNotIn(
            "members:enterprise_view", ROLE_PERMISSIONS["ops_center_finance"]
        )
        self.assertNotIn(
            "plans:import_global", ROLE_PERMISSIONS["ops_center_administration"]
        )
        self.assertNotEqual(
            ROLE_PERMISSIONS["ops_center_learning"],
            ROLE_PERMISSIONS["ops_center_finance"],
        )

    def test_forward_and_rollback_keep_legacy_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "migration.db"
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                for path in sorted((MIGRATION_ROOT / "sqlite").glob("*.sql")):
                    connection.executescript(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='volunteer_appointments'"
                    ).fetchone()
                )
                connection.executescript(
                    (
                        MIGRATION_ROOT
                        / "rollback"
                        / "sqlite"
                        / "0011_identity_appointments.down.sql"
                    ).read_text(encoding="utf-8")
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='volunteer_appointments'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='members'"
                    ).fetchone()
                )
            finally:
                connection.close()


class VolunteerLanguageTests(unittest.TestCase):
    def test_volunteer_dictionary_avoids_forbidden_management_language(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "admin-web"
            / "src"
            / "utils"
            / "productLanguage.ts"
        ).read_text(encoding="utf-8")
        volunteer_block = source.split("VOLUNTEER: {", 1)[1].split("\n  }\n}", 1)[0]
        for forbidden in (
            "强制指派",
            "绩效考核",
            "催缴",
            "处罚",
            "末位排名",
            "未达标人员",
            "驳回失败",
        ):
            self.assertNotIn(forbidden, volunteer_block)
        self.assertIn('item: "服务事项"', volunteer_block)
        self.assertIn('assignee: "担当人"', volunteer_block)


if __name__ == "__main__":
    unittest.main()
