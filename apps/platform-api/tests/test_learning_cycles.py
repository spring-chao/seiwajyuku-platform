from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import learning_cycles as learning_cycles_service
from app.services.learning_cycles import (
    _cycle_query_datetime,
    _normalize_datetime,
    _storage_datetime,
    bind_class_learning_plan,
    clear_learning_cycle_schedule_override,
    confirm_class_meeting,
    correct_class_learning_plan,
    get_class_learning_schedule,
    get_class_learning_progress,
    get_class_learning_plan_history,
    list_learning_plans,
    recommend_learning_plan,
    restart_class_learning_plan,
    retire_learning_plan,
    resume_class_learning_plan,
    scan_class_learning_plan_health,
    set_learning_cycle_schedule_override,
    update_current_learning_cycle,
)
from app.services.group_meeting_plan import build_group_meeting_plan


def test_mysql_cycle_query_datetime_normalizes_iso_boundary() -> None:
    assert _cycle_query_datetime(
        object(), "2026-08-26T05:48:51.987654+00:00"
    ) == "2026-08-26 05:48:51"


def test_mysql_cycle_storage_datetime_normalizes_iso_value() -> None:
    assert _storage_datetime(
        object(), "2026-08-26T05:48:51.987654+08:00"
    ) == "2026-08-25 21:48:51"


def test_date_only_start_normalizes_to_utc_day_boundary() -> None:
    assert _normalize_datetime("2026-09-03", "正式开始日期") == "2026-09-03T00:00:00+00:00"


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


def _schedule_fixture() -> tuple[int, str, str]:
    """Create two July-track classes for schedule override isolation tests."""

    admin = fetch_one("SELECT id FROM app_users WHERE username='admin'")
    assert admin
    suffix = uuid4().hex[:10]
    class_a = f"l1-schedule-a-{suffix}"
    class_b = f"l1-schedule-b-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        for class_id, label in ((class_a, "A"), (class_b, "B")):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'CLASS', 'org-suzhou', 1, ?, ?)",
                (class_id, f"L1_SCHEDULE_{label}_{suffix}", f"L1周期调整测试班{label}-{suffix}", now, now),
            )
        execute(
            connection,
            "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, 'L1周期调整测试计划', ?, 9, 'PUBLISHED', ?, ?)",
            (f"L1_SCHEDULE_{suffix}", f"2026-{suffix}", now, now),
        )
        plan_id = execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"]
        for cycle_index in range(1, 10):
            execute(
                connection,
                "INSERT INTO learning_plan_cycles(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
                "VALUES (?, 7, ?, ?, ?, ?, ?)",
                (
                    plan_id,
                    cycle_index,
                    1 if cycle_index <= 12 else 2,
                    f"7月轨道第{cycle_index}周期",
                    now,
                    now,
                ),
            )
    for class_id in (class_a, class_b):
        bind_class_learning_plan(
            actor_user_id=admin["id"],
            class_org_unit_id=class_id,
            plan_version_id=plan_id,
            cohort_month=7,
            started_at="2026-07-20T19:00:00+00:00",
        )
    return int(admin["id"]), class_a, class_b


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


