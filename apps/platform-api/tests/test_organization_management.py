from __future__ import annotations

import unittest
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import execute, fetch_all, fetch_one, transaction
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
        listed = self.client.get(
            "/api/v1/iam/org-units/learning-management", headers=self.headers
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_group = next(
            row for row in listed.json()["data"]["units"] if row["id"] == group_id
        )
        self.assertEqual(listed_group["name"], group_name)
        self.assertEqual(listed_group["parent_id"], class_id)
        group = fetch_one("SELECT parent_id FROM org_units WHERE id=?", (group_id,))
        self.assertEqual(group["parent_id"], class_id)
        audit = fetch_one(
            "SELECT action FROM audit_logs WHERE resource_id=? ORDER BY id DESC LIMIT 1",
            (group_id,),
        )
        self.assertEqual(audit["action"], "org.learning_unit.create")

    def test_rename_group_keeps_identity_memberships_and_updates_views(self) -> None:
        group_id = "group-management-move"
        old_name = fetch_one("SELECT name FROM org_units WHERE id=?", (group_id,))["name"]
        new_name = f"{old_name}-改名"
        before_relations = [
            (
                row["id"],
                row["member_id"],
                row["org_unit_id"],
                row["relation_type"],
                row["valid_from"],
                row["valid_until"],
            )
            for row in fetch_all(
                "SELECT id, member_id, org_unit_id, relation_type, valid_from, valid_until "
                "FROM member_org_relations WHERE org_unit_id=? ORDER BY id",
                (group_id,),
            )
        ]
        parent_before = fetch_one(
            "SELECT parent_id FROM org_units WHERE id=?", (group_id,)
        )["parent_id"]

        renamed = self.client.patch(
            f"/api/v1/iam/org-units/{group_id}/name",
            headers=self.headers,
            json={
                "name": new_name,
                "confirmation": f"确认将小组“{old_name}”改名为“{new_name}”",
            },
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertTrue(renamed.json()["data"]["changed"])
        self.assertTrue(renamed.json()["data"]["membership_unchanged"])

        group = fetch_one(
            "SELECT id, name, parent_id FROM org_units WHERE id=?", (group_id,)
        )
        self.assertEqual(group["id"], group_id)
        self.assertEqual(group["name"], new_name)
        self.assertEqual(group["parent_id"], parent_before)
        after_relations = [
            (
                row["id"],
                row["member_id"],
                row["org_unit_id"],
                row["relation_type"],
                row["valid_from"],
                row["valid_until"],
            )
            for row in fetch_all(
                "SELECT id, member_id, org_unit_id, relation_type, valid_from, valid_until "
                "FROM member_org_relations WHERE org_unit_id=? ORDER BY id",
                (group_id,),
            )
        ]
        self.assertEqual(after_relations, before_relations)

        listed = self.client.get(
            "/api/v1/iam/org-units/learning-management", headers=self.headers
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        listed_group = next(
            row for row in listed.json()["data"]["units"] if row["id"] == group_id
        )
        self.assertEqual(listed_group["name"], new_name)
        members = self.client.get("/api/v1/members", headers=self.headers)
        self.assertEqual(members.status_code, 200, members.text)
        member = next(
            row for row in members.json()["data"] if row["id"] == self.sync_member_id
        )
        self.assertEqual(member["group_name"], new_name)

        audit = fetch_one(
            "SELECT action, before_json, after_json FROM audit_logs "
            "WHERE resource_id=? AND action='org.learning_group.rename' "
            "ORDER BY id DESC LIMIT 1",
            (group_id,),
        )
        self.assertEqual(audit["action"], "org.learning_group.rename")
        self.assertIn(old_name, audit["before_json"])
        self.assertIn(new_name, audit["after_json"])

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

    def test_management_class_selectors_expose_one_canonical_node_per_name(self) -> None:
        suffix = uuid4().hex[:10]
        now = datetime.now(UTC).isoformat()
        older_id = f"selector-class-a-{suffix}"
        newer_id = f"selector-class-b-{suffix}"
        other_center_id = f"selector-class-c-{suffix}"
        class_name = f"下拉去重测试班-{suffix}"
        with transaction() as connection:
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'CLASS', 'org-management-a', 1, '2026-01-01T00:00:00+00:00', ?), "
                "(?, ?, ?, 'CLASS', 'org-management-a', 1, '2026-02-01T00:00:00+00:00', ?), "
                "(?, ?, ?, 'CLASS', 'org-management-b', 1, '2025-01-01T00:00:00+00:00', ?)",
                (
                    older_id,
                    f"SELECTOR_A_{suffix}",
                    class_name,
                    now,
                    newer_id,
                    f"SELECTOR_B_{suffix}",
                    class_name,
                    now,
                    other_center_id,
                    f"SELECTOR_C_{suffix}",
                    class_name,
                    now,
                ),
            )

        response = self.client.get(
            "/api/v1/iam/org-units/learning-management", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        selectable = [
            row for row in response.json()["data"]["classes"]
            if row["name"] == class_name
        ]
        self.assertEqual(
            {row["id"] for row in selectable}, {older_id, other_center_id}
        )
        self.assertEqual(
            [row["id"] for row in selectable if row["parent_id"] == "org-management-a"],
            [older_id],
        )
        full_nodes = [
            row for row in response.json()["data"]["units"]
            if row["name"] == class_name
        ]
        self.assertEqual(len(full_nodes), 3)
        self.assertEqual(sum(bool(row["is_name_canonical"]) for row in full_nodes), 2)

    def test_group_member_transfer_exposes_member_and_keeps_history(self) -> None:
        suffix = uuid4().hex[:10]
        now = datetime.now(UTC).isoformat()
        class_id = f"transfer-class-{suffix}"
        source_id = f"transfer-source-{suffix}"
        target_id = f"transfer-target-{suffix}"
        with transaction() as connection:
            for unit_id, code, name, unit_type, parent_id in (
                (class_id, f"TRANSFER_CLASS_{suffix}", "迁移测试班", "CLASS", "org-management-a"),
                (source_id, f"TRANSFER_SOURCE_{suffix}", "重复小组", "GROUP", class_id),
                (target_id, f"TRANSFER_TARGET_{suffix}", "正式目标组", "GROUP", class_id),
            ):
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (unit_id, code, name, unit_type, parent_id, now, now),
                )
            member_id = execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, class_name, group_name, created_at, updated_at) "
                "VALUES (?, '待迁移学员', 'org-management-a', 'ACTIVE', '迁移测试班', '重复小组', ?, ?)",
                (f"TRANSFER-MEMBER-{suffix}", now, now),
            ).lastrowid
            for relation_type, org_unit_id in (("STUDY_CLASS", class_id), ("STUDY_GROUP", source_id)):
                execute(
                    connection,
                    "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, source_type, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, 'TEST', ?, ?)",
                    (member_id, org_unit_id, relation_type, now, now),
                )

        options = self.client.get(
            f"/api/v1/iam/org-units/{source_id}/group-member-transfer-options",
            headers=self.headers,
        )
        self.assertEqual(options.status_code, 200, options.text)
        self.assertEqual(options.json()["data"]["members"][0]["member_id"], member_id)
        self.assertEqual(options.json()["data"]["target_groups"][0]["id"], target_id)

        moved = self.client.post(
            f"/api/v1/iam/org-units/{source_id}/group-member-transfer",
            headers=self.headers,
            json={
                "member_id": member_id,
                "target_group_org_unit_id": target_id,
                "reason": "清理重复小组关联",
                "confirmation": "确认将待迁移学员从重复小组转至正式目标组",
            },
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        member = fetch_one("SELECT group_name FROM members WHERE id=?", (member_id,))
        self.assertEqual(member["group_name"], "正式目标组")
        current = fetch_one(
            "SELECT org_unit_id FROM member_org_relations WHERE member_id=? "
            "AND relation_type='STUDY_GROUP' AND valid_until IS NULL",
            (member_id,),
        )
        self.assertEqual(current["org_unit_id"], target_id)
        history = fetch_one(
            "SELECT valid_until FROM member_org_relations WHERE member_id=? AND org_unit_id=? "
            "AND relation_type='STUDY_GROUP' ORDER BY id DESC LIMIT 1",
            (member_id, source_id),
        )
        self.assertTrue(history["valid_until"])
        audit = fetch_one(
            "SELECT action FROM audit_logs WHERE action='org.learning_group.member.transfer' "
            "AND resource_id=? ORDER BY id DESC LIMIT 1",
            (f"{member_id}:{source_id}:{target_id}",),
        )
        self.assertEqual(audit["action"], "org.learning_group.member.transfer")

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
