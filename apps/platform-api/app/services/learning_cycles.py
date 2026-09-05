from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.db import connect, execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids
from app.services.learning_cycle_schedule import (
    planned_class_meeting_at_for_cycle,
    year_month,
)
from app.services.learning_plan_baseline import (
    actual_snapshot,
    baseline_by_class_id,
    baseline_summary,
    compare_expectation,
    is_learning_plan_binding_required,
    load_baseline,
    public_expectation,
)


CLASS_MEETING_STATUSES = {"PLANNED", "POSTPONED"}
GROUP_MEETING_POLICIES = {"REQUIRED", "SUSPENDED", "WAIVED"}
GROUP_TASK_STATUSES = {"PENDING", "COMPLETED", "WAIVED"}
COHORT_TEMPLATE_MONTHS = {1, 4, 7, 10}
LIFECYCLE_TRANSITIONS = {"INITIAL", "RESTART", "RESUME", "PLAN_SWITCH", "CORRECTION"}
RETIREMENT_STORAGE_STATUS = "RETIRED"
DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_datetime(value: str | None, field_name: str, *, required: bool = False) -> str | None:
    """Normalize an ISO date or datetime to the service's UTC representation.

    The admin UI records a formal start date rather than a precise time.  A
    date-only value is therefore anchored at UTC midnight as a stable calendar
    boundary; existing timezone-aware timestamps remain fully supported.
    """

    if value is None or not str(value).strip():
        if required:
            raise ValueError(f"{field_name}不能为空")
        return None
    raw = str(value).strip()
    text = (
        f"{raw}T00:00:00+00:00"
        if DATE_ONLY_PATTERN.fullmatch(raw)
        else raw.replace("Z", "+00:00")
    )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 ISO 日期时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _cycle_query_datetime(connection, value: str) -> str:
    """Use a MySQL DATETIME-compatible value for cycle boundary lookups.

    SQLite stores the service's ISO-8601 strings verbatim, while MySQL returns
    DATETIME columns without the ``T`` separator, timezone suffix, or
    microseconds.  Comparing a MySQL DATETIME column to the ISO string can
    select the previous cycle when the next cycle opens at the same second.
    Keep the SQLite representation unchanged for its existing lexical-order
    semantics and normalize only the MySQL query parameter.
    """

    if isinstance(connection, sqlite3.Connection):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _storage_datetime(connection, value: str) -> str:
    """Use the timestamp representation accepted by the active SQL driver."""

    if isinstance(connection, sqlite3.Connection):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _public_plan_status(value: Any) -> str | None:
    """Expose the business name RETIRED while keeping old storage compatible.

    Releases before the lifecycle migration used ``ARCHIVED`` for a plan that
    could no longer be selected.  Existing bindings must keep reading that
    plan, so the service treats both values as retired and presents one stable
    business vocabulary to the admin UI.
    """

    if value is None:
        return None
    return "RETIRED" if str(value).upper() in {"ARCHIVED", "RETIRED"} else str(value).upper()


def _planned_at_for_binding_cycle(
    binding: dict[str, Any], learning_cycle_index: int
) -> str:
    """Calculate a plan-relative schedule from this binding's formal start.

    A RESUME binding can intentionally start at cycle 8.  Its cycle 8 is the
    first runtime cycle of the new binding, so it must be scheduled one month
    after the formal start, not eight months after it.
    """

    start_index = int(binding.get("start_cycle_index") or 1)
    offset = max(1, int(learning_cycle_index) - start_index + 1)
    return planned_class_meeting_at_for_cycle(binding["started_at"], offset)


def _visible_class(class_org_unit_id: str, user_id: int) -> dict[str, Any]:
    unit = fetch_one(
        "SELECT id, name, unit_type, parent_id FROM org_units "
        "WHERE id=? AND unit_type IN ('CLASS', 'SPECIAL_COHORT') AND is_active=1",
        (class_org_unit_id,),
    )
    if not unit:
        raise ValueError("班级不存在或已停用")
    allowed = accessible_org_ids(user_id)
    if allowed is not None and class_org_unit_id not in allowed:
        raise PermissionError("班级不在组织授权范围内")
    return unit


def _lock_class_for_update(connection, class_org_unit_id: str) -> None:
    """Serialize lifecycle and cycle writes for one class.

    The active-binding invariant is enforced in the service layer because the
    MySQL schema must retain multiple historical bindings. Locking the class
    row also covers the no-history case, where locking an empty binding result
    would not protect two concurrent first-bind requests from both inserting.
    """

    lock_clause = (
        " FOR UPDATE" if not isinstance(connection, sqlite3.Connection) else ""
    )
    row = execute(
        connection,
        "SELECT id FROM org_units WHERE id=? "
        "AND unit_type IN ('CLASS', 'SPECIAL_COHORT') AND is_active=1" + lock_clause,
        (class_org_unit_id,),
    ).fetchone()
    if not row:
        raise ValueError("班级不存在或已停用")


def _plan_cycle_payload(connection, plan_cycle_id: int) -> dict[str, Any] | None:
    cycle = execute(
        connection,
        "SELECT id, plan_version_id, cohort_month, cycle_index, year_index, cycle_label "
        "FROM learning_plan_cycles WHERE id=?",
        (plan_cycle_id,),
    ).fetchone()
    if not cycle:
        return None
    result = dict(cycle)
    # ``cycle_index`` is the legacy storage/API name.  Keep it for existing
    # consumers, but expose the business meaning explicitly: this is the
    # class-relative learning cycle, not the cohort template month.
    result["learning_cycle_index"] = int(cycle["cycle_index"])
    tasks = execute(
        connection,
        "SELECT id, task_type, title, description, credit_points, is_required, "
        "sort_order, metadata_json FROM learning_plan_tasks "
        "WHERE plan_cycle_id=? ORDER BY sort_order, id",
        (plan_cycle_id,),
    ).fetchall()
    result["tasks"] = []
    for task in tasks:
        item = dict(task)
        item["is_required"] = bool(item["is_required"])
        if item.get("metadata_json"):
            try:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = None
                item.pop("metadata_json", None)
        else:
            item.pop("metadata_json", None)
            item["metadata"] = None
        result["tasks"].append(item)
    return result


def list_learning_plans() -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT id, plan_key, plan_name, version_label, duration_cycles, status, "
        "source_name, created_at, updated_at FROM learning_plan_versions "
        "ORDER BY plan_name, version_label, id"
    )
    connection = connect()
    try:
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["status"] = _public_plan_status(item.get("status"))
            cycles = execute(
                connection,
                "SELECT id, cohort_month FROM learning_plan_cycles "
                "WHERE plan_version_id=? ORDER BY cohort_month, cycle_index",
                (row["id"],),
            ).fetchall()
            tracks: dict[int | None, list[dict[str, Any]]] = {}
            for cycle in cycles:
                track = cycle["cohort_month"]
                tracks.setdefault(track, []).append(
                    _plan_cycle_payload(connection, int(cycle["id"]))
                )
            item["cohort_tracks"] = [
                {"cohort_month": cohort_month, "cycles": tracks[cohort_month]}
                for cohort_month in sorted(
                    tracks, key=lambda value: (value is None, value or 0)
                )
            ]
            result.append(item)
        return result
    finally:
        connection.close()


def _active_binding(
    connection, class_org_unit_id: str, *, for_update: bool = False
) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if for_update and not isinstance(connection, sqlite3.Connection) else ""
    row = execute(
        connection,
        "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles "
        "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.class_org_unit_id=? AND b.status='ACTIVE' "
        "ORDER BY b.started_at DESC, b.id DESC LIMIT 1" + lock_clause,
        (class_org_unit_id,),
    ).fetchone()
    return dict(row) if row else None


def _latest_binding(connection, class_org_unit_id: str) -> dict[str, Any] | None:
    active = _active_binding(connection, class_org_unit_id)
    if active:
        return active
    row = execute(
        connection,
        "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles "
        "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.class_org_unit_id=? ORDER BY b.updated_at DESC, b.id DESC LIMIT 1",
        (class_org_unit_id,),
    ).fetchone()
    return dict(row) if row else None


def _cycle_at(
    connection, binding_id: int, at: str
) -> dict[str, Any] | None:
    """Return the latest runtime cycle that has actually opened by ``at``.

    A binding creates its first runtime row when the plan is configured so the
    schedule can be projected in advance.  That row may have a future
    ``opened_at``.  It must not be used as the current cycle before the formal
    start boundary; callers need ``None`` so they can expose NOT_STARTED and
    keep study-meeting registration closed.
    """

    query_at = _cycle_query_datetime(connection, at)
    row = execute(
        connection,
        "SELECT * FROM class_learning_cycles WHERE binding_id=? "
        "AND cycle_status IN ('OPEN', 'CLOSED') AND opened_at<=? "
        "ORDER BY opened_at DESC, learning_cycle_index DESC LIMIT 1",
        (binding_id, query_at),
    ).fetchone()
    return dict(row) if row else None


def _plan_cycle_for_track(
    connection, *, plan_version_id: int, cohort_month: int | None, cycle_index: int
) -> dict[str, Any] | None:
    """Prefer the class's cohort track and fall back only to a NULL generic track."""
    if cohort_month is not None:
        exact = execute(
            connection,
            "SELECT id FROM learning_plan_cycles WHERE plan_version_id=? "
            "AND cohort_month=? AND cycle_index=?",
            (plan_version_id, cohort_month, cycle_index),
        ).fetchone()
        if exact:
            return dict(exact)
    generic = execute(
        connection,
        "SELECT id FROM learning_plan_cycles WHERE plan_version_id=? "
        "AND cohort_month IS NULL AND cycle_index=?",
        (plan_version_id, cycle_index),
    ).fetchone()
    return dict(generic) if generic else None


def _active_schedule_override(
    connection, *, binding_id: int, learning_cycle_index: int
) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT * FROM class_learning_cycle_schedule_overrides "
        "WHERE binding_id=? AND learning_cycle_index=? AND status='ACTIVE'",
        (binding_id, learning_cycle_index),
    ).fetchone()
    return dict(row) if row else None