def _insert_lifecycle_plan(*, plan_key: str, version_label: str, cohort_month: int, duration: int = 3) -> int:
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO learning_plan_versions(plan_key, plan_name, version_label, duration_cycles, status, created_at, updated_at) "
            "VALUES (?, '生命周期测试计划', ?, ?, 'PUBLISHED', ?, ?)",
            (plan_key, version_label, duration, now, now),
        )
        plan_id = int(execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"])
        for cycle_index in range(1, duration + 1):
            execute(
                connection,
                "INSERT INTO learning_plan_cycles(plan_version_id, cohort_month, cycle_index, year_index, cycle_label, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?)",
                (plan_id, cohort_month, cycle_index, f"生命周期第{cycle_index}期", now, now),
            )
            cycle_id = int(execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"])
            execute(
                connection,
                "INSERT INTO learning_plan_tasks(plan_cycle_id, task_type, title, is_required, sort_order, created_at, updated_at) "
                "VALUES (?, 'GROUP_MEETING', ?, 1, 1, ?, ?)",
                (cycle_id, f"生命周期第{cycle_index}期小组会", now, now),
            )
    return plan_id


def test_restart_creates_new_round_and_preserves_previous_binding() -> None:
    admin, class_id, _, _ = _fixture()
    new_plan_id = _insert_lifecycle_plan(
        plan_key=f"L1_RESTART_{uuid4().hex[:8]}",
        version_label="2027",
        cohort_month=10,
    )
    restarted = restart_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=new_plan_id,
        cohort_month=10,
        started_at="2027-10-01T19:00:00+00:00",
        reason="班级决定重新系统学习",
    )
    assert restarted["binding"]["learning_round"] == 2
    assert restarted["binding"]["transition_type"] == "RESTART"
    assert restarted["current_cycle"] is None
    assert restarted["current_status"] == "NOT_STARTED"
    old = fetch_one(
        "SELECT status, ended_at, ended_reason FROM class_learning_bindings "
        "WHERE class_org_unit_id=? AND learning_round=1",
        (class_id,),
    )
    assert old and old["status"] == "ENDED"
    assert old["ended_at"]
    assert old["ended_reason"] == "班级决定重新系统学习"
    history = get_class_learning_plan_history(user_id=admin, class_org_unit_id=class_id)
    assert len(history["bindings"]) == 2
    assert history["history_preserved"] is True
    assert any(event["action"] == "learning.binding.restart" for event in history["events"])


def test_normal_progression_keeps_the_same_plan_and_learning_round() -> None:
    admin, class_id, _, _ = _fixture()
    plan_id = _insert_lifecycle_plan(
        plan_key=f"L1_CONTINUE_{uuid4().hex[:8]}",
        version_label="2027",
        cohort_month=4,
        duration=13,
    )
    restarted = restart_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=plan_id,
        cohort_month=4,
        started_at="2024-01-01T19:00:00+00:00",
        reason="为测试同一轮自然进阶建立历史样本",
    )
    binding_id = restarted["binding"]["id"]
    for month in range(1, 13):
        progressed = confirm_class_meeting(
            actor_user_id=admin,
            class_org_unit_id=class_id,
            actual_class_meeting_at=f"2024-{month:02d}-20T19:00:00+00:00",
            confirmation_reason=f"确认同一轮第{month}期班会",
        )
        assert progressed["binding"]["id"] == binding_id
        assert progressed["binding"]["plan_version_id"] == plan_id
        assert progressed["binding"]["cohort_month"] == 4
        assert progressed["binding"]["learning_round"] == 2
    assert progressed["current_cycle"]["learning_cycle_index"] == 13


def test_resume_starts_at_requested_cycle_without_switching_natural_progression() -> None:
    admin, class_id, _, _ = _fixture()
    new_plan_id = _insert_lifecycle_plan(
        plan_key=f"L1_RESUME_{uuid4().hex[:8]}",
        version_label="2027",
        cohort_month=4,
        duration=9,
    )
    resumed = resume_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=new_plan_id,
        cohort_month=4,
        started_at="2027-10-01T19:00:00+00:00",
        start_cycle_index=8,
        reason="历史资料不完整，运营确认从第8期接续",
    )
    assert resumed["binding"]["transition_type"] == "RESUME"
    assert resumed["binding"]["start_cycle_index"] == 8
    assert resumed["current_cycle"] is None
    assert resumed["current_status"] == "NOT_STARTED"
    schedule = get_class_learning_schedule(user_id=admin, class_org_unit_id=class_id)
    assert schedule["cycles"][0]["actual_status"] == "SKIPPED"
    assert schedule["cycles"][0]["planned_class_meeting_at"] is None
    assert schedule["cycles"][7]["planned_class_meeting_at"] == "2027-10-01T19:00:00+00:00"


