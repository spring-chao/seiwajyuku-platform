from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.db import execute, fetch_one, transaction
from app.services.learning_cycles import (
    _cycle_query_datetime,
    bind_class_learning_plan,
    confirm_class_meeting,
    get_class_learning_progress,
    list_learning_plans,
    update_current_learning_cycle,
)


def test_mysql_cycle_query_datetime_normalizes_iso_boundary() -> None:
    assert _cycle_query_datetime(
        object(), "2026-08-26T05:48:51.987654+00:00"
    ) == "2026-08-26 05:48:51"


def _fixture() -> tuple[int, str, str, str]:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin
    suffix = uuid4().hex[:10]
    class_id = f"l1-class-{suffix}"
    group_a = f"l1-group-a-{suffix}"
    group_b = f"l1-group-b-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'CLASS', 'org-suzhou', 1, ?, ?)",
            (class_id, f"L1_{suffix}", f"L1测试班-{suffix}", now, now),
        )
        for index, group_id in enumerate((group_a, group_b), start=1):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'GROUP', ?, 1, ?, ?)",
                (group_id, f"L1_G{index}_{suffix}", f"L1测试组{index}-{suffix}", class_id, now, now),
            )
        execute(
            connection,
            "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, 'L1测试三年计划', ?, 3, 'PUBLISHED', ?, ?)",
            (f"L1_{suffix}", f"2026-{suffix}", now, now),
        )
        plan_id = execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"]
        for cycle_index in range(1, 4):
            execute(
                connection,
                "INSERT INTO learning_plan_cycles(plan_version_id, cycle_index, year_index, cycle_label, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (plan_id, cycle_index, 1 if cycle_index <= 2 else 2, f"第{cycle_index}周期", now, now),
            )
            cycle_id = execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"]
            execute(
                connection,
                "INSERT INTO learning_plan_tasks(plan_cycle_id, task_type, title, credit_points, is_required, sort_order, created_at, updated_at) "
                "VALUES (?, 'CLASS_MEETING', ?, 0, 1, 1, ?, ?)",
                (cycle_id, f"第{cycle_index}周期班会", now, now),
            )
            execute(
                connection,
                "INSERT INTO learning_plan_tasks(plan_cycle_id, task_type, title, credit_points, is_required, sort_order, created_at, updated_at) "
                "VALUES (?, 'GROUP_MEETING', ?, 40, 1, 2, ?, ?)",
                (cycle_id, f"第{cycle_index}周期小组会", now, now),
            )
    bind_class_learning_plan(
        actor_user_id=admin["id"], class_org_unit_id=class_id, plan_version_id=plan_id,
        cohort_month=4, started_at="2026-07-20T19:00:00+00:00",
    )
    return int(admin["id"]), class_id, group_a, group_b


def _cohort_track_fixture() -> tuple[int, str, str, int]:
    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin
    suffix = uuid4().hex[:10]
    class_1 = f"l1-cohort-1-{suffix}"
    class_4 = f"l1-cohort-4-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        for cohort_month, class_id in ((1, class_1), (4, class_4)):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'CLASS', 'org-suzhou', 1, ?, ?)",
                (class_id, f"L1_TRACK_{cohort_month}_{suffix}", f"L1批次测试班{cohort_month}-{suffix}", now, now),
            )
        execute(
            connection,
            "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, 'L1批次测试计划', ?, 2, 'PUBLISHED', ?, ?)",
            (f"L1_TRACK_{suffix}", f"2026-{suffix}", now, now),
        )
        plan_id = execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"]
        for cohort_month in (1, 4):
            for cycle_index in (1, 2):
                execute(
                    connection,
                    "INSERT INTO learning_plan_cycles(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?)",
                    (plan_id, cohort_month, cycle_index, f"{cohort_month}月轨道第{cycle_index}周期", now, now),
                )
                cycle_id = execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"]
                execute(
                    connection,
                    "INSERT INTO learning_plan_tasks(plan_cycle_id, task_type, title, is_required, sort_order, created_at, updated_at) "
                    "VALUES (?, 'GROUP_MEETING', ?, 1, 1, ?, ?)",
                    (cycle_id, f"{cohort_month}月轨道第{cycle_index}周期小组会", now, now),
                )
    bind_class_learning_plan(
        actor_user_id=admin["id"], class_org_unit_id=class_1, plan_version_id=plan_id,
        cohort_month=1, started_at="2026-01-20T19:00:00+00:00",
    )
    bind_class_learning_plan(
        actor_user_id=admin["id"], class_org_unit_id=class_4, plan_version_id=plan_id,
        cohort_month=4, started_at="2026-04-20T19:00:00+00:00",
    )
    return int(admin["id"]), class_1, class_4, int(plan_id)