def _schedule_overrides(
    connection, *, binding_id: int
) -> dict[int, dict[str, Any]]:
    rows = execute(
        connection,
        "SELECT * FROM class_learning_cycle_schedule_overrides "
        "WHERE binding_id=? AND status='ACTIVE' ORDER BY learning_cycle_index",
        (binding_id,),
    ).fetchall()
    return {int(row["learning_cycle_index"]): dict(row) for row in rows}


def _output_datetime(value: Any, field_name: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _normalize_datetime(str(value), field_name)


def _schedule_override_payload(
    override: dict[str, Any] | None, *, source: str = "OVERRIDE"
) -> dict[str, Any] | None:
    if not override:
        return None
    return {
        "id": int(override["id"]) if override.get("id") is not None else None,
        "planned_class_meeting_at": _output_datetime(
            override.get("planned_class_meeting_at"), "计划班会时间"
        ),
        "adjustment_reason": override.get("adjustment_reason"),
        "status": override.get("status", "ACTIVE"),
        "source": source,
    }


def _cycle_schedule_item(
    connection,
    *,
    binding: dict[str, Any],
    learning_cycle_index: int,
    cycle: dict[str, Any] | None,
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    cohort_month = binding.get("cohort_month")
    start_index = int(binding.get("start_cycle_index") or 1)
    if learning_cycle_index < start_index and cycle is None:
        plan_cycle_row = _plan_cycle_for_track(
            connection,
            plan_version_id=int(binding["plan_version_id"]),
            cohort_month=cohort_month,
            cycle_index=learning_cycle_index,
        )
        plan_cycle = (
            _plan_cycle_payload(connection, int(plan_cycle_row["id"]))
            if plan_cycle_row
            else None
        )
        return {
            "learning_cycle_index": learning_cycle_index,
            "cohort_month": int(cohort_month) if cohort_month is not None else None,
            "plan_cycle_id": plan_cycle.get("id") if plan_cycle else None,
            "cycle_label": plan_cycle.get("cycle_label") if plan_cycle else None,
            "planned_month": None,
            "default_planned_class_meeting_at": None,
            "planned_class_meeting_at": None,
            "schedule_source": "NOT_APPLICABLE",
            "schedule_override": None,
            "actual_status": "SKIPPED",
            "cycle_status": "SKIPPED",
            "actual_start_at": None,
            "actual_class_meeting_at": None,
        }
    default_planned = _planned_at_for_binding_cycle(binding, learning_cycle_index)
    cycle_planned = _output_datetime(
        cycle.get("planned_class_meeting_at") if cycle else None,
        "周期记录中的计划班会时间",
    )
    if override:
        planned = _output_datetime(
            override.get("planned_class_meeting_at"), "计划班会时间"
        )
        schedule_source = "OVERRIDE"
        schedule_override = _schedule_override_payload(override)
    elif cycle_planned:
        planned = cycle_planned
        schedule_source = "CYCLE_RECORD" if planned != default_planned else "DEFAULT"
        schedule_override = (
            _schedule_override_payload(
                {
                    "planned_class_meeting_at": planned,
                    "adjustment_reason": cycle.get("adjustment_reason"),
                    "status": "ACTIVE",
                },
                source="CYCLE_RECORD",
            )
            if planned != default_planned or cycle.get("adjustment_reason")
            else None
        )
    else:
        planned = default_planned
        schedule_source = "DEFAULT"
        schedule_override = None

    instance_status = cycle.get("cycle_status") if cycle else "NOT_STARTED"
    actual_status = {
        "OPEN": "OPEN",
        "CLOSED": "CLOSED",
        "UPCOMING": "UPCOMING",
    }.get(str(instance_status), "NOT_STARTED")
    actual_start = (
        _output_datetime(cycle.get("opened_at"), "实际周期开始时间")
        if cycle and actual_status in {"OPEN", "CLOSED"}
        else None
    )
    actual_meeting = _output_datetime(
        cycle.get("actual_class_meeting_at") if cycle else None,
        "实际班会时间",
    )
    plan_cycle_row = (
        {"id": cycle["plan_cycle_id"]}
        if cycle and cycle.get("plan_cycle_id")
        else _plan_cycle_for_track(
            connection,
            plan_version_id=int(binding["plan_version_id"]),
            cohort_month=cohort_month,
            cycle_index=learning_cycle_index,
        )
    )
    plan_cycle = (
        _plan_cycle_payload(connection, int(plan_cycle_row["id"]))
        if plan_cycle_row
        else None
    )
    return {
        "learning_cycle_index": learning_cycle_index,
        "cohort_month": int(cohort_month) if cohort_month is not None else None,
        "plan_cycle_id": plan_cycle.get("id") if plan_cycle else None,
        "cycle_label": plan_cycle.get("cycle_label") if plan_cycle else None,
        "planned_month": year_month(planned),
        "default_planned_class_meeting_at": default_planned,
        "planned_class_meeting_at": planned,
        "schedule_source": schedule_source,
        "schedule_override": schedule_override,
        "actual_status": actual_status,
        "cycle_status": instance_status,
        "actual_start_at": actual_start,
        "actual_class_meeting_at": actual_meeting,
    }


def _build_cycle_schedule(
    connection, *, binding: dict[str, Any], as_of: str | None = None
) -> dict[str, Any]:
    effective_at = _normalize_datetime(as_of, "查询时间") or _now()
    duration = int(binding["duration_cycles"])
    cycle_rows = execute(
        connection,
        "SELECT * FROM class_learning_cycles WHERE binding_id=? "
        "ORDER BY learning_cycle_index",
        (binding["id"],),
    ).fetchall()
    cycles_by_index = {
        int(row["learning_cycle_index"]): dict(row) for row in cycle_rows
    }
    overrides = _schedule_overrides(connection, binding_id=int(binding["id"]))
    cycles = [
        _cycle_schedule_item(
            connection,
            binding=binding,
            learning_cycle_index=index,
            cycle=cycles_by_index.get(index),
            override=overrides.get(index),
        )
        for index in range(1, duration + 1)
    ]
    current = next(
        (
            item
            for item in cycles
            if item["actual_status"] == "OPEN"
            and item["actual_start_at"]
            and item["actual_start_at"] <= effective_at
        ),
        None,
    )
    has_future_open = any(
        item["actual_status"] == "OPEN"
        and item["actual_start_at"]
        and item["actual_start_at"] > effective_at
        for item in cycles
    )
    current_projection = {
        "class_org_unit_id": binding["class_org_unit_id"],
        "as_of": effective_at,
        "current_open_cycle": current["learning_cycle_index"] if current else None,
        "planned_month": current["planned_month"] if current else None,
        "planned_class_meeting_at": current["planned_class_meeting_at"] if current else None,
        "actual_status": current["actual_status"] if current else (
            "NOT_STARTED" if has_future_open else "COMPLETED"
        ),
        "actual_start_at": current["actual_start_at"] if current else None,
        "actual_class_meeting_at": current["actual_class_meeting_at"] if current else None,
        "schedule_override": current["schedule_override"] if current else None,
    }
    planned_projection = [
        {
            key: item[key]
            for key in (
                "learning_cycle_index",
                "cohort_month",
                "planned_month",
                "default_planned_class_meeting_at",
                "planned_class_meeting_at",
                "schedule_source",
                "schedule_override",
            )
        }
        for item in cycles
    ]
    actual_projection = [
        {
            key: item[key]
            for key in (
                "learning_cycle_index",
                "cohort_month",
                "actual_status",
                "cycle_status",
                "actual_start_at",
                "actual_class_meeting_at",
                "planned_month",
                "schedule_override",
            )
        }
        for item in cycles
    ]
    return {
        "projection_model": "PLANNED_SCHEDULE_PLUS_ACTUAL_CLASS_MEETING_BOUNDARY",
        "planned_projection": planned_projection,
        "actual_projection": actual_projection,
        "cycles": cycles,
        "current_projection": current_projection,
    }


def _groups(connection, class_org_unit_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in execute(
            connection,
            "SELECT id, name FROM org_units WHERE parent_id=? AND unit_type='GROUP' "
            "AND is_active=1 ORDER BY name, id",
            (class_org_unit_id,),
        ).fetchall()
    ]


def _group_plan_task(connection, plan_cycle_id: int) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT id, task_type, title FROM learning_plan_tasks "
        "WHERE plan_cycle_id=? AND task_type='GROUP_MEETING' "
        "ORDER BY sort_order, id LIMIT 1",
        (plan_cycle_id,),
    ).fetchone()
    return dict(row) if row else None


def _group_progress(
    connection, cycle: dict[str, Any], class_org_unit_id: str
) -> list[dict[str, Any]]:
    plan_task = _group_plan_task(connection, int(cycle["plan_cycle_id"]))
    rows = execute(
        connection,
        "SELECT t.*, g.name AS group_name FROM group_learning_cycle_tasks t "
        "JOIN org_units g ON g.id=t.group_org_unit_id "
        "WHERE t.class_learning_cycle_id=?",
        (cycle["id"],),
    ).fetchall()
    by_group = {row["group_org_unit_id"]: dict(row) for row in rows}
    result: list[dict[str, Any]] = []
    for group in _groups(connection, class_org_unit_id):
        row = by_group.get(group["id"])
        status = row["status"] if row else (
            "WAIVED" if cycle["group_meeting_policy"] in {"SUSPENDED", "WAIVED"}
            else "PENDING"
        )
        result.append({
            "group_org_unit_id": group["id"],
            "group_name": group["name"],
            "task_type": row["task_type"] if row else (plan_task["task_type"] if plan_task else "GROUP_MEETING"),
            "task_title": row["task_title"] if row else (plan_task["title"] if plan_task else None),
            "status": status,
            "completed_at": row["completed_at"] if row else None,
            "note": row["note"] if row else None,
        })
    return result


def _runtime_status(
    *,
    binding: dict[str, Any],
    cycle: dict[str, Any] | None,
    at: str,
) -> str:
    """Map runtime facts to the small set of business-facing states.

    ``NOT_STARTED`` is intentionally based on the binding start boundary, not
    on a future cycle row that was materialized for schedule projection.
    """

    if cycle:
        if str(cycle.get("class_meeting_status") or "").upper() == "POSTPONED":
            return "POSTPONED"
        if str(cycle.get("cycle_status") or "").upper() == "OPEN":
            return "NORMAL"
        if str(cycle.get("cycle_status") or "").upper() == "CLOSED":
            return "COMPLETED"
    started_at = _timestamp_as_datetime(binding.get("started_at"))
    query_at = _timestamp_as_datetime(at)
    if started_at and query_at and started_at > query_at:
        return "NOT_STARTED"
    return "MISSING_CURRENT_CYCLE"


def _progress_from_connection(
    connection, class_org_unit_id: str, *, at: str
) -> dict[str, Any]:
    binding = _latest_binding(connection, class_org_unit_id)
    if not binding:
        raise ValueError("该班级尚未绑定学习计划")
    cycle = _cycle_at(connection, int(binding["id"]), at)
    if not cycle:
        runtime_status = _runtime_status(binding=binding, cycle=None, at=at)
        return {
            "class_org_unit_id": class_org_unit_id,
            "binding": _binding_payload(binding),
            "current_cycle": None,
            "current_status": runtime_status,
            "actual_status": runtime_status,
            "current_at": at,
        }
    cycle_payload = dict(cycle)
    plan_cycle = _plan_cycle_payload(connection, int(cycle["plan_cycle_id"]))
    cycle_payload["plan_cycle"] = plan_cycle
    cycle_payload["groups"] = _group_progress(connection, cycle, class_org_unit_id)
    cycle_payload["current_at"] = at
    runtime_status = _runtime_status(binding=binding, cycle=cycle, at=at)
    return {
        "class_org_unit_id": class_org_unit_id,
        "binding": _binding_payload(binding),
        "current_cycle": cycle_payload,
        "current_status": runtime_status,
        "actual_status": runtime_status,
    }


def _binding_payload(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": binding["id"],
        "plan_version_id": binding["plan_version_id"],
        "plan_key": binding["plan_key"],
        "plan_name": binding["plan_name"],
        "version_label": binding["version_label"],
        "cohort_month": binding["cohort_month"],
        "started_at": _output_datetime(binding["started_at"], "学习计划开始时间"),
        "status": binding["status"],
        "duration_cycles": int(binding["duration_cycles"]),
        "learning_round": int(binding.get("learning_round") or 1),
        "start_cycle_index": int(binding.get("start_cycle_index") or 1),
        "ended_at": _output_datetime(binding.get("ended_at"), "学习轮次结束时间"),
        "ended_reason": binding.get("ended_reason"),
        "previous_binding_id": (
            int(binding["previous_binding_id"])
            if binding.get("previous_binding_id") is not None
            else None
        ),
        "transition_type": binding.get("transition_type") or "INITIAL",
    }


def _latest_learning_plan_confirmation(
    connection, *, binding_id: int
) -> dict[str, Any] | None:
    """Return the latest explicit lifecycle confirmation for a binding.

    The checked-in business baseline is a review reference.  A successful
    INITIAL, RESUME, RESTART, or CORRECTION operation is the auditable
    per-class confirmation that supersedes that reference for the active
    binding.  Reading the audit row here avoids adding another production
    flag or treating an unrelated cycle note as a business acknowledgement.
    """

    row = execute(
        connection,
        "SELECT action, purpose, after_json, created_at FROM audit_logs "
        "WHERE action IN ('learning.binding.create', 'learning.binding.resume', "
        "'learning.binding.restart', 'learning.binding.plan_switch', "
        "'learning.binding.correction') "
        "AND resource_type='class_learning_binding' AND resource_id=? "
        "AND result='SUCCESS' ORDER BY id DESC LIMIT 1",
        (str(binding_id),),
    ).fetchone()
    if not row or not row["after_json"]:
        return None
    try:
        payload = row["after_json"]
        if not isinstance(payload, dict):
            payload = json.loads(str(payload))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    binding_payload = payload.get("binding")
    if not isinstance(binding_payload, dict):
        return None
    if str(binding_payload.get("id")) != str(binding_id):
        return None
    return {
        "action": row["action"],
        "confirmed_at": _output_datetime(row["created_at"], "人工确认时间"),
        "reason": payload.get("reason") or row["purpose"],
    }


def get_class_learning_schedule(
    *, user_id: int, class_org_unit_id: str
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, user_id)
    connection = connect()
    try:
        binding = _latest_binding(connection, class_org_unit_id)
        if not binding:
            raise ValueError("该班级尚未绑定学习计划")
        schedule = _build_cycle_schedule(
            connection, binding=binding, as_of=_now()
        )
        return {
            "class_org_unit_id": class_org_unit_id,
            "binding": _binding_payload(binding),
            **schedule,
        }
    finally:
        connection.close()


def get_class_learning_progress(
    *, user_id: int, class_org_unit_id: str, at: str | None = None
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, user_id)
    current_at = _normalize_datetime(at, "查询时间") or _now()
    connection = connect()
    try:
        return _progress_from_connection(connection, class_org_unit_id, at=current_at)
    finally:
        connection.close()


def _binding_by_id(connection, binding_id: int) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles, "
        "p.status AS plan_status "
        "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.id=?",
        (binding_id,),
    ).fetchone()
    return dict(row) if row else None


