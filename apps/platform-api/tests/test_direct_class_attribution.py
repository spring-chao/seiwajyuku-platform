from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.db import execute, fetch_all, fetch_one, transaction
from app.migrations import run_migrations
from app.services.iam import seed_iam
from app.services.class_operations import class_operations_detail
from app.services.members import create_member
from app.services.plans import operations_snapshot


class DirectClassAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin_id = fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"]
        now = datetime.now(UTC).isoformat()
        units = [
            ("direct-learning-center", "DIRECT_LEARNING_CENTER", "直属学习测试分中心", "REGIONAL_CENTER", "org-suzhou"),
            ("direct-pioneer-class", "DIRECT_PIONEER_CLASS", "先锋班", "CLASS", "org-suzhou"),
            ("direct-pioneer-group", "DIRECT_PIONEER_GROUP", "直属测试精进组", "GROUP", "direct-pioneer-class"),
            ("direct-invalid-root-class", "DIRECT_INVALID_ROOT_CLASS", "黄埔班", "CLASS", "org-suzhou"),
        ]
        with transaction() as connection:
            for unit_id, code, name, unit_type, parent_id in units:
                if not execute(connection, "SELECT id FROM org_units WHERE id=?", (unit_id,)).fetchone():
                    execute(
                        connection,
                        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        (unit_id, code, name, unit_type, parent_id, now, now),
                    )

    def test_direct_learning_class_does_not_replace_development_attribution(self) -> None:
        member_id = create_member(
            self.admin_id,
            member_code="DIRECT-CLASS-ATTRIBUTION-001",
            name="直属学习归属验证学长",
            org_unit_id="direct-learning-center",
            development_org_unit_id="direct-learning-center",
            phone="13600136001",
            class_name="先锋班",
            group_name="直属测试精进组",
        )

        member = fetch_one(
            "SELECT org_unit_id, development_org_unit_id FROM members WHERE id=?",
            (member_id,),
        )
        self.assertEqual(member["org_unit_id"], "direct-learning-center")
        self.assertEqual(member["development_org_unit_id"], "direct-learning-center")

        relations = {
            (row["relation_type"], row["org_unit_id"])
            for row in fetch_all(
                "SELECT relation_type, org_unit_id FROM member_org_relations WHERE member_id=?",
                (member_id,),
            )
        }
        self.assertIn(("PRIMARY_REGION", "direct-learning-center"), relations)
        self.assertIn(("DEVELOPMENT_RELATION", "direct-learning-center"), relations)
        self.assertIn(("STUDY_CLASS", "direct-pioneer-class"), relations)
        self.assertIn(("STUDY_GROUP", "direct-pioneer-group"), relations)

        snapshot = operations_snapshot(user_id=self.admin_id, year=2026, month=8)
        class_row = next(
            row
            for row in snapshot["classes"]
            if row["class_org_unit_id"] == "direct-pioneer-class"
        )
        self.assertEqual(class_row["org_name"], "苏州塾直属")
        self.assertEqual(class_row["class_owner_org_unit_id"], "org-suzhou")
        self.assertEqual(class_row["class_owner_scope"], "DIRECT")
        center_row = next(
            row for row in snapshot["centers"] if row["id"] == "direct-learning-center"
        )
        self.assertGreaterEqual(center_row["active_member_count"], 1)

        detail = class_operations_detail(
            user_id=self.admin_id,
            class_org_unit_id="direct-pioneer-class",
            year=2026,
            month=8,
        )
        self.assertEqual(detail["org_name"], "苏州塾直属")
        self.assertEqual(detail["active_member_count"], 1)
        self.assertEqual(detail["class_owner_org_unit_id"], "org-suzhou")

    def test_invalid_root_class_cannot_be_selected_for_a_member(self) -> None:
        with self.assertRaisesRegex(ValueError, "学习班级"):
            create_member(
                self.admin_id,
                member_code="DIRECT-CLASS-ATTRIBUTION-INVALID-001",
                name="错误直属班级验证学长",
                org_unit_id="direct-learning-center",
                development_org_unit_id="direct-learning-center",
                phone="13600136002",
                class_org_unit_id="direct-invalid-root-class",
            )


if __name__ == "__main__":
    unittest.main()
