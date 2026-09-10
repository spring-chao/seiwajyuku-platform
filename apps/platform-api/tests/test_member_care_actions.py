from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.api import member_care_actions as member_care_api
from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.member_care_actions import (
    build_member_care_actions,
    complete_birthday_care,
)


def _care_fixture() -> dict[str, int | str]:
    suffix = uuid4().hex[:8]
    center_id = f"member-care-center-{suffix}"
    other_center_id = f"member-care-other-{suffix}"
    now = datetime.now(UTC).isoformat()
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    with transaction() as connection:
        for org_id, code, name in (
            (center_id, f"CARE_CENTER_{suffix}", "学长关爱测试分中心"),
            (other_center_id, f"CARE_OTHER_{suffix}", "学长关爱其他分中心"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
        member_ids: dict[str, int] = {}
        for key, name, org_id, birthday in (
            ("all", "三类关爱学长", center_id, "1980-08-23"),
            ("birthday", "生日关怀学长", center_id, "1980-08-20"),
            ("overdue", "逾期跟进学长", center_id, "1980-08-10"),
            ("other", "其他分中心学长", other_center_id, "1980-08-20"),
        ):
            member_ids[key] = int(
                execute(
                    connection,
                    "INSERT INTO members(member_code, name, org_unit_id, status, birthday, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)",
                    (f"CARE-MEMBER-{suffix}-{key}", name, org_id, birthday, now, now),
                ).lastrowid
            )

        cycle_id = int(
            execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
                "VALUES (?, 2099, ?, 11, 'IN_COMMUNICATION', ?, ?)",
                (member_ids["all"], center_id, now, now),
            ).lastrowid
        )
        other_cycle_id = int(
            execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
                "VALUES (?, 2099, ?, 11, 'IN_COMMUNICATION', ?, ?)",
                (member_ids["other"], other_center_id, now, now),
            ).lastrowid
        )
        closed_cycle_id = int(
            execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, status, created_at, updated_at) "
                "VALUES (?, 2099, ?, 11, 'RENEWED', ?, ?)",
                (member_ids["birthday"], center_id, now, now),
            ).lastrowid
        )

        followup_specs = [
            (member_ids["all"], center_id, "VISIT", "2099-08-20", "OPEN"),
            (member_ids["overdue"], center_id, "PHONE", "2099-08-19", "IN_PROGRESS"),
            (member_ids["birthday"], center_id, "CARE", None, "OPEN"),
            (member_ids["birthday"], center_id, "PHONE", "2099-08-18", "CLOSED"),
            (member_ids["other"], other_center_id, "PHONE", "2099-08-20", "OPEN"),
        ]
        followup_user_id = 1
        for member_id, org_id, task_type, due_at, status in followup_specs:
            execute(
                connection,
                "INSERT INTO followup_tasks(member_id, org_unit_id, task_type, service_purpose, assigned_user_id, status, confidentiality_level, due_at, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, '关怀沟通安排', ?, ?, 'ORG_MANAGERS', ?, ?, ?, ?)",
                (member_id, org_id, task_type, followup_user_id, status, due_at, admin_id, now, now),
            )

        template_id = int(
            execute(
                connection,
                "INSERT INTO operation_templates(template_code, name, scope_type, description, created_by, created_at, updated_at) "
                "VALUES (?, '关爱测试模板', 'CLASS', '测试', ?, ?, ?)",
                (f"CARE_TEMPLATE_{suffix}", admin_id, now, now),
            ).lastrowid
        )
        node_id = int(
            execute(
                connection,
                "INSERT INTO operation_template_nodes(template_id, node_code, title, category, rule_type, business_type, created_at, updated_at) "
                "VALUES (?, 'BIRTHDAY_CARE', '生日学长关怀', '学长关怀', 'BIRTHDAY_MONTH', 'BIRTHDAY_CARE', ?, ?)",
                (template_id, now, now),
            ).lastrowid
        )
        cycle = int(
            execute(
                connection,
                "INSERT INTO operation_cycles(template_id, period, org_unit_id, generated_by, created_at, updated_at) "
                "VALUES (?, '2099-08', ?, ?, ?, ?)",
                (template_id, center_id, admin_id, now, now),
            ).lastrowid
        )
        birthday_specs = [
            (member_ids["all"], "2099-08-16", "2099-08-23", "PENDING"),
            (member_ids["birthday"], "2099-08-13", "2099-08-20", "PENDING"),
            (member_ids["overdue"], "2099-08-01", "2099-08-10", "PENDING"),
        ]
        for index, (member_id, start_date, due_date, status) in enumerate(birthday_specs):
            execute(
                connection,
                "INSERT INTO operation_items(cycle_id, node_id, org_unit_id, period, item_key, title, category, status, start_date, due_date, business_type, business_id, created_at, updated_at) "
                "VALUES (?, ?, ?, '2099-08', ?, ?, '学长关怀', ?, ?, ?, 'BIRTHDAY_CARE', ?, ?, ?)",
                (
                    cycle,
                    node_id,
                    center_id,
                    f"BIRTHDAY_CARE:{member_id}:{index}",
                    "生日学长关怀",
                    status,
                    start_date,
                    due_date,
                    str(member_id),
                    now,
                    now,
                ),
            )

    scoped_user_id = create_user(
        admin_id,
        username=f"member-care-user-{suffix}",
        display_name="学长关爱测试账号",
        password="member-care-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    read_only_user_id = create_user(
        admin_id,
        username=f"member-care-readonly-{suffix}",
        display_name="学长关爱只读测试账号",
        password="member-care-test-password",
        roles=["read_only"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    followup_only_user_id = create_user(
        admin_id,
        username=f"member-care-followup-{suffix}",
        display_name="学长关爱跟进测试账号",
        password="member-care-test-password",
        roles=["ops_center_administration"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    birthday_only_user_id = create_user(
        admin_id,
        username=f"member-care-birthday-{suffix}",
        display_name="学长关爱生日测试账号",
        password="member-care-test-password",
        # Keep this a detail-only source test without using a volunteer role:
        # in IAM 2.0 volunteer grants require an active appointment and direct
        # legacy volunteer roles are deliberately suppressed.
        roles=["employee_learning_management"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    no_source_user_id = create_user(
        admin_id,
        username=f"member-care-nosource-{suffix}",
        display_name="学长关爱无来源权限账号",
        password="member-care-test-password",
        roles=["ops_center_management"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    return {
        "user_id": scoped_user_id,
        "read_only_user_id": read_only_user_id,
        "followup_only_user_id": followup_only_user_id,
        "birthday_only_user_id": birthday_only_user_id,
        "no_source_user_id": no_source_user_id,
        "center_id": center_id,
        "other_cycle_id": other_cycle_id,
        "closed_cycle_id": closed_cycle_id,
        "cycle_id": cycle_id,
        "all_member_id": member_ids["all"],
        "birthday_member_id": member_ids["birthday"],
        "overdue_member_id": member_ids["overdue"],
        "other_member_id": member_ids["other"],
    }


def _add_master_birthday_member(
    fixture: dict[str, int | str],
    *,
    name: str,
    birthday: str,
    org_unit_id: str | None = None,
) -> int:
    now = datetime.now(UTC).isoformat()
    suffix = uuid4().hex
    with transaction() as connection:
        return int(
            execute(
                connection,
                "INSERT INTO members(member_code, name, org_unit_id, status, birthday, created_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)",
                (
                    f"CARE-MASTER-{suffix}",
                    name,
                    org_unit_id or str(fixture["center_id"]),
                    birthday,
                    now,
                    now,
                ),
            ).lastrowid
        )


def test_member_care_actions_merge_sources_and_sort_urgency() -> None:
    fixture = _care_fixture()
    result = build_member_care_actions(
        int(fixture["user_id"]), as_of=date(2099, 8, 20)
    )
    people = result["people"]
    by_name = {person["member_name"]: person for person in people}

    assert result["summary"] == {
        "people_total": 3,
        "action_total": 5,
        "overdue_people_count": 1,
        "today_people_count": 2,
        "attention_people_count": 0,
        "renewal_people_count": 1,
        "birthday_people_count": 2,
        "followup_people_count": 2,
        "enterprise_visit_people_count": 1,
    }
    assert people[0]["member_name"] == "逾期跟进学长"
    assert people[1]["member_name"] == "三类关爱学长"
    assert by_name["三类关爱学长"]["action_count"] == 3
    assert {item["source"] for item in by_name["三类关爱学长"]["actions"]} == {
        "RENEWAL",
        "FOLLOWUP",
        "BIRTHDAY",
    }
    assert by_name["三类关爱学长"]["primary_action"]["urgency"] == "TODAY"
    assert by_name["三类关爱学长"]["has_overdue"] is False
    assert "生日关怀学长" in by_name
    assert "其他分中心学长" not in by_name
    assert all(
        "service_purpose" not in person
        and "subject_statement" not in json.dumps(person, ensure_ascii=False)
        for person in people
    )
    assert all(
        not any("phone" in key.lower() for key in person)
        for person in people
    )


def test_member_care_actions_respect_source_permissions_and_scope() -> None:
    fixture = _care_fixture()
    read_only = build_member_care_actions(
        int(fixture["read_only_user_id"]), as_of=date(2099, 8, 20)
    )
    read_only_sources = {
        item["source"]
        for person in read_only["people"]
        for item in person["actions"]
    }
    assert read_only_sources == {"RENEWAL"}

    followup_only = build_member_care_actions(
        int(fixture["followup_only_user_id"]), as_of=date(2099, 8, 20)
    )
    followup_sources = {
        item["source"]
        for person in followup_only["people"]
        for item in person["actions"]
    }
    assert followup_sources == {"FOLLOWUP", "BIRTHDAY"}
    assert all(
        item["action_type"] != "ENTERPRISE_VISIT"
        or item["navigation_type"] == "ENTERPRISE_VISIT"
        for person in followup_only["people"]
        for item in person["actions"]
    )

    birthday_only = build_member_care_actions(
        int(fixture["birthday_only_user_id"]), as_of=date(2099, 8, 20)
    )
    assert {
        item["source"]
        for person in birthday_only["people"]
        for item in person["actions"]
    } == {"BIRTHDAY"}


def test_member_care_actions_remove_missed_birthdays_from_daily_list() -> None:
    fixture = _care_fixture()
    result = build_member_care_actions(
        int(fixture["user_id"]), as_of=date(2099, 8, 24)
    )

    assert not [
        action
        for person in result["people"]
        for action in person["actions"]
        if action["source"] == "BIRTHDAY"
    ]
    assert result["summary"]["birthday_people_count"] == 0
    assert "生日关怀已逾期" not in json.dumps(result, ensure_ascii=False)


def test_master_birthday_window_covers_today_to_seven_days_and_cross_year() -> None:
    fixture = _care_fixture()
    names = {
        "today": "主档今天生日学长",
        "tomorrow": "主档明天生日学长",
        "seven": "主档七天后生日学长",
        "eight": "主档八天后生日学长",
        "leap": "主档闰日生日学长",
        "next_year": "主档跨年生日学长",
    }
    _add_master_birthday_member(fixture, name=names["today"], birthday="1988-08-20")
    _add_master_birthday_member(fixture, name=names["tomorrow"], birthday="1988-08-21")
    _add_master_birthday_member(fixture, name=names["seven"], birthday="1988-08-27")
    _add_master_birthday_member(fixture, name=names["eight"], birthday="1988-08-28")
    _add_master_birthday_member(fixture, name=names["leap"], birthday="2000-02-29")
    _add_master_birthday_member(fixture, name=names["next_year"], birthday="1988-01-02")

    august = build_member_care_actions(
        int(fixture["user_id"]), as_of=date(2099, 8, 20)
    )
    august_actions = {
        person["member_name"]: action
        for person in august["people"]
        for action in person["actions"]
        if action["source"] == "BIRTHDAY"
    }
    assert august_actions[names["today"]]["urgency"] == "TODAY"
    assert august_actions[names["tomorrow"]]["urgency"] == "WINDOW"
    assert august_actions[names["seven"]]["due_date"] == "2099-08-27"
    assert names["eight"] not in august_actions
    assert august_actions[names["today"]]["operation_item_id"] is None

    leap = build_member_care_actions(
        int(fixture["user_id"]), as_of=date(2099, 2, 21)
    )
    leap_action = next(
        action
        for person in leap["people"]
        if person["member_name"] == names["leap"]
        for action in person["actions"]
        if action["source"] == "BIRTHDAY"
    )
    assert leap_action["due_date"] == "2099-02-28"

    cross_year = build_member_care_actions(
        int(fixture["user_id"]), as_of=date(2099, 12, 28)
    )
    cross_year_action = next(
        action
        for person in cross_year["people"]
        if person["member_name"] == names["next_year"]
        for action in person["actions"]
        if action["source"] == "BIRTHDAY"
    )
    assert cross_year_action["due_date"] == "2100-01-02"


def test_master_birthday_completion_without_rhythm_is_audited_and_idempotent() -> None:
    fixture = _care_fixture()
    member_id = _add_master_birthday_member(
        fixture,
        name="无班级无节奏生日学长",
        birthday="1988-08-21",
    )
    as_of = datetime(2099, 8, 20, 10, 0, tzinfo=UTC)
    before = build_member_care_actions(int(fixture["user_id"]), as_of=as_of.date())
    birthday_action = next(
        action
        for person in before["people"]
        if person["member_id"] == member_id
        for action in person["actions"]
        if action["source"] == "BIRTHDAY"
    )
    assert birthday_action["operation_item_id"] is None
    assert fetch_one(
        "SELECT id FROM operation_items WHERE business_type='BIRTHDAY_CARE' AND business_id=?",
        (str(member_id),),
    ) is None

    completion = complete_birthday_care(
        member_id,
        int(fixture["user_id"]),
        birthday_year=2099,
        due_date="2099-08-21",
        channel="WECHAT",
        now=as_of,
    )
    assert completion["record_type"] == "BIRTHDAY_CARE_COMPLETION"
    assert completion["created"] is True
    duplicate = complete_birthday_care(
        member_id,
        int(fixture["user_id"]),
        birthday_year=2099,
        due_date="2099-08-21",
        channel="PHONE",
        now=as_of,
    )
    assert duplicate["id"] == completion["id"]
    assert duplicate["created"] is False
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM birthday_care_completions WHERE member_id=? AND birthday_year=2099",
        (member_id,),
    ) == {"count": 1}
    assert fetch_one(
        "SELECT id FROM audit_logs WHERE action='member_care.birthday.complete' "
        "AND resource_type='birthday_care_completion' AND resource_id=?",
        (str(completion["id"]),),
    ) is not None
    after = build_member_care_actions(int(fixture["user_id"]), as_of=as_of.date())
    assert not any(
        person["member_id"] == member_id
        and any(action["source"] == "BIRTHDAY" for action in person["actions"])
        for person in after["people"]
    )


def test_existing_birthday_operation_item_completes_without_new_completion_fact() -> None:
    fixture = _care_fixture()
    member_id = int(fixture["birthday_member_id"])
    operation_item = fetch_one(
        "SELECT id FROM operation_items WHERE business_type='BIRTHDAY_CARE' AND business_id=? "
        "AND due_date='2099-08-20'",
        (str(member_id),),
    )
    assert operation_item is not None
    completion = complete_birthday_care(
        member_id,
        int(fixture["user_id"]),
        birthday_year=2099,
        due_date="2099-08-20",
        channel="PHONE",
        operation_item_id=int(operation_item["id"]),
        now=datetime(2099, 8, 20, 9, 0, tzinfo=UTC),
    )
    assert completion["record_type"] == "OPERATION_ITEM"
    assert fetch_one(
        "SELECT status, actual_at FROM operation_items WHERE id=?",
        (operation_item["id"],),
    )["status"] == "COMPLETED"
    assert fetch_one(
        "SELECT id FROM birthday_care_completions WHERE member_id=? AND birthday_year=2099",
        (member_id,),
    ) is None


def test_member_care_api_denies_user_without_source_permission() -> None:
    fixture = _care_fixture()
    with pytest.raises(member_care_api.HTTPException) as exc:
        member_care_api.today_member_care_actions(
            user={"id": int(fixture["no_source_user_id"]), "permissions": {"org:read"}}
        )
    # The service obtains the authoritative permissions from IAM rather than
    # trusting a caller-supplied dictionary.
    assert exc.value.status_code == 403