def test_future_learning_round_is_not_started_before_formal_start() -> None:
    admin, class_id, _, _ = _fixture()
    new_plan_id = _insert_lifecycle_plan(
        plan_key=f"L1_FUTURE_{uuid4().hex[:8]}",
        version_label="2027",
        cohort_month=10,
    )
    created = restart_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=new_plan_id,
        cohort_month=10,
        started_at="2027-10-01T19:00:00+00:00",
        reason="验证正式开始前不开放第1周期",
    )
    assert created["current_cycle"] is None
    assert created["current_status"] == "NOT_STARTED"

    before_start = get_class_learning_progress(
        user_id=admin,
        class_org_unit_id=class_id,
        at="2027-09-30T19:00:00+00:00",
    )
    assert before_start["current_cycle"] is None
    assert before_start["actual_status"] == "NOT_STARTED"

    schedule = get_class_learning_schedule(user_id=admin, class_org_unit_id=class_id)
    assert schedule["current_projection"]["current_open_cycle"] is None
    assert schedule["current_projection"]["actual_status"] == "NOT_STARTED"

    health = scan_class_learning_plan_health(
        user_id=admin, class_org_unit_id=class_id
    )
    assert health["summary"]["missing_current_cycle"] == 0
    assert health["classes"][0]["runtime_status"] == "NOT_STARTED"
    assert health["classes"][0]["current_cycle"] is None


def test_correction_changes_current_setup_without_creating_new_round() -> None:
    admin, class_id, _, _ = _fixture()
    corrected = correct_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=fetch_one(
            "SELECT plan_version_id FROM class_learning_bindings WHERE class_org_unit_id=? AND status='ACTIVE'",
            (class_id,),
        )["plan_version_id"],
        cohort_month=4,
        learning_cycle_index=2,
        reason="运营核对后第2期才是当前周期",
    )
    assert corrected["binding"]["learning_round"] == 1
    assert corrected["current_cycle"]["learning_cycle_index"] == 2
    assert corrected["current_cycle"]["plan_cycle"]["cycle_label"] == "第2周期"
    assert fetch_one(
        "SELECT COUNT(*) AS count FROM class_learning_bindings WHERE class_org_unit_id=?",
        (class_id,),
    )["count"] == 1


def test_health_scan_reports_missing_binding_as_blocker() -> None:
    admin = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    suffix = uuid4().hex[:10]
    class_id = f"l1-health-unbound-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'CLASS', 'org-suzhou', 1, ?, ?)",
            (class_id, f"L1_HEALTH_{suffix}", f"健康扫描未绑定班-{suffix}", now, now),
        )
    result = scan_class_learning_plan_health(user_id=admin, class_org_unit_id=class_id)
    assert result["assessment"] == "NO-GO"
    assert result["summary"]["total_classes"] == 1
    assert result["summary"]["unbound"] == 1
    assert result["issues"][0]["issue_type"] == "MISSING_BINDING"


def test_health_scan_does_not_block_on_missing_volunteer_permission() -> None:
    admin, class_id, group_a, group_b = _fixture()
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        for index, group_id in enumerate((group_a, group_b), start=1):
            cursor = execute(
                connection,
                "INSERT INTO members(member_code, name, status, org_unit_id, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?, ?)",
                (f"L1_HEALTH_MEMBER_{uuid4().hex[:12]}", f"健康扫描学员{index}", class_id, now, now),
            )
            member_id = int(cursor.lastrowid)
            for relation_type, org_unit_id in (("STUDY_CLASS", class_id), ("STUDY_GROUP", group_id)):
                execute(
                    connection,
                    "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (member_id, org_unit_id, relation_type, now, now),
                )

    result = scan_class_learning_plan_health(user_id=admin, class_org_unit_id=class_id)
    row = result["classes"][0]

    assert result["assessment"] == "GO"
    assert result["summary"]["volunteer_permission_missing"] == 1
    assert row["volunteer_permission"] == "BLOCKED"
    assert row["status"] == "READY"
    assert all(issue["issue_type"] != "VOLUNTEER_PERMISSION_MISSING" for issue in row["issues"])


