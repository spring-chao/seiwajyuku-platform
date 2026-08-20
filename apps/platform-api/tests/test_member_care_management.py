from __future__ import annotations

import json
from datetime import date, datetime, UTC

import pytest

from app.api import member_care_management as management_api
from app.db import execute, fetch_one, transaction
from app.services.member_care_actions import build_member_care_actions
from app.services.member_care_management import (
    build_member_care_management_overview,
)
from test_member_care_actions import _care_fixture


def _augment_management_facts(fixture: dict[str, int | str]) -> None:
    center_id = str(fixture["center_id"])
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    all_member = fetch_one("SELECT id FROM members WHERE name='三类关爱学长' ORDER BY id DESC LIMIT 1")
    overdue_member = fetch_one("SELECT id FROM members WHERE name='逾期跟进学长' ORDER BY id DESC LIMIT 1")
    birthday_member = fetch_one("SELECT id FROM members WHERE name='生日关怀学长' ORDER BY id DESC LIMIT 1")
    assert all_member and overdue_member and birthday_member
    all_cycle = fetch_one(
        "SELECT id FROM renewal_cycles WHERE member_id=? AND renewal_year=2099",
        (all_member["id"],),
    )
    overdue_cycle = fetch_one(
        "SELECT id FROM renewal_cycles WHERE member_id=? AND renewal_year=2099",
        (overdue_member["id"],),
    )
    birthday_item = fetch_one(
        "SELECT cycle_id, node_id FROM operation_items WHERE business_type='BIRTHDAY_CARE' LIMIT 1"
    )
    assert all_cycle and birthday_item
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        if not overdue_cycle:
            overdue_cycle_id = execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) VALUES (?, 2099, ?, 11, 'IN_COMMUNICATION', ?, ?)",
                (overdue_member["id"], center_id, now, now),
            ).lastrowid
        else:
            overdue_cycle_id = overdue_cycle["id"]
        execute(
            connection,
            "INSERT INTO renewal_followups(renewal_cycle_id, followed_at, followed_by, channel, summary, intention, needs_support, next_action, next_followup_at, created_at) "
            "VALUES (?, '2099-08-18T09:00:00+00:00', ?, 'PHONE', '需要内部协助', '待确认', 1, NULL, NULL, ?)",
            (all_cycle["id"], admin_id, now),
        )
        execute(
            connection,
            "INSERT INTO renewal_followups(renewal_cycle_id, followed_at, followed_by, channel, summary, intention, needs_support, next_action, next_followup_at, created_at) "
            "VALUES (?, '2099-08-19T09:00:00+00:00', ?, 'PHONE', '昨天约定联系', '待确认', 0, NULL, '2099-08-19', ?)",
            (overdue_cycle_id, admin_id, now),
        )
        execute(
            connection,
            "INSERT INTO followup_tasks(member_id, org_unit_id, task_type, service_purpose, assigned_user_id, status, confidentiality_level, due_at, created_by, created_at, updated_at) "
            "VALUES (?, ?, 'VISIT', '企业走访安排', ?, 'OPEN', 'ORG_MANAGERS', '2099-08-19', ?, ?, ?)",
            (all_member["id"], center_id, admin_id, admin_id, now, now),
        )
        execute(
            connection,
            "INSERT INTO operation_items(cycle_id, node_id, org_unit_id, period, item_key, title, category, status, start_date, due_date, business_type, business_id, created_at, updated_at) "
            "VALUES (?, ?, ?, '2099-08', ?, '生日学长关怀（管理测试）', '学长关怀', 'PENDING', '2099-08-01', '2099-08-10', 'BIRTHDAY_CARE', ?, ?, ?)",
            (
                birthday_item["cycle_id"],
                birthday_item["node_id"],
                center_id,
                f"BIRTHDAY_CARE:management:{overdue_member['id']}",
                str(overdue_member["id"]),
                now,
                now,
            ),
        )
        recovery_member_id = execute(
            connection,
            "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) VALUES (?, '挽回管理学长', ?, 'ACTIVE', ?, ?)",
            (f"CARE-RECOVERY-{center_id}", center_id, now, now),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) VALUES (?, 2099, ?, 7, 'DEFERRED', ?, ?)",
            (recovery_member_id, center_id, now, now),
        )