def _validate_cohort_month(cohort_month: int | None) -> int | None:
    if cohort_month is None:
        return None
    value = int(cohort_month)
    if value not in COHORT_TEMPLATE_MONTHS:
        raise ValueError("开班月份模板必须是 1、4、7 或 10 月")
    return value


def _validate_start_cycle_index(start_cycle_index: int, duration: int) -> int:
    index = int(start_cycle_index)
    if not 1 <= index <= duration:
        raise ValueError(f"起始学习周期必须在 1 到 {duration} 之间")
    return index


def _create_learning_binding(
    connection,
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    plan_version_id: int,
    cohort_month: int | None,
    started_at: str,
    start_cycle_index: int,
    transition_type: str,
    previous_binding_id: int | None = None,
) -> dict[str, Any]:
    transition = str(transition_type or "").upper()
    if transition not in LIFECYCLE_TRANSITIONS:
        raise ValueError("未知的学习轮次变更类型")
    plan = execute(
        connection,
        "SELECT * FROM learning_plan_versions WHERE id=?",
        (plan_version_id,),
    ).fetchone()
    if not plan:
        raise ValueError("学习计划版本不存在")
    if str(plan["status"]).upper() != "PUBLISHED":
        raise ValueError("新学习轮次只能选择已发布的学习计划版本")
    duration = int(plan["duration_cycles"])
    index = _validate_start_cycle_index(start_cycle_index, duration)
    selected_cycle = _plan_cycle_for_track(
        connection,
        plan_version_id=plan_version_id,
        cohort_month=cohort_month,
        cycle_index=index,
    )
    if not selected_cycle:
        cohort_label = f"{cohort_month}月开班" if cohort_month else "通用"
        raise ValueError(f"学习计划缺少{cohort_label}第{index}学习周期")
    latest_round = execute(
        connection,
        "SELECT COALESCE(MAX(learning_round), 0) AS learning_round "
        "FROM class_learning_bindings WHERE class_org_unit_id=?",
        (class_org_unit_id,),
    ).fetchone()["learning_round"]
    learning_round = int(latest_round or 0) + 1
    now = _now()
    stored_start = _storage_datetime(connection, started_at)
    cursor = execute(
        connection,
        "INSERT INTO class_learning_bindings("
        "class_org_unit_id, plan_version_id, cohort_month, started_at, status, "
        "learning_round, start_cycle_index, previous_binding_id, transition_type, "
        "created_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?)",
        (
            class_org_unit_id,
            plan_version_id,
            cohort_month,
            stored_start,
            learning_round,
            index,
            previous_binding_id,
            transition,
            actor_user_id,
            now,
            now,
        ),
    )
    binding_id = int(cursor.lastrowid)
    binding = _binding_by_id(connection, binding_id)
    if not binding:
        raise ValueError("创建学习轮次后无法读取绑定记录")
    first_planned = _planned_at_for_binding_cycle(binding, index)
    stored_first_planned = _storage_datetime(connection, first_planned)
    execute(
        connection,
        "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
        "plan_cycle_id, opened_at, planned_class_meeting_at, class_meeting_status, "
        "group_meeting_policy, cycle_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'PLANNED', 'REQUIRED', 'OPEN', ?, ?)",
        (
            binding_id,
            class_org_unit_id,
            index,
            selected_cycle["id"],
            stored_start,
            stored_first_planned,
            now,
            now,
        ),
    )
    return binding


def bind_class_learning_plan(
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    plan_version_id: int,
    cohort_month: int | None = None,
    started_at: str | None = None,
    start_cycle_index: int = 1,
) -> dict[str, Any]:
    """Create the first binding for a class.

    A class with historical bindings must use the explicit RESUME or RESTART
    operation.  This prevents a routine first-bind form from silently starting
    another learning round after a previous round was ended.
    """

    _visible_class(class_org_unit_id, actor_user_id)
    cohort_month = _validate_cohort_month(cohort_month)
    start = _normalize_datetime(started_at, "学习计划开始时间") or _now()
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        existing = execute(
            connection,
            "SELECT * FROM class_learning_bindings WHERE class_org_unit_id=? "
            "AND status='ACTIVE' ORDER BY started_at DESC, id DESC LIMIT 1"
            + (" FOR UPDATE" if not isinstance(connection, sqlite3.Connection) else ""),
            (class_org_unit_id,),
        ).fetchone()
        if existing:
            if (
                int(existing["plan_version_id"]) == int(plan_version_id)
                and int(existing["start_cycle_index"] or 1) == int(start_cycle_index)
                and (existing["cohort_month"] or None) == cohort_month
            ):
                return _progress_from_connection(connection, class_org_unit_id, at=now)
            raise ValueError("该班级已有生效中的学习计划绑定，请使用明确的学习轮次操作")
        historical = execute(
            connection,
            "SELECT id FROM class_learning_bindings WHERE class_org_unit_id=? LIMIT 1",
            (class_org_unit_id,),
        ).fetchone()
        if historical:
            raise ValueError("该班级已有历史学习轮次，请使用“从指定周期接续”或“重新开始学习”")
        binding = _create_learning_binding(
            connection,
            actor_user_id=actor_user_id,
            class_org_unit_id=class_org_unit_id,
            plan_version_id=plan_version_id,
            cohort_month=cohort_month,
            started_at=start,
            start_cycle_index=start_cycle_index,
            transition_type="INITIAL",
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.binding.create",
            resource_type="class_learning_binding",
            resource_id=str(binding["id"]),
            org_unit_id=class_org_unit_id,
            purpose="首次绑定三年学习计划",
            after=_binding_payload(binding),
        )
        return _progress_from_connection(connection, class_org_unit_id, at=now)


