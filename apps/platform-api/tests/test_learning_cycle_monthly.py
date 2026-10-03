from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.db import execute, fetch_all, fetch_one, transaction
from app.services import learning_cycles as cycles
from app.services import learning_cycle_monthly as monthly


@pytest.fixture
def calendar_class(monkeypatch):
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


def test_catch_up_is_idempotent_and_does_not_create_meeting_or_credit_facts(calendar_class):
    _, class_id, _, binding_id = calendar_class
    credits = fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"]
    result = monthly.refresh_class(class_id)
    assert result["advanced"] == 3
    assert result["learning_cycle_index"] == 4
    rows = _runtime(binding_id)
    assert [row["cycle_status"] for row in rows] == ["CLOSED", "CLOSED", "CLOSED", "OPEN"]
    assert all(row["actual_class_meeting_at"] is None and row["class_meeting_status"] == "PLANNED" for row in rows)
    assert rows[-1]["opened_at"] in ("2026-09-30T16:00:00+00:00", "2026-09-30 16:00:00") or str(rows[-1]["opened_at"]) == "2026-09-30 16:00:00"
    assert monthly.refresh_class(class_id)["advanced"] == 0
    assert fetch_one("SELECT COUNT(*) AS count FROM group_learning_cycle_tasks WHERE class_learning_cycle_id IN "
                     "(SELECT id FROM class_learning_cycles WHERE binding_id=?)", (binding_id,))["count"] == 0
    assert fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"] == credits
    assert cycles.get_class_learning_plan_history(user_id=calendar_class[0], class_org_unit_id=class_id)["bindings"][0]["cycle_summary"]["completed_through_cycle"] == 0


def test_manual_cycle_six_stays_this_month_and_advances_to_seven_next_month(calendar_class):
    admin, class_id, plan_id, binding_id = calendar_class
    cycles.correct_class_learning_plan(actor_user_id=admin, class_org_unit_id=class_id, plan_version_id=plan_id,
                                      cohort_month=4, learning_cycle_index=6, reason="本月人工校正为第六次")
    assert monthly.refresh_class(class_id)["advanced"] == 0
    assert _runtime(binding_id)[-1]["learning_cycle_index"] == 6
    assert monthly.refresh_class(class_id, at="2026-10-31T16:00:00+00:00")["learning_cycle_index"] == 7
    assert monthly.refresh_class(class_id, at="2026-11-15T00:00:00+00:00")["advanced"] == 0


def test_shanghai_month_boundary_and_year_rollover(calendar_class):
    _, class_id, _, binding_id = calendar_class
    assert monthly.refresh_class(class_id, at="2026-07-31T15:59:59+00:00")["advanced"] == 0
    assert monthly.refresh_class(class_id, at="2026-07-31T16:00:00+00:00")["learning_cycle_index"] == 2
    assert monthly.refresh_class(class_id, at="2026-12-31T16:00:00+00:00")["learning_cycle_index"] == 7
    assert len(_runtime(binding_id)) == 7


def test_confirmation_records_fact_without_advancing_twice_in_one_month(calendar_class):
    admin, class_id, _, binding_id = calendar_class
    monthly.refresh_class(class_id)
    result = cycles.confirm_class_meeting(actor_user_id=admin, class_org_unit_id=class_id,
                                          actual_class_meeting_at="2026-10-02T10:00:00+00:00")
    assert result["current_cycle"]["learning_cycle_index"] == 4
    assert result["current_cycle"]["cycle_status"] == "OPEN"
    assert result["current_cycle"]["class_meeting_status"] == "HELD"
    with pytest.raises(ValueError, match="不能重复确认"):
        cycles.confirm_class_meeting(actor_user_id=admin, class_org_unit_id=class_id,
                                     actual_class_meeting_at="2026-10-02T10:00:00+00:00")
    assert monthly.refresh_class(class_id)["advanced"] == 0
    monthly.refresh_class(class_id, at="2026-10-31T16:00:00+00:00")
    rows = _runtime(binding_id)
    assert rows[-2]["class_meeting_status"] == "HELD"
    assert rows[-1]["learning_cycle_index"] == 5
    assert rows[-1]["actual_class_meeting_at"] is None


def test_missing_template_rolls_back_whole_class(calendar_class):
    _, class_id, plan_id, binding_id = calendar_class
    with transaction() as connection:
        execute(connection, "DELETE FROM learning_plan_tasks WHERE plan_cycle_id IN "
                "(SELECT id FROM learning_plan_cycles WHERE plan_version_id=? AND cycle_index=3)", (plan_id,))
        execute(connection, "DELETE FROM learning_plan_cycles WHERE plan_version_id=? AND cycle_index=3", (plan_id,))
    with pytest.raises(ValueError, match="缺少第3"):
        monthly.refresh_class(class_id)
    assert len(_runtime(binding_id)) == 1
    assert _runtime(binding_id)[0]["cycle_status"] == "OPEN"