def test_management_overview_aggregates_exceptions_and_deduplicates_people() -> None:
    fixture = _care_fixture()
    _augment_management_facts(fixture)
    result = build_member_care_management_overview(
        int(fixture["user_id"]), as_of=date(2099, 8, 20)
    )

    assert result["summary"] == {
        "today_care_people_count": 4,
        "today_care_action_count": 9,
        "overdue_people_count": 2,
        "oldest_overdue_days": 10,
        "renewal_support_needed_count": 1,
        "renewal_recovery_open_count": 1,
        "renewal_unassigned_count": 3,
        "followup_no_schedule_count": 1,
        "renewal_overdue_count": 1,
        "followup_overdue_count": 1,
        "enterprise_visit_overdue_count": 1,
        "birthday_overdue_count": 1,
    }
    assert len(result["organizations"]) == 1
    assert result["organizations"][0]["overdue_people_count"] == 2
    assert result["organizations"][0]["oldest_overdue_days"] == 10
    assert {item["exception_type"] for item in result["exceptions"]} == {
        "CARE_OVERDUE",
        "RENEWAL_RECOVERY_OPEN",
        "RENEWAL_SUPPORT_NEEDED",
        "RENEWAL_STAGE_UNTOUCHED",
        "RENEWAL_UNASSIGNED",
        "FOLLOWUP_NO_SCHEDULE",
    }
    assert sum(item["exception_type"] == "CARE_OVERDUE" for item in result["exceptions"]) == 4
    assert result["exceptions"][0]["exception_type"] == "CARE_OVERDUE"

    care_people = build_member_care_actions(
        int(fixture["user_id"]), as_of=date(2099, 8, 20)
    )["people"]
    assert len(care_people) == result["summary"]["today_care_people_count"]
    assert len({person["member_id"] for person in care_people}) == 4


def test_management_overview_source_coverage_is_not_reported_as_zero() -> None:
    fixture = _care_fixture()
    read_only = build_member_care_management_overview(
        int(fixture["read_only_user_id"]), as_of=date(2099, 8, 20)
    )
    assert read_only["source_coverage"] == {
        "renewal": {"accessible": True},
        "followup": {"accessible": False},
        "birthday": {"accessible": True},
    }
    assert read_only["summary"]["followup_no_schedule_count"] is None
    assert read_only["summary"]["followup_overdue_count"] is None
    assert read_only["summary"]["enterprise_visit_overdue_count"] is None
    assert read_only["organizations"][0]["followup_no_schedule_count"] is None

    with pytest.raises(PermissionError):
        build_member_care_management_overview(
            int(fixture["no_source_user_id"]), as_of=date(2099, 8, 20)
        )


def test_management_overview_org_scope_and_privacy() -> None:
    fixture = _care_fixture()
    result = build_member_care_management_overview(
        int(fixture["user_id"]),
        as_of=date(2099, 8, 20),
        org_unit_id=str(fixture["center_id"]),
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert "phone" not in serialized.lower()
    assert "service_purpose" not in serialized
    assert "subject_statement" not in serialized
    assert "objective_facts" not in serialized
    assert "staff_judgment" not in serialized
    with pytest.raises(PermissionError):
        build_member_care_management_overview(
            int(fixture["user_id"]),
            as_of=date(2099, 8, 20),
            org_unit_id="org-management-outside-scope",
        )


def test_management_overview_api_returns_read_only_payload_and_403_without_source() -> None:
    fixture = _care_fixture()
    payload = management_api.management_overview(
        as_of=date(2099, 8, 20),
        org_unit_id=None,
        user={"id": int(fixture["user_id"])},
    )
    assert payload["success"] is True
    assert "organizations" in payload["data"]
    with pytest.raises(management_api.HTTPException) as exc:
        management_api.management_overview(
            as_of=date(2099, 8, 20),
            org_unit_id=None,
            user={"id": int(fixture["no_source_user_id"])},
        )
    assert exc.value.status_code == 403
