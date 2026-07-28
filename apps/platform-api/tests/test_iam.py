from __future__ import annotations

import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.attendance import list_event_groups
from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.checkin_rosters import upsert_relation
from app.services.iam import create_user
from app.services.members import create_member, list_members


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
        )
        upsert_relation(
            admin["id"],
            member_id=member_id,
            org_unit_id="class-a",
            relation_type="STUDY_CLASS",
            is_primary=True,
        )
        visible_ids = {row["id"] for row in list_members(counselor_id)}
        self.assertIn(member_id, visible_ids)
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


if __name__ == "__main__":
    unittest.main()
