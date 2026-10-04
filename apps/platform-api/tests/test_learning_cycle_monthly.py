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
                                   meeting_date="2026-09-20", member_ids=[fixture["member_id"]], cross_group_member_ids=[], has_course=False)
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


def test_monthly_sweep_isolates_missing_template_and_keeps_other_class_idempotent(calendar_class, monkeypatch):
    _, good_class, _, good_binding = calendar_class
    _, _, bad_plan, bad_binding = _seed_calendar_class(monkeypatch)
    with transaction() as connection:
        execute(connection, "DELETE FROM learning_plan_tasks WHERE plan_cycle_id IN "
                "(SELECT id FROM learning_plan_cycles WHERE plan_version_id=? AND cycle_index=3)", (bad_plan,))
        execute(connection, "DELETE FROM learning_plan_cycles WHERE plan_version_id=? AND cycle_index=3", (bad_plan,))
    first = monthly.refresh_all()
    assert first["scanned"] >= 2 and first["updated"] >= 1 and first["failed"] >= 1
    assert _runtime(good_binding)[-1]["learning_cycle_index"] == 4
    assert len(_runtime(bad_binding)) == 1
    assert monthly.refresh_class(good_class)["advanced"] == 0
    second = monthly.refresh_all()
    assert second["updated"] == 0 and second["failed"] == first["failed"]
    assert _runtime(good_binding)[-1]["learning_cycle_index"] == 4
    assert len(_runtime(bad_binding)) == 1


@pytest.fixture
def imported_progress(calendar_class, monkeypatch):
    admin, class_id, plan_id, binding_id = calendar_class
    with transaction() as connection:
        plan_cycle = monthly.cycles._plan_cycle_for_track(connection, plan_version_id=plan_id, cohort_month=7, cycle_index=3)
        execute(connection, "UPDATE class_learning_bindings SET start_cycle_index=3, created_at=? WHERE id=?",
                ("2026-09-03T00:00:00+00:00", binding_id))
        execute(connection, "UPDATE class_learning_cycles SET learning_cycle_index=3, plan_cycle_id=?, created_at=? WHERE binding_id=?",
                (plan_cycle["id"], "2026-09-03T00:00:00+00:00", binding_id))
    yield calendar_class
    with transaction() as connection:
        execute(connection, "UPDATE class_learning_bindings SET status='COMPLETED' WHERE id=?", (binding_id,))


@pytest.mark.parametrize("cohort", [1, 4, 7, 10])
def test_september_cycle_three_advances_to_october_four_regardless_of_cohort(imported_progress, cohort):
    _, class_id, _, binding_id = imported_progress
    with transaction() as connection:
        execute(connection, "UPDATE class_learning_bindings SET cohort_month=?, started_at=? WHERE id=?",
                (cohort, f"2025-{cohort:02d}-01T00:00:00+00:00", binding_id))
        execute(connection, "UPDATE class_learning_cycles SET opened_at=? WHERE binding_id=?",
                (f"2025-{cohort:02d}-01T00:00:00+00:00", binding_id))
    first = monthly.refresh_class(class_id)
    assert first["learning_cycle_index"] == 4 and first["advanced"] == 1
    assert monthly.refresh_class(class_id)["advanced"] == 0
    assert len(_runtime(binding_id)) == 2


def _obsolete_jump(imported_progress):
    _, class_id, plan_id, binding_id = imported_progress
    with transaction() as connection:
        original = execute(connection, "SELECT * FROM class_learning_cycles WHERE binding_id=?", (binding_id,)).fetchone()
        execute(connection, "UPDATE class_learning_cycles SET cycle_status='CLOSED', closed_at=? WHERE id=?",
                ("2026-07-31T16:00:00+00:00", original["id"]))
        final_id = None
        for index in range(4, 7):
            plan_cycle = cycles._plan_cycle_for_track(connection, plan_version_id=plan_id, cohort_month=7, cycle_index=index)
            boundary = calendar.month_start(calendar.month_number("2026-07-01") + index - 3)
            cursor = execute(connection, "INSERT INTO class_learning_cycles(binding_id,class_org_unit_id,learning_cycle_index,plan_cycle_id,"
                "opened_at,planned_class_meeting_at,class_meeting_status,group_meeting_policy,cycle_status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'PLANNED','REQUIRED',?,'2026-10-03T13:00:00+00:00','2026-10-03T13:00:00+00:00')",
                (binding_id, class_id, index, plan_cycle["id"], boundary, boundary, "OPEN" if index == 6 else "CLOSED"))
            final_id = int(cursor.lastrowid)
        write_audit(connection, actor_user_id=None, action="learning.cycle.monthly_refresh", resource_type="class_learning_binding",
            resource_id=str(binding_id), org_unit_id=class_id,
            before={"learning_cycle_index": 3, "cycle_id": original["id"]},
            after={"learning_cycle_index": 6, "cycle_id": final_id, "binding_id": binding_id,
                   "as_of": "2026-10-03T13:00:00+00:00", "advanced": 3})


