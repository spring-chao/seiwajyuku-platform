from __future__ import annotations

import json
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


CLASS_MEETING_STATUSES = {"PLANNED", "POSTPONED"}
GROUP_MEETING_POLICIES = {"REQUIRED", "SUSPENDED", "WAIVED"}
GROUP_TASK_STATUSES = {"PENDING", "COMPLETED", "WAIVED"}
COHORT_TEMPLATE_MONTHS = {1, 4, 7, 10}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_datetime(value: str | None, field_name: str, *, required: bool = False) -> str | None:
    if value is None or not str(value).strip():
        if required:
            raise ValueError(f"{field_name}不能为空")
        return None
    text = str(value).strip().replace("Z", "+00:00")
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


def _active_binding(connection, class_org_unit_id: str) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles "
        "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.class_org_unit_id=? AND b.status='ACTIVE' "
        "ORDER BY b.started_at DESC, b.id DESC LIMIT 1",
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
    query_at = _cycle_query_datetime(connection, at)
    row = execute(
        connection,
        "SELECT * FROM class_learning_cycles WHERE binding_id=? "
        "AND cycle_status IN ('OPEN', 'CLOSED') AND opened_at<=? "
        "ORDER BY opened_at DESC, learning_cycle_index DESC LIMIT 1",
        (binding_id, query_at),
    ).fetchone()
    if not row:
        row = execute(
            connection,
            "SELECT * FROM class_learning_cycles WHERE binding_id=? "
            "AND cycle_status IN ('OPEN', 'CLOSED') "
            "ORDER BY learning_cycle_index LIMIT 1",
            (binding_id,),
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
    default_planned = planned_class_meeting_at_for_cycle(
        binding["started_at"], learning_cycle_index
    )
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


def _progress_from_connection(
    connection, class_org_unit_id: str, *, at: str
) -> dict[str, Any]:
    binding = _latest_binding(connection, class_org_unit_id)
    if not binding:
        raise ValueError("该班级尚未绑定学习计划")
    cycle = _cycle_at(connection, int(binding["id"]), at)
    if not cycle:
        raise ValueError("学习计划尚未生成学习周期")
    cycle_payload = dict(cycle)
    plan_cycle = _plan_cycle_payload(connection, int(cycle["plan_cycle_id"]))
    cycle_payload["plan_cycle"] = plan_cycle
    cycle_payload["groups"] = _group_progress(connection, cycle, class_org_unit_id)
    cycle_payload["current_at"] = at
    return {
        "class_org_unit_id": class_org_unit_id,
        "binding": {
            "id": binding["id"],
            "plan_version_id": binding["plan_version_id"],
            "plan_key": binding["plan_key"],
            "plan_name": binding["plan_name"],
            "version_label": binding["version_label"],
            "cohort_month": binding["cohort_month"],
            "started_at": binding["started_at"],
            "status": binding["status"],
            "duration_cycles": binding["duration_cycles"],
        },
        "current_cycle": cycle_payload,
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


def bind_class_learning_plan(
    *, actor_user_id: int, class_org_unit_id: str, plan_version_id: int,
    cohort_month: int | None = None, started_at: str | None = None,
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, actor_user_id)
    if cohort_month is not None and int(cohort_month) not in COHORT_TEMPLATE_MONTHS:
        raise ValueError("开班月份模板必须是 1、4、7 或 10 月")
    start = _normalize_datetime(started_at, "学习计划开始时间") or _now()
    now = _now()
    with transaction() as connection:
        plan = execute(
            connection,
            "SELECT * FROM learning_plan_versions WHERE id=?",
            (plan_version_id,),
        ).fetchone()
        if not plan:
            raise ValueError("学习计划版本不存在")
        if plan["status"] != "PUBLISHED":
            raise ValueError("只有已发布的学习计划版本才能绑定班级")
        existing = execute(
            connection,
            "SELECT * FROM class_learning_bindings WHERE class_org_unit_id=? "
            "AND status='ACTIVE' ORDER BY started_at DESC, id DESC LIMIT 1",
            (class_org_unit_id,),
        ).fetchone()
        if existing:
            if int(existing["plan_version_id"]) == int(plan_version_id):
                return _progress_from_connection(connection, class_org_unit_id, at=now)
            raise ValueError("该班级已有生效中的学习计划绑定")
        first_cycle = _plan_cycle_for_track(
            connection,
            plan_version_id=plan_version_id,
            cohort_month=cohort_month,
            cycle_index=1,
        )
        if not first_cycle:
            cohort_label = f"{cohort_month}月开班" if cohort_month else "通用"
            raise ValueError(f"学习计划缺少{cohort_label}第1学习周期")
        cursor = execute(
            connection,
            "INSERT INTO class_learning_bindings(class_org_unit_id, plan_version_id, cohort_month, "
            "started_at, status, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (class_org_unit_id, plan_version_id, cohort_month, start, actor_user_id, now, now),
        )
        binding_id = cursor.lastrowid
        first_planned = planned_class_meeting_at_for_cycle(start, 1)
        stored_first_planned = _storage_datetime(connection, first_planned)
        execute(
            connection,
            "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
            "plan_cycle_id, opened_at, planned_class_meeting_at, class_meeting_status, "
            "group_meeting_policy, cycle_status, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?, ?, 'PLANNED', 'REQUIRED', 'OPEN', ?, ?)",
            (binding_id, class_org_unit_id, first_cycle["id"], start, stored_first_planned, now, now),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.binding.create",
            resource_type="class_learning_binding",
            resource_id=str(binding_id),
            org_unit_id=class_org_unit_id,
            purpose="绑定三年学习计划",
            after={"class_org_unit_id": class_org_unit_id, "plan_version_id": plan_version_id},
        )
        return _progress_from_connection(connection, class_org_unit_id, at=now)


def update_current_learning_cycle(
    *, actor_user_id: int, class_org_unit_id: str, updates: dict[str, Any]
) -> dict[str, Any]:
    _visible_class(class_org_unit_id, actor_user_id)
    now = _now()
    with transaction() as connection:
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
                default_planned = planned_class_meeting_at_for_cycle(
                    binding["started_at"], index
                )
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
                else planned_class_meeting_at_for_cycle(binding["started_at"], next_index)
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
                "UPDATE class_learning_bindings SET status='COMPLETED', updated_at=? WHERE id=?",
                (now, binding["id"]),
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
