from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.db import connect, execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


CLASS_MEETING_STATUSES = {"PLANNED", "POSTPONED"}
GROUP_MEETING_POLICIES = {"REQUIRED", "SUSPENDED", "WAIVED"}
GROUP_TASK_STATUSES = {"PENDING", "COMPLETED", "WAIVED"}


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


def _cycle_at(
    connection, binding_id: int, at: str
) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT * FROM class_learning_cycles WHERE binding_id=? AND opened_at<=? "
        "ORDER BY opened_at DESC, learning_cycle_index DESC LIMIT 1",
        (binding_id, at),
    ).fetchone()
    if not row:
        row = execute(
            connection,
            "SELECT * FROM class_learning_cycles WHERE binding_id=? "
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
    binding = _active_binding(connection, class_org_unit_id)
    if not binding:
        completed = execute(
            connection,
            "SELECT b.*, p.plan_key, p.plan_name, p.version_label, p.duration_cycles "
            "FROM class_learning_bindings b JOIN learning_plan_versions p ON p.id=b.plan_version_id "
            "WHERE b.class_org_unit_id=? ORDER BY b.updated_at DESC, b.id DESC LIMIT 1",
            (class_org_unit_id,),
        ).fetchone()
        if completed:
            binding = dict(completed)
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
    if cohort_month is not None and not 1 <= int(cohort_month) <= 12:
        raise ValueError("开班批次月份必须在 1 到 12 之间")
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
        execute(
            connection,
            "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
            "plan_cycle_id, opened_at, class_meeting_status, group_meeting_policy, cycle_status, "
            "created_at, updated_at) VALUES (?, ?, 1, ?, ?, 'PLANNED', 'REQUIRED', 'OPEN', ?, ?)",
            (binding_id, class_org_unit_id, first_cycle["id"], start, now, now),
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
            params.append(_normalize_datetime(updates.get("planned_class_meeting_at"), "计划班会时间"))
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
            execute(
                connection,
                "INSERT INTO class_learning_cycles(binding_id, class_org_unit_id, learning_cycle_index, "
                "plan_cycle_id, opened_at, class_meeting_status, group_meeting_policy, cycle_status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'PLANNED', 'REQUIRED', 'OPEN', ?, ?)",
                (binding["id"], class_org_unit_id, next_index, next_plan_cycle["id"], actual, now, now),
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
