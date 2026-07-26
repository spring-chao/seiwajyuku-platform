from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.migrations import run_migrations
from app.services.followups import (
    add_followup_record,
    add_visit_record,
    close_task,
    create_task,
    list_assignees,
    list_tasks,
)
from app.services.iam import create_user, seed_iam
from app.services.members import create_member, reveal_contact


class FollowupLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            if not execute(
                connection, "SELECT id FROM org_units WHERE id='followup-center'"
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, "
                    "created_at, updated_at) VALUES ('followup-center', 'FOLLOWUP_CENTER', ?, "
                    "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                    ("关怀测试中心", now, now),
                )
        cls.owner_id = create_user(
            cls.admin["id"],
            username="followup-owner",
            display_name="关怀责任人",
            password="followup-owner-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "SUBTREE", "org_unit_id": "followup-center"}],
        )
        cls.member_id = create_member(
            cls.admin["id"],
            member_code="FOLLOWUP-001",
            name="关怀测试学长",
            org_unit_id="followup-center",
            development_org_unit_id=None,
            phone="13900139000",
            company_name="关怀测试企业",
        )

    def test_phone_and_visit_close_loop(self) -> None:
        due = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        task_id = create_task(
            self.admin["id"],
            member_id=self.member_id,
            task_type="CARE",
            service_purpose="了解经营近况并匹配支持资源",
            assigned_user_id=self.owner_id,
            due_at=due,
        )
        visible = list_tasks(self.owner_id, "OPEN")
        self.assertIn(task_id, {row["id"] for row in visible})
        revealed = reveal_contact(
            member_id=self.member_id,
            task_id=task_id,
            actor_user_id=self.owner_id,
            purpose="执行电话关怀任务",
            client_reference="followup-loop-test",
        )
        self.assertEqual(revealed["phone"], "13900139000")
        next_at = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        record_id = add_followup_record(
            task_id,
            self.owner_id,
            channel="PHONE",
            contacted_at=datetime.now(UTC).isoformat(),
            outcome_code="CONNECTED",
            subject_statement="希望交流库存周转经验",
            objective_facts="本季度安排了两次内部改善会议",
            staff_judgment="可匹配同业交流",
            next_action="预约企业走访",
            next_followup_at=next_at,
        )
        self.assertGreater(record_id, 0)
        visit_id = add_visit_record(
            task_id,
            self.owner_id,
            appointment_at=next_at,
            visited_at=next_at,
            purpose="了解库存改善现场",
            participants=["学长", "关怀责任人"],
            location_type="ENTERPRISE",
            objective_facts="现场展示了按周统计的库存看板",
            expressed_needs="希望引荐精益改善经验",
            support_provided="提供同业交流活动信息",
            staff_judgment="适合参加下月专题交流",
            next_action="发送活动邀请",
            next_followup_at=None,
        )
        self.assertGreater(visit_id, 0)
        close_task(task_id, self.owner_id, "电话与走访均已完成，转入日常维护")
        task = fetch_one("SELECT status, next_followup_at FROM followup_tasks WHERE id=?", (task_id,))
        self.assertEqual(task["status"], "CLOSED")
        self.assertIsNone(task["next_followup_at"])

    def test_cannot_close_without_result(self) -> None:
        task_id = create_task(
            self.admin["id"],
            member_id=self.member_id,
            task_type="CARE",
            service_purpose="验证无结果不得关闭",
            assigned_user_id=self.owner_id,
            due_at=None,
        )
        with self.assertRaises(ValueError):
            close_task(task_id, self.owner_id, "尚未执行")

    def test_task_requires_service_purpose_of_at_least_four_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少填写 4 个字符"):
            create_task(
                self.admin["id"],
                member_id=self.member_id,
                task_type="CARE",
                service_purpose="走访",
                assigned_user_id=self.owner_id,
                due_at=None,
            )

    def test_assignee_candidates_respect_org_scope(self) -> None:
        rows = list_assignees(self.admin["id"], "followup-center")
        self.assertIn(self.owner_id, {row["id"] for row in rows})
        visible = list_tasks(self.owner_id)
        self.assertTrue(all(row["can_record"] for row in visible))

    def test_assignee_route_exists(self) -> None:
        response = TestClient(app).get("/api/v1/followups/assignees")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
