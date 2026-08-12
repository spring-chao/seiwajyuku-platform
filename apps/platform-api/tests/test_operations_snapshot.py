from __future__ import annotations

from datetime import UTC, datetime

from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.plans import operations_snapshot


def _insert_member(
    *, code: str, name: str, org_id: str, join_date: str | None, birthday: str | None
) -> int:
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        return execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, join_date, birthday, "
            "created_at, updated_at) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
            (code, name, org_id, join_date, birthday, now, now),
        ).lastrowid


def test_operations_snapshot_uses_master_facts_and_event_groups() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        for org_id, code, name in (
            ("snapshot-center-a", "SNAPSHOT_A", "驾驶舱测试分中心A"),
            ("snapshot-center-b", "SNAPSHOT_B", "驾驶舱测试分中心B"),
        ):
            execute(
                connection,
                "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, 'REGIONAL_CENTER', "
                "'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, "
            "is_active, created_at, updated_at) VALUES "
            "('snapshot-class-a', 'SNAPSHOT_CLASS_A', '驾驶舱一班', 'CLASS', "
            "'snapshot-center-a', 1, ?, ?)",
            (now, now),
        )
        execute(
            connection,
            "INSERT OR IGNORE INTO org_units(id, unit_code, name, unit_type, parent_id, "
            "is_active, created_at, updated_at) VALUES "
            "('snapshot-class-unscheduled', 'SNAPSHOT_CLASS_UNSCHEDULED', '驾驶舱待排期班', "
            "'CLASS', 'snapshot-center-a', 1, ?, ?)",
            (now, now),
        )

    renewed_id = _insert_member(
        code="SNAPSHOT-RENEWED",
        name="续费测试学长",
        org_id="snapshot-center-a",
        join_date="2026-08-03",
        birthday="1980-08-15",
    )
    _insert_member(
        code="SNAPSHOT-MISSING-JOIN",
        name="缺入塾日期学长",
        org_id="snapshot-center-a",
        join_date=None,
        birthday="1975-01-01",
    )
    _insert_member(
        code="SNAPSHOT-OUTSIDE",
        name="范围外学长",
        org_id="snapshot-center-b",
        join_date="2026-08-04",
        birthday="1970-08-20",
    )

    with transaction() as connection:
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, "
            "status, completed_at, created_at, updated_at) "
            "VALUES (?, 2099, 'snapshot-center-a', 8, 'RENEWED', ?, ?, ?)",
            (renewed_id, now, now, now),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, "
            "changed_by, created_at) VALUES (?, 'CONTACTED', 'RENEWED', ?, '2026-08-10T08:00:00')",
            (cycle_id, admin["id"]),
        )
        for external_id, title, activity_type, event_date in (
            ("snapshot-july-meeting", "七月班会", "CLASS_MEETING", "2026-07-05"),
            ("snapshot-aug-meeting", "八月班会", "CLASS_MEETING", "2026-08-05"),
            ("snapshot-course", "经营课程", "COURSE", "2026-08-12"),
            ("snapshot-activity", "分中心报告会", "CENTER_MONTHLY_REPORT", "2026-08-18"),
        ):
            group_id = execute(
                connection,
                "INSERT INTO attendance_event_groups(source_key, external_group_id, org_unit_id, "
                "study_org_unit_id, title, activity_type, event_date, status, created_at, updated_at) "
                "VALUES ('snapshot-test', ?, 'snapshot-center-a', 'snapshot-class-a', ?, ?, ?, "
                "'ACTIVE', ?, ?)",
                (external_id, title, activity_type, event_date, now, now),
            ).lastrowid
            execute(
                connection,
                "INSERT INTO attendance_sessions(event_group_id, external_session_id, session_code, "
                "session_name, session_order, status, created_at, updated_at) "
                "VALUES (?, ?, 'MORNING', '上午场', 1, 'ACTIVE', ?, ?)",
                (group_id, f"{external_id}-morning", now, now),
            )
            if external_id == "snapshot-aug-meeting":
                execute(
                    connection,
                    "INSERT INTO attendance_sessions(event_group_id, external_session_id, session_code, "
                    "session_name, session_order, status, created_at, updated_at) "
                    "VALUES (?, ?, 'AFTERNOON', '下午场', 2, 'ACTIVE', ?, ?)",
                    (group_id, f"{external_id}-afternoon", now, now),
                )

    scoped_user_id = create_user(
        admin["id"],
        username="snapshot-scoped-user",
        display_name="驾驶舱范围测试",
        password="snapshot-test-password",
        roles=["read_only"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": "snapshot-center-a"}],
    )
    result = operations_snapshot(user_id=scoped_user_id, year=2026, month=8)

    assert result["period"] == "2026-08"
    assert result["summary"]["renewed_member_count"] == 1
    assert result["summary"]["new_member_count"] == 1
    assert result["summary"]["birthday_member_count"] == 1
    assert result["summary"]["class_meeting_count"] == 1
    assert result["summary"]["course_count"] == 1
    assert result["summary"]["activity_count"] == 1
    assert result["class_meetings"][0]["year_sequence"] == 2
    assert {row["status"] for row in result["class_meeting_schedule"]} == {
        "SCHEDULED",
        "UNSCHEDULED",
    }
    assert result["data_quality"]["unscheduled_class_count"] == 1
    assert result["data_quality"]["unlinked_class_meeting_count"] == 0
    assert result["birthday_members"][0]["birthday"] == "08-15"
    assert result["data_quality"]["missing_join_date_count"] == 1
    assert result["data_quality"]["course_schedule_source_ready"] is True
    assert {row["id"] for row in result["centers"]} == {"snapshot-center-a"}


def test_operations_snapshot_hides_renewals_without_permission() -> None:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    user_id = create_user(
        admin["id"],
        username="snapshot-learning-user",
        display_name="驾驶舱学习测试",
        password="snapshot-learning-password",
        roles=["ops_center_learning"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": "snapshot-center-a"}],
    )

    result = operations_snapshot(user_id=user_id, year=2026, month=8)

    assert result["summary"]["renewed_member_count"] is None
    assert result["data_quality"]["renewal_source_authorized"] is False