def _start_learning_round(
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    plan_version_id: int,
    cohort_month: int | None,
    started_at: str | None,
    start_cycle_index: int,
    transition_type: str,
    reason: str,
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, actor_user_id)
    cohort_month = _validate_cohort_month(cohort_month)
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("学习轮次变更必须填写原因")
    start = _normalize_datetime(started_at, "本轮正式开始时间", required=True)
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        active = _active_binding(connection, class_org_unit_id, for_update=True)
        previous = active or _latest_binding(connection, class_org_unit_id)
        before = _binding_payload(previous) if previous else None
        if active:
            execute(
                connection,
                "UPDATE class_learning_bindings SET status='ENDED', ended_at=?, ended_reason=?, updated_at=? "
                "WHERE id=? AND status='ACTIVE'",
                (_storage_datetime(connection, now), normalized_reason, now, active["id"]),
            )
        binding = _create_learning_binding(
            connection,
            actor_user_id=actor_user_id,
            class_org_unit_id=class_org_unit_id,
            plan_version_id=plan_version_id,
            cohort_month=cohort_month,
            started_at=start,
            start_cycle_index=start_cycle_index,
            transition_type=transition_type,
            previous_binding_id=int(previous["id"]) if previous else None,
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action=f"learning.binding.{str(transition_type).lower()}",
            resource_type="class_learning_binding",
            resource_id=str(binding["id"]),
            org_unit_id=class_org_unit_id,
            purpose=normalized_reason,
            before=before,
            after={
                "ended_binding_id": int(active["id"]) if active else None,
                "new_binding": _binding_payload(binding),
                "reason": normalized_reason,
            },
        )
        return _progress_from_connection(connection, class_org_unit_id, at=now)


def restart_class_learning_plan(
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    plan_version_id: int,
    cohort_month: int | None,
    started_at: str | None,
    reason: str,
) -> dict[str, Any]:
    return _start_learning_round(
        actor_user_id=actor_user_id,
        class_org_unit_id=class_org_unit_id,
        plan_version_id=plan_version_id,
        cohort_month=cohort_month,
        started_at=started_at,
        start_cycle_index=1,
        transition_type="RESTART",
        reason=reason,
    )


def resume_class_learning_plan(
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    plan_version_id: int,
    cohort_month: int | None,
    started_at: str | None,
    start_cycle_index: int,
    reason: str,
) -> dict[str, Any]:
    return _start_learning_round(
        actor_user_id=actor_user_id,
        class_org_unit_id=class_org_unit_id,
        plan_version_id=plan_version_id,
        cohort_month=cohort_month,
        started_at=started_at,
        start_cycle_index=start_cycle_index,
        transition_type="RESUME",
        reason=reason,
    )


def correct_class_learning_plan(
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    plan_version_id: int,
    cohort_month: int | None,
    learning_cycle_index: int,
    reason: str,
) -> dict[str, Any]:
    """Correct the active round's current setup without starting a new round."""

    _visible_class(class_org_unit_id, actor_user_id)
    cohort_month = _validate_cohort_month(cohort_month)
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("学习计划修正必须填写修正原因")
    target_index = int(learning_cycle_index)
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        binding = _active_binding(connection, class_org_unit_id, for_update=True)
        if not binding:
            raise ValueError("该班级尚未配置生效中的学习计划")
        plan = execute(
            connection,
            "SELECT * FROM learning_plan_versions WHERE id=?",
            (plan_version_id,),
        ).fetchone()
        if not plan:
            raise ValueError("学习计划版本不存在")
        plan_status = str(plan["status"]).upper()
        if plan_status != "PUBLISHED" and not (
            int(plan["id"]) == int(binding["plan_version_id"])
            and plan_status in {"ARCHIVED", "RETIRED"}
        ):
            raise ValueError("当前设置修正只能选择已发布的学习计划版本")
        duration = int(plan["duration_cycles"])
        target_index = _validate_start_cycle_index(target_index, duration)
        current = _cycle_at(connection, int(binding["id"]), now)
        if not current or current["cycle_status"] != "OPEN":
            raise ValueError("当前没有可修正的开放学习周期")
        target_plan_cycle = _plan_cycle_for_track(
            connection,
            plan_version_id=plan_version_id,
            cohort_month=cohort_month,
            cycle_index=target_index,
        )
        if not target_plan_cycle:
            cohort_label = f"{cohort_month}月开班" if cohort_month else "通用"
            raise ValueError(f"学习计划缺少{cohort_label}第{target_index}学习周期")
        collision = execute(
            connection,
            "SELECT id FROM class_learning_cycles WHERE binding_id=? AND learning_cycle_index=? AND id<>?",
            (binding["id"], target_index, current["id"]),
        ).fetchone()
        if collision:
            raise ValueError("目标学习周期已有历史记录，不能覆盖；请使用重新开始或接续")
        before = {
            "binding": _binding_payload(binding),
            "cycle": dict(current),
        }
        execute(
            connection,
            "UPDATE class_learning_bindings SET plan_version_id=?, cohort_month=?, updated_at=? WHERE id=?",
            (plan_version_id, cohort_month, now, binding["id"]),
        )
        execute(
            connection,
            "UPDATE class_learning_cycles SET learning_cycle_index=?, plan_cycle_id=?, "
            "adjustment_reason=?, updated_at=? WHERE id=?",
            (target_index, target_plan_cycle["id"], normalized_reason, now, current["id"]),
        )
        plan_task = _group_plan_task(connection, int(target_plan_cycle["id"]))
        if plan_task:
            execute(
                connection,
                "UPDATE group_learning_cycle_tasks SET plan_task_id=?, task_title=?, updated_at=? "
                "WHERE class_learning_cycle_id=?",
                (plan_task["id"], plan_task["title"], now, current["id"]),
            )
        corrected = _binding_by_id(connection, int(binding["id"]))
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.binding.correction",
            resource_type="class_learning_binding",
            resource_id=str(binding["id"]),
            org_unit_id=class_org_unit_id,
            purpose=normalized_reason,
            before=before,
            after={
                "binding": _binding_payload(corrected or binding),
                "cycle": dict(
                    execute(
                        connection,
                        "SELECT * FROM class_learning_cycles WHERE id=?",
                        (current["id"],),
                    ).fetchone()
                ),
                "reason": normalized_reason,
            },
        )
        return _progress_from_connection(connection, class_org_unit_id, at=now)


def recommend_learning_plan(*, started_at: str) -> dict[str, Any] | None:
    """Recommend a published plan and cohort template for a new round.

    The recommendation is advisory only.  The caller still has to confirm the
    selected plan in the RESTART form, which keeps the decision auditable.
    """

    start = _normalize_datetime(started_at, "本轮正式开始时间", required=True)
    parsed = datetime.fromisoformat(start)
    cohort_month = max(
        (month for month in COHORT_TEMPLATE_MONTHS if month <= parsed.month),
        default=1,
    )
    rows = fetch_all(
        "SELECT DISTINCT p.id, p.plan_key, p.plan_name, p.version_label, "
        "p.duration_cycles, p.status, p.source_name, p.updated_at "
        "FROM learning_plan_versions p JOIN learning_plan_cycles c "
        "ON c.plan_version_id=p.id AND c.cycle_index=1 "
        "AND (c.cohort_month=? OR c.cohort_month IS NULL) "
        "WHERE p.status='PUBLISHED'",
        (cohort_month,),
    )
    if not rows:
        return None

    def version_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        numbers = tuple(int(item) for item in re.findall(r"\d+", str(row.get("version_label") or "")))
        return numbers or (0,), str(row.get("updated_at") or ""), int(row["id"])

    selected = max(rows, key=version_sort_key)
    return {
        "started_at": start,
        "plan_version_id": int(selected["id"]),
        "plan_key": selected["plan_key"],
        "plan_name": selected["plan_name"],
        "version_label": selected["version_label"],
        "duration_cycles": int(selected["duration_cycles"]),
        "cohort_month": cohort_month,
        "start_cycle_index": 1,
        "selection_rule": "最新已发布版本 + 正式开始日期之前最近的 1/4/7/10 月模板",
    }


def retire_learning_plan(
    *, actor_user_id: int, plan_version_id: int, reason: str
) -> dict[str, Any]:
    """Retire a plan for future rounds without interrupting active bindings.

    New writes use ``RETIRED`` after the lifecycle migration. Existing rows
    using the legacy ``ARCHIVED`` value are exposed as ``RETIRED`` as well, so
    old active bindings remain readable during the rollout.
    """

    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise ValueError("计划版本退役必须填写原因")
    now = _now()
    with transaction() as connection:
        plan = execute(
            connection,
            "SELECT * FROM learning_plan_versions WHERE id=?",
            (plan_version_id,),
        ).fetchone()
        if not plan:
            raise ValueError("学习计划版本不存在")
        status = str(plan["status"]).upper()
        if status == RETIREMENT_STORAGE_STATUS or status == "RETIRED":
            return {
                "id": int(plan["id"]),
                "status": "RETIRED",
                "changed": False,
            }
        if status != "PUBLISHED":
            raise ValueError("只有已发布的学习计划版本才能退役")
        before = dict(plan)
        execute(
            connection,
            "UPDATE learning_plan_versions SET status=?, updated_at=? WHERE id=?",
            (RETIREMENT_STORAGE_STATUS, now, plan_version_id),
        )
        active_count = execute(
            connection,
            "SELECT COUNT(*) AS count FROM class_learning_bindings "
            "WHERE plan_version_id=? AND status='ACTIVE'",
            (plan_version_id,),
        ).fetchone()["count"]
        after = dict(
            execute(
                connection,
                "SELECT * FROM learning_plan_versions WHERE id=?",
                (plan_version_id,),
            ).fetchone()
        )
        after["status"] = "RETIRED"
        after["active_binding_count"] = int(active_count)
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.plan.retire",
            resource_type="learning_plan_version",
            resource_id=str(plan_version_id),
            purpose=normalized_reason,
            before=before,
            after=after,
        )
        return {"id": int(plan_version_id), "status": "RETIRED", "changed": True}


