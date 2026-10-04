"""A learning ordinal belongs to a recorded month, never to a cohort month.

The September reference is read only. Repairs are limited to rows created by
the obsolete monthly job, with an exact, operator-reviewed snapshot digest.
Meeting, attendance, completion and credit facts are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.db import connect, execute, transaction
from app.services import learning_cycles as cycles
from app.services.audit import write_audit
from app.services.learning_cycle_schedule import add_calendar_months, parse_utc_datetime
from app.services.learning_plan_baseline import baseline_by_class_id, is_learning_plan_binding_required, load_baseline

POLICY = "REGISTERED_MONTH_V2"
REPAIR_ACTION = "learning.cycle.monthly_anchor_repair"
REPAIR_ENV = "LEARNING_CYCLE_MONTHLY_REPAIR_SNAPSHOT"
BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
logger = logging.getLogger("uvicorn.error")


def month_number(value: Any) -> int:
    local = parse_utc_datetime(value).astimezone(BUSINESS_TIMEZONE)
    return local.year * 12 + local.month - 1


def month_start(number: int) -> str:
    year, month = divmod(number, 12)
    return datetime(year, month + 1, 1, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC).isoformat()


def month_label(number: int) -> str:
    year, month = divmod(number, 12)
    return f"{year:04d}-{month + 1:02d}"


def _payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        result = json.loads(raw or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _events(connection, binding_id: int) -> list[dict]:
    return [dict(row) for row in execute(connection,
        "SELECT id, action, before_json, after_json, created_at FROM audit_logs "
        "WHERE resource_type='class_learning_binding' AND resource_id=? AND result='SUCCESS' "
        "AND action IN ('learning.cycle.monthly_refresh','learning.binding.correction', "
        "'learning.cycle.monthly_anchor_repair') ORDER BY id", (str(binding_id),)).fetchall()]


def _reference(connection, binding: dict, cycle: dict) -> dict | None:
    baseline = load_baseline()
    item = baseline_by_class_id(baseline).get(str(binding["class_org_unit_id"]))
    org = execute(connection, "SELECT name FROM org_units WHERE id=?", (binding["class_org_unit_id"],)).fetchone()
    if (not item or not org or item.get("class_name") != org["name"]
        or item.get("migration_status") == "MANUAL_REVIEW_REQUIRED"
        or str(item.get("expected_plan_version")) != str(binding["version_label"])
        or item.get("expected_cohort_month") != binding.get("cohort_month")
        or item.get("expected_current_cycle") != int(cycle["learning_cycle_index"])):
        return None
    return {"month": month_number(str(baseline["baseline_as_of"]) + "-01"),
            "source": "BUSINESS_BASELINE_RECORDED_MONTH"}


def clock(connection, binding: dict, cycle: dict, *, events: list[dict] | None = None) -> dict:
    """Resolve the month of this ordinal; an imported start index can be >1."""
    index = int(cycle["learning_cycle_index"])
    events = _events(connection, int(binding["id"])) if events is None else events
    for event in reversed(events):
        after = _payload(event["after_json"])
        if event["action"] == REPAIR_ACTION and int(after.get("current_cycle_id") or 0) == int(cycle["id"]):
            return {"month": month_number(after["current_month"] + "-01"), "source": POLICY}
        if event["action"] == "learning.cycle.monthly_refresh" and after.get("policy_version") == POLICY:
            if int(after.get("cycle_id") or 0) == int(cycle["id"]):
                return {"month": month_number(cycle["opened_at"]), "source": POLICY}
        if event["action"] == "learning.binding.correction":
            corrected = after.get("cycle") or {}
            if int(corrected.get("id") or 0) == int(cycle["id"]) and int(corrected.get("learning_cycle_index") or 0) == index:
                return {"month": month_number(event["created_at"]), "source": "MANUAL_CORRECTION_MONTH"}
    if binding.get("transition_type") == "CORRECTION":
        return {"month": month_number(binding["updated_at"]), "source": "MANUAL_CORRECTION_MONTH"}
    # The meeting date is a business date in China, not a UTC instant.
    registered = execute(connection, "SELECT MAX(meeting_date) AS last_date, COUNT(*) AS count "
        "FROM study_meeting_sessions WHERE learning_cycle_id=? AND status='SUBMITTED'", (cycle["id"],)).fetchone()
    if registered["last_date"]:
        return {"month": month_number(str(registered["last_date"])[:10]), "source": "REGISTERED_MEETING_MONTH"}
    if cycle.get("actual_class_meeting_at"):
        return {"month": month_number(cycle["actual_class_meeting_at"]), "source": "ACTUAL_CLASS_MEETING_MONTH"}
    # Legacy class-meeting confirmation already opened the following ordinal.
    # It belongs to the following month, even if the row was created that day.
    previous = execute(connection, "SELECT learning_cycle_index, actual_class_meeting_at FROM class_learning_cycles "
        "WHERE binding_id=? AND learning_cycle_index<? AND class_meeting_status='HELD' "
        "AND actual_class_meeting_at IS NOT NULL ORDER BY learning_cycle_index DESC LIMIT 1",
        (binding["id"], index)).fetchone()
    if previous:
        return {"month": month_number(previous["actual_class_meeting_at"]) + index - int(previous["learning_cycle_index"]),
                "source": "FOLLOWING_CONFIRMED_CLASS_MEETING"}
    reference = _reference(connection, binding, cycle)
    if reference:
        return reference
    if index == 1 and int(binding.get("start_cycle_index") or 1) == 1:
        return {"month": month_number(cycle["opened_at"]), "source": "FIRST_CYCLE_FORMAL_START"}
    # INITIAL/RESUME at ordinal 3 or 9 records current progress at configuration
    # time. Its historical formal start remains a cohort/schedule reference.
    return {"month": month_number(cycle["created_at"]), "source": "CURRENT_CYCLE_RECORDING_MONTH"}


def _fact_counts(connection, cycle_id: int) -> dict[str, int]:
    result = {}
    for key, sql in {
        "registrations": "SELECT COUNT(*) AS count FROM study_meeting_sessions WHERE learning_cycle_id=?",
        "credits": "SELECT COUNT(*) AS count FROM learning_credit_entries WHERE learning_cycle_id=?",
        "completed_group_tasks": "SELECT COUNT(*) AS count FROM group_learning_cycle_tasks WHERE class_learning_cycle_id=? AND status<>'PENDING'",
    }.items():
        result[key] = int(execute(connection, sql, (cycle_id,)).fetchone()["count"])
    return result


def inspect_binding(connection, binding: dict, current: dict | None, *, at: str) -> dict:
    if not current:
        return {"status": "NOT_STARTED", "repair": None}
    events = _events(connection, int(binding["id"]))
    last_override = max((int(event["id"]) for event in events if event["action"] in
        {"learning.binding.correction", REPAIR_ACTION}), default=0)
    old = [event for event in events if int(event["id"]) > last_override
        and event["action"] == "learning.cycle.monthly_refresh"
        and _payload(event["after_json"]).get("policy_version") != POLICY]
    if not old:
        return {"status": "CURRENT", "repair": None, "clock": clock(connection, binding, current, events=events)}
    before = _payload(old[0]["before_json"])
    previous_after = None
    for event in old:
        start, end = _payload(event["before_json"]), _payload(event["after_json"])
        if (not end.get("as_of") or int(end.get("binding_id") or 0) != int(binding["id"])
            or int(end.get("learning_cycle_index") or 0) - int(start.get("learning_cycle_index") or 0) != int(end.get("advanced") or 0)
            or (previous_after and (start.get("cycle_id") != previous_after.get("cycle_id")
                                    or start.get("learning_cycle_index") != previous_after.get("learning_cycle_index")))):
            return {"status": "REPAIR_REVIEW_REQUIRED", "reason": "MONTHLY_AUDIT_CHAIN_INVALID", "repair": None}
        previous_after = end
    original_row = execute(connection, "SELECT * FROM class_learning_cycles WHERE id=? AND binding_id=?",
                           (before.get("cycle_id"), binding["id"])).fetchone()
    if not original_row or int(original_row["learning_cycle_index"]) != int(before.get("learning_cycle_index") or 0):
        return {"status": "REPAIR_REVIEW_REQUIRED", "reason": "ORIGINAL_CYCLE_CHANGED", "repair": None}
    original = dict(original_row)
    base_clock = clock(connection, binding, original, events=events)
    anchor = int(base_clock["month"])
    original_index = int(original["learning_cycle_index"])
    target = min(int(binding["duration_cycles"]), original_index + max(0, month_number(at) - anchor))
    # A future postponement is an explicit business exception to the calendar.
    planned = original.get("planned_class_meeting_at")
    scheduled_anchor = anchor
    if original["class_meeting_status"] == "POSTPONED":
        if not planned or month_number(planned) < anchor or parse_utc_datetime(planned) > parse_utc_datetime(at):
            # A stale cohort-relative date is not a resumption decision. Keep
            # the paused ordinal while restoring only the erroneous rows.
            target = original_index
            scheduled_anchor = max(month_number(at), month_number(planned) if planned else anchor)
        else:
            scheduled_anchor = max(anchor, month_number(planned))
            target = min(int(binding["duration_cycles"]), original_index + max(0, month_number(at) - scheduled_anchor))
    if int(current["learning_cycle_index"]) == target:
        return {"status": "CURRENT", "repair": None,
                "clock": {"month": anchor + target - original_index, "source": base_clock["source"]}}
    final_after = _payload(old[-1]["after_json"])
    generated_end = int(final_after.get("learning_cycle_index") or 0)
    if (generated_end != int(current["learning_cycle_index"]) or generated_end < target
        or int(final_after.get("cycle_id") or 0) != int(current["id"])):
        return {"status": "REPAIR_REVIEW_REQUIRED", "reason": "LATER_PROGRESS_CONFLICT", "repair": None}
    generated = [dict(row) for row in execute(connection, "SELECT * FROM class_learning_cycles WHERE binding_id=? "
        "AND learning_cycle_index>? AND learning_cycle_index<=? ORDER BY learning_cycle_index",
        (binding["id"], original_index, generated_end)).fetchall()]
    if [int(row["learning_cycle_index"]) for row in generated] != list(range(original_index + 1, generated_end + 1)):
        return {"status": "REPAIR_REVIEW_REQUIRED", "reason": "GENERATED_CHAIN_INCOMPLETE", "repair": None}
    snapshots = []
    for row in generated:
        facts = _fact_counts(connection, int(row["id"]))
        if (any(facts.values()) or row.get("actual_class_meeting_at") or row.get("source_event_group_id")
            or row["class_meeting_status"] != "PLANNED" or row.get("adjustment_reason")
            or cycles._active_schedule_override(connection, binding_id=int(binding["id"]),
                                                learning_cycle_index=int(row["learning_cycle_index"]))):
            return {"status": "REPAIR_REVIEW_REQUIRED", "reason": "GENERATED_CYCLE_HAS_BUSINESS_FACTS_OR_OVERRIDE",
                    "conflicting_cycle_index": int(row["learning_cycle_index"]), "repair": None}
        snapshots.append({key: row.get(key) for key in ("id", "learning_cycle_index", "plan_cycle_id", "opened_at",
            "planned_class_meeting_at", "cycle_status", "closed_at", "updated_at")})
    repair = {"class_org_unit_id": binding["class_org_unit_id"], "binding_id": int(binding["id"]),
        "plan_version_id": int(binding["plan_version_id"]), "cohort_month": binding.get("cohort_month"),
        "binding_updated_at": binding["updated_at"],
        "audit_ids": [int(event["id"]) for event in old], "original_cycle_id": int(original["id"]),
        "original_index": original_index, "original_month": month_label(anchor), "clock_source": base_clock["source"],
        "original_class_meeting_status": original["class_meeting_status"],
        "original_planned_class_meeting_at": original.get("planned_class_meeting_at"),
        "scheduled_anchor_month": month_label(scheduled_anchor),
        "original_closed_at": original.get("closed_at"), "original_updated_at": original["updated_at"],
        "current_index": int(current["learning_cycle_index"]), "target_index": target,
        "current_month": month_label(scheduled_anchor + target - original_index), "generated": snapshots}
    return {"status": "REPAIR_REQUIRED", "repair": repair, "clock": base_clock}


def can_activate_upcoming(connection, binding_id: int, cycle: dict) -> bool:
    if cycle["cycle_status"] != "UPCOMING" or cycle.get("actual_class_meeting_at") or any(_fact_counts(connection, int(cycle["id"])).values()):
        return False
    return any(int(cycle["id"]) in _payload(event["after_json"]).get("repaired_cycle_ids", [])
               for event in _events(connection, binding_id) if event["action"] == REPAIR_ACTION)


def collect_audit(connection, *, at: str) -> dict:
    rows, plans = [], []
    references = baseline_by_class_id(load_baseline())
    classes = execute(connection, "SELECT id, unit_code, name FROM org_units WHERE is_active=1 "
        "AND unit_type IN ('CLASS','SPECIAL_COHORT') ORDER BY id").fetchall()
    for org in classes:
        binding = cycles._active_binding(connection, org["id"])
        row = {"class_id": org["id"], "unit_code": org["unit_code"], "class_name": org["name"],
               "status": "UNBOUND", "current_index": None, "target_index": None}
        reference = references.get(str(org["id"]))
        if not binding and reference and reference.get("class_name") == org["name"] and not is_learning_plan_binding_required(reference):
            row["status"] = "NOT_APPLICABLE"
        if binding:
            count = execute(connection, "SELECT COUNT(*) AS count FROM class_learning_bindings "
                            "WHERE class_org_unit_id=? AND status='ACTIVE'", (org["id"],)).fetchone()["count"]
            if int(count) != 1:
                rows.append({**row, "status": "REPAIR_REVIEW_REQUIRED", "reason": "MULTIPLE_ACTIVE_BINDINGS"})
                continue
            current = cycles._cycle_at(connection, int(binding["id"]), at)
            state = inspect_binding(connection, binding, current, at=at)
            row.update(binding_id=int(binding["id"]), cohort_month=binding.get("cohort_month"),
                plan_version=binding["version_label"], status=state["status"],
                current_index=int(current["learning_cycle_index"]) if current else None,
                class_meeting_status=current["class_meeting_status"] if current else None,
                binding_created_month=month_label(month_number(binding["created_at"])),
                transition_type=binding.get("transition_type"),
                cycle_opened_month=month_label(month_number(current["opened_at"])) if current else None)
            if state.get("clock"):
                row.update(recorded_month=month_label(state["clock"]["month"]), clock_source=state["clock"]["source"])
            if state.get("reason"):
                row.update(reason=state["reason"], conflicting_cycle_index=state.get("conflicting_cycle_index"))
            if state.get("repair"):
                plan = state["repair"]
                row.update(target_index=plan["target_index"], original_index=plan["original_index"],
                    original_month=plan["original_month"], old_monthly_audit_ids=plan["audit_ids"],
                    original_class_meeting_status=plan["original_class_meeting_status"],
                    original_planned_class_meeting_at=plan["original_planned_class_meeting_at"],
                    generated_cycle_count=len(plan["generated"]))
                plans.append(plan)
            else:
                row["target_index"] = row["current_index"]
        rows.append(row)
    # MySQL timestamps and SQLite ISO strings must canonicalize identically
    # within one inspected target. Precision is preserved for optimistic guards.
    canonical = json.dumps(plans, sort_keys=True, default=str, separators=(",", ":"))
    return {"policy_version": POLICY, "month": month_label(month_number(at)), "rows": rows,
            "repairs": plans, "snapshot_id": hashlib.sha256(canonical.encode()).hexdigest()}


def apply_snapshot(expected: str, *, at: str) -> dict:
    from app.services.learning_cycle_monthly import enabled
    if not enabled():
        return {"status": "DISABLED", "repaired": 0}
    with transaction() as connection:
        class_rows = execute(connection, "SELECT DISTINCT class_org_unit_id FROM class_learning_bindings "
            "WHERE status='ACTIVE' ORDER BY class_org_unit_id").fetchall()
        for row in class_rows:
            cycles._lock_class_for_update(connection, row["class_org_unit_id"])
        audit = collect_audit(connection, at=at)
        if not audit["repairs"]:
            return {"status": "NO_REPAIR_REQUIRED", "repaired": 0}
        if audit["snapshot_id"] != expected:
            return {"status": "REPAIR_SNAPSHOT_CHANGED", "repaired": 0}
        now = cycles._storage_datetime(connection, at)
        for plan in audit["repairs"]:
            binding = cycles._active_binding(connection, plan["class_org_unit_id"])
            anchor = month_number(plan["scheduled_anchor_month"] + "-01")
            original = execute(connection, "SELECT * FROM class_learning_cycles WHERE id=?", (plan["original_cycle_id"],)).fetchone()
            target_id = plan["original_cycle_id"]
            for row in plan["generated"]:
                index = int(row["learning_cycle_index"])
                period_month = anchor + index - plan["original_index"]
                boundary = cycles._storage_datetime(connection, month_start(period_month))
                planned = row["planned_class_meeting_at"] or month_start(period_month)
                planned = add_calendar_months(planned, period_month - month_number(planned))
                status = "CLOSED" if index < plan["target_index"] else "OPEN" if index == plan["target_index"] else "UPCOMING"
                closed = cycles._storage_datetime(connection, month_start(period_month + 1)) if status == "CLOSED" else None
                execute(connection, "UPDATE class_learning_cycles SET opened_at=?, planned_class_meeting_at=?, "
                    "cycle_status=?, closed_at=?, updated_at=? WHERE id=? AND binding_id=?",
                    (boundary, cycles._storage_datetime(connection, planned), status, closed, now, row["id"], binding["id"]))
                if index == plan["target_index"]:
                    target_id = int(row["id"])
            first_boundary = cycles._storage_datetime(connection, month_start(anchor + 1))
            execute(connection, "UPDATE class_learning_cycles SET cycle_status=?, closed_at=?, updated_at=? WHERE id=?",
                ("CLOSED" if plan["target_index"] > plan["original_index"] else "OPEN",
                 first_boundary if plan["target_index"] > plan["original_index"] else None, now, original["id"]))
            write_audit(connection, actor_user_id=None, action=REPAIR_ACTION, resource_type="class_learning_binding",
                resource_id=str(binding["id"]), org_unit_id=plan["class_org_unit_id"],
                purpose="用户2026-10-04明确要求修复错误月更；保留登记、课程、学分和人工校正事实",
                before=plan, after={"policy_version": POLICY, "snapshot_id": expected,
                    "current_cycle_id": target_id, "learning_cycle_index": plan["target_index"],
                    "current_month": plan["current_month"], "repaired_cycle_ids": [row["id"] for row in plan["generated"]],
                    "future_rows_preserved_as_upcoming": True})
        return {"status": "REPAIRED", "repaired": len(audit["repairs"]), "snapshot_id": expected}


def audit_and_repair(*, at: str) -> dict:
    """Private operations logs only; no business data added to public probes."""
    connection = connect()
    try:
        audit = collect_audit(connection, at=at)
    finally:
        connection.close()
    from app.core.build_info import get_build_info
    commit = get_build_info()["commit_sha"]
    for row in audit["rows"]:
        logger.info("MONTHLY_CALENDAR_AUDIT_ROW %s", json.dumps({**row,
            "snapshot_id": audit["snapshot_id"], "commit_sha": commit, "month": audit["month"]}, ensure_ascii=False, default=str))
    configured = os.environ.get(REPAIR_ENV, "")
    result = apply_snapshot(configured, at=at) if configured else {"status": "AUDIT_ONLY", "repaired": 0}
    summary = {"commit_sha": commit, "as_of": at, "month": audit["month"], "snapshot_id": audit["snapshot_id"],
        "scanned": len(audit["rows"]), "repair_required": len(audit["repairs"]),
        "review_required": sum(row["status"] == "REPAIR_REVIEW_REQUIRED" for row in audit["rows"]), **result}
    logger.info("MONTHLY_CALENDAR_AUDIT_RESULT %s", json.dumps(summary, sort_keys=True))
    return summary


def log_verification(*, at: str) -> dict:
    connection = connect()
    try:
        audit = collect_audit(connection, at=at)
    finally:
        connection.close()
    from app.core.build_info import get_build_info
    commit = get_build_info()["commit_sha"]
    for row in audit["rows"]:
        logger.info("MONTHLY_CALENDAR_VERIFIED_ROW %s", json.dumps({**row,
            "snapshot_id": audit["snapshot_id"], "commit_sha": commit, "month": audit["month"]}, ensure_ascii=False, default=str))
    summary = {"commit_sha": commit, "as_of": at, "month": audit["month"], "snapshot_id": audit["snapshot_id"],
        "scanned": len(audit["rows"]), "repair_required": len(audit["repairs"]),
        "review_required": sum(row["status"] == "REPAIR_REVIEW_REQUIRED" for row in audit["rows"]),
        "unbound": sum(row["status"] == "UNBOUND" for row in audit["rows"]),
        "not_applicable": sum(row["status"] == "NOT_APPLICABLE" for row in audit["rows"])}
    logger.info("MONTHLY_CALENDAR_VERIFIED_RESULT %s", json.dumps(summary, sort_keys=True))
    return summary
