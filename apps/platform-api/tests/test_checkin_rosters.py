from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.migrations import run_migrations
from app.services.iam import seed_iam
from app.services.checkin_rosters import (
    cross_class_members,
    roster_integrity_summary,
    roster_members,
    roster_options,
)


class CheckinRosterIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            for values in (
                (
                    "roster-center",
                    "ROSTER_CENTER",
                    "名单测试分中心",
                    "REGIONAL_CENTER",
                    "org-suzhou",
                ),
                (
                    "roster-class",
                    "ROSTER_CLASS",
                    "名单测试班",
                    "CLASS",
                    "roster-center",
                ),
                (
                    "roster-group",
                    "ROSTER_GROUP",
                    "名单测试组",
                    "GROUP",
                    "roster-class",
                ),
                (
                    "roster-other-class",
                    "ROSTER_OTHER_CLASS",
                    "外班名单测试班",
                    "CLASS",
                    "roster-center",
                ),
            ):
                if not execute(
                    connection,
                    "SELECT id FROM org_units WHERE id=?",
                    (values[0],),
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO org_units"
                        "(id, unit_code, name, unit_type, parent_id, is_active, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                        values + (now, now),
                    )
            member = execute(
                connection,
                "SELECT id FROM members WHERE member_code='ROSTER-001'",
            ).fetchone()
            if member:
                cls.member_id = member["id"]
            else:
                cursor = execute(
                    connection,
                    "INSERT INTO members"
                    "(member_code, name, org_unit_id, status, created_at, updated_at) "
                    "VALUES ('ROSTER-001', '名单测试学长', 'roster-center', "
                    "'ACTIVE', ?, ?)",
                    (now, now),
                )
                cls.member_id = cursor.lastrowid
            for relation_type, org_id in (
                ("STUDY_CLASS", "roster-class"),
                ("STUDY_GROUP", "roster-group"),
            ):
                if not execute(
                    connection,
                    "SELECT id FROM member_org_relations "
                    "WHERE member_id=? AND relation_type=? AND org_unit_id=?",
                    (cls.member_id, relation_type, org_id),
                ).fetchone():
                    execute(
                        connection,
                        "INSERT INTO member_org_relations"
                        "(member_id, org_unit_id, relation_type, is_primary, "
                        "source_type, created_at, updated_at) "
                        "VALUES (?, ?, ?, 1, 'TEST', ?, ?)",
                        (cls.member_id, org_id, relation_type, now, now),
                    )
            cross_member = execute(
                connection,
                "SELECT id FROM members WHERE member_code='ROSTER-CROSS-001'",
            ).fetchone()
            if cross_member:
                cls.cross_member_id = cross_member["id"]
            else:
                cursor = execute(
                    connection,
                    "INSERT INTO members"
                    "(member_code, name, org_unit_id, status, company_name, created_at, updated_at) "
                    "VALUES ('ROSTER-CROSS-001', '跨班测试学长', 'roster-center', "
                    "'ACTIVE', '测试企业', ?, ?)",
                    (now, now),
                )
                cls.cross_member_id = cursor.lastrowid
            if not execute(
                connection,
                "SELECT id FROM member_org_relations "
                "WHERE member_id=? AND relation_type='STUDY_CLASS' AND org_unit_id='roster-other-class'",
                (cls.cross_member_id,),
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO member_org_relations"
                    "(member_id, org_unit_id, relation_type, is_primary, "
                    "source_type, created_at, updated_at) "
                    "VALUES (?, 'roster-other-class', 'STUDY_CLASS', 1, 'TEST', ?, ?)",
                    (cls.cross_member_id, now, now),
                )

    def test_options_publish_org_id_source_and_counts(self) -> None:
        data = roster_options(0)
        self.assertEqual(data["source"], "PLATFORM_ORG_RELATIONS")
        self.assertEqual(data["query_mode"], "ORG_UNIT_ID")
        self.assertEqual(data["fallback_mode"], "FAIL_CLOSED")
        roster_class = next(
            row for row in data["classes"] if row["id"] == "roster-class"
        )
        self.assertEqual(roster_class["member_count"], 1)

    def test_members_are_scoped_by_relation_org_id(self) -> None:
        rows = roster_members(0, group_org_unit_id="roster-group")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relation_org_id"], "roster-group")
        self.assertEqual(rows[0]["relation_type"], "STUDY_GROUP")
        self.assertEqual(rows[0]["class_name"], "名单测试班")
        self.assertEqual(rows[0]["group_name"], "名单测试组")

    def test_integrity_summary_is_aggregate_and_passes(self) -> None:
        data = roster_integrity_summary()
        self.assertEqual(data["source"], "PLATFORM_ORG_RELATIONS")
        self.assertEqual(data["group_class_mismatch_count"], 0)
        self.assertEqual(data["invalid_relation_count"], 0)
        self.assertTrue(data["passed"])
        self.assertNotIn("members", data)

    def test_cross_class_members_require_exact_name_and_exclude_event_class(self) -> None:
        rows = cross_class_members(
            name="跨班测试学长",
            event_class_org_unit_id="roster-class",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["member_id"], self.cross_member_id)
        self.assertEqual(rows[0]["home_class_org_unit_id"], "roster-other-class")
        self.assertEqual(rows[0]["home_class_name"], "外班名单测试班")
        self.assertNotIn("phone", rows[0])
        self.assertEqual(
            cross_class_members(
                name="名单测试学长",
                event_class_org_unit_id="roster-class",
            ),
            [],
            "当前已属于活动班级的学长不能被作为外班候选",
        )
        self.assertEqual(
            cross_class_members(
                name="跨班",
                event_class_org_unit_id="roster-class",
            ),
            [],
        )

    def test_cross_class_lookup_api_requires_key_and_omits_phone(self) -> None:
        with patch.dict("os.environ", {"SIGNIN_SERVICE_API_KEY": "cross-test-key"}):
            with TestClient(app) as client:
                denied = client.get(
                    "/api/v1/checkin-rosters/cross-class-members",
                    params={
                        "name": "跨班测试学长",
                        "event_class_org_unit_id": "roster-class",
                    },
                )
                self.assertEqual(denied.status_code, 401)
                allowed = client.get(
                    "/api/v1/checkin-rosters/cross-class-members",
                    params={
                        "name": "跨班测试学长",
                        "event_class_org_unit_id": "roster-class",
                    },
                    headers={"X-API-Key": "cross-test-key"},
                )
                self.assertEqual(allowed.status_code, 200)
                member = allowed.json()["data"]["members"][0]
                self.assertEqual(member["home_class_name"], "外班名单测试班")
                self.assertNotIn("phone", member)

    def test_scheduled_sync_requires_service_key(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {"SIGNIN_SERVICE_API_KEY": "scheduled-test-key"},
            ),
            patch(
                "app.api.attendance._run_sync",
                return_value={"success": True, "data": {"status": "SUCCESS"}},
            ),
        ):
            with TestClient(app) as client:
                denied = client.post("/api/v1/attendance/sync/scheduled")
                self.assertEqual(denied.status_code, 401)
                allowed = client.post(
                    "/api/v1/attendance/sync/scheduled",
                    headers={"X-API-Key": "scheduled-test-key"},
                )
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.json()["data"]["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