def get_class_learning_plan_history(
    *, user_id: int, class_org_unit_id: str
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, user_id)
    connection = connect()
    try:
        rows = execute(
            connection,
            "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles, "
            "p.status AS plan_status FROM class_learning_bindings b "
            "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
            "WHERE b.class_org_unit_id=? ORDER BY b.learning_round, b.started_at, b.id",
            (class_org_unit_id,),
        ).fetchall()
        bindings: list[dict[str, Any]] = []
        for row in rows:
            binding = dict(row)
            item = _binding_payload(binding)
            item["plan_status"] = _public_plan_status(binding.get("plan_status"))
            summary = execute(
                connection,
                "SELECT COUNT(*) AS materialized_cycles, "
                "MAX(CASE WHEN cycle_status='CLOSED' THEN learning_cycle_index ELSE 0 END) AS "
                "completed_through_cycle, "
                "MAX(CASE WHEN cycle_status='OPEN' THEN learning_cycle_index ELSE 0 END) AS "
                "open_cycle_index FROM class_learning_cycles WHERE binding_id=?",
                (binding["id"],),
            ).fetchone()
            item["cycle_summary"] = {
                "materialized_cycles": int(summary["materialized_cycles"] or 0),
                "completed_through_cycle": int(summary["completed_through_cycle"] or 0),
                "open_cycle_index": int(summary["open_cycle_index"] or 0) or None,
            }
            item["is_current"] = binding["status"] == "ACTIVE"
            bindings.append(item)
        event_rows = execute(
            connection,
            "SELECT action, resource_type, resource_id, purpose, result, before_json, "
            "after_json, created_at FROM audit_logs "
            "WHERE org_unit_id=? AND resource_type='class_learning_binding' "
            "AND action LIKE ? ORDER BY created_at, id",
            (class_org_unit_id, "learning.binding.%"),
        ).fetchall()
        events: list[dict[str, Any]] = []
        for row in event_rows:
            item = dict(row)
            for field in ("before_json", "after_json"):
                raw = item.pop(field, None)
                if raw:
                    try:
                        item[field.removesuffix("_json")] = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        item[field.removesuffix("_json")] = None
                else:
                    item[field.removesuffix("_json")] = None
            events.append(item)
        return {
            "class_org_unit_id": class_org_unit_id,
            "current_binding": next((item for item in bindings if item["is_current"]), None),
            "bindings": bindings,
            "events": events,
            "history_preserved": True,
        }
    finally:
        connection.close()


def _health_issue(
    *,
    class_row: dict[str, Any],
    issue_type: str,
    current_data: dict[str, Any],
    suggested_action: str,
) -> dict[str, Any]:
    return {
        "class_org_unit_id": class_row["id"],
        "class_name": class_row["name"],
        "unit_code": class_row.get("unit_code"),
        "issue_type": issue_type,
        "severity": "BLOCKER",
        "current_data": current_data,
        "suggested_action": suggested_action,
    }


def _timestamp_as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _scope_covers_class(
    *, class_org_unit_id: str, appointment: dict[str, Any], parent_by_id: dict[str, Any]
) -> bool:
    if appointment.get("scope_type") == "UNIT":
        return appointment.get("org_unit_id") == class_org_unit_id
    current: str | None = class_org_unit_id
    visited: set[str] = set()
    while current and current not in visited:
        if current == appointment.get("org_unit_id"):
            return True
        visited.add(current)
        current = parent_by_id.get(current)
    return False