def test_group_meeting_before_class_meeting_stays_in_current_cycle() -> None:
    admin, class_id, _, _ = _fixture()
    before = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T15:00:00+00:00"
    )
    same_day_before = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T18:59:00+00:00"
    )
    assert before["current_cycle"]["learning_cycle_index"] == 1
    assert same_day_before["current_cycle"]["learning_cycle_index"] == 1


def test_class_meeting_confirmation_opens_next_cycle_at_actual_start() -> None:
    admin, class_id, _, _ = _fixture()
    confirmed = confirm_class_meeting(
        actor_user_id=admin, class_org_unit_id=class_id,
        actual_class_meeting_at="2026-08-20T19:00:00+00:00",
        confirmation_reason="测试确认班会实际召开",
    )
    after = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T21:00:00+00:00"
    )
    assert confirmed["current_cycle"]["learning_cycle_index"] == 2
    assert after["current_cycle"]["learning_cycle_index"] == 2
    assert after["current_cycle"]["opened_at"] == "2026-08-20T19:00:00+00:00"


def test_postponed_class_meeting_does_not_advance_cycle() -> None:
    admin, class_id, _, _ = _fixture()
    updated = update_current_learning_cycle(
        actor_user_id=admin, class_org_unit_id=class_id,
        updates={"class_meeting_status": "POSTPONED", "adjustment_reason": "大会延期"},
    )
    later = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-09-05T12:00:00+00:00"
    )
    assert updated["current_cycle"]["class_meeting_status"] == "POSTPONED"
    assert later["current_cycle"]["learning_cycle_index"] == 1


def test_suspended_group_meeting_is_waived_and_class_can_advance() -> None:
    admin, class_id, _, _ = _fixture()
    current = update_current_learning_cycle(
        actor_user_id=admin, class_org_unit_id=class_id,
        updates={"group_meeting_policy": "SUSPENDED"},
    )
    confirmed = confirm_class_meeting(
        actor_user_id=admin, class_org_unit_id=class_id,
        actual_class_meeting_at="2026-08-20T19:00:00+00:00",
        confirmation_reason="测试小组会豁免后班会召开",
    )
    assert {row["status"] for row in current["current_cycle"]["groups"]} == {"WAIVED"}
    assert confirmed["current_cycle"]["learning_cycle_index"] == 2


def test_class_and_group_pause_together_keep_cycle_open() -> None:
    admin, class_id, _, _ = _fixture()
    update_current_learning_cycle(
        actor_user_id=admin, class_org_unit_id=class_id,
        updates={"class_meeting_status": "POSTPONED", "group_meeting_policy": "SUSPENDED"},
    )
    later = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-10-01T00:00:00+00:00"
    )
    assert later["current_cycle"]["learning_cycle_index"] == 1
    assert later["current_cycle"]["cycle_status"] == "OPEN"
    assert {row["status"] for row in later["current_cycle"]["groups"]} == {"WAIVED"}


