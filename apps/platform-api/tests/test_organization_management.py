from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services.members import list_members
from app.services.renewals import list_cycle_coverage


class OrganizationManagementTests(unittest.TestCase):
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
        cls.headers = {
            "Authorization": f"Bearer {login.json()['data']['access_token']}"
        }
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for values in (
                (
                    "org-management-a",
                    "ORG_MANAGEMENT_A",
                    "组织管理甲分中心",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    "org-management-b",
                    "ORG_MANAGEMENT_B",
                    "组织管理乙分中心",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    "class-management-move",
                    "CLASS_MANAGEMENT_MOVE",
                    "归属调整测试班",
                    "CLASS",
                    "org-suzhou",
                ),
                (
                    "group-management-move",
                    "GROUP_MANAGEMENT_MOVE",
                    "归属调整测试组",
                    "GROUP",
                    "class-management-move",
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
            existing_member = execute(
                connection,
                "SELECT id FROM members WHERE member_code='ORG-MANAGEMENT-SYNC'",
            ).fetchone()
            if existing_member:
                cls.sync_member_id = existing_member["id"]
            else:
                cls.sync_member_id = execute(
                    connection,
                    "INSERT INTO members(member_code, name, org_unit_id, status, "
                    "class_name, group_name, created_at, updated_at) "
                    "VALUES ('ORG-MANAGEMENT-SYNC', '组织同步测试学员', 'org-suzhou', "
                    "'ACTIVE', '旧班级显示', '旧小组显示', ?, ?)",
                    (now, now),
                ).lastrowid
            for relation_type, org_unit_id in (
                ("PRIMARY_REGION", "org-suzhou"),
                ("STUDY_CLASS", "class-management-move"),
                ("STUDY_GROUP", "group-management-move"),
            ):
                if not execute(
                    connection,
                    "SELECT id FROM member_org_relations WHERE member_id=? "
                    "AND org_unit_id=? AND relation_type=?",
                    (cls.sync_member_id, org_unit_id, relation_type),
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, "
                        "is_primary, source_type, created_at, updated_at) "
                        "VALUES (?, ?, ?, 1, 'TEST', ?, ?)",
                        (cls.sync_member_id, org_unit_id, relation_type, now, now),
                    )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)

    def test_create_class_and_group_with_audit(self) -> None:
        class_name = "受控新增测试班"
        created_class = self.client.post(
            "/api/v1/iam/org-units/learning-management",
            headers=self.headers,
            json={
                "name": class_name,
                "unit_type": "CLASS",
                "parent_id": "org-management-a",
                "confirmation": f"确认新增班级：{class_name}",
            },
        )
        self.assertEqual(created_class.status_code, 200, created_class.text)
        class_id = created_class.json()["data"]["id"]
        group_name = "受控新增测试组"
        created_group = self.client.post(
            "/api/v1/iam/org-units/learning-management",
            headers=self.headers,
            json={
                "name": group_name,
                "unit_type": "GROUP",
                "parent_id": class_id,
                "confirmation": f"确认新增小组：{group_name}",
            },
        )
        self.assertEqual(created_group.status_code, 200, created_group.text)
        group_id = created_group.json()["data"]["id"]
        group = fetch_one("SELECT parent_id FROM org_units WHERE id=?", (group_id,))
        self.assertEqual(group["parent_id"], class_id)
        audit = fetch_one(
            "SELECT action FROM audit_logs WHERE resource_id=? ORDER BY id DESC LIMIT 1",
            (group_id,),
        )
        self.assertEqual(audit["action"], "org.learning_unit.create")

    def test_move_class_keeps_group_tree_and_records_rollback_parent(self) -> None:
        preview = self.client.get(
            "/api/v1/iam/org-units/class-management-move/move-preview",
            headers=self.headers,
            params={"target_parent_id": "org-management-b"},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        data = preview.json()["data"]
        self.assertEqual(data["target_parent_name"], "组织管理乙分中心")
        moved = self.client.post(
            "/api/v1/iam/org-units/class-management-move/move",
            headers=self.headers,
            json={
                "target_parent_id": "org-management-b",
                "reason": "业务负责人确认班级属于乙分中心",
                "confirmation": data["confirmation"],
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["data"]["previous_parent_id"], "org-suzhou")
        class_row = fetch_one(
            "SELECT parent_id FROM org_units WHERE id='class-management-move'"
        )
        group_row = fetch_one(
            "SELECT parent_id FROM org_units WHERE id='group-management-move'"
        )
        self.assertEqual(class_row["parent_id"], "org-management-b")
        self.assertEqual(group_row["parent_id"], "class-management-move")
        member = fetch_one(
            "SELECT org_unit_id, class_name, group_name FROM members WHERE id=?",
            (self.sync_member_id,),
        )
        self.assertEqual(member["org_unit_id"], "org-management-b")
        self.assertEqual(member["class_name"], "归属调整测试班")
        self.assertEqual(member["group_name"], "归属调整测试组")
        primary_relation = fetch_one(
            "SELECT org_unit_id FROM member_org_relations WHERE member_id=? "
            "AND relation_type='PRIMARY_REGION'",
            (self.sync_member_id,),
        )
        self.assertEqual(primary_relation["org_unit_id"], "org-management-b")
        self.assertEqual(moved.json()["data"]["synced_member_count"], 1)
        audit = fetch_one(
            "SELECT before_json, after_json FROM audit_logs "
            "WHERE resource_id='class-management-move' "
            "AND action='org.learning_class.move' ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("org-suzhou", audit["before_json"])
        self.assertIn("org-management-b", audit["after_json"])

    def test_deactivate_fails_closed_with_active_children(self) -> None:
        response = self.client.post(
            "/api/v1/iam/org-units/class-management-move/deactivate",
            headers=self.headers,
            json={
                "reason": "班级已经停止运营并完成复核",
                "confirmation": "确认停用班级：归属调整测试班",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("仍有关联", response.json()["detail"])

    def test_management_list_includes_reference_counts(self) -> None:
        response = self.client.get(
            "/api/v1/iam/org-units/learning-management", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        item = next(
            row
            for row in response.json()["data"]["units"]
            if row["id"] == "class-management-move"
        )
        self.assertEqual(item["reference_counts"]["active_children"], 1)

    def test_member_and_renewal_views_ignore_ended_or_legacy_class_text(self) -> None:
        suffix = uuid4().hex[:10]
        now = datetime.now(UTC).isoformat()
        old_until = "2000-01-01T00:00:00+00:00"
        member_name = f"正式关系展示测试{suffix}"
        class_id = f"formal-class-{suffix}"
        group_id = f"formal-group-{suffix}"
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, 'CLASS', 'org-suzhou', 1, ?, ?)",
                (class_id, f"FORMAL_CLASS_{suffix}", "已结束正式班级", now, now),
            )
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, 'GROUP', ?, 1, ?, ?)",
                (group_id, f"FORMAL_GROUP_{suffix}", "已结束正式小组", class_id, now, now),
            )
            member_id = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, class_name, group_name, created_at, updated_at) "
                "VALUES (?, ?, 'org-suzhou', 'ACTIVE', '旧文本班级', '旧文本小组', ?, ?)",
                (f"FORMAL-RELATION-{suffix}", member_name, now, now),
            ).lastrowid
            for relation_type, org_unit_id in (("STUDY_CLASS", class_id), ("STUDY_GROUP", group_id)):
                execute(
                    connection,
                    "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, source_type, valid_until, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, 'TEST', ?, ?, ?)",
                    (member_id, org_unit_id, relation_type, old_until, now, now),
                )

        admin_id = fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"]
        member = next(row for row in list_members(admin_id) if row["id"] == member_id)
        self.assertIsNone(member["class_name"])
        self.assertIsNone(member["group_name"])
        coverage = list_cycle_coverage(
            admin_id,
            datetime.now(UTC).year,
            member_name=member_name,
            include_synced=True,
        )
        self.assertEqual(len(coverage["rows"]), 1)
        self.assertIsNone(coverage["rows"][0]["member_class_name"])
        self.assertIsNone(coverage["rows"][0]["member_group_name"])


if __name__ == "__main__":
    unittest.main()