def scan_class_learning_plan_health(
    *, user_id: int, class_org_unit_id: str | None = None
) -> dict[str, Any]:
    """Read-only GO/NO-GO scan for all visible formal learning classes."""

    if class_org_unit_id:
        _visible_class(class_org_unit_id, user_id)
    allowed = accessible_org_ids(user_id)
    connection = connect()
    try:
        query = (
            "SELECT id, unit_code, name, unit_type, parent_id FROM org_units "
            "WHERE unit_type IN ('CLASS', 'SPECIAL_COHORT') AND is_active=1"
        )
        params: list[Any] = []
        if class_org_unit_id:
            query += " AND id=?"
            params.append(class_org_unit_id)
        elif allowed is not None:
            if not allowed:
                rows = []
            else:
                placeholders = ", ".join("?" for _ in allowed)
                query += f" AND id IN ({placeholders})"
                params.extend(sorted(allowed))
        if class_org_unit_id or allowed is None or allowed:
            rows = execute(connection, query + " ORDER BY name, id", tuple(params)).fetchall()
        class_rows = [dict(row) for row in rows]
        parent_by_id = {
            str(row["id"]): row["parent_id"]
            for row in execute(connection, "SELECT id, parent_id FROM org_units").fetchall()
        }
        appointments = [
            dict(row)
            for row in execute(
                connection,
                "SELECT va.org_unit_id, va.scope_type, va.starts_at, va.ends_at "
                "FROM volunteer_appointments va "
                "JOIN volunteer_position_capabilities pc ON pc.position_key=va.appointment_key "
                "WHERE va.status='ACTIVE' AND pc.capability_key='STUDY_MEETING_MANAGE'",
            ).fetchall()
        ]
        scan_at = _now()
        now_dt = _timestamp_as_datetime(scan_at) or datetime.now(UTC)
        relation_effective_date = now_dt.date().isoformat()
        baseline = load_baseline()
        baseline_index = baseline_by_class_id(baseline)
        name_counts: dict[str, int] = {}
        for row in class_rows:
            name_counts[str(row["name"])] = name_counts.get(str(row["name"]), 0) + 1

        summary: dict[str, Any] = {
            "total_classes": len(class_rows),
            "correctly_bound": 0,
            "unbound": 0,
            "multiple_active_bindings": 0,
            "invalid_templates": 0,
            "missing_current_cycle": 0,
            "plan_cycle_mismatch": 0,
            "group_relation_anomalies": 0,
            "group_meeting_config_missing": 0,
            "volunteer_permission_missing": 0,
            "expected_cycle_mismatch": 0,
            "expected_template_mismatch": 0,
            "expected_plan_version_mismatch": 0,
            "expected_status_mismatch": 0,
            "manual_review_required": 0,
            "not_applicable_classes": 0,
            "baseline_id_name_mismatch": 0,
            "ready_classes": 0,
        }
        classes: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        class_ids_in_scope: set[str] = set()
        for class_row in class_rows:
            class_ids_in_scope.add(str(class_row["id"]))
            class_issues: list[dict[str, Any]] = []
            expectation = baseline_index.get(str(class_row["id"]))
            expectation_name_matches = True
            if expectation:
                expected_name = str(expectation.get("class_name") or "").strip()
                expectation_name_matches = not expected_name or expected_name == str(class_row["name"])
            learning_plan_binding_required = (
                is_learning_plan_binding_required(expectation)
                if expectation and expectation_name_matches
                else True
            )
            if name_counts.get(str(class_row["name"]), 0) > 1:
                duplicate_ids = [
                    row["id"] for row in class_rows if row["name"] == class_row["name"]
                ]
                class_issues.append(
                    _health_issue(
                        class_row=class_row,
                        issue_type="DUPLICATE_CLASS_NAME",
                        current_data={"class_org_unit_ids": duplicate_ids},
                        suggested_action="按组织 ID 逐班确认，不自动合并或停用同名班级",
                    )
                )
            binding_rows = [
                dict(row)
                for row in execute(
                    connection,
                    "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles, "
                    "p.status AS plan_status FROM class_learning_bindings b "
                    "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
                    "WHERE b.class_org_unit_id=? AND b.status='ACTIVE' "
                    "ORDER BY b.started_at DESC, b.id DESC",
                    (class_row["id"],),
                ).fetchall()
            ]
            binding = binding_rows[0] if len(binding_rows) == 1 else None
            plan_health_applicable = learning_plan_binding_required or bool(binding_rows)
            if not learning_plan_binding_required:
                summary["not_applicable_classes"] += 1
            if not binding_rows:
                if learning_plan_binding_required:
                    summary["unbound"] += 1
                    class_issues.append(
                        _health_issue(
                            class_row=class_row,
                            issue_type="MISSING_BINDING",
                            current_data={"active_binding_count": 0},
                            suggested_action="在班级学习计划管理中选择已发布计划、模板和正式开始时间后首次绑定",
                        )
                    )
            elif len(binding_rows) > 1:
                summary["multiple_active_bindings"] += 1
                class_issues.append(
                    _health_issue(
                        class_row=class_row,
                        issue_type="MULTIPLE_ACTIVE_BINDINGS",
                        current_data={
                            "active_binding_count": len(binding_rows),
                            "binding_ids": [row["id"] for row in binding_rows],
                        },
                        suggested_action="由运营人员确认唯一当前轮次，禁止自动删除历史或抢占绑定",
                    )
                )

            current_cycle: dict[str, Any] | None = None
            plan_cycle: dict[str, Any] | None = None
            confirmation: dict[str, Any] | None = None
            runtime_status = "NOT_APPLICABLE" if not plan_health_applicable else "UNBOUND"
            if binding:
                confirmation = _latest_learning_plan_confirmation(
                    connection, binding_id=int(binding["id"])
                )
                binding_payload = _binding_payload(binding)
                plan_status = _public_plan_status(binding.get("plan_status"))
                if plan_status not in {"PUBLISHED", "RETIRED"}:
                    class_issues.append(
                        _health_issue(
                            class_row=class_row,
                            issue_type="INVALID_PLAN_VERSION",
                            current_data={"plan_version_id": binding["plan_version_id"], "status": plan_status},
                            suggested_action="确认班级计划；正常延续可保留已退役旧版本，新的轮次请选择已发布版本",
                        )
                    )
                cohort = binding.get("cohort_month")
                if cohort not in COHORT_TEMPLATE_MONTHS:
                    summary["invalid_templates"] += 1
                    class_issues.append(
                        _health_issue(
                            class_row=class_row,
                            issue_type="INVALID_COHORT_TEMPLATE",
                            current_data={"cohort_month": cohort},
                            suggested_action="按实际学习体系使用 1、4、7 或 10 月开班模板进行修正",
                        )
                    )
                current_cycle = _cycle_at(connection, int(binding["id"]), scan_at)
                runtime_status = _runtime_status(
                    binding=binding, cycle=current_cycle, at=scan_at
                )
                if not current_cycle:
                    if runtime_status != "NOT_STARTED":
                        summary["missing_current_cycle"] += 1
                        class_issues.append(
                            _health_issue(
                                class_row=class_row,
                                issue_type="MISSING_CURRENT_CYCLE",
                                current_data={"binding_id": binding["id"]},
                                suggested_action="使用接续或当前设置修正生成明确的当前学习周期",
                            )
                        )
                else:
                    plan_cycle_row = execute(
                        connection,
                        "SELECT pc.*, p.status AS plan_status FROM learning_plan_cycles pc "
                        "JOIN learning_plan_versions p ON p.id=pc.plan_version_id WHERE pc.id=?",
                        (current_cycle["plan_cycle_id"],),
                    ).fetchone()
                    plan_cycle = dict(plan_cycle_row) if plan_cycle_row else None
                    mismatch = (
                        plan_cycle is None
                        or int(plan_cycle["plan_version_id"]) != int(binding["plan_version_id"])
                        or int(plan_cycle["cycle_index"]) != int(current_cycle["learning_cycle_index"])
                        or (
                            binding.get("cohort_month") is not None
                            and plan_cycle.get("cohort_month") not in (None, binding.get("cohort_month"))
                        )
                    )
                    if mismatch:
                        summary["plan_cycle_mismatch"] += 1
                        class_issues.append(
                            _health_issue(
                                class_row=class_row,
                                issue_type="PLAN_CYCLE_MISMATCH",
                                current_data={
                                    "current_cycle_index": current_cycle["learning_cycle_index"],
                                    "current_plan_cycle_id": current_cycle["plan_cycle_id"],
                                    "binding_plan_version_id": binding["plan_version_id"],
                                    "plan_cycle": plan_cycle,
                                },
                                suggested_action="使用当前设置修正；若是新一轮则使用重新开始/接续，禁止偷偷改显示期数",
                            )
                        )
                    elif not _group_plan_task(connection, int(plan_cycle["id"])):
                        summary["group_meeting_config_missing"] += 1
                        class_issues.append(
                            _health_issue(
                                class_row=class_row,
                                issue_type="GROUP_MEETING_CONFIG_MISSING",
                                current_data={"plan_cycle_id": plan_cycle["id"]},
                                suggested_action="补齐该计划周期的小组学习会配置后再进行真机验收",
                            )
                        )
            if expectation:
                expected_name = str(expectation.get("class_name") or "").strip()
                if expected_name and expected_name != str(class_row["name"]):
                    summary["baseline_id_name_mismatch"] += 1
                    class_issues.append(
                        _health_issue(
                            class_row=class_row,
                            issue_type="BASELINE_ID_NAME_MISMATCH",
                            current_data={
                                "baseline_class_name": expected_name,
                                "actual_class_name": class_row["name"],
                                "class_org_unit_id": class_row["id"],
                            },
                            suggested_action="历史组织 ID 与当前班级名称不一致，先核对当前组织主数据，禁止按名称自动改写。",
                        )
                    )
                    expectation = None
                elif not is_learning_plan_binding_required(expectation):
                    pass
                elif (
                    expectation.get("migration_status") == "MANUAL_REVIEW_REQUIRED"
                    and not confirmation
                ):
                    summary["manual_review_required"] += 1
                    class_issues.append(
                        _health_issue(
                            class_row=class_row,
                            issue_type="MANUAL_REVIEW_REQUIRED",
                            current_data={
                                "business_expectation": public_expectation(expectation),
                                "runtime_status": runtime_status,
                            },
                            suggested_action="业务确认模板、计划版本和实际班会次数后，再单独形成生产修正方案。",
                        )
                    )
                elif binding and not confirmation:
                    actual = actual_snapshot(
                        binding=binding,
                        current_cycle=current_cycle,
                        runtime_status=runtime_status,
                    )
                    for mismatch in compare_expectation(expectation, actual):
                        summary_key = {
                            "EXPECTED_CYCLE_MISMATCH": "expected_cycle_mismatch",
                            "EXPECTED_TEMPLATE_MISMATCH": "expected_template_mismatch",
                            "EXPECTED_PLAN_VERSION_MISMATCH": "expected_plan_version_mismatch",
                            "EXPECTED_STATUS_MISMATCH": "expected_status_mismatch",
                        }[mismatch["issue_type"]]
                        summary[summary_key] += 1
                        class_issues.append(
                            _health_issue(
                                class_row=class_row,
                                issue_type=mismatch["issue_type"],
                                current_data={
                                    "field": mismatch["field"],
                                    "expected": mismatch["expected"],
                                    "actual": mismatch["actual"],
                                    "before": actual,
                                    "proposed": public_expectation(expectation),
                                    "binding_id": binding["id"],
                                },
                                suggested_action="先复核业务证据；确认后使用 CORRECTION 或当前状态修正，不自动创建新轮次。",
                            )
                        )
            groups = _groups(connection, class_row["id"])
            group_anomalies = 0
            if plan_health_applicable:
                for group in groups:
                    member_count = execute(
                        connection,
                        "SELECT COUNT(DISTINCT r.member_id) AS count FROM member_org_relations r "
                        "JOIN members m ON m.id=r.member_id "
                        "WHERE r.org_unit_id=? AND r.relation_type='STUDY_GROUP' AND m.status='ACTIVE' "
                        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
                        "AND (r.valid_until IS NULL OR r.valid_until>=?)",
                        (group["id"], relation_effective_date, relation_effective_date),
                    ).fetchone()["count"]
                    if int(member_count or 0) == 0:
                        group_anomalies += 1
                        class_issues.append(
                            _health_issue(
                                class_row=class_row,
                                issue_type="GROUP_WITHOUT_ACTIVE_MEMBERS",
                                current_data={
                                    "group_org_unit_id": group["id"],
                                    "group_name": group["name"],
                                    "relation_effective_date": relation_effective_date,
                                },
                                suggested_action="按组织 ID 核对小组关系和学员主档，不从旧系统或姓名猜测回填",
                            )
                        )
                    mismatch_count = execute(
                        connection,
                        "SELECT COUNT(DISTINCT r.member_id) AS count FROM member_org_relations r "
                        "JOIN members m ON m.id=r.member_id "
                        "WHERE r.org_unit_id=? AND r.relation_type='STUDY_GROUP' AND m.status='ACTIVE' "
                        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
                        "AND (r.valid_until IS NULL OR r.valid_until>=?) "
                        "AND NOT EXISTS (SELECT 1 FROM member_org_relations cr "
                        "WHERE cr.member_id=r.member_id AND cr.org_unit_id=? "
                        "AND cr.relation_type='STUDY_CLASS' "
                        "AND (cr.valid_from IS NULL OR cr.valid_from<=?) "
                        "AND (cr.valid_until IS NULL OR cr.valid_until>=?))",
                        (
                            group["id"],
                            relation_effective_date,
                            relation_effective_date,
                            class_row["id"],
                            relation_effective_date,
                            relation_effective_date,
                        ),
                    ).fetchone()["count"]
                    if int(mismatch_count or 0) > 0:
                        group_anomalies += int(mismatch_count)
                        class_issues.append(
                            _health_issue(
                                class_row=class_row,
                                issue_type="GROUP_CLASS_RELATION_MISMATCH",
                                current_data={
                                    "group_org_unit_id": group["id"],
                                    "group_name": group["name"],
                                    "member_count": int(mismatch_count),
                                    "relation_effective_date": relation_effective_date,
                                },
                                suggested_action="核对学员的 STUDY_CLASS 与 STUDY_GROUP 关系，保留跨组参加事实不改正式归属",
                            )
                        )
                if not groups:
                    group_anomalies += 1
                    class_issues.append(
                        _health_issue(
                            class_row=class_row,
                            issue_type="NO_ACTIVE_GROUPS",
                            current_data={"active_group_count": 0},
                            suggested_action="先核对班级下的正式小组组织关系，再进行小组学习会验收",
                        )
                    )
            if group_anomalies:
                summary["group_relation_anomalies"] += 1
            volunteer_ok = True
            if plan_health_applicable:
                volunteer_ok = any(
                    str(appointment.get("starts_at") or "")
                    and _timestamp_as_datetime(appointment.get("starts_at")) is not None
                    and (_timestamp_as_datetime(appointment.get("starts_at")) <= now_dt)
                    and (_timestamp_as_datetime(appointment.get("ends_at")) is None or _timestamp_as_datetime(appointment.get("ends_at")) >= now_dt)
                    and _scope_covers_class(
                        class_org_unit_id=class_row["id"],
                        appointment=appointment,
                        parent_by_id=parent_by_id,
                    )
                    for appointment in appointments
                )
            if plan_health_applicable and not volunteer_ok:
                summary["volunteer_permission_missing"] += 1
                # Volunteer appointments remain an independent authorization for
                # performing study-meeting operations. They are informational in
                # this plan-health scan and must not turn a one-time class plan
                # confirmation into a second publish gate.
            if binding and not any(
                item["issue_type"] in {
                    "INVALID_PLAN_VERSION",
                    "INVALID_COHORT_TEMPLATE",
                    "MISSING_CURRENT_CYCLE",
                    "PLAN_CYCLE_MISMATCH",
                    "GROUP_MEETING_CONFIG_MISSING",
                    "EXPECTED_CYCLE_MISMATCH",
                    "EXPECTED_TEMPLATE_MISMATCH",
                    "EXPECTED_PLAN_VERSION_MISMATCH",
                    "EXPECTED_STATUS_MISMATCH",
                    "MANUAL_REVIEW_REQUIRED",
                    "BASELINE_ID_NAME_MISMATCH",
                }
                for item in class_issues
            ):
                summary["correctly_bound"] += 1
            if not class_issues:
                summary["ready_classes"] += 1
            class_status = (
                "NOT_APPLICABLE"
                if not plan_health_applicable and not class_issues
                else "READY"
                if not class_issues
                else "BLOCKED"
            )
            class_payload = {
                "class_org_unit_id": class_row["id"],
                "unit_code": class_row["unit_code"],
                "class_name": class_row["name"],
                "binding": _binding_payload(binding) if binding else None,
                "plan_status": _public_plan_status(binding.get("plan_status")) if binding else None,
                "current_cycle": (
                    {
                        "learning_cycle_index": current_cycle["learning_cycle_index"],
                        "plan_cycle_id": current_cycle["plan_cycle_id"],
                        "cycle_status": current_cycle["cycle_status"],
                        "class_meeting_status": current_cycle["class_meeting_status"],
                        "group_meeting_policy": current_cycle["group_meeting_policy"],
                        "opened_at": _output_datetime(
                            current_cycle.get("opened_at"), "实际周期开始时间"
                        ),
                        "planned_class_meeting_at": _output_datetime(
                            current_cycle.get("planned_class_meeting_at"), "计划班会时间"
                        ),
                    }
                    if current_cycle
                    else None
                ),
                "runtime_status": runtime_status,
                "business_expectation": (
                    public_expectation(expectation) if expectation else None
                ),
                "business_expectation_resolution": (
                    {
                        "mode": "EXPLICIT_CONFIRMATION",
                        **confirmation,
                    }
                    if confirmation and expectation
                    else {"mode": "BASELINE"}
                    if expectation
                    else None
                ),
                "group_count": len(groups),
                "volunteer_permission": (
                    "NOT_APPLICABLE"
                    if not plan_health_applicable
                    else "PASS"
                    if volunteer_ok
                    else "BLOCKED"
                ),
                "status": class_status,
                "issues": class_issues,
            }
            classes.append(class_payload)
            issues.extend(class_issues)
        summary["assessment"] = "GO" if class_rows and summary["ready_classes"] == len(class_rows) else "NO-GO"
        return {
            "generated_at": _now(),
            "scope": "VISIBLE_FORMAL_CLASSES",
            "baseline": {
                **baseline_summary(baseline),
                "resolved_ids_in_scope": sum(
                    1 for class_id in baseline_index if class_id in class_ids_in_scope
                ),
                "resolved_ids_out_of_scope": sum(
                    1 for class_id in baseline_index if class_id not in class_ids_in_scope
                ),
            },
            "summary": summary,
            "classes": classes,
            "issues": issues,
            "assessment": summary["assessment"],
        }
    finally:
        connection.close()


