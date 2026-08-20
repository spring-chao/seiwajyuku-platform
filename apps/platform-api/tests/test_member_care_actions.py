from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.api import member_care_actions as member_care_api
from app.db import execute, fetch_one, transaction
from app.services.iam import create_user
from app.services.member_care_actions import build_member_care_actions


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
        for key, name, org_id in (
            ("all", "三类关爱学长", center_id),
            ("birthday", "生日关怀学长", center_id),
            ("overdue", "逾期跟进学长", center_id),
            ("other", "其他分中心学长", other_center_id),
        ):
            member_ids[key] = int(
                execute(
                    connection,
                    "INSERT INTO members(member_code, name, org_unit_id, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                    (f"CARE-MEMBER-{suffix}-{key}", name, org_id, now, now),
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
            (member_ids["overdue"], "2099-08-01", "2099-08-10", "COMPLETED"),
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
        roles=["ops_center_management"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    no_source_user_id = create_user(
        admin_id,
        username=f"member-care-nosource-{suffix}",
        display_name="学长关爱无来源权限账号",
        password="member-care-test-password",
        roles=["volunteer_activity"],
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
    }


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
    assert read_only_sources == {"RENEWAL", "BIRTHDAY"}

    followup_only = build_member_care_actions(
        int(fixture["followup_only_user_id"]), as_of=date(2099, 8, 20)
    )
    followup_sources = {
        item["source"]
        for person in followup_only["people"]
        for item in person["actions"]
    }
    assert followup_sources == {"FOLLOWUP"}
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


def test_member_care_api_denies_user_without_source_permission() -> None:
    fixture = _care_fixture()
    with pytest.raises(member_care_api.HTTPException) as exc:
        member_care_api.today_member_care_actions(
            user={"id": int(fixture["no_source_user_id"]), "permissions": {"org:read"}}
        )
    # The service obtains the authoritative permissions from IAM rather than
    # trusting a caller-supplied dictionary.
    assert exc.value.status_code == 403
