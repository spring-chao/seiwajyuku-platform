"""Calendar progression of configured class content; never create meeting facts."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.core.settings import get_settings
from app.db import execute, fetch_all, transaction
from app.services.audit import write_audit
from app.services.learning_cycle_schedule import parse_utc_datetime
from app.services import learning_cycles as cycles

BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


def enabled() -> bool:
    settings = get_settings()
    return (
        settings.learning_cycle_monthly_refresh_enabled
        and not settings.deployment_read_only
        and (not settings.is_production or settings.allow_production_mutations)
    )


def _month_number(value: Any) -> int:
    local = parse_utc_datetime(value).astimezone(BUSINESS_TIMEZONE)
    return local.year * 12 + local.month - 1


def _month_start(number: int) -> str:
    year, month = divmod(number, 12)
    return datetime(year, month + 1, 1, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC).isoformat()


def monthly_status(binding: dict, cycle: dict | None, at: str) -> dict[str, Any]:
    result: dict[str, Any] = {"enabled": enabled(), "timezone": "Asia/Shanghai"}
    if not cycle or binding["status"] != "ACTIVE":
        return {**result, "status": "NOT_STARTED" if binding["status"] == "ACTIVE" else "INACTIVE"}
    anchor = parse_utc_datetime(cycle["opened_at"])
    # A manual correction declares the current cycle for its own month. It
    # must not be replayed from the original, possibly much older start date.
    if binding.get("transition_type") == "CORRECTION":
        anchor = max(anchor, parse_utc_datetime(binding["updated_at"]))
    anchor_month = _month_number(anchor)
    now = parse_utc_datetime(at)
    planned = cycle.get("planned_class_meeting_at")
    postponed = cycle.get("class_meeting_status") == "POSTPONED" and planned
    if postponed and parse_utc_datetime(planned) > now:
        return {**result, "status": "POSTPONED", "next_refresh_at": None, "due_cycles": 0}
    if postponed:
        anchor_month = max(anchor_month, _month_number(planned))
    current_index = int(cycle["learning_cycle_index"])
    remaining = max(0, int(binding["duration_cycles"]) - current_index)
    due = min(remaining, max(0, _month_number(now) - anchor_month))
    return {
        **result,
        "status": "PLAN_END_REACHED" if not remaining else "DUE" if due else "CURRENT",
        "anchor_month": _month_start(anchor_month),
        "next_refresh_at": _month_start(anchor_month + 1) if remaining else None,
        "due_cycles": due,
    }


def refresh_in_connection(connection, class_org_unit_id: str, *, at: str) -> dict[str, Any]:
    """Called only inside a transaction; lock one class before reading its clock."""
    if not enabled():
        return {"status": "DISABLED", "advanced": 0}
    cycles._lock_class_for_update(connection, class_org_unit_id)
    if isinstance(connection, sqlite3.Connection):
        execute(connection, "UPDATE org_units SET id=id WHERE id=?", (class_org_unit_id,))
    bindings = execute(
        connection, "SELECT id FROM class_learning_bindings WHERE class_org_unit_id=? AND status='ACTIVE'",
        (class_org_unit_id,),
    ).fetchall()
    if len(bindings) > 1:
        raise ValueError("该班级存在多个有效学习计划，不能自动更新")
    binding = cycles._active_binding(connection, class_org_unit_id)
    if not binding:
        return {"status": "UNBOUND", "advanced": 0}
    cycle = cycles._cycle_at(connection, int(binding["id"]), at)
    status = monthly_status(binding, cycle, at)
    if not cycle or not status.get("due_cycles"):
        return {**status, "advanced": 0}
    if cycle["cycle_status"] != "OPEN":
        raise ValueError("当前学习周期异常关闭，不能自动更新")
    # Validate the whole catch-up first. A missing template never leaves a
    # class halfway advanced or substitutes another class/cohort's content.
    next_cycles = []
    current_index = int(cycle["learning_cycle_index"])
    for index in range(current_index + 1, current_index + int(status["due_cycles"]) + 1):
        plan = cycles._plan_cycle_for_track(
            connection, plan_version_id=int(binding["plan_version_id"]),
            cohort_month=binding.get("cohort_month"), cycle_index=index,
        )
        if not plan:
            raise ValueError(f"学习计划缺少第{index}学习周期，不能自动更新")
        if execute(connection, "SELECT id FROM class_learning_cycles WHERE binding_id=? AND learning_cycle_index=?",
                   (binding["id"], index)).fetchone():
            raise ValueError("目标周期已有历史记录，不能自动覆盖")
        next_cycles.append(plan)
        override = cycles._active_schedule_override(connection, binding_id=int(binding["id"]), learning_cycle_index=index)
        if override and parse_utc_datetime(override["planned_class_meeting_at"]) > parse_utc_datetime(at):
            break
    now = cycles._storage_datetime(connection, at)
    anchor_month = _month_number(status["anchor_month"])
    previous_id = cycle["id"]
    for offset, plan in enumerate(next_cycles, start=1):
        boundary = cycles._storage_datetime(connection, _month_start(anchor_month + offset))
        # Closing a calendar period does not say its class meeting was held,
        # its courses were completed, or its groups were absent.
        execute(connection, "UPDATE class_learning_cycles SET cycle_status='CLOSED', closed_at=?, updated_at=? WHERE id=?",
                (boundary, now, previous_id))
        index = current_index + offset
        override = cycles._active_schedule_override(connection, binding_id=int(binding["id"]), learning_cycle_index=index)
        planned = (cycles._output_datetime(override["planned_class_meeting_at"], "计划班会时间")
                   if override else cycles._planned_at_for_binding_cycle(binding, index))
        cursor = execute(connection,
            "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
            "plan_cycle_id, opened_at, planned_class_meeting_at, class_meeting_status, group_meeting_policy, "
            "cycle_status, adjustment_reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'REQUIRED', 'OPEN', ?, ?, ?)",
            (binding["id"], class_org_unit_id, index, plan["id"], boundary,
             cycles._storage_datetime(connection, planned), "POSTPONED" if override else "PLANNED",
             override["adjustment_reason"] if override else None, now, now))
        previous_id = int(cursor.lastrowid)
    write_audit(connection, actor_user_id=None, action="learning.cycle.monthly_refresh",
                resource_type="class_learning_binding", resource_id=str(binding["id"]), org_unit_id=class_org_unit_id,
                purpose="按上海时区每月更新学习内容，不生成班会、课程完成或学分事实",
                before={"learning_cycle_index": current_index, "cycle_id": cycle["id"]},
                after={"learning_cycle_index": current_index + len(next_cycles), "cycle_id": previous_id,
                       "binding_id": binding["id"], "as_of": at, "advanced": len(next_cycles)})
    return {"status": "UPDATED", "advanced": len(next_cycles), "learning_cycle_index": current_index + len(next_cycles)}


def refresh_class(class_org_unit_id: str, *, at: str | None = None) -> dict[str, Any]:
    if not enabled():
        return {"status": "DISABLED", "advanced": 0}
    with transaction() as connection:
        return refresh_in_connection(connection, class_org_unit_id, at=at or cycles._now())


def refresh_all() -> dict[str, int]:
    if not enabled():
        return {"updated": 0, "failed": 0}
    classes = fetch_all("SELECT DISTINCT b.class_org_unit_id FROM class_learning_bindings b "
                        "JOIN org_units o ON o.id=b.class_org_unit_id "
                        "WHERE b.status='ACTIVE' AND o.is_active=1 AND o.unit_type IN ('CLASS','SPECIAL_COHORT') "
                        "ORDER BY b.class_org_unit_id")
    result = {"updated": 0, "failed": 0}
    at = cycles._now()
    for row in classes:
        try:
            change = refresh_class(row["class_org_unit_id"], at=at)
            result["updated"] += int(bool(change["advanced"]))
        except Exception:
            result["failed"] += 1
            logger.exception("Monthly content refresh failed for class %s", row["class_org_unit_id"])
    return result


async def run_monthly_refresh() -> None:
    """Startup catch-up and hourly sweeps; reads also recover idle containers."""
    while True:
        try:
            await asyncio.to_thread(refresh_all)
        except Exception:
            logger.exception("Monthly content sweep failed; will retry")
        await asyncio.sleep(3600)