def test_imported_in_october_uses_the_dated_september_reference(imported_progress, monkeypatch):
    _, class_id, _, binding_id = imported_progress
    monkeypatch.setattr(calendar, "load_baseline", lambda: {"baseline_as_of": "2026-09", "classes": [{
        "class_org_unit_id": class_id, "class_name": "月更测试班", "expected_plan_version": "2026",
        "expected_cohort_month": 7, "expected_current_cycle": 3}]})
    with transaction() as connection:
        execute(connection, "UPDATE class_learning_cycles SET created_at=? WHERE binding_id=?", (cycles._now(), binding_id))
    assert monthly.refresh_class(class_id)["learning_cycle_index"] == 4


def test_obsolete_monthly_jump_waits_for_reviewed_snapshot_and_preserves_all_rows(imported_progress):
    _, class_id, _, binding_id = imported_progress
    _obsolete_jump(imported_progress)
    before = _runtime(binding_id)
    credits_before = fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"]
    assert monthly.refresh_class(class_id)["status"] == "REPAIR_REQUIRED"
    health = cycles.scan_class_learning_plan_health(user_id=imported_progress[0], class_org_unit_id=class_id)
    assert health["summary"]["monthly_anchor_errors"] == 1
    assert any(issue["issue_type"] == "MONTHLY_ANCHOR_REPAIR_REQUIRED" for issue in health["classes"][0]["issues"])
    from app.services.study_meetings import StudyMeetingError, _refresh_monthly_content
    with pytest.raises(StudyMeetingError, match="错误周期"):
        _refresh_monthly_content(class_id)
    assert calendar.apply_snapshot("0" * 64, at=cycles._now())["status"] == "REPAIR_SNAPSHOT_CHANGED"
    with transaction() as connection:
        proof = calendar.collect_audit(connection, at=cycles._now())
    result = calendar.apply_snapshot(proof["snapshot_id"], at=cycles._now())
    assert result["status"] == "REPAIRED"
    rows = _runtime(binding_id)
    assert [row["id"] for row in rows] == [row["id"] for row in before]
    assert [row["cycle_status"] for row in rows] == ["CLOSED", "OPEN", "UPCOMING", "UPCOMING"]
    assert cycles.get_class_learning_progress(user_id=imported_progress[0], class_org_unit_id=class_id)["current_cycle"]["learning_cycle_index"] == 4
    assert monthly.refresh_class(class_id)["advanced"] == 0
    assert cycles.scan_class_learning_plan_health(user_id=imported_progress[0], class_org_unit_id=class_id)["summary"]["monthly_anchor_errors"] == 0
    assert monthly.refresh_class(class_id, at="2026-10-31T16:00:00+00:00")["learning_cycle_index"] == 5
    assert _runtime(binding_id)[2]["id"] == before[2]["id"]
    assert monthly.refresh_class(class_id, at="2026-11-30T16:00:00+00:00")["learning_cycle_index"] == 6
    assert len(_runtime(binding_id)) == len(before)
    assert calendar.apply_snapshot(proof["snapshot_id"], at="2026-12-01T00:00:00+00:00")["status"] == "NO_REPAIR_REQUIRED"
    assert fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"] == credits_before