def test_health_scan_uses_explicit_correction_as_business_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, class_id, group_a, group_b = _fixture()
    now = datetime.now(UTC).isoformat()
    class_name = fetch_one("SELECT name FROM org_units WHERE id=?", (class_id,))["name"]
    binding = fetch_one(
        "SELECT b.*, p.version_label FROM class_learning_bindings b "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.class_org_unit_id=? AND b.status='ACTIVE'",
        (class_id,),
    )
    assert binding
    with transaction() as connection:
        for index, group_id in enumerate((group_a, group_b), start=1):
            cursor = execute(
                connection,
                "INSERT INTO members(member_code, name, status, org_unit_id, created_at, updated_at) "
                "VALUES (?, ?, 'ACTIVE', ?, ?, ?)",
                (f"L1_CORRECTION_MEMBER_{uuid4().hex[:12]}", f"修正确认学员{index}", class_id, now, now),
            )
            member_id = int(cursor.lastrowid)
            for relation_type, org_unit_id in (("STUDY_CLASS", class_id), ("STUDY_GROUP", group_id)):
                execute(
                    connection,
                    "INSERT INTO member_org_relations(member_id, org_unit_id, relation_type, is_primary, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (member_id, org_unit_id, relation_type, now, now),
                )

    monkeypatch.setattr(
        learning_cycles_service,
        "load_baseline",
        lambda: {
            "schema_version": 1,
            "status": "DRY_RUN_ONLY",
            "classes": [{
                "class_name": class_name,
                "class_org_unit_id": class_id,
                "expected_plan_version": binding["version_label"],
                "expected_cohort_month": 4,
                "expected_current_cycle": 1,
                "meeting_status": "PLANNED",
                "group_meeting_policy": "REQUIRED",
                "expected_runtime_status": "NORMAL",
                "migration_status": "READY_FOR_DRY_RUN",
            }],
        },
    )

    before = scan_class_learning_plan_health(user_id=admin, class_org_unit_id=class_id)
    assert before["classes"][0]["status"] == "READY"

    correct_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=binding["plan_version_id"],
        cohort_month=4,
        learning_cycle_index=2,
        reason="运营确认实际已进入第2周期",
    )

    after = scan_class_learning_plan_health(user_id=admin, class_org_unit_id=class_id)
    row = after["classes"][0]
    assert after["assessment"] == "GO"
    assert row["current_cycle"]["learning_cycle_index"] == 2
    assert row["status"] == "READY"
    assert after["summary"]["expected_cycle_mismatch"] == 0
    assert row["business_expectation_resolution"]["mode"] == "EXPLICIT_CONFIRMATION"
    assert row["business_expectation_resolution"]["reason"] == "运营确认实际已进入第2周期"


