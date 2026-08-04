from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app.api.attendance import event_group_detail, list_event_groups
from app.db import execute, fetch_one, transaction
from app.migrations import run_migrations
from app.services.iam import seed_iam
from app.services.members import create_member


class AttendanceParticipationRateTests(unittest.TestCase):
    """Verify class and regional participation counts use organization relations."""

    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(
                connection,
                "INSERT OR IGNORE INTO org_units"
                "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES ('participation-rate-center', 'PARTICIPATION_RATE_CENTER', '参会率测试分中心', "
                "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (now, now),
            )
            execute(
                connection,
                "INSERT OR IGNORE INTO org_units"
                "(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES ('participation-rate-class', 'PARTICIPATION_RATE_CLASS', '参会率测试班', "
                "'CLASS', 'participation-rate-center', 1, ?, ?)",
                (now, now),
            )

        cls.member_ids = []
        for code, name in (
            ("PARTICIPATION-RATE-001", "参会率测试学员一"),
            ("PARTICIPATION-RATE-002", "参会率测试学员二"),
            ("PARTICIPATION-RATE-003", "参会率测试学员三"),
        ):
            member = fetch_one("SELECT id FROM members WHERE member_code=?", (code,))
            member_id = (
                member["id"]
                if member
                else create_member(
                    cls.admin["id"],
                    member_code=code,
                    name=name,
                    org_unit_id="participation-rate-center",
                    development_org_unit_id=None,
                    phone=None,
                )
            )
            cls.member_ids.append(member_id)

        with transaction() as connection:
            now = datetime.now(UTC).isoformat()
            for member_id in cls.member_ids[:2]:
                execute(
                    connection,
                    "INSERT OR IGNORE INTO member_org_relations"
                    "(member_id, org_unit_id, relation_type, is_primary, source_type, created_at, updated_at) "
                    "VALUES (?, 'participation-rate-class', 'STUDY_CLASS', 1, 'TEST', ?, ?)",
                    (member_id, now, now),
                )

            cls.group_ids = []
            for external_id, title, activity_type, study_org_unit_id in (
                (
                    "participation-rate-class-group",
                    "参会率测试班级活动",
                    "CLASS_MEETING",
                    "participation-rate-class",
                ),
                (
                    "participation-rate-report-group",
                    "参会率测试分中心报告会",
                    "CENTER_QUARTERLY_REPORT",
                    None,
                ),
            ):
                group = execute(
                    connection,
                    "INSERT INTO attendance_event_groups"
                    "(source_key, external_group_id, org_unit_id, study_org_unit_id, title, activity_type, "
                    "event_date, status, created_at, updated_at) "
                    "VALUES ('participation-rate-test', ?, 'participation-rate-center', ?, ?, ?, '2026-08-04', 'ACTIVE', ?, ?)",
                    (external_id, study_org_unit_id, title, activity_type, now, now),
                )
                group_id = group.lastrowid
                cls.group_ids.append(group_id)
                session = execute(
                    connection,
                    "INSERT INTO attendance_sessions"
                    "(event_group_id, external_session_id, session_code, session_name, session_order, status, created_at, updated_at) "
                    "VALUES (?, ?, 'MORNING', '上午', 1, 'ACTIVE', ?, ?)",
                    (group_id, f"{external_id}-session", now, now),
                )
                session_id = session.lastrowid
                for index, member_id in enumerate(cls.member_ids[:2]):
                    execute(
                        connection,
                        "INSERT INTO attendance_records"
                        "(attendance_session_id, external_record_id, member_id, member_code_snapshot, name_snapshot, "
                        "participant_type, score_eligible, attendance_status, received_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 'MEMBER', 1, ?, ?, ?, ?)",
                        (
                            session_id,
                            f"{external_id}-member-{index}",
                            member_id,
                            f"PARTICIPATION-RATE-00{index + 1}",
                            f"参会率测试学员{['一', '二'][index]}",
                            "PRESENT" if index == 0 else "ABSENT",
                            now,
                            now,
                            now,
                        ),
                    )
                if activity_type == "CLASS_MEETING":
                    execute(
                        connection,
                        "INSERT INTO attendance_records"
                        "(attendance_session_id, external_record_id, member_id, member_code_snapshot, name_snapshot, "
                        "participant_type, score_eligible, attendance_status, received_at, created_at, updated_at) "
                        "VALUES (?, ?, NULL, NULL, '参会率测试非塾生嘉宾', 'GUEST', 0, 'PRESENT', ?, ?, ?)",
                        (session_id, f"{external_id}-guest-1", now, now, now),
                    )
                if activity_type.startswith("CENTER_"):
                    execute(
                        connection,
                        "INSERT INTO attendance_records"
                        "(attendance_session_id, external_record_id, member_id, member_code_snapshot, name_snapshot, "
                        "participant_type, score_eligible, attendance_status, received_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 'MEMBER', 1, 'PRESENT', ?, ?, ?)",
                        (
                            session_id,
                            f"{external_id}-member-3",
                            cls.member_ids[2],
                            "PARTICIPATION-RATE-003",
                            "参会率测试学员三",
                            now,
                            now,
                            now,
                        ),
                    )

    def test_event_groups_include_both_scopes(self) -> None:
        response = list_event_groups(
            month="2026-08", user={"id": self.admin["id"]}
        )
        rows = {row["title"]: row for row in self._detail_rows(response)}
        class_row = rows["参会率测试班级活动"]
        report_row = rows["参会率测试分中心报告会"]
        self.assertEqual(
            (class_row["class_member_count"], class_row["class_present_count"]),
            (2, 1),
        )
        self.assertEqual(
            (class_row["record_count"], class_row["present_count"]),
            (3, 2),
        )
        self.assertEqual(
            (report_row["region_member_count"], report_row["region_present_count"]),
            (3, 2),
        )

    def test_event_group_detail_includes_session_scope_counts(self) -> None:
        response = event_group_detail(
            self.group_ids[0], user={"id": self.admin["id"]}
        )
        session = response["data"]["sessions"][0]
        self.assertEqual(session["class_member_count"], 2)
        self.assertEqual(session["class_present_count"], 1)
        self.assertEqual((session["record_count"], session["present_count"]), (3, 2))

        report_response = event_group_detail(
            self.group_ids[1], user={"id": self.admin["id"]}
        )
        report_breakdown = report_response["data"]["class_breakdown"]
        self.assertEqual(len(report_breakdown), 1)
        self.assertEqual(report_breakdown[0]["class_name"], "参会率测试班")
        self.assertEqual(
            (
                report_breakdown[0]["class_member_count"],
                report_breakdown[0]["class_present_count"],
            ),
            (2, 1),
        )

    @staticmethod
    def _detail_rows(response: dict) -> list[dict]:
        rows = response["data"]
        # Use the known test titles to identify the two rows without relying on IDs.
        return [row for row in rows if row["title"] in {"参会率测试班级活动", "参会率测试分中心报告会"}]


if __name__ == "__main__":
    unittest.main()