def test_completed_group_is_preserved_when_class_meeting_closes_cycle() -> None:
    admin, class_id, group_a, group_b = _fixture()
    update_current_learning_cycle(
        actor_user_id=admin, class_org_unit_id=class_id,
        updates={"group_tasks": [{"group_org_unit_id": group_a, "status": "COMPLETED"}]},
    )
    confirmed = confirm_class_meeting(
        actor_user_id=admin, class_org_unit_id=class_id,
        actual_class_meeting_at="2026-08-20T19:00:00+00:00",
        confirmation_reason="测试完成与未完成结算",
    )
    historical = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T18:00:00+00:00"
    )
    statuses = {row["group_org_unit_id"]: row["status"] for row in historical["current_cycle"]["groups"]}
    assert confirmed["current_cycle"]["learning_cycle_index"] == 2
    assert statuses[group_a] == "COMPLETED"
    assert statuses[group_b] == "MISSED"


def test_cycle_progress_does_not_follow_natural_month() -> None:
    admin, class_id, _, _ = _fixture()
    update_current_learning_cycle(
        actor_user_id=admin, class_org_unit_id=class_id,
        updates={"class_meeting_status": "POSTPONED"},
    )
    progress = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-10-31T23:59:00+00:00"
    )
    assert progress["current_cycle"]["learning_cycle_index"] == 1
    assert progress["current_cycle"]["plan_cycle"]["cycle_label"] == "第1周期"


def test_cycle_boundary_is_timestamp_not_calendar_day() -> None:
    admin, class_id, _, _ = _fixture()
    confirm_class_meeting(
        actor_user_id=admin, class_org_unit_id=class_id,
        actual_class_meeting_at="2026-08-20T19:00:00+00:00",
        confirmation_reason="测试同日时间边界",
    )
    before = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T18:59:59+00:00"
    )
    after = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T19:00:00+00:00"
    )
    assert before["current_cycle"]["learning_cycle_index"] == 1
    assert after["current_cycle"]["learning_cycle_index"] == 2


def test_cohort_tracks_return_distinct_first_cycles_and_do_not_cross() -> None:
    admin, class_1, class_4, plan_id = _cohort_track_fixture()
    first = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_1, at="2026-05-01T00:00:00+00:00"
    )
    fourth = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_4, at="2026-05-01T00:00:00+00:00"
    )
    plans = list_learning_plans()
    plan = next(item for item in plans if item["id"] == plan_id)
    assert first["current_cycle"]["plan_cycle"]["cohort_month"] == 1
    assert fourth["current_cycle"]["plan_cycle"]["cohort_month"] == 4
    assert first["current_cycle"]["plan_cycle"]["tasks"][0]["title"] == "1月轨道第1周期小组会"
    assert fourth["current_cycle"]["plan_cycle"]["tasks"][0]["title"] == "4月轨道第1周期小组会"
    assert [track["cohort_month"] for track in plan["cohort_tracks"]] == [1, 4]


def test_confirmed_cohort_cycle_advances_within_the_same_track() -> None:
    admin, _, class_4, _ = _cohort_track_fixture()
    next_cycle = confirm_class_meeting(
        actor_user_id=admin, class_org_unit_id=class_4,
        actual_class_meeting_at="2026-05-20T19:00:00+00:00",
        confirmation_reason="验证4月班不得串入其他批次轨道",
    )
    plan_cycle = next_cycle["current_cycle"]["plan_cycle"]
    assert next_cycle["current_cycle"]["learning_cycle_index"] == 2
    assert plan_cycle["cohort_month"] == 4
    assert plan_cycle["tasks"][0]["title"] == "4月轨道第2周期小组会"


def test_cohort_binding_falls_back_to_the_generic_track_only_when_needed() -> None:
    admin, class_id, _, _ = _fixture()
    progress = get_class_learning_progress(
        user_id=admin, class_org_unit_id=class_id, at="2026-08-20T15:00:00+00:00"
    )
    assert progress["binding"]["cohort_month"] == 4
    assert progress["current_cycle"]["plan_cycle"]["cohort_month"] is None