def test_health_scan_treats_out_of_scope_class_as_not_applicable(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    suffix = uuid4().hex[:10]
    class_id = f"l1-health-out-of-scope-{suffix}"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        execute(
            connection,
            "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'CLASS', 'org-suzhou', 1, ?, ?)",
            (class_id, f"L1_HEALTH_OUT_{suffix}", "健康扫描不适用班", now, now),
        )
    monkeypatch.setattr(
        learning_cycles_service,
        "load_baseline",
        lambda: {
            "schema_version": 1,
            "status": "DRY_RUN_ONLY",
            "classes": [{
                "class_name": "健康扫描不适用班",
                "class_org_unit_id": class_id,
                "learning_plan_scope": "OUT_OF_SCOPE",
                "binding_requirement": "NOT_REQUIRED",
                "migration_status": "NOT_APPLICABLE",
            }],
        },
    )

    result = scan_class_learning_plan_health(user_id=admin, class_org_unit_id=class_id)

    row = result["classes"][0]
    assert result["assessment"] == "GO"
    assert result["summary"]["unbound"] == 0
    assert result["summary"]["not_applicable_classes"] == 1
    assert row["runtime_status"] == "NOT_APPLICABLE"
    assert row["volunteer_permission"] == "NOT_APPLICABLE"
    assert row["status"] == "NOT_APPLICABLE"
    assert row["issues"] == []


def test_retired_plan_is_not_selectable_for_new_round_but_active_round_keeps_reading() -> None:
    admin, class_id, _, _ = _fixture()
    plan_id = int(fetch_one(
        "SELECT plan_version_id FROM class_learning_bindings WHERE class_org_unit_id=?",
        (class_id,),
    )["plan_version_id"])
    retired = retire_learning_plan(
        actor_user_id=admin,
        plan_version_id=plan_id,
        reason="年度计划迭代，停止新轮次选择",
    )
    assert retired == {"id": plan_id, "status": "RETIRED", "changed": True}
    plan = next(item for item in list_learning_plans() if item["id"] == plan_id)
    assert plan["status"] == "RETIRED"
    progress = get_class_learning_progress(user_id=admin, class_org_unit_id=class_id)
    assert progress["binding"]["plan_version_id"] == plan_id
    corrected = correct_class_learning_plan(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        plan_version_id=plan_id,
        cohort_month=4,
        learning_cycle_index=1,
        reason="退役旧计划仍需纠正当前轮次的周期显示",
    )
    assert corrected["binding"]["plan_version_id"] == plan_id
    assert corrected["binding"]["transition_type"] == "INITIAL"
    with pytest.raises(ValueError, match="已发布"):
        restart_class_learning_plan(
            actor_user_id=admin,
            class_org_unit_id=class_id,
            plan_version_id=plan_id,
            cohort_month=10,
            started_at="2027-10-01T19:00:00+00:00",
            reason="不应选择已退役计划",
        )


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


def test_schedule_override_keeps_future_cycle_planned_until_class_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin, class_id, _ = _schedule_fixture()
    schedule = set_learning_cycle_schedule_override(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        learning_cycle_index=8,
        planned_class_meeting_at="2027-03-20T19:00:00+00:00",
        adjustment_reason="春节期间暂停班会",
    )
    cycle_8 = schedule["cycles"][7]
    assert schedule["current_projection"]["current_open_cycle"] == 1
    assert cycle_8["learning_cycle_index"] == 8
    assert cycle_8["default_planned_class_meeting_at"] == "2027-02-20T19:00:00+00:00"
    assert cycle_8["planned_class_meeting_at"] == "2027-03-20T19:00:00+00:00"
    assert cycle_8["planned_month"] == "2027-03"
    assert cycle_8["schedule_source"] == "OVERRIDE"
    assert cycle_8["actual_status"] == "NOT_STARTED"
    assert cycle_8["schedule_override"]["adjustment_reason"] == "春节期间暂停班会"

    actuals = (
        datetime(2026, 8, 20, 19, tzinfo=UTC),
        datetime(2026, 9, 20, 19, tzinfo=UTC),
        datetime(2026, 10, 20, 19, tzinfo=UTC),
        datetime(2026, 11, 20, 19, tzinfo=UTC),
        datetime(2026, 12, 20, 19, tzinfo=UTC),
        datetime(2027, 1, 20, 19, tzinfo=UTC),
        datetime(2027, 2, 20, 19, tzinfo=UTC),
    )
    for cycle_index, actual in enumerate(actuals, start=1):
        now = actual.replace(minute=1)
        monkeypatch.setattr(
            learning_cycles_service, "_now", lambda now=now: now.isoformat()
        )
        progress = confirm_class_meeting(
            actor_user_id=admin,
            class_org_unit_id=class_id,
            actual_class_meeting_at=actual.isoformat(),
            confirmation_reason=f"确认第{cycle_index}周期班会",
        )
        assert progress["current_cycle"]["learning_cycle_index"] == cycle_index + 1

    runtime_schedule = get_class_learning_schedule(
        user_id=admin, class_org_unit_id=class_id
    )
    assert runtime_schedule["current_projection"]["current_open_cycle"] == 8
    assert runtime_schedule["current_projection"]["planned_month"] == "2027-03"

    delayed = get_class_learning_progress(
        user_id=admin,
        class_org_unit_id=class_id,
        at="2027-03-01T12:00:00+00:00",
    )
    assert delayed["current_cycle"]["learning_cycle_index"] == 8
    assert delayed["current_cycle"]["plan_cycle"]["cycle_label"] == "7月轨道第8周期"
    assert delayed["current_cycle"]["planned_class_meeting_at"] == "2027-03-20T19:00:00+00:00"
    delayed_plan = build_group_meeting_plan(
        plan_cycle=delayed["current_cycle"]["plan_cycle"],
        cohort_month=7,
        learning_cycle_index=delayed["current_cycle"]["learning_cycle_index"],
    )
    assert delayed_plan["learning_cycle_index"] == 8
    assert delayed_plan["cohort_month"] == 7
    assert delayed_plan["learning_contents"][0]["title"] == "成功方程式49天讲解"

    monkeypatch.setattr(
        learning_cycles_service, "_now", lambda: "2027-03-20T20:00:00+00:00"
    )
    confirmed = confirm_class_meeting(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        actual_class_meeting_at="2027-03-20T19:00:00+00:00",
        confirmation_reason="春节后确认第8周期班会",
    )
    assert confirmed["current_cycle"]["learning_cycle_index"] == 9
    assert confirmed["current_cycle"]["opened_at"] == "2027-03-20T19:00:00+00:00"
    history = get_class_learning_progress(
        user_id=admin,
        class_org_unit_id=class_id,
        at="2027-03-20T19:00:00+00:00",
    )
    assert history["current_cycle"]["learning_cycle_index"] == 9


def test_schedule_override_is_isolated_to_one_class() -> None:
    admin, class_a, class_b = _schedule_fixture()
    set_learning_cycle_schedule_override(
        actor_user_id=admin,
        class_org_unit_id=class_a,
        learning_cycle_index=8,
        planned_class_meeting_at="2027-03-20T19:00:00+00:00",
        adjustment_reason="本班临时顺延",
    )
    class_a_schedule = get_class_learning_schedule(
        user_id=admin, class_org_unit_id=class_a
    )
    class_b_schedule = get_class_learning_schedule(
        user_id=admin, class_org_unit_id=class_b
    )
    assert class_a_schedule["cycles"][7]["schedule_source"] == "OVERRIDE"
    assert class_b_schedule["cycles"][7]["schedule_source"] == "DEFAULT"
    assert class_b_schedule["cycles"][7]["schedule_override"] is None


def test_future_schedule_override_can_be_revoked_without_advancing_cycle() -> None:
    admin, class_id, _ = _schedule_fixture()
    set_learning_cycle_schedule_override(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        learning_cycle_index=2,
        planned_class_meeting_at="2026-10-05T19:00:00+00:00",
        adjustment_reason="临时顺延",
    )
    schedule = clear_learning_cycle_schedule_override(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        learning_cycle_index=2,
    )
    cycle_2 = schedule["cycles"][1]
    assert cycle_2["schedule_source"] == "DEFAULT"
    assert cycle_2["planned_class_meeting_at"] == "2026-08-20T19:00:00+00:00"
    assert cycle_2["schedule_override"] is None
    override = fetch_one(
        "SELECT status FROM class_learning_cycle_schedule_overrides "
        "WHERE binding_id=(SELECT id FROM class_learning_bindings WHERE class_org_unit_id=? LIMIT 1) "
        "AND learning_cycle_index=2",
        (class_id,),
    )
    assert override == {"status": "REVOKED"}


def test_open_cycle_schedule_override_marks_postponed_and_can_restore_default() -> None:
    admin, class_id, _ = _schedule_fixture()
    updated = set_learning_cycle_schedule_override(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        learning_cycle_index=1,
        planned_class_meeting_at="2026-08-20T19:00:00+00:00",
        adjustment_reason="本周期班会顺延",
    )
    assert updated["current_projection"]["current_open_cycle"] == 1
    assert updated["current_projection"]["schedule_override"]["adjustment_reason"] == "本周期班会顺延"
    cycle = fetch_one(
        "SELECT class_meeting_status, adjustment_reason FROM class_learning_cycles "
        "WHERE class_org_unit_id=? AND learning_cycle_index=1",
        (class_id,),
    )
    assert cycle == {
        "class_meeting_status": "POSTPONED",
        "adjustment_reason": "本周期班会顺延",
    }

    restored = clear_learning_cycle_schedule_override(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        learning_cycle_index=1,
    )
    assert restored["current_projection"]["schedule_override"] is None
    cycle = fetch_one(
        "SELECT class_meeting_status, planned_class_meeting_at, adjustment_reason "
        "FROM class_learning_cycles WHERE class_org_unit_id=? AND learning_cycle_index=1",
        (class_id,),
    )
    assert cycle == {
        "class_meeting_status": "PLANNED",
        "planned_class_meeting_at": "2026-07-20T19:00:00+00:00",
        "adjustment_reason": None,
    }


def test_closed_cycle_schedule_cannot_be_rewritten() -> None:
    admin, class_id, _, _ = _fixture()
    confirm_class_meeting(
        actor_user_id=admin,
        class_org_unit_id=class_id,
        actual_class_meeting_at="2026-08-20T19:00:00+00:00",
        confirmation_reason="冻结历史周期",
    )
    with pytest.raises(ValueError, match="已关闭"):
        set_learning_cycle_schedule_override(
            actor_user_id=admin,
            class_org_unit_id=class_id,
            learning_cycle_index=1,
            planned_class_meeting_at="2026-09-01T19:00:00+00:00",
            adjustment_reason="不应修改历史",
        )


def test_schedule_api_exposes_read_and_class_cycle_override_operations() -> None:
    _, class_id, _ = _schedule_fixture()
    with TestClient(app) as client:
        assert client.get(
            f"/api/v1/classes/{class_id}/learning-cycle-schedule"
        ).status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        initial = client.get(
            f"/api/v1/classes/{class_id}/learning-cycle-schedule",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["data"]["current_projection"]["current_open_cycle"] == 1

        updated = client.put(
            f"/api/v1/classes/{class_id}/learning-cycles/8/schedule-override",
            headers=headers,
            json={
                "planned_class_meeting_at": "2027-03-20T19:00:00+00:00",
                "adjustment_reason": "春节期间暂停班会",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["cycles"][7]["schedule_source"] == "OVERRIDE"

        cleared = client.delete(
            f"/api/v1/classes/{class_id}/learning-cycles/8/schedule-override",
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["data"]["cycles"][7]["schedule_source"] == "DEFAULT"


def test_learning_plan_lifecycle_api_exposes_health_history_and_recommendation() -> None:
    _, class_id, _, _ = _fixture()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-admin-password"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        health = client.get(
            "/api/v1/classes/learning-plan-health",
            params={"class_org_unit_id": class_id},
            headers=headers,
        )
        assert health.status_code == 200, health.text
        assert health.json()["data"]["summary"]["total_classes"] == 1
        history = client.get(
            f"/api/v1/classes/{class_id}/learning-plan-history", headers=headers
        )
        assert history.status_code == 200, history.text
        assert history.json()["data"]["history_preserved"] is True
        recommendation = client.get(
            "/api/v1/learning-plans/recommendation",
            params={"started_at": "2027-10-01T19:00:00Z"},
            headers=headers,
        )
        assert recommendation.status_code == 200, recommendation.text
        assert recommendation.json()["data"]["cohort_month"] == 10


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