@pytest.mark.parametrize("gate", ["DEPLOYMENT_READ_ONLY", "production_without_mutation", "feature_disabled"])
def test_existing_write_gates_are_preserved(calendar_class, monkeypatch, gate):
    _, class_id, _, binding_id = calendar_class
    if gate == "production_without_mutation":
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ALLOW_PRODUCTION_MUTATIONS", "false")
    elif gate == "feature_disabled":
        monkeypatch.setenv("LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED", "false")
    else:
        monkeypatch.setenv(gate, "true")
    assert monthly.refresh_class(class_id)["status"] == "DISABLED"
    assert len(_runtime(binding_id)) == 1


def test_future_start_and_plan_end_do_not_fabricate_completion(calendar_class):
    _, class_id, _, binding_id = calendar_class
    assert monthly.refresh_class(class_id, at="2026-06-30T16:00:00+00:00")["advanced"] == 0
    assert monthly.refresh_class(class_id, at="2028-01-01T00:00:00+00:00")["learning_cycle_index"] == 12
    assert monthly.refresh_class(class_id, at="2028-02-01T00:00:00+00:00")["status"] == "PLAN_END_REACHED"
    assert _runtime(binding_id)[-1]["cycle_status"] == "OPEN"
    assert fetch_one("SELECT status FROM class_learning_bindings WHERE id=?", (binding_id,))["status"] == "ACTIVE"


def test_concurrent_workers_advance_each_month_once(calendar_class):
    _, class_id, _, binding_id = calendar_class
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: monthly.refresh_class(class_id), range(2)))
    assert sorted(result["advanced"] for result in results) == [0, 3]
    assert len(_runtime(binding_id)) == 4


def test_read_progress_recovers_an_idle_container(calendar_class):
    admin, class_id, _, binding_id = calendar_class
    assert len(_runtime(binding_id)) == 1
    progress = cycles.get_class_learning_progress(user_id=admin, class_org_unit_id=class_id)
    assert progress["current_cycle"]["learning_cycle_index"] == 4
    assert progress["current_cycle"]["plan_cycle"]["tasks"][0]["title"] == "本期内容4"


def test_postponement_is_preserved(calendar_class):
    admin, class_id, _, binding_id = calendar_class
    cycles.update_current_learning_cycle(actor_user_id=admin, class_org_unit_id=class_id,
                                        updates={"class_meeting_status": "POSTPONED", "planned_class_meeting_at": "2026-11-20T00:00:00+00:00"})
    assert monthly.refresh_class(class_id)["status"] == "POSTPONED"
    assert len(_runtime(binding_id)) == 1


def test_catch_up_stops_at_a_future_postponed_cycle(calendar_class):
    admin, class_id, _, binding_id = calendar_class
    cycles.set_learning_cycle_schedule_override(actor_user_id=admin, class_org_unit_id=class_id,
        learning_cycle_index=2, planned_class_meeting_at="2026-11-20T00:00:00+00:00", adjustment_reason="下期明确延期")
    assert monthly.refresh_class(class_id)["advanced"] == 1
    assert monthly.refresh_class(class_id)["status"] == "POSTPONED"
    assert _runtime(binding_id)[-1]["learning_cycle_index"] == 2


def test_miniprogram_context_catches_up_and_keeps_historical_registration(monkeypatch):
    from test_v12_mvp import _seed_group_leader_fixture
    from app.services.study_meetings import create_study_meeting, get_study_meeting_context
    fixture = _seed_group_leader_fixture()
    monkeypatch.setenv("STUDY_MEETING_SUBMISSION_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED", "false")
    meeting = create_study_meeting(member_id=fixture["member_id"], group_org_unit_id=fixture["group_id"],
                                   meeting_date=None, member_ids=[fixture["member_id"]], cross_group_member_ids=[], has_course=False)
    monkeypatch.setenv("LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED", "true")
    monkeypatch.setattr(cycles, "_now", lambda: "2026-10-03T13:00:00+00:00")
    with transaction() as connection:
        row = execute(connection, "SELECT binding_id FROM class_learning_cycles WHERE id=?", (fixture["learning_cycle_id"],)).fetchone()
        binding = execute(connection, "SELECT plan_version_id FROM class_learning_bindings WHERE id=?", (row["binding_id"],)).fetchone()
        execute(connection, "UPDATE class_learning_cycles SET opened_at=? WHERE id=?", ("2026-09-01T00:00:00+00:00", fixture["learning_cycle_id"]))
        execute(connection, "INSERT INTO learning_plan_cycles(plan_version_id,cohort_month,cycle_index,year_index,cycle_label,created_at,updated_at) "
                "VALUES (?,1,2,1,'第2期','2026-09-01','2026-09-01')", (binding["plan_version_id"],))
    context = get_study_meeting_context(member_id=fixture["member_id"], group_org_unit_id=fixture["group_id"])
    assert context["assignment"]["current_cycle"]["learning_cycle_index"] == 2
    assert fetch_one("SELECT learning_cycle_id FROM study_meeting_sessions WHERE id=?", (meeting["id"],))["learning_cycle_id"] == fixture["learning_cycle_id"]
