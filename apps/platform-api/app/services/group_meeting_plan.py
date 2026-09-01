"""Build the current group's meeting plan from the bound learning cycle.

The checked-in flow catalog is source evidence for the meeting steps.  The
class learning plan and its ``plan_cycle_id`` remain the runtime authority for
which cycle is active; the flow catalog only supplies the corresponding
meeting process and its learning-content semantics.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


class GroupMeetingPlanConfigError(ValueError):
    """The current cycle cannot be mapped to one safe meeting plan."""


COHORT_TEMPLATE_MONTHS = (1, 4, 7, 10)


def _cohort_template_label(cohort_month: int) -> str:
    month = int(cohort_month)
    if month not in COHORT_TEMPLATE_MONTHS:
        raise GroupMeetingPlanConfigError(
            "小组学习会只支持 1、4、7、10 月开班月份模板"
        )
    return f"{month}月开班模板"


def _data_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "data" / "learning-plans"
        if (candidate / "group-meeting-flows-2026.1.json").is_file():
            return candidate
    raise GroupMeetingPlanConfigError("找不到小组学习会流程配置")


@lru_cache(maxsize=1)
def _flow_catalog() -> dict[str, Any]:
    path = _data_root() / "group-meeting-flows-2026.1.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _cycle_flow_mapping() -> dict[tuple[int, int, int], dict[str, Any]]:
    path = _data_root() / "cycle-flow-mapping-2026.1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        (
            int(item.get("year_index") or 0),
            int(item.get("learning_cycle_index") or item.get("cycle_index") or 0),
            int(item.get("cohort_month") or 0),
        ): item
        for item in payload.get("mappings", [])
    }


def clear_flow_catalog_cache() -> None:
    """Allow tests or a controlled worker reload to see a new artifact."""

    _flow_catalog.cache_clear()
    _cycle_flow_mapping.cache_clear()


def _normalize_text(value: Any) -> str:
    return "".join(
        str(value or "").lower().split()
    ).translate(str.maketrans({char: "" for char in "\"“”‘’'（）()【】《》、，,；;：:。．"}))


def _flow_for_cycle(*, year_index: int, cycle_index: int, cohort_month: int) -> dict[str, Any]:
    if cohort_month not in COHORT_TEMPLATE_MONTHS:
        raise GroupMeetingPlanConfigError(
            "小组学习会只支持 1、4、7、10 月开班月份模板"
        )
    mapping = _cycle_flow_mapping().get((year_index, cycle_index, cohort_month))
    if not mapping or mapping.get("status") == "MAPPING_MISSING":
        raise GroupMeetingPlanConfigError(
            f"第{cycle_index}学习周期的小组学习会内容尚未配置，请联系运营人员检查学习计划"
        )
    if mapping.get("status") == "MAPPING_CONFLICT":
        raise GroupMeetingPlanConfigError(
            f"第{cycle_index}学习周期的小组学习会配置存在冲突，请联系运营人员检查学习计划"
        )
    flow_key = mapping.get("flow_key")
    if mapping.get("status") != "MAPPED" or not flow_key:
        raise GroupMeetingPlanConfigError(
            f"第{cycle_index}学习周期的小组学习会内容尚未配置，请联系运营人员检查学习计划"
        )
    flow = next(
        (item for item in _flow_catalog().get("flows", []) if item.get("flow_key") == flow_key),
        None,
    )
    if not flow or flow.get("status") != "PARSED":
        raise GroupMeetingPlanConfigError(
            f"第{cycle_index}学习周期的小组学习会内容尚未配置，请联系运营人员检查学习计划"
        )
    if (
        int(flow.get("year_index") or 0) != year_index
        or int(flow.get("cycle_index") or 0) != cycle_index
        or cohort_month not in (flow.get("eligible_cohort_months") or [])
    ):
        raise GroupMeetingPlanConfigError(
            f"第{cycle_index}学习周期的小组学习会配置存在冲突，请联系运营人员检查学习计划"
        )
    return flow


def _best_plan_task(node: dict[str, Any], plan_tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    node_key = node.get("course_key") or node.get("credit_rule_key")
    node_title = _normalize_text(node.get("title"))
    candidates: list[dict[str, Any]] = []
    for task in plan_tasks:
        if task.get("task_type") not in {"ONLINE_COURSE", "OFFLINE_COURSE", "GROUP_MEETING"}:
            continue
        metadata = task.get("metadata") or {}
        if node_key and metadata.get("canonical_key") == node_key:
            return task
        task_text = _normalize_text(
            f"{task.get('title') or ''} {task.get('description') or ''}"
        )
        if node_title and node_title in task_text:
            candidates.append(task)
    return candidates[0] if candidates else None


def build_group_meeting_plan(
    *,
    plan_cycle: dict[str, Any],
    cohort_month: int | None,
    learning_cycle_index: int | None = None,
) -> dict[str, Any]:
    """Return a public-safe meeting plan for one active ``plan_cycle``.

    A missing or ambiguous flow is an explicit configuration error.  The
    caller must surface that error instead of falling back to the global
    course-credit catalog.
    """

    if cohort_month is None:
        raise GroupMeetingPlanConfigError(
            "班级尚未配置开班批次，无法确定当前小组学习会内容"
        )
    cohort_month = int(cohort_month)
    template_label = _cohort_template_label(cohort_month)
    plan_cycle_index = int(
        plan_cycle.get("learning_cycle_index")
        or plan_cycle.get("cycle_index")
        or 0
    )
    cycle_index = int(learning_cycle_index or plan_cycle_index)
    year_index = int(plan_cycle.get("year_index") or 0)
    if not cycle_index or not year_index:
        raise GroupMeetingPlanConfigError("当前学习周期缺少有效的学习计划索引")
    if plan_cycle_index and plan_cycle_index != cycle_index:
        raise GroupMeetingPlanConfigError(
            "当前学习周期与学习计划周期不一致，请联系运营人员检查学习计划"
        )
    expected_year_index = ((cycle_index - 1) // 12) + 1 if 1 <= cycle_index <= 36 else 0
    if expected_year_index != year_index:
        raise GroupMeetingPlanConfigError(
            "当前学习周期与学习计划学年不一致，请联系运营人员检查学习计划"
        )
    plan_cohort_month = plan_cycle.get("cohort_month")
    if plan_cohort_month is not None and int(plan_cohort_month) != cohort_month:
        raise GroupMeetingPlanConfigError(
            "当前学习周期与班级开班批次不一致，请联系运营人员检查学习计划"
        )
    flow = _flow_for_cycle(
        year_index=year_index,
        cycle_index=cycle_index,
        cohort_month=int(cohort_month),
    )
    plan_tasks = [dict(task) for task in plan_cycle.get("tasks", [])]
    nodes = [dict(node) for node in flow.get("learning_content_nodes", [])]
    if "learning_content_nodes" not in flow:
        raise GroupMeetingPlanConfigError(
            "当前小组学习会流程尚未生成学习内容配置，请联系运营人员"
        )

    learning_contents: list[dict[str, Any]] = []
    content_keys_by_step: dict[int, list[str]] = {}
    for node in sorted(nodes, key=lambda item: (int(item.get("sort_order") or 0), item.get("content_key") or "")):
        content_key = str(node.get("content_key") or "").strip()
        if not content_key:
            raise GroupMeetingPlanConfigError("学习内容缺少稳定标识")
        step_no = int(node.get("source_step_no") or 0)
        if not step_no:
            raise GroupMeetingPlanConfigError(f"学习内容 {content_key} 缺少流程步骤")
        plan_task = _best_plan_task(node, plan_tasks)
        course_key = node.get("course_key") or node.get("credit_rule_key")
        credit_points = node.get("credit_points")
        # The flow's explicit credit mapping is the source snapshot.  A plan
        # task may confirm it, but never replaces a missing mapping by a guess.
        learning_contents.append(
            {
                "content_key": content_key,
                "task_type": node.get("task_type"),
                "title": node.get("title"),
                "description": node.get("description"),
                "required": bool(node.get("is_required", True)),
                "sort_order": int(node.get("sort_order") or 0),
                "credit_rule_key": course_key,
                "credit_points": credit_points,
                "verification_mode": node.get("verification_mode") or "MEETING_CONFIRM",
                "content_access": {
                    "type": "QR" if node.get("qr_refs") else "NONE",
                    "label": "扫码打开学习内容" if node.get("qr_refs") else None,
                },
                "plan_match_status": "MATCHED" if plan_task else "NOT_FOUND",
            }
        )
        content_keys_by_step.setdefault(step_no, []).append(content_key)

    steps: list[dict[str, Any]] = []
    for step in flow.get("steps", []):
        step_no = int(step.get("step_no") or 0)
        steps.append(
            {
                "step_no": step_no,
                "title": step.get("title"),
                "content": step.get("content"),
                "required": bool(step.get("is_required", True)),
                "is_terminal": bool(step.get("is_terminal")),
                "learning_content_keys": content_keys_by_step.get(step_no, []),
            }
        )

    return {
        "configuration_status": "CONFIGURED",
        "plan_cycle_id": int(plan_cycle["id"]),
        "cohort_month": cohort_month,
        "cohort_template_label": template_label,
        "learning_cycle_index": cycle_index,
        "learning_cycle_label": f"{template_label} · 第{cycle_index}学习周期",
        # Keep cycle_index for existing admin consumers until V1.3-C removes
        # the legacy course-selection contract.
        "cycle_index": cycle_index,
        "year_index": year_index,
        "year_cycle_index": ((cycle_index - 1) % 12) + 1,
        "title": f"第{cycle_index}次小组学习会",
        "steps": steps,
        "learning_contents": learning_contents,
    }
