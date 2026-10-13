from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db import execute, fetch_all, fetch_one, transaction
from app.services import learning_cycles as cycles
from app.services import learning_cycle_monthly as monthly
from app.services import learning_cycle_calendar as calendar
from app.services.audit import write_audit


@pytest.fixture
def calendar_class(monkeypatch):
    return _seed_calendar_class(monkeypatch)


def _seed_calendar_class(monkeypatch):
    monkeypatch.setenv("LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED", "true")
    monkeypatch.setenv("DEPLOYMENT_READ_ONLY", "false")
    monkeypatch.setattr(cycles, "_now", lambda: "2026-10-03T13:00:00+00:00")
    suffix = uuid4().hex[:10]
    class_id = f"monthly-class-{suffix}"
    admin = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    now = cycles._now()
    with transaction() as connection:
        execute(connection, "INSERT INTO org_units(id,unit_code,name,unit_type,parent_id,is_active,created_at,updated_at) "
                "VALUES (?,?,?,'CLASS','org-suzhou',1,?,?)", (class_id, class_id, "月更测试班", now, now))
        cursor = execute(connection, "INSERT INTO learning_plan_versions(plan_key,plan_name,version_label,duration_cycles,status,created_at,updated_at) "
                         "VALUES (?,'月更测试计划','2026',12,'PUBLISHED',?,?)", (class_id, now, now))
        plan_id = int(cursor.lastrowid)
        policy = execute(connection, "SELECT generic_rule_version_id,course_credit_rule_version_id FROM learning_plan_credit_rule_mappings "
                         "WHERE plan_key='standard-3y' AND plan_version_label='2026' AND status='ACTIVE' LIMIT 1").fetchone()
        execute(connection, "INSERT INTO learning_plan_credit_rule_mappings(plan_key,plan_version_label,generic_rule_version_id,course_credit_rule_version_id,"
                "status,mapping_source,created_at,updated_at) VALUES (?,'2026',?,?,'ACTIVE','MONTHLY_TEST',?,?)",
                (class_id, policy["generic_rule_version_id"], policy["course_credit_rule_version_id"], now, now))
        for index in range(1, 13):
            cursor = execute(connection, "INSERT INTO learning_plan_cycles(plan_version_id,cycle_index,year_index,cycle_label,created_at,updated_at) "
                             "VALUES (?,?,1,?,?,?)", (plan_id, index, f"第{index}次", now, now))
            execute(connection, "INSERT INTO learning_plan_tasks(plan_cycle_id,task_type,title,is_required,sort_order,created_at,updated_at) "
                    "VALUES (?,'GROUP_MEETING',?,1,1,?,?)", (cursor.lastrowid, f"本期内容{index}", now, now))
    progress = cycles.bind_class_learning_plan(actor_user_id=admin, class_org_unit_id=class_id, plan_version_id=plan_id,
                                               cohort_month=7, started_at="2026-07-20T00:00:00+00:00")
    return admin, class_id, plan_id, int(progress["binding"]["id"])


def _runtime(binding_id):
    return fetch_all("SELECT * FROM class_learning_cycles WHERE binding_id=? ORDER BY learning_cycle_index", (binding_id,))


@pytest.mark.parametrize("at", ["2026-07-31T16:00:00+00:00", "2026-10-31T16:00:00+00:00", "2028-01-01T00:00:00+00:00"])
def test_natural_months_never_advance_even_with_old_flag_enabled(calendar_class, at):
    admin, class_id, _, binding_id = calendar_class
    before = _runtime(binding_id)
    assert monthly.refresh_class(class_id, at=at)["advanced"] == 0
    assert _runtime(binding_id) == before
    status = monthly.monthly_status({"status":"ACTIVE"}, before[0], at)
    assert status["progression_mode"] == "CLASS_MEETING_SETTLEMENT"
    assert status["next_refresh_at"] is None and not status["enabled"]


def test_plan_reads_keep_current_cycle_across_months(calendar_class):
    admin, class_id, _, binding_id = calendar_class
    before = _runtime(binding_id)
    progress = cycles.get_class_learning_progress(user_id=admin, class_org_unit_id=class_id)
    schedule = cycles.get_class_learning_schedule(user_id=admin, class_org_unit_id=class_id)
    assert progress["current_cycle"]["learning_cycle_index"] == 1
    assert schedule["actual_projection"][0]["learning_cycle_index"] == 1
    assert _runtime(binding_id) == before


def test_existing_scheduler_and_startup_do_not_write_or_apply_old_repairs(calendar_class):
    _, _, _, binding_id = calendar_class
    before = _runtime(binding_id)
    assert monthly.refresh_sweep(startup_repair=True)["updated"] == 0
    assert not monthly.refresh_sweep()["enabled"]
    asyncio.run(monthly.run_monthly_refresh())
    assert _runtime(binding_id) == before


def test_manual_meeting_confirmation_still_uses_actual_boundary(calendar_class):
    admin, class_id, _, binding_id = calendar_class
    progress = cycles.confirm_class_meeting(actor_user_id=admin, class_org_unit_id=class_id,
        actual_class_meeting_at="2026-10-02T01:00:00+00:00")
    assert progress["current_cycle"]["learning_cycle_index"] == 2
    assert len(_runtime(binding_id)) == 2
    assert monthly.refresh_class(class_id, at="2026-11-01T00:00:00+00:00")["advanced"] == 0
