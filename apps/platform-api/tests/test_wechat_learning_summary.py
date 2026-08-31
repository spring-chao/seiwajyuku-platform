from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import execute, transaction
from app.main import app
from test_v12_mvp import _seed_group_leader_fixture


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _insert_learning_facts(fixture: dict) -> None:
    suffix = fixture["suffix"]
    now = _stamp()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE org_units SET name=? WHERE id=?",
            ("吴越二班", fixture["class_id"]),
        )
        execute(
            connection,
            "UPDATE org_units SET name=? WHERE id=?",
            ("卓越组", fixture["group_id"]),
        )

        event_group = execute(
            connection,
            "INSERT INTO attendance_event_groups "
            "(source_key, external_group_id, org_unit_id, study_org_unit_id, title, "
            "activity_type, event_date, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'CLASS_MEETING', ?, 'ACTIVE', ?, ?)",
            (
                f"test-attendance-{suffix}",
                f"event-{suffix}",
                fixture["class_id"],
                fixture["group_id"],
                "班级月度学习会",
                "2026-08-28",
                now,
                now,
            ),
        )
        event_group_id = int(event_group.lastrowid)
        for code, name in (("MORNING", "上午"), ("AFTERNOON", "下午")):
            session = execute(
                connection,
                "INSERT INTO attendance_sessions "
                "(event_group_id, external_session_id, session_code, session_name, "
                "session_order, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                (event_group_id, f"{suffix}-{code}", code, name, 1, now, now),
            )
            execute(
                connection,
                "INSERT INTO attendance_records "
                "(attendance_session_id, external_record_id, member_id, participant_type, "
                "score_eligible, attendance_status, checked_at, received_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'MEMBER', 1, 'PRESENT', ?, ?, ?, ?)",
                (
                    int(session.lastrowid),
                    f"{suffix}-{code}-member",
                    fixture["member_id"],
                    "2026-08-28T09:00:00+00:00",
                    now,
                    now,
                    now,
                ),
            )
            execute(
                connection,
                "INSERT INTO attendance_records "
                "(attendance_session_id, external_record_id, member_id, participant_type, "
                "score_eligible, attendance_status, checked_at, received_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'MEMBER', 1, 'PRESENT', ?, ?, ?, ?)",
                (
                    int(session.lastrowid),
                    f"{suffix}-{code}-other",
                    fixture["other_member_id"],
                    "2026-08-28T09:01:00+00:00",
                    now,
                    now,
                    now,
                ),
            )

        meeting = execute(
            connection,
            "INSERT INTO study_meeting_sessions "
            "(session_code, class_org_unit_id, study_group_org_unit_id, learning_cycle_id, "
            "meeting_date, created_by_member_id, created_by_role, has_course, course_key, "
            "course_name_snapshot, course_credit_snapshot, status, submitted_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'GROUP_LEADER', 1, 'course-test', ?, NULL, 'SUBMITTED', ?, ?, ?)",
            (
                f"study-meeting-{suffix}",
                fixture["class_id"],
                fixture["group_id"],
                fixture["learning_cycle_id"],
                "2026-08-23",
                fixture["member_id"],
                "经营十二条",
                now,
                now,
                now,
            ),
        )
        meeting_id = int(meeting.lastrowid)
        execute(
            connection,
            "INSERT INTO study_meeting_attendances "
            "(study_meeting_session_id, member_id, home_study_group_org_unit_id, "
            "attended_study_group_org_unit_id, attendance_type, added_by_member_id, "
            "added_by_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'HOME_GROUP', ?, NULL, ?, ?)",
            (
                meeting_id,
                fixture["member_id"],
                fixture["group_id"],
                fixture["group_id"],
                fixture["member_id"],
                now,
                now,
            ),
        )
        execute(
            connection,
            "INSERT INTO study_meeting_attendances "
            "(study_meeting_session_id, member_id, home_study_group_org_unit_id, "
            "attended_study_group_org_unit_id, attendance_type, added_by_member_id, "
            "added_by_user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'HOME_GROUP', ?, NULL, ?, ?)",
            (
                meeting_id,
                fixture["other_member_id"],
                fixture["other_group_id"],
                fixture["group_id"],
                fixture["member_id"],
                now,
                now,
            ),
        )
        execute(
            connection,
            "INSERT INTO study_meeting_courses "
            "(study_meeting_session_id, course_key, course_name_snapshot, "
            "course_credit_snapshot, course_rule_status, rule_reference_json, created_at, updated_at) "
            "VALUES (?, 'course-test', '经营十二条', NULL, 'PENDING', '{}', ?, ?)",
            (meeting_id, now, now),
        )

        batch = execute(
            connection,
            "INSERT INTO import_batches "
            "(import_type, source_name, source_sha256, status, preview_json, created_at) "
            "VALUES ('TEST', ?, ?, 'APPLIED', '{}', ?)",
            (f"learning-summary-{suffix}", uuid4().hex, now),
        )
        execute(
            connection,
            "INSERT INTO member_activity_facts "
            "(source_system, source_table, external_id, member_id, org_unit_id, activity_type, "
            "occurred_on, participation_status, title, import_batch_id, imported_at) "
            "VALUES ('legacy-test', 'class_sessions', ?, ?, ?, 'CLASS_MEETING', ?, 'COMPLETED', ?, ?, ?)",
            (
                f"legacy-duplicate-{suffix}",
                fixture["member_id"],
                fixture["class_id"],
                "2026-08-28",
                "班级月度学习会",
                int(batch.lastrowid),
                now,
            ),
        )
        execute(
            connection,
            "INSERT INTO member_activity_facts "
            "(source_system, source_table, external_id, member_id, org_unit_id, activity_type, "
            "occurred_on, participation_status, title, import_batch_id, imported_at) "
            "VALUES ('legacy-test', 'reading_shares', ?, ?, ?, 'READING_SHARE', ?, 'RECORDED', ?, ?, ?)",
            (
                f"legacy-reading-{suffix}",
                fixture["member_id"],
                fixture["class_id"],
                "2026-08-18",
                "八月读书分享",
                int(batch.lastrowid),
                now,
            ),
        )


