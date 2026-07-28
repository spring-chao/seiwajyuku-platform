from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from app.api.attendance import AdjudicationPayload, create_adjudication
from app.db import execute, fetch_one, transaction
from app.migrations import run_migrations
from app.services.attendance_scoring import (
    calculate_score,
    recalculate_event_group,
    upsert_score_record,
)
from app.services.attendance_sync import _upsert_record, sync_from_signin
from app.services.iam import seed_iam
from app.services.members import create_member


class AttendanceScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        run_migrations()
        seed_iam()
        cls.admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            if not execute(
                connection,
                "SELECT id FROM org_units WHERE id='attendance-center'",
            ).fetchone():
                execute(
                    connection,
                    "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, "
                    "is_active, created_at, updated_at) VALUES "
                    "('attendance-center', 'ATTENDANCE_CENTER', '出勤测试中心', "
                    "'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                    (now, now),
                )
        member = fetch_one(
            "SELECT id FROM members WHERE member_code='ATTENDANCE-001'"
        )
        cls.member_id = (
            member["id"]
            if member
            else create_member(
                cls.admin["id"],
                member_code="ATTENDANCE-001",
                name="出勤测试学长",
                org_unit_id="attendance-center",
                development_org_unit_id=None,
                phone="13600136000",
            )
        )

        with transaction() as connection:
            group = execute(
                connection,
                "INSERT INTO attendance_event_groups"
                "(source_key, external_group_id, org_unit_id, title, activity_type, "
                "event_date, status, created_at, updated_at) "
                "VALUES ('test', 'attendance-group-001', 'attendance-center', "
                "'出勤测试活动', 'class_meeting', '2026-07-28', 'ACTIVE', ?, ?)",
                (now, now),
            )
            cls.group_id = group.lastrowid
            session = execute(
                connection,
                "INSERT INTO attendance_sessions"
                "(event_group_id, external_session_id, session_code, session_name, "
                "session_order, scheduled_start_at, status, created_at, updated_at) "
                "VALUES (?, 'attendance-session-001', 'MORNING', '上午', 1, "
                "'2026-07-28T09:00:00+00:00', 'ACTIVE', ?, ?)",
                (cls.group_id, now, now),
            )
            cls.session_id = session.lastrowid

    def test_mysql_decimal_rule_values_are_json_serializable(self) -> None:
        rule = {
            "id": 99,
            "rule_version": 1,
            "base_points": Decimal("7.00"),
            "late_deduction": Decimal("1.00"),
            "early_leave_deduction": Decimal("1.00"),
        }
        with (
            patch(
                "app.services.attendance_scoring.get_active_rule",
                return_value=rule,
            ),
            patch(
                "app.services.attendance_scoring.has_active_early_leave",
                return_value=False,
            ),
        ):
            score = calculate_score(
                attendance_record_id=1,
                member_id=1,
                session_code="MORNING",
                attendance_status="PRESENT",
                checked_at="2026-07-29T09:05:00+00:00",
                scheduled_start_at="2026-07-29T09:00:00+00:00",
                score_eligible=True,
            )
            datetime_score = calculate_score(
                attendance_record_id=1,
                member_id=1,
                session_code="MORNING",
                attendance_status="PRESENT",
                checked_at=datetime(2026, 7, 29, 9, 5, tzinfo=UTC),
                scheduled_start_at=datetime(2026, 7, 29, 9, 0, tzinfo=UTC),
                score_eligible=True,
            )
        self.assertEqual(score["final_points"], 6.0)
        self.assertIn('"base_points": 7.0', score["calculation_detail_json"])
        self.assertEqual(datetime_score["final_points"], 6.0)

    def _create_record(
        self,
        external_id: str,
        *,
        status: str = "PRESENT",
        member_code: str = "ATTENDANCE-001",
        checked_at: str | None = "2026-07-28T08:59:00+00:00",
    ) -> tuple[int, int | None, str, bool]:
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            record_id, _, member_id, effective_status, eligible = _upsert_record(
                connection,
                session_id=self.session_id,
                record_data={
                    "external_record_id": external_id,
                    "member_code": member_code,
                    "name": "出勤测试学长",
                    "attendance_status": status,
                    "checked_at": checked_at,
                    "revision": 1,
                },
                now=now,
            )
        return record_id, member_id, effective_status, eligible

    def test_member_code_is_resolved_and_unknown_member_is_unmatched(self) -> None:
        _, member_id, status, eligible = self._create_record(
            "attendance-record-match"
        )
        self.assertEqual(member_id, self.member_id)
        self.assertEqual(status, "PRESENT")
        self.assertTrue(eligible)

        _, member_id, status, eligible = self._create_record(
            "attendance-record-unmatched",
            member_code="UNKNOWN-MEMBER",
        )
        self.assertIsNone(member_id)
        self.assertEqual(status, "UNMATCHED")
        self.assertFalse(eligible)

    def test_lowercase_activity_recalculates_and_early_leave_can_cancel(self) -> None:
        record_id, member_id, status, eligible = self._create_record(
            "attendance-record-adjudication"
        )
        upsert_score_record(
            attendance_record_id=record_id,
            member_id=member_id,
            session_code="MORNING",
            attendance_status=status,
            checked_at="2026-07-28T08:59:00+00:00",
            scheduled_start_at="2026-07-28T09:00:00+00:00",
            score_eligible=eligible,
            activity_type="class_meeting",
        )
        recalculate_event_group(self.group_id)
        self.assertEqual(
            fetch_one(
                "SELECT final_points FROM attendance_score_records "
                "WHERE attendance_record_id=?",
                (record_id,),
            )["final_points"],
            7,
        )

        create_adjudication(
            record_id,
            AdjudicationPayload(
                adjudication_type="EARLY_LEAVE",
                reason="现场确认提前离场",
            ),
            {"id": self.admin["id"]},
        )
        self.assertEqual(
            fetch_one(
                "SELECT final_points FROM attendance_score_records "
                "WHERE attendance_record_id=?",
                (record_id,),
            )["final_points"],
            6,
        )
        create_adjudication(
            record_id,
            AdjudicationPayload(
                adjudication_type="CANCEL_EARLY_LEAVE",
                reason="复核后撤销早退裁定",
            ),
            {"id": self.admin["id"]},
        )
        self.assertEqual(
            fetch_one(
                "SELECT final_points FROM attendance_score_records "
                "WHERE attendance_record_id=?",
                (record_id,),
            )["final_points"],
            7,
        )

    def test_manual_checkin_changes_status_and_score(self) -> None:
        record_id, _, _, _ = self._create_record(
            "attendance-record-manual",
            status="ABSENT",
            checked_at=None,
        )
        create_adjudication(
            record_id,
            AdjudicationPayload(
                adjudication_type="MANUAL_CHECKIN",
                reason="纸质签到表核验补签",
            ),
            {"id": self.admin["id"]},
        )
        record = fetch_one(
            "SELECT attendance_status FROM attendance_records WHERE id=?",
            (record_id,),
        )
        score = fetch_one(
            "SELECT final_points FROM attendance_score_records "
            "WHERE attendance_record_id=?",
            (record_id,),
        )
        self.assertEqual(record["attendance_status"], "MANUAL_PRESENT")
        self.assertEqual(score["final_points"], 7)

    def test_sync_consumes_all_record_pages(self) -> None:
        class FakeResponse:
            def __init__(self, payload: dict) -> None:
                self.payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return self.payload

        class FakeClient:
            record_calls = 0

            def __init__(self, **_: object) -> None:
                pass

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def get(
                self, url: str, *, params: dict, headers: dict
            ) -> FakeResponse:
                self.assert_headers(headers)
                if url.endswith("/sessions"):
                    return FakeResponse(
                        {
                            "items": [
                                {
                                    "session_id": "paged-session-001",
                                    "external_session_id": "paged-session-001",
                                    "session_code": "MORNING",
                                    "session_name": "上午",
                                    "session_order": 1,
                                    "scheduled_start_at": "2026-07-29T09:00:00+00:00",
                                    "event_group": {
                                        "external_group_id": "paged-group-001",
                                        "org_unit_id": "attendance-center",
                                        "event_date": "2026-07-29",
                                        "activity_type": "class_meeting",
                                        "title": "分页同步测试",
                                    },
                                }
                            ],
                            "next_cursor": None,
                        }
                    )
                FakeClient.record_calls += 1
                page = 2 if params.get("cursor") else 1
                return FakeResponse(
                    {
                        "items": [
                            {
                                "external_record_id": f"paged-record-{page}",
                                "member_code": "ATTENDANCE-001",
                                "name": "出勤测试学长",
                                "attendance_status": "PRESENT",
                                "checked_at": "2026-07-29T08:59:00+00:00",
                                "revision": 1,
                            }
                        ],
                        "next_cursor": "page-2" if page == 1 else None,
                    }
                )

            @staticmethod
            def assert_headers(headers: dict) -> None:
                if headers.get("X-API-Key") != "attendance-test-key":
                    raise AssertionError("missing service API key")

        with (
            patch.dict(
                "os.environ",
                {
                    "SIGNIN_API_BASE_URL": "https://signin.test",
                    "SIGNIN_SERVICE_API_KEY": "attendance-test-key",
                },
            ),
            patch(
                "app.services.attendance_sync.httpx.Client",
                FakeClient,
            ),
        ):
            result = sync_from_signin()
        self.assertEqual(result["received_records"], 2)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(FakeClient.record_calls, 2)


if __name__ == "__main__":
    unittest.main()