def test_real_fact_after_obsolete_jump_blocks_automatic_repair(imported_progress):
    _, class_id, _, binding_id = imported_progress
    _obsolete_jump(imported_progress)
    with transaction() as connection:
        execute(connection, "UPDATE class_learning_cycles SET class_meeting_status='HELD',actual_class_meeting_at=? "
                "WHERE binding_id=? AND learning_cycle_index=6", ("2026-10-02T10:00:00+00:00", binding_id))
    before = _runtime(binding_id)
    assert monthly.refresh_class(class_id)["status"] == "REPAIR_REVIEW_REQUIRED"
    with transaction() as connection:
        report = calendar.collect_audit(connection, at=cycles._now())
    row = next(row for row in report["rows"] if row["class_id"] == class_id)
    assert row["reason"] == "GENERATED_CYCLE_HAS_BUSINESS_FACTS_OR_OVERRIDE"
    assert _runtime(binding_id) == before


def test_manual_confirmation_after_obsolete_job_is_preserved(imported_progress):
    admin, class_id, plan_id, binding_id = imported_progress
    _obsolete_jump(imported_progress)
    cycles.correct_class_learning_plan(actor_user_id=admin, class_org_unit_id=class_id, plan_version_id=plan_id,
                                      cohort_month=7, learning_cycle_index=8, reason="本月人工明确为第八次")
    assert monthly.refresh_class(class_id)["advanced"] == 0
    assert _runtime(binding_id)[-1]["learning_cycle_index"] == 8


@pytest.mark.parametrize("planned", [None, "2025-10-01T00:00:00+00:00", "2026-11-20T00:00:00+00:00"])
def test_repair_retains_original_postponement_instead_of_advancing_it(imported_progress, planned):
    admin, class_id, _, binding_id = imported_progress
    _obsolete_jump(imported_progress)
    with transaction() as connection:
        execute(connection, "UPDATE class_learning_cycles SET class_meeting_status='POSTPONED',planned_class_meeting_at=? "
                "WHERE binding_id=? AND learning_cycle_index=3", (planned, binding_id))
        report = calendar.collect_audit(connection, at=cycles._now())
    proposal = next(row for row in report["repairs"] if row["binding_id"] == binding_id)
    assert proposal["target_index"] == 3
    assert calendar.apply_snapshot(report["snapshot_id"], at=cycles._now())["status"] == "REPAIRED"
    current = cycles.get_class_learning_progress(user_id=admin, class_org_unit_id=class_id)["current_cycle"]
    assert current["learning_cycle_index"] == 3 and current["class_meeting_status"] == "POSTPONED"
    assert monthly.refresh_class(class_id)["status"] == "POSTPONED"
    assert [row["cycle_status"] for row in _runtime(binding_id)] == ["OPEN", "UPCOMING", "UPCOMING", "UPCOMING"]


def test_production_startup_finishes_once_without_an_idle_background_loop(monkeypatch):
    calls = []
    monkeypatch.setattr(monthly, "get_settings", lambda: SimpleNamespace(is_production=True))
    monkeypatch.setattr(monthly, "refresh_sweep", lambda **kwargs: calls.append(kwargs))
    asyncio.run(asyncio.wait_for(monthly.run_monthly_refresh(), timeout=2))
    assert calls == [{"startup_repair": True}]


def test_request_sweep_is_idempotent_and_cannot_apply_an_old_repair(imported_progress, monkeypatch):
    _, class_id, _, binding_id = imported_progress
    # Focus this scheduled sweep on one isolated imported September ordinal.
    def refresh_fixture():
        result = monthly.refresh_class(class_id)
        return {"scanned": 1, "updated": int(bool(result["advanced"])), "failed": 0}
    monkeypatch.setattr(monthly, "refresh_all", refresh_fixture)
    def forbid_repair(**kwargs):
        raise AssertionError("A timer cannot apply a historical repair snapshot")
    monkeypatch.setattr(calendar, "audit_and_repair", forbid_repair)
    monkeypatch.setenv(calendar.REPAIR_ENV, "0" * 64)
    credits_before = fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"]
    first = monthly.refresh_sweep()
    second = monthly.refresh_sweep()
    assert first["updated"] == 1 and second["updated"] == 0
    assert _runtime(binding_id)[-1]["learning_cycle_index"] == 4
    assert fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries")["count"] == credits_before
    assert all(row["actual_class_meeting_at"] is None for row in _runtime(binding_id))