def _bind(fixture: dict, client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/wechat/member-bindings/verify",
        json={"code": "learning-summary-code", "name": "V1.2组长", "phone": fixture["phone"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_learning_summary_requires_bound_session_and_isolates_member() -> None:
    fixture = _seed_group_leader_fixture()
    _insert_learning_facts(fixture)
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v11a-learning-test",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v11a-learning-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "v11a-learning-test", "openid": f"learning-{fixture['suffix']}"},
    ), TestClient(app) as client:
        assert client.get("/api/v1/wechat/learning-summary").status_code == 401
        headers = _bind(fixture, client)
        response = client.get(
            f"/api/v1/wechat/learning-summary?member_id={fixture['foreign_member_id']}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]

        assert data["current_learning"] == [
            {
                "class_name": "吴越二班",
                "group_name": "卓越组",
                "plan_name": "V1.2测试计划",
                "cycle_index": 1,
                "cycle_label": "第1周期",
                "year_index": 1,
                "status_name": "进行中",
            }
        ]
        records = data["recent_learning"]
        assert [item["title"] for item in records] == [
            "班级月度学习会",
            "小组学习会 · 经营十二条",
            "八月读书分享",
        ]
        assert records[0]["source_type"] == "签到活动"
        assert records[1]["source_type"] == "小组学习会记录"
        assert records[2]["source_type"] == "历史学习事实"
        assert all(item["status_name"] in {"已参加", "已完成", "已记录"} for item in records)
        assert all("final_points" not in item and "credit_points" not in item for item in records)
        assert all("member_id" not in item and "id" not in item for item in records)
        assert all("source_id" not in item for item in records)
        assert all(
            set(item)
            == {
                "occurred_at",
                "learning_type",
                "title",
                "class_name",
                "group_name",
                "source_type",
                "status_name",
            }
            for item in records
        )


def test_learning_summary_returns_explicit_empty_state_without_facts() -> None:
    fixture = _seed_group_leader_fixture()
    with patch.dict(
        os.environ,
        {
            "WECHAT_MEMBER_BINDING_ENABLED": "true",
            "WECHAT_MINIPROGRAM_APP_ID": "v11a-learning-empty-test",
            "WECHAT_MINIPROGRAM_APP_SECRET": "v11a-learning-empty-secret",
        },
    ), patch(
        "app.services.wechat_identity.exchange_wechat_code",
        return_value={"appid": "v11a-learning-empty-test", "openid": f"empty-{fixture['suffix']}"},
    ), TestClient(app) as client:
        headers = _bind(fixture, client)
        response = client.get("/api/v1/wechat/learning-summary", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["recent_learning"] == []
        assert data["current_learning"][0]["status_name"] == "进行中"
