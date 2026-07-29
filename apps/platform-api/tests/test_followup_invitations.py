from __future__ import annotations

import unittest
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.migrations import run_migrations
from app.services.followup_invitations import (
    accept_invitation,
    create_invitation,
    list_my_invitations,
    mark_unavailable,
    request_adjustment,
    respond_to_adjustment,
)
from app.services.followups import (
    add_followup_record,
    close_task,
    create_task,
    list_tasks,
)
from app.services.iam import create_user, seed_iam
from app.services.members import create_member, reveal_contact


class FollowupInvitationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin_id = fetch_one(
            "SELECT id FROM app_users WHERE username='admin'"
        )["id"]
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for org_id, code, name in (
                ("invite-center-a", "INVITE_A", "邀请试点中心 A"),
                ("invite-center-b", "INVITE_B", "邀请试点中心 B"),
            ):
                if not execute(
                    connection, "SELECT id FROM org_units WHERE id=?", (org_id,)
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                        "is_active, created_at, updated_at) VALUES (?, ?, ?, "
                        "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                        (org_id, code, name, now, now),
                    )
        cls.primary_id = create_user(
            cls.admin_id,
            username="invitation-primary",
            display_name="担当志工",
            password="invitation-primary-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "UNIT", "org_unit_id": "invite-center-a"}],
        )
        cls.companion_id = create_user(
            cls.admin_id,
            username="invitation-companion",
            display_name="同行志工",
            password="invitation-companion-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "UNIT", "org_unit_id": "invite-center-a"}],
        )
        cls.out_of_scope_id = create_user(
            cls.admin_id,
            username="invitation-out-of-scope",
            display_name="范围外志工",
            password="invitation-out-of-scope-password",
            roles=["regional_manager"],
            scopes=[{"scope_type": "UNIT", "org_unit_id": "invite-center-b"}],
        )
        cls.member_id = create_member(
            cls.admin_id,
            member_code="INVITATION-MEMBER-001",
            name="邀请试点学长",
            org_unit_id="invite-center-a",
            development_org_unit_id=None,
            phone="13800138001",
            company_name="邀请试点企业",
        )

    def _task_with_invitation(self) -> tuple[int, int]:
        task_id = create_task(
            self.admin_id,
            member_id=self.member_id,
            task_type="CARE",
            service_purpose="邀请志工提供温暖清晰的关怀服务",
            assigned_user_id=self.primary_id,
            due_at=(datetime.now(UTC) + timedelta(days=5)).isoformat(),
            invitation_mode=True,
            invitation_message="想邀请您在方便时共同关怀这位学长",
            invitation_valid_until=(
                datetime.now(UTC) + timedelta(days=2)
            ).isoformat(),
        )
        invitation_id = fetch_one(
            "SELECT id FROM followup_service_invitations WHERE task_id=? "
            "AND invitation_type='ASSIGNEE'",
            (task_id,),
        )["id"]
        return task_id, invitation_id

    def _record(self, task_id: int, user_id: int) -> int:
        return add_followup_record(
            task_id,
            user_id,
            channel="PHONE",
            contacted_at=datetime.now(UTC).isoformat(),
            outcome_code="CONNECTED",
            subject_statement="学长愿意继续交流",
            objective_facts=None,
            staff_judgment=None,
            next_action="约定下次联系",
            next_followup_at=None,
        )

    def test_pending_invitation_blocks_service_data_until_acceptance(self) -> None:
        task_id, invitation_id = self._task_with_invitation()
        with self.assertRaisesRegex(PermissionError, "接受服务邀请"):
            self._record(task_id, self.primary_id)
        with self.assertRaisesRegex(PermissionError, "接受服务邀请"):
            reveal_contact(
                member_id=self.member_id,
                task_id=task_id,
                actor_user_id=self.primary_id,
                purpose="准备执行关怀联系",
                client_reference="invitation-test",
            )

        accept_invitation(invitation_id, self.primary_id, "愿意共同担当")
        self.assertGreater(self._record(task_id, self.primary_id), 0)
        revealed = reveal_contact(
            member_id=self.member_id,
            task_id=task_id,
            actor_user_id=self.primary_id,
            purpose="执行已接受的关怀服务",
            client_reference="invitation-test",
        )
        self.assertEqual(revealed["phone"], "13800138001")
        task = next(row for row in list_tasks(self.primary_id) if row["id"] == task_id)
        self.assertTrue(task["can_record"])
        self.assertTrue(task["can_close"])

    def test_adjustment_round_trip_then_accept(self) -> None:
        task_id, invitation_id = self._task_with_invitation()
        requested = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        request_adjustment(
            invitation_id,
            self.primary_id,
            requested_due_at=requested,
            response_note="这周已有服务安排，建议下周完成",
        )
        self.assertEqual(
            fetch_one(
                "SELECT status FROM followup_service_invitations WHERE id=?",
                (invitation_id,),
            )["status"],
            "ADJUSTMENT_REQUESTED",
        )
        agreed = (datetime.now(UTC) + timedelta(days=8)).isoformat()
        respond_to_adjustment(
            invitation_id,
            self.admin_id,
            proposed_due_at=agreed,
            response_note="感谢说明，按建议时间安排",
        )
        accept_invitation(invitation_id, self.primary_id)
        task = fetch_one("SELECT due_at FROM followup_tasks WHERE id=?", (task_id,))
        self.assertEqual(task["due_at"], agreed)

    def test_unavailable_response_is_terminal_and_audited(self) -> None:
        task_id, invitation_id = self._task_with_invitation()
        mark_unavailable(
            invitation_id,
            self.primary_id,
            "本周暂时无法妥善投入，期待下次参与",
        )
        invitation = fetch_one(
            "SELECT status FROM followup_service_invitations WHERE id=?",
            (invitation_id,),
        )
        self.assertEqual(invitation["status"], "UNAVAILABLE")
        with self.assertRaises(ValueError):
            accept_invitation(invitation_id, self.primary_id)
        audit = fetch_one(
            "SELECT action FROM audit_logs WHERE resource_type='followup_service_invitation' "
            "AND resource_id=? ORDER BY id DESC LIMIT 1",
            (str(invitation_id),),
        )
        self.assertEqual(audit["action"], "followups.invitation.unavailable")
        self.assertNotIn(task_id, {row["id"] for row in list_tasks(self.primary_id)})

    def test_companion_can_record_but_cannot_reveal_or_close(self) -> None:
        task_id, invitation_id = self._task_with_invitation()
        accept_invitation(invitation_id, self.primary_id)
        companion_invitation_id = create_invitation(
            task_id,
            self.primary_id,
            invited_user_id=self.companion_id,
            invitation_type="COMPANION",
            invitation_message="想邀请您与我同行协力",
            proposed_due_at=None,
            valid_until=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        )
        accept_invitation(companion_invitation_id, self.companion_id)
        self.assertGreater(self._record(task_id, self.companion_id), 0)
        with self.assertRaises(PermissionError):
            reveal_contact(
                member_id=self.member_id,
                task_id=task_id,
                actor_user_id=self.companion_id,
                purpose="同行服务联系",
                client_reference="companion-test",
            )
        with self.assertRaises(PermissionError):
            close_task(task_id, self.companion_id, "同行服务已经完成")
        row = next(row for row in list_tasks(self.companion_id) if row["id"] == task_id)
        self.assertTrue(row["can_record"])
        self.assertFalse(row["can_close"])

    def test_scope_and_feature_gate_are_enforced(self) -> None:
        task_id, _ = self._task_with_invitation()
        with self.assertRaisesRegex(ValueError, "任职范围"):
            create_invitation(
                task_id,
                self.admin_id,
                invited_user_id=self.out_of_scope_id,
                invitation_type="ASSIGNEE",
                invitation_message=None,
                proposed_due_at=None,
                valid_until=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            )
        disabled = SimpleNamespace(volunteer_service_invitations_enabled=False)
        with patch(
            "app.services.followup_invitations.get_settings",
            return_value=disabled,
        ):
            with self.assertRaisesRegex(PermissionError, "尚未启用"):
                accept_invitation(1, self.primary_id)
            self.assertEqual(list_my_invitations(self.primary_id), [])

    def test_expired_invitation_cannot_be_accepted(self) -> None:
        task_id, invitation_id = self._task_with_invitation()
        with transaction() as connection:
            execute(
                connection,
                "UPDATE followup_service_invitations SET valid_until=? WHERE id=?",
                (
                    (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                    invitation_id,
                ),
            )
        mine = next(
            item
            for item in list_my_invitations(self.primary_id)
            if item["id"] == invitation_id
        )
        self.assertEqual(mine["status"], "EXPIRED")
        with self.assertRaisesRegex(ValueError, "有效期"):
            accept_invitation(invitation_id, self.primary_id)

    def test_invitation_routes_require_authentication(self) -> None:
        client = TestClient(app)
        self.assertEqual(
            client.get("/api/v1/followups/invitations/mine").status_code, 401
        )
        self.assertEqual(
            client.get("/api/v1/followups/capabilities").status_code, 401
        )

    def test_0012_forward_and_rollback_preserve_followup_tasks(self) -> None:
        migration_root = Path(__file__).resolve().parents[3] / "migrations"
        with tempfile.TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / "invitation.db")
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                for path in sorted((migration_root / "sqlite").glob("*.sql")):
                    connection.executescript(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE name='followup_service_invitations'"
                    ).fetchone()
                )
                connection.executescript(
                    (
                        migration_root
                        / "rollback"
                        / "sqlite"
                        / "0012_followup_service_invitations.down.sql"
                    ).read_text(encoding="utf-8")
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE name='followup_service_invitations'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    connection.execute(
                        "SELECT name FROM sqlite_master WHERE name='followup_tasks'"
                    ).fetchone()
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