def update_current_learning_cycle(
    *, actor_user_id: int, class_org_unit_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, actor_user_id)
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        binding = _active_binding(connection, class_org_unit_id)
        if not binding:
            raise ValueError("该班级尚未绑定学习计划")
        cycle = _cycle_at(connection, int(binding["id"]), now)
        if not cycle or cycle["cycle_status"] != "OPEN":
            raise ValueError("当前没有可维护的开放学习周期")
        before = dict(cycle)
        assignments: list[str] = []
        params: list[Any] = []
        if "planned_class_meeting_at" in updates:
            assignments.append("planned_class_meeting_at=?")
            normalized_planned = _normalize_datetime(
                updates.get("planned_class_meeting_at"), "计划班会时间"
            )
            params.append(
                _storage_datetime(connection, normalized_planned)
                if normalized_planned
                else None
            )
        if "class_meeting_status" in updates:
            status = str(updates["class_meeting_status"]).upper()
            if status not in CLASS_MEETING_STATUSES:
                raise ValueError("当前接口只能将班会状态设为 PLANNED 或 POSTPONED")
            assignments.append("class_meeting_status=?")
            params.append(status)
        if "group_meeting_policy" in updates:
            policy = str(updates["group_meeting_policy"]).upper()
            if policy not in GROUP_MEETING_POLICIES:
                raise ValueError("未知小组会策略")
            assignments.append("group_meeting_policy=?")
            params.append(policy)
        if "adjustment_reason" in updates:
            assignments.append("adjustment_reason=?")
            params.append((updates.get("adjustment_reason") or "").strip() or None)
        if assignments:
            assignments.append("updated_at=?")
            params.extend([now, cycle["id"]])
            execute(
                connection,
                "UPDATE class_learning_cycles SET " + ", ".join(assignments) + " WHERE id=?",
                tuple(params),
            )
        for item in updates.get("group_tasks", []) or []:
            group_id = str(item.get("group_org_unit_id") or "").strip()
            if not execute(
                connection,
                "SELECT id FROM org_units WHERE id=? AND parent_id=? AND unit_type='GROUP' AND is_active=1",
                (group_id, class_org_unit_id),
            ).fetchone():
                raise ValueError("小组不属于当前班级")
            status = str(item.get("status") or "").upper()
            if status not in GROUP_TASK_STATUSES:
                raise ValueError("小组任务状态只能是 PENDING、COMPLETED 或 WAIVED")
            plan_task = _group_plan_task(connection, int(cycle["plan_cycle_id"]))
            existing = execute(
                connection,
                "SELECT id FROM group_learning_cycle_tasks WHERE class_learning_cycle_id=? "
                "AND group_org_unit_id=? AND task_type='GROUP_MEETING'",
                (cycle["id"], group_id),
            ).fetchone()
            completed_at = now if status == "COMPLETED" else None
            values = (
                cycle["id"], group_id, plan_task["id"] if plan_task else None,
                plan_task["title"] if plan_task else None, status, completed_at,
                actor_user_id, item.get("note"), now, now,
            )
            if existing:
                execute(
                    connection,
                    "UPDATE group_learning_cycle_tasks SET plan_task_id=?, task_title=?, status=?, "
                    "completed_at=?, adjusted_by=?, note=?, updated_at=? WHERE id=?",
                    (*values[2:], existing["id"]),
                )
            else:
                execute(
                    connection,
                    "INSERT INTO group_learning_cycle_tasks(class_learning_cycle_id, group_org_unit_id, "
                    "plan_task_id, task_type, task_title, status, completed_at, adjusted_by, note, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'GROUP_MEETING', ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
        after = execute(
            connection, "SELECT * FROM class_learning_cycles WHERE id=?", (cycle["id"],)
        ).fetchone()
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.cycle.update",
            resource_type="class_learning_cycle",
            resource_id=str(cycle["id"]),
            org_unit_id=class_org_unit_id,
            purpose="维护当前学习周期班会与小组会策略",
            before=before,
            after={"cycle": dict(after), "group_tasks": updates.get("group_tasks", [])},
        )
        return _progress_from_connection(connection, class_org_unit_id, at=now)


