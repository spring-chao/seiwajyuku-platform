from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.api.attendance import list_event_groups
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.iam import create_user, user_context
from app.services.followups import create_task, list_tasks
from app.services.members import create_member, list_members, merge_members, update_member


class IamIsolationTests(unittest.TestCase):
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
        cls.admin_token = login.json()["data"]["access_token"]
        cls.admin_headers = {"Authorization": f"Bearer {cls.admin_token}"}
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for values in (
                ("org-a", "CENTER_A", "园区分中心", "REGIONAL_CENTER", "org-suzhou"),
                ("class-a", "CLASS_A", "圆融一班", "CLASS", "org-a"),
                ("org-b", "CENTER_B", "吴江分中心", "REGIONAL_CENTER", "org-suzhou"),
                ("class-b", "CLASS_B", "吴江一班", "CLASS", "org-b"),
            ):
                existing = execute(connection, "SELECT id FROM org_units WHERE id=?", (values[0],)).fetchone()
                if not existing:
                    execute(
                        connection,
                        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        values + (now, now),
                    )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    def test_admin_does_not_inherit_sensitive_export(self) -> None:
        response = self.client.get("/api/v1/me", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("exports:sensitive", response.json()["data"]["permissions"])

    def test_regional_scope_cannot_see_other_center(self) -> None:
        username = "regional-a"
        create = self.client.post(
            "/api/v1/iam/users",
            headers=self.admin_headers,
            json={
                "username": username,
                "display_name": "园区负责人",
                "password": "regional-password",
                "roles": ["regional_manager"],
                "scopes": [{"scope_type": "SUBTREE", "org_unit_id": "org-a"}],
            },
        )
        if create.status_code not in {200, 400}:
            self.fail(create.text)
        login = self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "regional-password"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        tree = self.client.get("/api/v1/org-units/tree", headers=headers)
        self.assertEqual(tree.status_code, 200)
        ids = {row["id"] for row in tree.json()["data"]}
        self.assertEqual(ids, {"org-a", "class-a"})
        forbidden = self.client.post(
            "/api/v1/iam/users",
            headers=headers,
            json={
                "username": "forbidden",
                "display_name": "越权",
                "password": "forbidden-password",
                "roles": ["read_only"],
                "scopes": [{"scope_type": "UNIT", "org_unit_id": "org-b"}],
            },
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_regional_manager_cannot_set_development_relation_outside_scope(self) -> None:
        admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        regional = create_user(
            admin["id"],
            username="regional-development-guard",
            display_name="发展关系越权测试",
            password="regional-development-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "SUBTREE", "org_unit_id": "org-a"}],
        )
        with self.assertRaisesRegex(PermissionError, "授权范围外"):
            create_member(
                regional,
                member_code="DEVELOPMENT-OUTSIDE-001",
                name="越权发展关系学员",
                org_unit_id="org-a",
                development_org_unit_id="org-b",
                phone="13500135001",
            )
        member_id = create_member(
            admin["id"],
            member_code="UPDATE-SCOPE-001",
            name="档案变更范围测试学员",
            org_unit_id="org-a",
            development_org_unit_id=None,
            phone="13500135002",
        )
        update_member(regional, member_id, {"status": "SUSPENDED"})
        updated = fetch_one("SELECT status FROM members WHERE id=?", (member_id,))
        self.assertEqual(updated["status"], "SUSPENDED")
        with self.assertRaisesRegex(PermissionError, "授权范围外"):
            update_member(regional, member_id, {"org_unit_id": "org-b"})
        history = fetch_one(
            "SELECT change_type FROM member_change_history WHERE member_id=?",
            (member_id,),
        )
        self.assertEqual(history["change_type"], "PROFILE_UPDATE")

    def test_member_primary_org_must_be_regional_center(self) -> None:
        admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        with self.assertRaisesRegex(ValueError, "正式区域分中心"):
            create_member(
                admin["id"],
                member_code="INVALID-PRIMARY-CLASS-001",
                name="错误主归属测试学员",
                org_unit_id="class-a",
                development_org_unit_id=None,
                phone=None,
            )
        member_id = create_member(
            admin["id"],
            member_code="VALID-PRIMARY-REGION-001",
            name="主归属更新测试学员",
            org_unit_id="org-a",
            development_org_unit_id=None,
            phone=None,
        )
        with self.assertRaisesRegex(ValueError, "正式区域分中心"):
            update_member(admin["id"], member_id, {"org_unit_id": "class-a"})

    def test_identity_first_account_can_start_without_legacy_roles_or_scopes(self) -> None:
        response = self.client.post(
            "/api/v1/iam/users",
            headers=self.admin_headers,
            json={
                "username": "identity-first-account",
                "display_name": "身份优先测试账号",
                "password": "identity-first-password",
                "roles": [],
                "scopes": [],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        user_id = response.json()["data"]["id"]
        context = user_context(user_id)
        self.assertEqual(context["roles"], [])
        self.assertEqual(context["permissions"], [])
        self.assertEqual(context["scopes"], [])

    def test_account_management_password_reset_revokes_existing_sessions(self) -> None:
        created = self.client.post(
            "/api/v1/iam/users",
            headers=self.admin_headers,
            json={
                "username": "password-reset-account",
                "display_name": "改密测试账号",
                "password": "password-reset-before",
                "roles": ["read_only"],
                "scopes": [],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["data"]["id"]
        old_login = self.client.post(
            "/api/v1/auth/login",
            json={"username": "password-reset-account", "password": "password-reset-before"},
        )
        self.assertEqual(old_login.status_code, 200, old_login.text)
        old_token = old_login.json()["data"]["access_token"]
        listed = self.client.get("/api/v1/iam/users", headers=self.admin_headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(user_id, {row["id"] for row in listed.json()["data"]})
        reset = self.client.post(
            f"/api/v1/iam/users/{user_id}/password",
            headers=self.admin_headers,
            json={"password": "password-reset-after", "reason": "账号本人已通过管理员渠道申请重置密码"},
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertTrue(reset.json()["data"]["sessions_revoked"])
        self.assertEqual(
            self.client.get("/api/v1/me", headers={"Authorization": f"Bearer {old_token}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login",
                json={"username": "password-reset-account", "password": "password-reset-before"},
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login",
                json={"username": "password-reset-account", "password": "password-reset-after"},
            ).status_code,
            200,
        )
        audit = fetch_one(
            "SELECT action, after_json FROM audit_logs WHERE resource_id=? "
            "AND action='iam.user.password_reset' ORDER BY id DESC LIMIT 1",
            (str(user_id),),
        )
        self.assertEqual(audit["action"], "iam.user.password_reset")
        self.assertNotIn("password-reset-after", audit["after_json"])

    def test_duplicate_class_preview_identifies_duplicate_names(self) -> None:
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES ('class-duplicate-a', 'CLASS_DUP_A', '唯一性测试班', 'CLASS', 'org-b', 1, ?, ?), "
                "('class-duplicate-b', 'CLASS_DUP_B', '唯一性测试班', 'CLASS', 'org-b', 1, ?, ?)",
                (now, now, now, now),
            )
        preview = self.client.get(
            "/api/v1/iam/org-units/class-name-cleanup", headers=self.admin_headers
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        candidate = next(
            item for item in preview.json()["data"]["candidates"]
            if item["class_name"] == "唯一性测试班"
        )
        self.assertEqual(candidate["duplicate_count"], 1)
        with transaction() as connection:
            execute(connection, "UPDATE org_units SET is_active=0 WHERE id IN ('class-duplicate-a', 'class-duplicate-b')")
        preview = self.client.get(
            "/api/v1/iam/org-units/class-name-cleanup", headers=self.admin_headers
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertNotIn(
            "唯一性测试班",
            {item["class_name"] for item in preview.json()["data"]["candidates"]},
        )

    def test_class_scope_can_access_member_through_formal_relation(self) -> None:
        admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        counselor_id = create_user(
            admin["id"],
            username="class-counselor-a",
            display_name="圆融一班班主任",
            password="class-counselor-password",
            roles=["class_counselor"],
            scopes=[{"scope_type": "UNIT", "org_unit_id": "class-a"}],
        )
        member_id = create_member(
            admin["id"],
            member_code="CLASS-RELATION-001",
            name="班级关系测试学长",
            org_unit_id="org-a",
            development_org_unit_id=None,
            phone="13500135000",
            class_org_unit_id="class-a",
        )
        primary_relation = fetch_one(
            "SELECT id FROM member_org_relations "
            "WHERE member_id=? AND org_unit_id='org-a' "
            "AND relation_type='PRIMARY_REGION'",
            (member_id,),
        )
        class_relation = fetch_one(
            "SELECT id FROM member_org_relations "
            "WHERE member_id=? AND org_unit_id='class-a' "
            "AND relation_type='STUDY_CLASS'",
            (member_id,),
        )
        self.assertIsNotNone(primary_relation)
        self.assertIsNotNone(class_relation)
        visible_ids = {row["id"] for row in list_members(counselor_id)}
        self.assertIn(member_id, visible_ids)
        task_id = create_task(
            counselor_id,
            member_id=member_id,
            task_type="CARE",
            service_purpose="班主任关怀权限一致性测试",
            assigned_user_id=counselor_id,
            due_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        )
        visible_tasks = list_tasks(counselor_id, "OPEN")
        self.assertIn(task_id, {row["id"] for row in visible_tasks})
        task = fetch_one("SELECT org_unit_id FROM followup_tasks WHERE id=?", (task_id,))
        self.assertEqual(task["org_unit_id"], "class-a")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO attendance_event_groups"
                "(source_key, external_group_id, org_unit_id, study_org_unit_id, "
                "title, activity_type, event_date, status, created_at, updated_at) "
                "VALUES ('test', 'class-visibility-001', 'org-a', 'class-a', "
                "'班级活动权限测试', 'CLASS_MEETING', '2026-07-30', 'ACTIVE', ?, ?)",
                (now, now),
            )
        activity_ids = {
            row["id"]
            for row in list_event_groups(
                month=None,
                org_unit_id=None,
                user={"id": counselor_id},
            )["data"]
        }
        activity = fetch_one(
            "SELECT id FROM attendance_event_groups "
            "WHERE external_group_id='class-visibility-001'"
        )
        self.assertIn(activity["id"], activity_ids)

    def test_member_without_phone_is_kept_with_primary_relation(self) -> None:
        admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        member_id = create_member(
            admin["id"],
            member_code="MISSING-PHONE-001",
            name="待补手机号学长",
            org_unit_id="org-b",
            development_org_unit_id=None,
            phone=None,
        )
        member = fetch_one(
            "SELECT phone_ciphertext, phone_hash, phone_masked "
            "FROM members WHERE id=?",
            (member_id,),
        )
        relation = fetch_one(
            "SELECT id FROM member_org_relations "
            "WHERE member_id=? AND org_unit_id='org-b' "
            "AND relation_type='PRIMARY_REGION'",
            (member_id,),
        )
        self.assertIsNone(member["phone_ciphertext"])
        self.assertIsNone(member["phone_hash"])
        self.assertIsNone(member["phone_masked"])
        self.assertIsNotNone(relation)

    def test_member_merge_requires_reason_and_preserves_audit(self) -> None:
        admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        survivor_id = create_member(
            admin["id"], member_code="MERGE-SURVIVOR-001", name="合并主档",
            org_unit_id="org-a", development_org_unit_id=None, phone="13500135003",
        )
        duplicate_id = create_member(
            admin["id"], member_code="MERGE-DUPLICATE-001", name="合并重复档",
            org_unit_id="org-a", development_org_unit_id=None, phone="13500135004",
        )
        with self.assertRaisesRegex(ValueError, "至少6个字符"):
            merge_members(admin["id"], survivor_id, duplicate_id, "太短")
        merge_members(admin["id"], survivor_id, duplicate_id, "手机号和姓名已人工核对，保留主档")
        duplicate = fetch_one(
            "SELECT status, notes FROM members WHERE id=?", (duplicate_id,)
        )
        self.assertEqual(duplicate["status"], "INACTIVE")
        self.assertIn("MERGE-SURVIVOR-001", duplicate["notes"])
        merge_log = fetch_one(
            "SELECT survivor_member_id, duplicate_member_id FROM member_merge_history "
            "WHERE duplicate_member_id=?",
            (duplicate_id,),
        )
        self.assertEqual(merge_log["survivor_member_id"], survivor_id)


if __name__ == "__main__":
    unittest.main()
