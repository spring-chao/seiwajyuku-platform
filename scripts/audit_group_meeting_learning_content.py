"""Audit all 36 learning cycles and their group-meeting learning content.

This is a read-only audit of the checked-in plan and source-flow artifacts.
It intentionally reports unresolved mappings instead of inventing a course or
using a QR image as a substitute for learning semantics.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from learning_plan_semantics import (
    COHORT_TEMPLATE_MONTHS,
    cohort_template_label,
    learning_cycle_label,
)


DEFAULT_ROOT = Path("data/learning-plans")
DEFAULT_OUTPUT = DEFAULT_ROOT / "group-meeting-learning-content-audit-2026.1.json"


def _normalized(value: Any) -> str:
    return re.sub(r"[\s\"“”‘’'（）()【】《》、，,；;：:。．]+", "", str(value or "")).lower()


def _is_learning_task(task: dict[str, Any]) -> bool:
    task_type = task.get("task_type")
    text = f"{task.get('title') or ''} {task.get('description') or ''}"
    return task_type in {"ONLINE_COURSE", "OFFLINE_COURSE"} and (
        task.get("canonical_key") or "视频" in text or "课程" in text
    )


def _rule_key_for_task(task: dict[str, Any], rules: list[dict[str, Any]]) -> str | None:
    text = _normalized(
        f"{task.get('canonical_key') or ''} {task.get('title') or ''} {task.get('description') or ''}"
    )
    for rule in rules:
        aliases = [rule.get("course_name"), *(rule.get("aliases") or [])]
        if any(_normalized(alias) and _normalized(alias) in text for alias in aliases):
            return str(rule.get("course_key"))
    return None


def _content_payload(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_key": node.get("content_key"),
        "task_type": node.get("task_type"),
        "title": node.get("title"),
        "is_required": bool(node.get("is_required")),
        "has_qr": bool(node.get("qr_refs")),
        "credit_rule_key": node.get("credit_rule_key"),
        "credit_points": node.get("credit_points"),
        "source_step_no": node.get("source_step_no"),
    }


def build_audit(
    *, plan_path: Path, flows_path: Path, mapping_path: Path, rules_path: Path
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    flows_catalog = json.loads(flows_path.read_text(encoding="utf-8"))
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rules = json.loads(rules_path.read_text(encoding="utf-8")).get("rules", [])
    flows = {flow.get("flow_key"): flow for flow in flows_catalog.get("flows", [])}
    plan_tracks = {
        (int(track["cohort_month"]), int(cycle["cycle_index"])): cycle
        for track in plan.get("cohort_tracks", [])
        for cycle in track.get("cycles", [])
    }

    entries: list[dict[str, Any]] = []
    for item in mapping.get("mappings", []):
        cohort_month = int(item["cohort_month"])
        cycle_index = int(item["cycle_index"])
        if cohort_month not in COHORT_TEMPLATE_MONTHS:
            raise ValueError(f"非法开班月份模板: {cohort_month}")
        flow = flows.get(item.get("flow_key"))
        nodes = list(flow.get("learning_content_nodes", [])) if flow else []
        plan_cycle = plan_tracks.get((cohort_month, cycle_index), {})
        flow_rule_keys = {node.get("credit_rule_key") for node in nodes if node.get("credit_rule_key")}
        plan_tasks = [task for task in plan_cycle.get("tasks", []) if _is_learning_task(task)]
        planned_rules_not_in_flow = []
        for task in plan_tasks:
            rule_key = _rule_key_for_task(task, rules)
            if rule_key and rule_key not in flow_rule_keys:
                planned_rules_not_in_flow.append(
                    {"title": task.get("title"), "rule_key": rule_key}
                )
        findings: list[str] = []
        if item.get("status") != "MAPPED":
            findings.append(item.get("status"))
        if any(
            node.get("task_type") == "VIDEO_LEARNING"
            and node.get("is_required")
            and not node.get("qr_refs")
            for node in nodes
        ):
            findings.append("REQUIRED_VIDEO_WITHOUT_QR")
        if any(node.get("credit_rule_key") is None for node in nodes):
            findings.append("LEARNING_CONTENT_WITHOUT_CREDIT_RULE")
        if planned_rules_not_in_flow:
            findings.append("PLAN_RULE_NOT_IN_GROUP_CONTENT")
        entries.append(
            {
                "cohort_month": cohort_month,
                "template_key": item.get("template_key") or f"COHORT_MONTH_{cohort_month:02d}",
                "template_label": item.get("template_label") or cohort_template_label(cohort_month),
                "cycle_index": cycle_index,
                "learning_cycle_index": cycle_index,
                "learning_cycle_label": item.get("learning_cycle_label") or learning_cycle_label(cohort_month, cycle_index),
                "year_index": item.get("year_index"),
                "year_cycle_index": item.get("year_cycle_index"),
                "mapping_status": item.get("status"),
                "flow_key": item.get("flow_key"),
                "learning_contents": [_content_payload(node) for node in nodes],
                "learning_content_count": len(nodes),
                "required_video_without_qr_count": sum(
                    node.get("task_type") == "VIDEO_LEARNING"
                    and node.get("is_required")
                    and not node.get("qr_refs")
                    for node in nodes
                ),
                "learning_content_without_credit_rule_count": sum(
                    node.get("credit_rule_key") is None for node in nodes
                ),
                "planned_learning_task_count": len(plan_tasks),
                "planned_rules_not_in_group_content": planned_rules_not_in_flow,
                "findings": findings,
            }
        )

    source_nodes = [
        node
        for flow in flows.values()
        for node in flow.get("learning_content_nodes", [])
    ]
    qr_review_nodes = [
        node
        for flow in flows.values()
        for node in flow.get("course_nodes", [])
        if node.get("credit_status") == "QR_REVIEW_REQUIRED"
    ]
    configuration_finding_counts = Counter(
        finding
        for entry in entries
        for finding in entry["findings"]
    )
    return {
        "schema_version": 1,
        "plan_key": plan.get("plan_key"),
        "version_label": "2026.1",
        "status": "AUDIT_ONLY",
        "summary": {
            "cohort_track_count": len(plan.get("cohort_tracks", [])),
            "audit_model": "cohort_month_template_x_learning_cycle_index",
            "template_cycle_definition": "4个开班月份模板 × 36学习周期",
            "cycles_per_template": {
                str(track.get("cohort_month")): len(track.get("cycles", []))
                for track in plan.get("cohort_tracks", [])
            },
            "cycles_per_track": {
                str(track.get("cohort_month")): len(track.get("cycles", []))
                for track in plan.get("cohort_tracks", [])
            },
            "cycle_entry_count": len(entries),
            "source_flow_count": len(flows),
            "mapped_cycle_entry_count": sum(item["mapping_status"] == "MAPPED" for item in entries),
            "mapping_missing_cycle_entry_count": sum(item["mapping_status"] == "MAPPING_MISSING" for item in entries),
            "mapping_conflict_cycle_entry_count": sum(item["mapping_status"] == "MAPPING_CONFLICT" for item in entries),
            "source_learning_content_count": len(source_nodes),
            "source_video_learning_count": sum(node.get("task_type") == "VIDEO_LEARNING" for node in source_nodes),
            "source_required_video_without_qr_count": sum(
                node.get("task_type") == "VIDEO_LEARNING"
                and node.get("is_required")
                and not node.get("qr_refs")
                for node in source_nodes
            ),
            "cycle_entry_required_video_without_qr_count": sum(
                item["required_video_without_qr_count"] for item in entries
            ),
            "source_learning_content_without_credit_rule_count": sum(
                node.get("credit_rule_key") is None for node in source_nodes
            ),
            "source_qr_without_credit_rule_count": len(qr_review_nodes),
            "cycle_entry_with_configuration_findings": sum(bool(item["findings"]) for item in entries),
            "configuration_finding_counts": dict(sorted(configuration_finding_counts.items())),
        },
        "findings": {
            "required_video_without_qr": [
                {
                    "cohort_month": flow.get("eligible_cohort_months"),
                    "template_label": [
                        cohort_template_label(month)
                        for month in (flow.get("eligible_cohort_months") or [])
                        if month in COHORT_TEMPLATE_MONTHS
                    ],
                    "cycle_index": flow.get("cycle_index"),
                    "learning_cycle_index": flow.get("cycle_index"),
                    "learning_cycle_label": [
                        learning_cycle_label(month, int(flow.get("cycle_index") or 0))
                        for month in (flow.get("eligible_cohort_months") or [])
                        if month in COHORT_TEMPLATE_MONTHS and flow.get("cycle_index")
                    ],
                    "year_index": flow.get("year_index"),
                    "cohort_months": flow.get("eligible_cohort_months"),
                    "title": node.get("title"),
                    "content_key": node.get("content_key"),
                }
                for flow in flows.values()
                for node in flow.get("learning_content_nodes", [])
                if node.get("task_type") == "VIDEO_LEARNING"
                and node.get("is_required")
                and not node.get("qr_refs")
            ],
            "learning_content_without_credit_rule": [
                {
                    "cohort_month": flow.get("eligible_cohort_months"),
                    "template_label": [
                        cohort_template_label(month)
                        for month in (flow.get("eligible_cohort_months") or [])
                        if month in COHORT_TEMPLATE_MONTHS
                    ],
                    "cycle_index": flow.get("cycle_index"),
                    "learning_cycle_index": flow.get("cycle_index"),
                    "learning_cycle_label": [
                        learning_cycle_label(month, int(flow.get("cycle_index") or 0))
                        for month in (flow.get("eligible_cohort_months") or [])
                        if month in COHORT_TEMPLATE_MONTHS and flow.get("cycle_index")
                    ],
                    "year_index": flow.get("year_index"),
                    "cohort_months": flow.get("eligible_cohort_months"),
                    "title": node.get("title"),
                    "content_key": node.get("content_key"),
                }
                for flow in flows.values()
                for node in flow.get("learning_content_nodes", [])
                if node.get("credit_rule_key") is None
            ],
            "qr_without_credit_rule": [
                {
                    "cohort_month": flow.get("eligible_cohort_months"),
                    "template_label": [
                        cohort_template_label(month)
                        for month in (flow.get("eligible_cohort_months") or [])
                        if month in COHORT_TEMPLATE_MONTHS
                    ],
                    "cycle_index": flow.get("cycle_index"),
                    "learning_cycle_index": flow.get("cycle_index"),
                    "learning_cycle_label": [
                        learning_cycle_label(month, int(flow.get("cycle_index") or 0))
                        for month in (flow.get("eligible_cohort_months") or [])
                        if month in COHORT_TEMPLATE_MONTHS and flow.get("cycle_index")
                    ],
                    "year_index": flow.get("year_index"),
                    "cohort_months": flow.get("eligible_cohort_months"),
                    "context_step_no": node.get("context_step_no"),
                }
                for flow in flows.values()
                for node in flow.get("course_nodes", [])
                if node.get("credit_status") == "QR_REVIEW_REQUIRED"
            ],
        },
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.root
    payload = build_audit(
        plan_path=root / "standard-3y-2026.json",
        flows_path=root / "group-meeting-flows-2026.1.json",
        mapping_path=root / "cycle-flow-mapping-2026.1.json",
        rules_path=root / "course-credit-rules-2026.json",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