def set_learning_cycle_schedule_override(
    *,
    actor_user_id: int,
    class_org_unit_id: str,
    learning_cycle_index: int,
    planned_class_meeting_at: str,
    adjustment_reason: str,
) -> dict[str, Any]:
    """Set one class-cycle planned meeting date without moving the cycle clock.

    Future cycles are intentionally stored in the override table because they
    do not have a runtime ``class_learning_cycles`` row yet.  When the
    preceding class meeting is confirmed, the existing engine opens that
    cycle and copies the active override onto its runtime row.
    """

    _visible_class(class_org_unit_id, actor_user_id)
    index = int(learning_cycle_index)
    reason = str(adjustment_reason or "").strip()
    if not reason:
        raise ValueError("周期调整必须填写调整原因")
    planned = _normalize_datetime(planned_class_meeting_at, "计划班会时间", required=True)
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        stored_planned = _storage_datetime(connection, planned)
        binding = _active_binding(connection, class_org_unit_id)
        if not binding:
            raise ValueError("该班级尚未配置生效中的学习计划")
        duration = int(binding["duration_cycles"])
        if not 1 <= index <= duration:
            raise ValueError(f"学习周期必须在 1 到 {duration} 之间")
        plan_cycle = _plan_cycle_for_track(
            connection,
            plan_version_id=int(binding["plan_version_id"]),
            cohort_month=binding.get("cohort_month"),
            cycle_index=index,
        )
        if not plan_cycle:
            raise ValueError(f"学习计划缺少第{index}学习周期")
        cycle = execute(
            connection,
            "SELECT * FROM class_learning_cycles WHERE binding_id=? "
            "AND learning_cycle_index=?",
            (binding["id"], index),
        ).fetchone()
        if cycle and cycle["cycle_status"] == "CLOSED":
            raise ValueError("已关闭的历史学习周期不可调整")
        if cycle and cycle["actual_class_meeting_at"]:
            raise ValueError("已确认实际班会的学习周期不可调整")
        latest_materialized = execute(
            connection,
            "SELECT MAX(learning_cycle_index) AS learning_cycle_index "
            "FROM class_learning_cycles WHERE binding_id=?",
            (binding["id"],),
        ).fetchone()["learning_cycle_index"]
        if cycle is None and latest_materialized is not None and index <= int(latest_materialized):
            raise ValueError("目标学习周期已进入历史，但找不到可调整的周期记录")
        existing = execute(
            connection,
            "SELECT * FROM class_learning_cycle_schedule_overrides "
            "WHERE binding_id=? AND learning_cycle_index=?",
            (binding["id"], index),
        ).fetchone()
        before = dict(existing) if existing else None
        if existing:
            override_id = existing["id"]
            execute(
                connection,
                "UPDATE class_learning_cycle_schedule_overrides SET "
                "class_org_unit_id=?, planned_class_meeting_at=?, adjustment_reason=?, "
                "status='ACTIVE', updated_by=?, updated_at=? WHERE id=?",
                (
                    class_org_unit_id,
                    stored_planned,
                    reason,
                    actor_user_id,
                    now,
                    override_id,
                ),
            )
            action = "learning.cycle.schedule_override.update"
        else:
            cursor = execute(
                connection,
                "INSERT INTO class_learning_cycle_schedule_overrides("
                "binding_id, class_org_unit_id, learning_cycle_index, "
                "planned_class_meeting_at, adjustment_reason, status, created_by, "
                "updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)",
                (
                    binding["id"],
                    class_org_unit_id,
                    index,
                    stored_planned,
                    reason,
                    actor_user_id,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            override_id = cursor.lastrowid
            action = "learning.cycle.schedule_override.create"
        if cycle:
            execute(
                connection,
                "UPDATE class_learning_cycles SET planned_class_meeting_at=?, "
                "class_meeting_status='POSTPONED', adjustment_reason=?, updated_at=? "
                "WHERE id=?",
                (stored_planned, reason, now, cycle["id"]),
            )
        after = execute(
            connection,
            "SELECT * FROM class_learning_cycle_schedule_overrides WHERE id=?",
            (override_id,),
        ).fetchone()
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action=action,
            resource_type="class_learning_cycle_schedule_override",
            resource_id=str(override_id),
            org_unit_id=class_org_unit_id,
            purpose="调整班级单个学习周期计划班会时间",
            before=before,
            after=dict(after),
        )
        return _build_cycle_schedule(connection, binding=binding, as_of=now)


def clear_learning_cycle_schedule_override(
    *, actor_user_id: int, class_org_unit_id: str, learning_cycle_index: int
) -> dict[str, Any]:
    """Revoke one schedule override while retaining its audit history."""

    _visible_class(class_org_unit_id, actor_user_id)
    index = int(learning_cycle_index)
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        binding = _active_binding(connection, class_org_unit_id)
        if not binding:
            raise ValueError("该班级尚未配置生效中的学习计划")
        duration = int(binding["duration_cycles"])
        if not 1 <= index <= duration:
            raise ValueError(f"学习周期必须在 1 到 {duration} 之间")
        cycle = execute(
            connection,
            "SELECT * FROM class_learning_cycles WHERE binding_id=? "
            "AND learning_cycle_index=?",
            (binding["id"], index),
        ).fetchone()
        if cycle and cycle["cycle_status"] == "CLOSED":
            raise ValueError("已关闭的历史学习周期不可调整")
        existing = execute(
            connection,
            "SELECT * FROM class_learning_cycle_schedule_overrides "
            "WHERE binding_id=? AND learning_cycle_index=? AND status='ACTIVE'",
            (binding["id"], index),
        ).fetchone()
        if existing:
            execute(
                connection,
                "UPDATE class_learning_cycle_schedule_overrides SET status='REVOKED', "
                "updated_by=?, updated_at=? WHERE id=?",
                (actor_user_id, now, existing["id"]),
            )
            if cycle and cycle["cycle_status"] == "OPEN":
                default_planned = _planned_at_for_binding_cycle(binding, index)
                execute(
                    connection,
                    "UPDATE class_learning_cycles SET planned_class_meeting_at=?, "
                    "class_meeting_status='PLANNED', adjustment_reason=NULL, updated_at=? "
                    "WHERE id=?",
                    (_storage_datetime(connection, default_planned), now, cycle["id"]),
                )
            after = execute(
                connection,
                "SELECT * FROM class_learning_cycle_schedule_overrides WHERE id=?",
                (existing["id"],),
            ).fetchone()
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="learning.cycle.schedule_override.revoke",
                resource_type="class_learning_cycle_schedule_override",
                resource_id=str(existing["id"]),
                org_unit_id=class_org_unit_id,
                purpose="撤销班级单个学习周期计划班会时间调整",
                before=dict(existing),
                after=dict(after),
            )
        return _build_cycle_schedule(connection, binding=binding, as_of=now)


def _resolve_actual_class_meeting_at(
    connection, *, class_org_unit_id: str, actual_at: str | None, source_event_group_id: int | None
) -> tuple[str, int | None]:
    source_id = source_event_group_id
    source_time = None
    if source_id is not None:
        event = execute(
            connection,
            "SELECT id FROM attendance_event_groups WHERE id=? AND study_org_unit_id=? "
            "AND activity_type='CLASS_MEETING' AND status NOT IN ('CANCELLED', 'INACTIVE')",
            (source_id, class_org_unit_id),
        ).fetchone()
        if not event:
            raise ValueError("班会事实不属于当前班级或已失效")
        session = execute(
            connection,
            "SELECT scheduled_start_at FROM attendance_sessions WHERE event_group_id=? "
            "AND finalized_at IS NOT NULL AND scheduled_start_at IS NOT NULL "
            "ORDER BY scheduled_start_at DESC, id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        if session:
            source_time = session["scheduled_start_at"]
    resolved = _normalize_datetime(actual_at, "实际班会开始时间")
    if resolved is None and source_time is not None:
        resolved = _normalize_datetime(str(source_time), "签到班会开始时间")
    if resolved is None:
        raise ValueError("确认班会召开必须提供实际开始时间，或提供已完成的签到班会事实")
    return resolved, source_id


def confirm_class_meeting(
    *, actor_user_id: int, class_org_unit_id: str, actual_class_meeting_at: str | None,
    source_event_group_id: int | None = None, confirmation_reason: str | None = None,
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, actor_user_id)
    now = _now()
    with transaction() as connection:
        _lock_class_for_update(connection, class_org_unit_id)
        binding = _active_binding(connection, class_org_unit_id)
        if not binding:
            raise ValueError("该班级尚未绑定学习计划")
        cycle = _cycle_at(connection, int(binding["id"]), now)
        if not cycle or cycle["cycle_status"] != "OPEN":
            raise ValueError("当前没有可确认的开放学习周期")
        actual, source_id = _resolve_actual_class_meeting_at(
            connection,
            class_org_unit_id=class_org_unit_id,
            actual_at=actual_class_meeting_at,
            source_event_group_id=source_event_group_id,
        )
        if actual <= str(cycle["opened_at"]):
            raise ValueError("实际班会时间必须晚于当前学习周期开始时间")
        before = dict(cycle)
        execute(
            connection,
            "UPDATE class_learning_cycles SET actual_class_meeting_at=?, class_meeting_status='HELD', "
            "cycle_status='CLOSED', closed_at=?, source_event_group_id=?, updated_at=? WHERE id=?",
            (actual, actual, source_id, now, cycle["id"]),
        )
        plan_task = _group_plan_task(connection, int(cycle["plan_cycle_id"]))
        policy = str(cycle["group_meeting_policy"])
        final_counts = {"COMPLETED": 0, "WAIVED": 0, "MISSED": 0}
        for group in _groups(connection, class_org_unit_id):
            existing = execute(
                connection,
                "SELECT id, status FROM group_learning_cycle_tasks WHERE class_learning_cycle_id=? "
                "AND group_org_unit_id=? AND task_type='GROUP_MEETING'",
                (cycle["id"], group["id"]),
            ).fetchone()
            current_status = existing["status"] if existing else None
            final_status = (
                "WAIVED" if policy in {"SUSPENDED", "WAIVED"} else
                current_status if current_status in {"COMPLETED", "WAIVED"} else "MISSED"
            )
            final_counts[final_status] += 1
            if existing:
                execute(
                    connection,
                    "UPDATE group_learning_cycle_tasks SET status=?, completed_at=?, adjusted_by=?, updated_at=? "
                    "WHERE id=?",
                    (final_status, actual if final_status == "COMPLETED" else None,
                     actor_user_id, now, existing["id"]),
                )
            else:
                execute(
                    connection,
                    "INSERT INTO group_learning_cycle_tasks(class_learning_cycle_id, group_org_unit_id, "
                    "plan_task_id, task_type, task_title, status, completed_at, adjusted_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'GROUP_MEETING', ?, ?, ?, ?, ?, ?)",
                    (cycle["id"], group["id"], plan_task["id"] if plan_task else None,
                     plan_task["title"] if plan_task else None, final_status,
                     actual if final_status == "COMPLETED" else None, actor_user_id, now, now),
                )
        next_index = int(cycle["learning_cycle_index"]) + 1
        duration = int(binding["duration_cycles"])
        if next_index <= duration:
            next_plan_cycle = _plan_cycle_for_track(
                connection,
                plan_version_id=int(binding["plan_version_id"]),
                cohort_month=binding.get("cohort_month"),
                cycle_index=next_index,
            )
            if not next_plan_cycle:
                cohort_label = f"{binding['cohort_month']}月开班" if binding.get("cohort_month") else "通用"
                raise ValueError(f"学习计划缺少{cohort_label}第{next_index}学习周期")
            next_override = _active_schedule_override(
                connection,
                binding_id=int(binding["id"]),
                learning_cycle_index=next_index,
            )
            next_planned = (
                _output_datetime(next_override["planned_class_meeting_at"], "计划班会时间")
                if next_override
                else _planned_at_for_binding_cycle(binding, next_index)
            )
            stored_next_planned = _storage_datetime(connection, next_planned)
            next_status = "POSTPONED" if next_override else "PLANNED"
            next_reason = next_override["adjustment_reason"] if next_override else None
            execute(
                connection,
                "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
                "plan_cycle_id, opened_at, planned_class_meeting_at, adjustment_reason, "
                "class_meeting_status, group_meeting_policy, cycle_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'REQUIRED', 'OPEN', ?, ?)",
                (
                    binding["id"], class_org_unit_id, next_index, next_plan_cycle["id"],
                    actual, stored_next_planned, next_reason, next_status, now, now,
                ),
            )
            binding_status = "ACTIVE"
        else:
            execute(
                connection,
                "UPDATE class_learning_bindings SET status='COMPLETED', ended_at=?, "
                "ended_reason='三年学习计划已完成', updated_at=? WHERE id=?",
                (_storage_datetime(connection, now), now, binding["id"]),
            )
            binding_status = "COMPLETED"
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.cycle.confirm_class_meeting",
            resource_type="class_learning_cycle",
            resource_id=str(cycle["id"]),
            org_unit_id=class_org_unit_id,
            purpose=(confirmation_reason or "确认实际班会召开").strip(),
            before=before,
            after={
                "actual_class_meeting_at": actual,
                "source_event_group_id": source_id,
                "final_group_counts": final_counts,
                "binding_status": binding_status,
            },
        )
        return _progress_from_connection(connection, class_org_unit_id, at=actual)
