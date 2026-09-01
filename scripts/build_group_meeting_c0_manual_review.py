"""Build the V1.3-C0 manual review package for group-meeting content mappings.

The C0 package is deliberately an audit artifact.  It does not assign credit
or modify production data.  After business confirmation, resolved mappings
are no longer included in the unresolved primary list; remaining
template-only exceptions stay visible in the appendix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("data/learning-plans")
DEFAULT_JSON = DEFAULT_ROOT / "group-meeting-learning-content-manual-review-2026.1.json"
DEFAULT_MARKDOWN = DEFAULT_ROOT / "group-meeting-learning-content-manual-review-2026.1.md"
BASE_COMMIT = "51ce48fe2cd70314acb00be01af0a67455cac6b0"
IN_SCOPE = "GROUP_MEETING_36_CYCLES"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
UNRESOLVED = {"MAPPING_CONFLICT", "MAPPING_MISSING"}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _normalized(value: Any) -> str:
    return re.sub(r"[\s\"“”‘’'（）()【】《》、，,；;：:。．]+", "", str(value or "")).lower()


def _year_index(cycle_index: int) -> int:
    if not 1 <= int(cycle_index) <= 36:
        raise ValueError("learning_cycle_index 必须在 1 到 36 之间")
    return ((int(cycle_index) - 1) // 12) + 1


def _plan_index(plan: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    return {
        (int(track["cohort_month"]), int(cycle["cycle_index"])): cycle
        for track in plan.get("cohort_tracks", [])
        for cycle in track.get("cycles", [])
    }


def _mapping_index(mapping: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    return {
        (int(item["cohort_month"]), int(item.get("learning_cycle_index") or item.get("cycle_index") or 0)): item
        for item in mapping.get("mappings", [])
    }


def _flow_index(flows: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(flow.get("flow_key")): flow
        for flow in flows.get("flows", [])
        if flow.get("flow_key")
    }


def _group_tasks(plan_cycle: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not plan_cycle:
        return []
    return [
        task
        for task in plan_cycle.get("tasks", [])
        if task.get("task_type") == "GROUP_MEETING"
    ]


def _semantic_task_types(text: str) -> list[str]:
    candidates: list[str] = []
    if "视频" in text:
        candidates.append("VIDEO_LEARNING")
    if "课程" in text:
        candidates.append("COURSE_LEARNING")
    if any(marker in text for marker in ("实操", "制作", "编写", "核算表")):
        candidates.append("PRACTICE")
    if any(marker in text for marker in ("研讨", "辅导", "检视", "分享", "观摩", "沟通")):
        candidates.append("DISCUSSION")
    return _unique(candidates)


def _rule_candidates(text: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_text = _normalized(text)
    candidates: list[dict[str, Any]] = []
    for rule in rules:
        aliases = [rule.get("course_name"), *(rule.get("aliases") or [])]
        matched_aliases = [
            str(alias)
            for alias in aliases
            if _normalized(alias) and _normalized(alias) in normalized_text
        ]
        if matched_aliases:
            candidates.append(
                {
                    "course_key": rule.get("course_key"),
                    "course_name": rule.get("course_name"),
                    "year_index": rule.get("year_index"),
                    "credit_points": rule.get("credit_points"),
                    "rule_status": rule.get("status"),
                    "matched_aliases": _unique(matched_aliases),
                    "confidence": "CANDIDATE_ONLY",
                }
            )
    return candidates


def _flow_evidence(flow: dict[str, Any]) -> dict[str, Any]:
    source = flow.get("source") or {}
    return {
        "flow_key": flow.get("flow_key"),
        "title": flow.get("title"),
        "status": flow.get("status"),
        "source_filename": source.get("filename"),
        "source_relative_path": source.get("relative_path"),
        "source_sha256": source.get("sha256"),
        "steps": [
            {
                "step_no": step.get("step_no"),
                "title": step.get("title"),
                "content": step.get("content"),
                "is_required": step.get("is_required"),
                "is_terminal": step.get("is_terminal"),
                "source_paragraph_index": step.get("source_paragraph_index"),
                "qr_refs": step.get("qr_refs") or [],
            }
            for step in flow.get("steps", [])
        ],
        "learning_content_nodes": [dict(node) for node in flow.get("learning_content_nodes", [])],
        "course_nodes": [dict(node) for node in flow.get("course_nodes", [])],
    }


def _planned_task_evidence(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        metadata = task.get("metadata") or {}
        result.append(
            {
                "task_type": task.get("task_type"),
                "title": task.get("title"),
                "description": task.get("description"),
                "is_required": task.get("is_required"),
                "credit_points": task.get("credit_points"),
                "source_file": metadata.get("source_file"),
                "source_sheet": metadata.get("source_sheet"),
                "source_cell": metadata.get("source_cell"),
                "source_text": metadata.get("source_text"),
            }
        )
    return result


def _business_confirmation() -> dict[str, Any]:
    return {
        "final_content_name": "",
        "is_group_meeting_content": None,
        "is_required": None,
        "content_type": "",
        "has_credit": "",
        "credit_rule_key": "",
        "credit_points": None,
        "qr_location": "",
        "resolution": "",
        "notes": "",
    }


def _priority(
    *,
    projection: dict[str, Any] | None,
    learning_cycle_index: int,
) -> tuple[str, str]:
    if projection is None:
        return "P2", "仅模板完整性问题，当前没有纳入范围的真实班级投影"
    current = projection.get("current_projection") or {}
    if current.get("status") == "LOADED" and current.get("current_open_cycle") == learning_cycle_index:
        return "P0", "当前真实 OPEN 周期直接受到影响"
    if current.get("status") == "NOT_PROVIDED":
        return "P1", "纳入范围的真实班级未来会遇到，但运行时 OPEN 周期尚未提供"
    return "P1", "纳入范围的真实班级未来会遇到"


def _build_item(
    *,
    status: str,
    mapping: dict[str, Any],
    plan_cycle: dict[str, Any] | None,
    flow_index: dict[str, dict[str, Any]],
    rules: list[dict[str, Any]],
    projection: dict[str, Any] | None,
    source_scope: str,
    ordinal: int,
) -> dict[str, Any]:
    cohort_month = int(mapping.get("cohort_month"))
    learning_cycle_index = int(mapping.get("learning_cycle_index") or mapping.get("cycle_index"))
    year_index = int(mapping.get("year_index") or _year_index(learning_cycle_index))
    candidate_flow_keys = [str(key) for key in mapping.get("candidate_flow_keys") or []]
    flow_evidence = [_flow_evidence(flow_index[key]) for key in candidate_flow_keys if key in flow_index]
    planned_group_tasks = _group_tasks(plan_cycle)
    current_nodes = [
        node
        for flow in flow_evidence
        for node in flow.get("learning_content_nodes", [])
    ]
    current_nodes = _unique(current_nodes)
    source_texts = [
        str(task.get("description") or task.get("title") or "")
        for task in planned_group_tasks
    ]
    source_texts.extend(
        str(step.get("content") or step.get("title") or "")
        for flow in flow_evidence
        for step in flow.get("steps", [])
    )
    source_text = "\n".join(text for text in source_texts if text)
    task_type_candidates = _semantic_task_types(source_text)
    if current_nodes:
        current_task_types = _unique([node.get("task_type") for node in current_nodes if node.get("task_type")])
        identified_task_type = current_task_types[0] if len(current_task_types) == 1 else "REVIEW_REQUIRED"
    else:
        identified_task_type = task_type_candidates[0] if len(task_type_candidates) == 1 else "REVIEW_REQUIRED"
        current_task_types = []
    required_values = _unique(
        [node.get("is_required") for node in current_nodes]
        + [task.get("is_required") for task in planned_group_tasks]
        + [step.get("is_required") for flow in flow_evidence for step in flow.get("steps", [])]
    )
    is_required: bool | None
    if required_values and all(value is True for value in required_values):
        is_required = True
    elif len(required_values) == 1 and isinstance(required_values[0], bool):
        is_required = required_values[0]
    else:
        is_required = None
    qr_refs = _unique(
        [ref for node in current_nodes for ref in node.get("qr_refs") or []]
        + [ref for flow in flow_evidence for step in flow.get("steps", []) for ref in step.get("qr_refs") or []]
    )
    current_titles = _unique([node.get("title") for node in current_nodes if node.get("title")])
    candidate_courses = _rule_candidates(source_text, rules)
    candidate_rule_keys = _unique([item.get("course_key") for item in candidate_courses if item.get("course_key")])
    candidate_points = _unique([item.get("credit_points") for item in candidate_courses])
    current_rule_keys = _unique(
        [node.get("credit_rule_key") or node.get("course_key") for node in current_nodes]
    )
    current_rule_keys = [key for key in current_rule_keys if key]
    current_points = _unique([node.get("credit_points") for node in current_nodes])
    planned_text = _planned_task_evidence(planned_group_tasks)
    class_impact: list[dict[str, Any]] = []
    if projection is not None:
        current_projection = projection.get("current_projection") or {}
        class_impact.append(
            {
                "class_name": projection.get("class_name"),
                "class_org_unit_id": projection.get("class_org_unit_id"),
                "class_open_date": projection.get("actual_open_year_month"),
                "cohort_month": cohort_month,
                "learning_cycle_index": learning_cycle_index,
                "planned_date": (
                    next(
                        (
                            cycle.get("planned_date")
                            or cycle.get("specified_date")
                            or cycle.get("planned_month")
                            for cycle in projection.get("cycle_projections", [])
                            if cycle.get("learning_cycle_index") == learning_cycle_index
                        ),
                        None,
                    )
                ),
                "current_open_cycle": current_projection.get("current_open_cycle"),
                "actual_projection_status": current_projection.get("status", "NOT_PROVIDED"),
                "impact_level": "P0" if current_projection.get("status") == "LOADED" and current_projection.get("current_open_cycle") == learning_cycle_index else "P1",
            }
        )
    priority, impact_reason = _priority(
        projection=projection,
        learning_cycle_index=learning_cycle_index,
    )
    technical_reason = (
        f"cohort_month={cohort_month}, learning_cycle_index={learning_cycle_index}, year_index={year_index} "
        f"命中 {len(candidate_flow_keys)} 个候选流程，无法安全选择唯一 flow。"
        if status == "MAPPING_CONFLICT"
        else "按 cohort_month + learning_cycle_index + year_index 未找到对应的小组学习会流程；不能用自然月份或相邻周期猜测。"
    )
    suggestion = (
        "人工对照候选原始文件，确认唯一流程；若只是重复文件，确认后再做 alias/去重标准化。"
        if status == "MAPPING_CONFLICT"
        else "人工核对原始资料；确认有流程后补充 mapping，确认没有小组会后标记 EXEMPTED，否则标记 SOURCE_MISSING。"
    )
    suffix_source = projection.get("class_name") if projection else "template-only"
    suffix = hashlib.sha256(
        f"{source_scope}|{suffix_source}|{cohort_month}|{learning_cycle_index}|{ordinal}".encode("utf-8")
    ).hexdigest()[:8]
    source_files = _unique(
        [
            flow.get("source_relative_path")
            for flow in flow_evidence
            if flow.get("source_relative_path")
        ]
        + [task.get("source_file") for task in planned_text if task.get("source_file")]
    )
    source_steps = [
        {
            "flow_key": flow.get("flow_key"),
            "step_no": step.get("step_no"),
            "title": step.get("title"),
            "content": step.get("content"),
            "is_required": step.get("is_required"),
            "is_terminal": step.get("is_terminal"),
            "source_paragraph_index": step.get("source_paragraph_index"),
        }
        for flow in flow_evidence
        for step in flow.get("steps", [])
    ]
    return {
        "finding_id": f"c0-{status.lower()}-{cohort_month}-{learning_cycle_index}-{suffix}",
        "source_scope": source_scope,
        "status": status,
        "review_status": "REVIEW_REQUIRED",
        "cohort_month": cohort_month,
        "learning_cycle_index": learning_cycle_index,
        "year_index": year_index,
        "template_label": f"{cohort_month}月开班模板",
        "learning_cycle_label": f"{cohort_month}月开班模板 · 第{learning_cycle_index}学习周期",
        "flow_key": mapping.get("flow_key"),
        "candidate_flow_keys": candidate_flow_keys,
        "source_file_names": [
            str(flow.get("source_filename"))
            for flow in flow_evidence
            if flow.get("source_filename")
        ],
        "source_files": source_files,
        "original_source_steps": source_steps,
        "planned_group_meeting_tasks": planned_text,
        "identified_task_type": identified_task_type,
        "task_type_candidates": task_type_candidates,
        "source_task_type": "GROUP_MEETING" if planned_group_tasks else None,
        "is_required": is_required,
        "qr_refs": qr_refs,
        "current_learning_content_titles": current_titles,
        "current_learning_content_nodes": current_nodes,
        "candidate_standard_courses": candidate_courses,
        "current_credit_rule_keys": current_rule_keys,
        "current_credit_points": current_points,
        "candidate_credit_rule_keys": candidate_rule_keys,
        "candidate_credit_points": candidate_points,
        "group_meeting_base_credit_note": "小组会基础出席分（当前规则为每周期每人4分）不是本内容节点的课程积分，本轮不自动归属。",
        "technical_reason": technical_reason,
        "codex_suggestion": suggestion,
        "safe_to_auto_fix": False,
        "auto_fix_reason": "当前属于业务映射未确认项；没有足够证据安全选择流程、必学属性或积分规则。",
        "must_be_manually_confirmed": True,
        "priority": priority,
        "impact_reason": impact_reason,
        "class_impact": class_impact,
        "actual_projection": (projection or {}).get("current_projection") if projection else {
            "status": "NOT_APPLICABLE",
            "source": "没有当前纳入范围的真实班级投影",
        },
        "schedule_override_invariance": {
            "lookup_identity": ["cohort_month", "learning_cycle_index", "year_index", "plan_cycle_id"],
            "planned_date_may_change": True,
            "learning_content_identity_may_change": False,
            "note": "班会顺延只调整计划/实际时间，不改变 learning_cycle_index、plan_cycle_id 或学习内容身份。",
        },
        "business_confirmation": _business_confirmation(),
    }


def _build_required_video_without_qr(
    *,
    flows: dict[str, Any],
    mappings: dict[tuple[int, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flow in flows.get("flows", []):
        cycle_index = int(flow.get("cycle_index") or 0)
        year_index = int(flow.get("year_index") or _year_index(cycle_index))
        for node in flow.get("learning_content_nodes", []):
            if not (
                node.get("task_type") == "VIDEO_LEARNING"
                and node.get("is_required")
                and not node.get("qr_refs")
            ):
                continue
            for cohort_month in flow.get("eligible_cohort_months") or []:
                mapping = mappings.get((int(cohort_month), cycle_index))
                rows.append(
                    {
                        "cohort_month": int(cohort_month),
                        "learning_cycle_index": cycle_index,
                        "year_index": year_index,
                        "flow_key": flow.get("flow_key"),
                        "content_key": node.get("content_key"),
                        "title": node.get("title"),
                        "required": True,
                        "qr_refs": [],
                        "credit_rule_key": node.get("credit_rule_key"),
                        "credit_points": node.get("credit_points"),
                        "match_status": mapping.get("status") if mapping else "MAPPING_MISSING",
                        "review_status": "REVIEW_REQUIRED",
                        "note": "无二维码不代表不是学习内容，也不代表自动有积分；请单独确认内容与积分。",
                    }
                )
    return sorted(
        rows,
        key=lambda item: (
            item["cohort_month"],
            item["learning_cycle_index"],
            str(item.get("content_key") or ""),
        ),
    )


def _build_historical_qr_nodes(
    *,
    flows: dict[str, Any],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for flow in flows.get("flows", []):
        source = flow.get("source") or {}
        for node_index, node in enumerate(flow.get("course_nodes", []), start=1):
            if node.get("credit_status") != "QR_REVIEW_REQUIRED":
                continue
            context_text = str(node.get("context_text") or "")
            candidates = _rule_candidates(context_text, rules)
            rows.append(
                {
                    "node_id": f"{flow.get('flow_key')}-course-node-{node_index}",
                    "cohort_months": flow.get("eligible_cohort_months") or [],
                    "learning_cycle_index": flow.get("cycle_index"),
                    "year_index": flow.get("year_index"),
                    "flow_key": flow.get("flow_key"),
                    "source_file": source.get("filename"),
                    "context_step_no": node.get("context_step_no"),
                    "context_text": context_text,
                    "qr_location": {
                        "media_target": node.get("media_target"),
                        "relationship_id": node.get("relationship_id"),
                        "source_paragraph_index": node.get("source_paragraph_index"),
                        "qr_url_present": bool(node.get("qr_url")),
                    },
                    "candidate_standard_courses": candidates,
                    "candidate_credit_rule_keys": [item.get("course_key") for item in candidates],
                    "candidate_credit_points": [item.get("credit_points") for item in candidates],
                    "review_classification": "D. 无法确认（待业务核对）",
                    "credit_rule_key": None,
                    "credit_points": None,
                    "review_status": "REVIEW_REQUIRED",
                    "note": "二维码仅作为访问入口；不得由二维码本身生成课程或积分规则。",
                }
            )
    return rows


def build_review(*, root: Path, base_commit: str = BASE_COMMIT) -> dict[str, Any]:
    projection = _read(root / "group-meeting-class-projection-audit-2026.1.json")
    plan = _read(root / "standard-3y-2026.json")
    mapping = _read(root / "cycle-flow-mapping-2026.1.json")
    flows = _read(root / "group-meeting-flows-2026.1.json")
    rules_payload = _read(root / "course-credit-rules-2026.json")
    rules = rules_payload.get("rules", [])
    plan_index = _plan_index(plan)
    mappings = _mapping_index(mapping)
    flow_index = _flow_index(flows)
    primary: list[dict[str, Any]] = []
    ordinal = 0
    primary_mapping_keys: set[tuple[int, int]] = set()
    for class_projection in projection.get("classes", []):
        if class_projection.get("learning_plan_scope") != IN_SCOPE:
            continue
        for cycle in class_projection.get("cycle_projections", []):
            status = cycle.get("plan_match_status")
            if status not in UNRESOLVED:
                continue
            ordinal += 1
            cohort_month = int(cycle["cohort_month"])
            learning_cycle_index = int(cycle["learning_cycle_index"])
            primary_mapping_keys.add((cohort_month, learning_cycle_index))
            mapping_item = mappings.get((cohort_month, learning_cycle_index))
            if mapping_item is None:
                mapping_item = {
                    "cohort_month": cohort_month,
                    "learning_cycle_index": learning_cycle_index,
                    "cycle_index": learning_cycle_index,
                    "year_index": _year_index(learning_cycle_index),
                    "status": status,
                    "candidate_flow_keys": [],
                    "candidate_source_files": [],
                }
            primary.append(
                _build_item(
                    status=status,
                    mapping=mapping_item,
                    plan_cycle=plan_index.get((cohort_month, learning_cycle_index)),
                    flow_index=flow_index,
                    rules=rules,
                    projection=class_projection,
                    source_scope="IN_SCOPE_CLASS_PROJECTION",
                    ordinal=ordinal,
                )
            )
    supplemental: list[dict[str, Any]] = []
    for mapping_item in mapping.get("mappings", []):
        status = mapping_item.get("status")
        key = (
            int(mapping_item.get("cohort_month")),
            int(mapping_item.get("learning_cycle_index") or mapping_item.get("cycle_index")),
        )
        if status not in UNRESOLVED or key in primary_mapping_keys:
            continue
        ordinal += 1
        supplemental.append(
            _build_item(
                status=status,
                mapping=mapping_item,
                plan_cycle=plan_index.get(key),
                flow_index=flow_index,
                rules=rules,
                projection=None,
                source_scope="TEMPLATE_ONLY_SUPPLEMENTAL",
                ordinal=ordinal,
            )
        )
    primary_counts = Counter(item["status"] for item in primary)
    supplemental_counts = Counter(item["status"] for item in supplemental)
    all_items = primary + supplemental
    all_priorities = Counter(item["priority"] for item in all_items)
    primary_priorities = Counter(item["priority"] for item in primary)
    template_counts = Counter(
        item.get("status")
        for item in mapping.get("mappings", [])
        if item.get("status") in UNRESOLVED
    )
    required_video_without_qr = _build_required_video_without_qr(
        flows=flows,
        mappings=mappings,
    )
    historical_qr_nodes = _build_historical_qr_nodes(flows=flows, rules=rules)
    return {
        "schema_version": 1,
        "review_type": "V1.3-C0_GROUP_MEETING_LEARNING_CONTENT_MANUAL_REVIEW",
        "status": "PENDING_BUSINESS_REVIEW",
        "base_commit": base_commit,
        "business_boundary": {
            "mapping_key": ["cohort_month", "learning_cycle_index", "year_index"],
            "calendar_month_is_not_content_identity": True,
            "schedule_override_changes_time_only": True,
            "formal_mapping_changes_allowed": False,
            "production_deploy_allowed": False,
            "production_migration_allowed": False,
        },
        "baseline": {
            "canonical_scope": "REAL_IN_SCOPE_CLASS_PROJECTION",
            "canonical_source": "group-meeting-class-projection-audit-2026.1.json",
            "actual_projection_status": projection.get("summary", {}).get("actual_projection_status", "NOT_PROVIDED"),
            "class_projection_summary": projection.get("summary", {}),
            "template_mapping_source": "cycle-flow-mapping-2026.1.json",
            "template_mapping_source_counts": {
                "MAPPING_CONFLICT": template_counts.get("MAPPING_CONFLICT", 0),
                "MAPPING_MISSING": template_counts.get("MAPPING_MISSING", 0),
            },
            "template_scope_note": "全模板 mapping 文件包含当前真实班级范围之外的1月/10月模板异常；它们列为补充项，不计入C0主清单。",
        },
        "source_fingerprints": {
            filename: _sha256(root / filename)
            for filename in (
                "group-meeting-class-projection-audit-2026.1.json",
                "standard-3y-2026.json",
                "cycle-flow-mapping-2026.1.json",
                "group-meeting-flows-2026.1.json",
                "course-credit-rules-2026.json",
            )
        },
        "summary": {
            "cycle_count": 144,
            "canonical_item_count": len(primary),
            "mapping_conflict_count": primary_counts.get("MAPPING_CONFLICT", 0),
            "mapping_missing_count": primary_counts.get("MAPPING_MISSING", 0),
            "finding_count": len(primary),
            "mapped_count": projection.get("summary", {}).get("mapped_projected_cycle_count", 0),
            "safe_auto_fix_count": sum(not item["must_be_manually_confirmed"] for item in all_items),
            "review_required_count": sum(item["must_be_manually_confirmed"] for item in all_items),
            "canonical_review_required_count": sum(item["must_be_manually_confirmed"] for item in primary),
            "template_only_supplemental_count": len(supplemental),
            "supplemental_mapping_conflict_count": supplemental_counts.get("MAPPING_CONFLICT", 0),
            "supplemental_mapping_missing_count": supplemental_counts.get("MAPPING_MISSING", 0),
            "priority_counts": {
                "P0": all_priorities.get("P0", 0),
                "P1": all_priorities.get("P1", 0),
                "P2": all_priorities.get("P2", 0),
            },
            "canonical_priority_counts": {
                "P0": primary_priorities.get("P0", 0),
                "P1": primary_priorities.get("P1", 0),
                "P2": primary_priorities.get("P2", 0),
            },
            "required_video_without_qr_count": len(required_video_without_qr),
            "historical_qr_node_count": len(historical_qr_nodes),
            "actual_projection_status": projection.get("summary", {}).get("actual_projection_status", "NOT_PROVIDED"),
        },
        "items": primary,
        "template_only_supplemental_items": supplemental,
        "appendices": {
            "required_video_without_qr": required_video_without_qr,
            "historical_qr_nodes": historical_qr_nodes,
        },
    }


def _md(value: Any, *, limit: int | None = None) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        text = "、".join(_md(item) for item in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = text.replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")
    if limit and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _item_heading(item: dict[str, Any]) -> str:
    return f"### {item['finding_id']} · {item['status']} · {item['cohort_month']}月模板 / 第{item['learning_cycle_index']}周期"


def _render_item(item: dict[str, Any]) -> list[str]:
    lines = [_item_heading(item), "", "| 字段 | 当前审计结果 |", "|---|---|"]
    fields = (
        ("模板", item.get("template_label")),
        ("learning_cycle_index", item.get("learning_cycle_index")),
        ("flow_key", item.get("flow_key")),
        ("候选流程", item.get("candidate_flow_keys")),
        ("当前识别的 task_type", item.get("identified_task_type")),
        ("task_type 候选", item.get("task_type_candidates")),
        ("是否 required", item.get("is_required")),
        ("qr_refs", item.get("qr_refs")),
        ("当前 learning content title", item.get("current_learning_content_titles")),
        ("候选标准课程", item.get("candidate_standard_courses")),
        ("当前 credit_rule", item.get("current_credit_rule_keys")),
        ("候选 credit_rule", item.get("candidate_credit_rule_keys")),
        ("候选积分", item.get("candidate_credit_points")),
        ("优先级", item.get("priority")),
        ("影响判断", item.get("impact_reason")),
        ("是否可以安全自动修复", "否" if item.get("safe_to_auto_fix") is False else "是"),
        ("是否必须人工确认", "是" if item.get("must_be_manually_confirmed") else "否"),
    )
    lines.extend(f"| {label} | {_md(value)} |" for label, value in fields)
    lines.extend(
        [
            "",
            "**产生冲突/缺失的技术原因**",
            "",
            str(item.get("technical_reason") or "—"),
            "",
            "**Codex 建议**",
            "",
            str(item.get("codex_suggestion") or "—"),
            "",
            "#### 真实班级影响",
            "",
            "| 班级 | class_org_unit_id | 开班年月 | 周期 | 计划日期 | 当前 OPEN | 实际状态 | 影响级别 |",
            "|---|---|---|---:|---|---:|---|---|",
        ]
    )
    if item.get("class_impact"):
        for impact in item["class_impact"]:
            lines.append(
                "| "
                + " | ".join(
                    _md(impact.get(key))
                    for key in (
                        "class_name",
                        "class_org_unit_id",
                        "class_open_date",
                        "learning_cycle_index",
                        "planned_date",
                        "current_open_cycle",
                        "actual_projection_status",
                        "impact_level",
                    )
                )
                + " |"
            )
    else:
        lines.append("| — | — | — | — | — | — | NOT_APPLICABLE | P2 |")
    lines.extend(["", "#### 原始文件与步骤", ""])
    if item.get("source_files"):
        lines.extend(f"- 原始文件：{_md(path)}" for path in item["source_files"])
    else:
        lines.append("- 原始小组学习会流程文件：未找到")
    if item.get("original_source_steps"):
        for step in item["original_source_steps"]:
            lines.append(
                f"- `{_md(step.get('flow_key'))}` 第{_md(step.get('step_no'))}步："
                f"{_md(step.get('content') or step.get('title'))}"
            )
    else:
        lines.append("- 原始流程步骤：未找到对应流程；以下计划表任务仅作为待核对线索。")
        for task in item.get("planned_group_meeting_tasks", []):
            lines.append(
                f"- [计划表] {_md(task.get('title'))}：{_md(task.get('description'))}"
            )
    lines.extend(
        [
            "",
            "#### 业务确认（请填写）",
            "",
            "- 最终标准内容名称：",
            "- 是否属于本周期小组学习会：是 / 否",
            "- 是否必学：是 / 否",
            "- 内容类型：VIDEO_LEARNING / COURSE_LEARNING / DISCUSSION / PRACTICE / OTHER",
            "- 是否有积分：是 / 否 / 待确认",
            "- 如有积分，标准 credit_rule_key：",
            "- 积分值：",
            "- 二维码定位：仅访问入口 / 无二维码 / 不适用",
            "- 处理方式：确认映射 / 新增 alias / 新增规则 / 保持无积分 / 忽略该节点 / 需要进一步核对原始资料",
            "- 备注：",
            "",
            "#### B2 周期顺延不变量",
            "",
            "班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。",
            "",
        ]
    )
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# V1.3-C0 小组学习会学习内容映射人工审核清单",
        "",
        "> 状态：PENDING_BUSINESS_REVIEW。本文档只提供证据和待确认项，不写入正式 mapping、不赋予正式积分。",
        "",
        "## 审核口径",
        "",
        f"- 基线 commit：`{payload['base_commit']}`",
        f"- 主清单采用最新真实班级投影口径：{summary['mapping_conflict_count']} 个冲突 + "
        f"{summary['mapping_missing_count']} 个缺失 = {summary['canonical_item_count']} 个审核项。",
        "- 主清单只覆盖当前纳入小组学习会 36 周期计划的真实班级；模板层但暂无真实班级影响的异常放入补充附录。",
        "- 1/26 等写法统一解释为 `cohort_month=1, learning_cycle_index=26`，不使用自然月份替代学习周期。",
        "- 实际周期状态来自 `class_learning_cycles`；当前审计的实际运行状态为 `NOT_PROVIDED`，没有虚构 OPEN 周期。",
        "- 班会顺延只能改变计划/实际时间；不得改变 `cohort_month`、`learning_cycle_index`、`plan_cycle_id` 或学习内容身份。",
        "",
        "## 汇总",
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
        f"| 144 模板周期 | {summary['cycle_count']} |",
        f"| MATCHED | {summary['mapped_count']} |",
        f"| 主清单 MAPPING_CONFLICT | {summary['mapping_conflict_count']} |",
        f"| 主清单 MAPPING_MISSING | {summary['mapping_missing_count']} |",
        f"| 主清单审核项 | {summary['canonical_item_count']} |",
        f"| 模板补充项 | {summary['template_only_supplemental_count']} |",
        f"| 可安全自动修复 | {summary['safe_auto_fix_count']} |",
        f"| 全部 REVIEW_REQUIRED | {summary['review_required_count']} |",
        f"| P0 / P1 / P2 | {summary['priority_counts']['P0']} / {summary['priority_counts']['P1']} / {summary['priority_counts']['P2']} |",
        f"| 无二维码必学视频投影 | {summary['required_video_without_qr_count']} |",
        f"| 历史二维码节点 | {summary['historical_qr_node_count']} |",
        "",
        "### 模板层计数说明",
        "",
        f"仓库全模板 mapping 文件当前仍有 {summary['supplemental_mapping_conflict_count']} 个冲突 + "
        f"{summary['supplemental_mapping_missing_count']} 个缺失；这些是当前纳入范围之外的模板项，"
        "不计入主清单，也不删除，见补充附录。",
        "",
        "## 主清单汇总表",
        "",
        "| ID | 模板 | 周期 | 状态 | 原内容 | 候选规则 | 影响班级 | 优先级 | Codex建议 | 是否人工确认 |",
        "|---|---|---:|---|---|---|---|---|---|---|",
    ]
    for item in payload["items"]:
        planned = item.get("planned_group_meeting_tasks") or []
        original = item.get("current_learning_content_titles") or [
            task.get("description") or task.get("title") for task in planned
        ]
        impact = [row.get("class_name") for row in item.get("class_impact") or []]
        lines.append(
            "| "
            + " | ".join(
                (
                    _md(item.get("finding_id")),
                    _md(item.get("template_label")),
                    _md(item.get("learning_cycle_index")),
                    _md(item.get("status")),
                    _md(original, limit=100),
                    _md(item.get("candidate_credit_rule_keys")),
                    _md(impact),
                    _md(item.get("priority")),
                    _md(item.get("codex_suggestion"), limit=100),
                    "是",
                )
            )
            + "|"
        )
    lines.extend(["", f"## {summary['canonical_item_count']} 项主清单逐项证据与业务确认", ""])
    for item in payload["items"]:
        lines.extend(_render_item(item))
    lines.extend(
        [
            f"## 附录 A：模板层补充异常（不计入主清单 {summary['canonical_item_count']} 项）",
            "",
            "这些项仍然需要业务确认，但当前没有纳入范围的真实班级影响，因此统一为 P2。",
            "",
            "| ID | 模板 | 周期 | 状态 | 影响 | 优先级 |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for item in payload["template_only_supplemental_items"]:
        lines.append(
            f"| {_md(item.get('finding_id'))} | {_md(item.get('template_label'))} | "
            f"{_md(item.get('learning_cycle_index'))} | {_md(item.get('status'))} | 无当前纳入范围班级 | P2 |"
        )
    lines.extend(["", "## 附录 B：无二维码必学视频复核", "", "二维码为空不代表内容不存在，也不自动生成积分。", ""])
    lines.extend(
        [
            "| cohort_month | learning_cycle_index | 标题 | required | qr_refs | credit_rule | 积分 | match_status |",
            "|---:|---:|---|---|---|---|---:|---|",
        ]
    )
    for row in payload["appendices"]["required_video_without_qr"]:
        lines.append(
            "| "
            + " | ".join(
                _md(row.get(key))
                for key in (
                    "cohort_month",
                    "learning_cycle_index",
                    "title",
                    "required",
                    "qr_refs",
                    "credit_rule_key",
                    "credit_points",
                    "match_status",
                )
            )
            + " |"
        )
    lines.extend(["", "## 附录 C：历史二维码节点审核", "", "本附录暂不替任何二维码节点作 A/B/C 判断，全部保持 D：无法确认，等待业务逐项确认。", ""])
    lines.extend(
        [
            "| node_id | 模板 | 周期 | 原始文件 | 原始步骤全文 | 二维码定位 | 候选规则 | 当前结论 |",
            "|---|---|---:|---|---|---|---|---|",
        ]
    )
    for row in payload["appendices"]["historical_qr_nodes"]:
        lines.append(
            "| "
            + " | ".join(
                (
                    _md(row.get("node_id")),
                    _md(row.get("cohort_months")),
                    _md(row.get("learning_cycle_index")),
                    _md(row.get("source_file")),
                    _md(row.get("context_text")),
                    _md(row.get("qr_location")),
                    _md(row.get("candidate_credit_rule_keys")),
                    _md(row.get("review_classification")),
                )
            )
            + "|"
        )
    lines.extend(
        [
            "",
            "## 业务确认后才能进入下一步",
            "",
            "本文件只保留尚未确认的模板项；已经由业务确认的流程已写入当前 mapping。剩余项目以后有明确资料时再补，本轮不开始 V1.3-C 小程序页面开发。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 V1.3-C0 小组学习会内容人工审核清单")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--base-commit", default=BASE_COMMIT)
    args = parser.parse_args()
    payload = build_review(root=args.root, base_commit=args.base_commit)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    args.output_markdown.write_bytes(render_markdown(payload).encode("utf-8"))
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
