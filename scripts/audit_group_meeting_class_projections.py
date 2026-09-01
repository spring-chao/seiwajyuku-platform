"""Audit planned and runtime class-instance projections.

This is a read-only audit artifact.  The input records are an explicitly
confirmed scope baseline, not a runtime class-name rule and not a production
database export.  An in-scope class is projected from its actual opening
year-month for the planned projection; an out-of-scope class has no 36-cycle
group-meeting requirement.

The planned projection is deliberately not an actual-cycle result.  Runtime
actual/current state can be supplied separately from ``class_learning_cycles``
with ``--actual-state``; without that optional input the artifact reports
``NOT_PROVIDED`` instead of inventing production state.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from learning_plan_semantics import (
    COHORT_TEMPLATE_MONTHS,
    add_months,
    cohort_month_from_open_year_month,
    cohort_template_label,
    learning_cycle_index_for_month,
    learning_cycle_label,
    normalize_year_month,
    year_cycle_index_for_learning_cycle,
    year_index_for_learning_cycle,
)


DEFAULT_ROOT = Path("data/learning-plans")
DEFAULT_BASELINE = DEFAULT_ROOT / "group-meeting-class-projection-baseline-2026.1.json"
DEFAULT_OUTPUT = DEFAULT_ROOT / "group-meeting-class-projection-audit-2026.1.json"
IN_SCOPE = "GROUP_MEETING_36_CYCLES"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
DEFAULT_CHECKPOINT_OFFSETS = (0, 1, 12, 35)
PLANNED_PROJECTION = "PLANNED"
ACTUAL_CURRENT_PROJECTION = "ACTUAL_CURRENT"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _plan_index(plan: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    return {
        (int(track["cohort_month"]), int(cycle["cycle_index"])): cycle
        for track in plan.get("cohort_tracks", [])
        for cycle in track.get("cycles", [])
    }


def _mapping_index(mapping: dict[str, Any]) -> dict[tuple[int, int, int], dict[str, Any]]:
    return {
        (
            int(item["cohort_month"]),
            int(item.get("learning_cycle_index") or item.get("cycle_index") or 0),
            int(item["year_index"]),
        ): item
        for item in mapping.get("mappings", [])
    }


def _plan_match_status(
    plan_cycle: dict[str, Any] | None,
    mapping: dict[str, Any] | None,
) -> str:
    if plan_cycle is None:
        return "PLAN_CYCLE_MISSING"
    if mapping is None or mapping.get("status") == "MAPPING_MISSING":
        return "MAPPING_MISSING"
    if mapping.get("status") == "MAPPING_CONFLICT":
        return "MAPPING_CONFLICT"
    if mapping.get("status") != "MAPPED" or not mapping.get("flow_key"):
        return "NOT_MATCHED"
    return "MATCHED"


def _cycle_projection(
    *,
    class_name: str,
    actual_open_year_month: str,
    cohort_month: int,
    learning_cycle_index: int,
    plan_cycle: dict[str, Any] | None,
    mapping: dict[str, Any] | None,
) -> dict[str, Any]:
    year_index = year_index_for_learning_cycle(learning_cycle_index)
    mapping_status = mapping.get("status") if mapping else "MAPPING_MISSING"
    plan_match_status = _plan_match_status(plan_cycle, mapping)
    return {
        "projection_type": PLANNED_PROJECTION,
        "class_name": class_name,
        "actual_open_year_month": actual_open_year_month,
        "cohort_month": cohort_month,
        "template_key": f"COHORT_MONTH_{cohort_month:02d}",
        "template_label": cohort_template_label(cohort_month),
        "specified_date": add_months(actual_open_year_month, learning_cycle_index - 1),
        "planned_month": add_months(actual_open_year_month, learning_cycle_index - 1),
        "learning_cycle_index": learning_cycle_index,
        "year_index": year_index,
        "year_cycle_index": year_cycle_index_for_learning_cycle(learning_cycle_index),
        "learning_cycle_label": learning_cycle_label(cohort_month, learning_cycle_index),
        "plan_cycle_id": plan_cycle.get("id") if plan_cycle else None,
        "flow_key": mapping.get("flow_key") if mapping else None,
        "flow_mapping_status": mapping_status,
        "plan_match_status": plan_match_status,
        "findings": [] if plan_match_status == "MATCHED" else [plan_match_status],
    }


def _current_projection(
    *, row: dict[str, Any], actual_states: dict[str, Any]
) -> dict[str, Any]:
    """Return runtime state when explicitly supplied, otherwise a clear gap."""

    class_name = str(row.get("class_name") or "").strip()
    class_id = row.get("class_org_unit_id")
    if str(row.get("learning_plan_scope") or "").strip() == OUT_OF_SCOPE:
        return {
            "projection_type": ACTUAL_CURRENT_PROJECTION,
            "class_name": class_name,
            "status": "NOT_APPLICABLE",
            "source": "learning_plan_scope",
            "current_open_cycle": None,
            "planned_month": None,
            "actual_status": "NOT_APPLICABLE",
            "schedule_override": None,
        }

    state = actual_states.get(str(class_id)) if class_id else None
    if state is None:
        state = actual_states.get(class_name)
    if not isinstance(state, dict):
        state = None
    return {
        "projection_type": ACTUAL_CURRENT_PROJECTION,
        "class_name": class_name,
        "status": "LOADED" if state else "NOT_PROVIDED",
        "source": (
            state.get("source", "class_learning_cycles")
            if state
            else "class_learning_cycles (runtime state required)"
        ),
        "current_open_cycle": state.get("current_open_cycle") if state else None,
        "planned_month": state.get("planned_month") if state else None,
        "actual_status": state.get("actual_status", "NOT_LOADED") if state else "NOT_LOADED",
        "schedule_override": state.get("schedule_override") if state else None,
    }


def _checkpoint_offsets(row: dict[str, Any], duration_cycles: int) -> list[int]:
    specified_dates = row.get("specified_dates")
    if specified_dates:
        return [
            learning_cycle_index_for_month(row["actual_open_year_month"], date) - 1
            for date in specified_dates
        ]
    return [offset for offset in DEFAULT_CHECKPOINT_OFFSETS if offset < duration_cycles]


def _project_class(
    *,
    row: dict[str, Any],
    plan_cycles: dict[tuple[int, int], dict[str, Any]],
    mappings: dict[tuple[int, int, int], dict[str, Any]],
    duration_cycles: int,
    actual_states: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    class_name = str(row.get("class_name") or "").strip()
    if not class_name:
        raise ValueError("班级投影基线缺少 class_name")
    scope = str(row.get("learning_plan_scope") or "").strip()
    if scope == OUT_OF_SCOPE:
        return (
            {
                "class_name": class_name,
                "class_org_unit_id": row.get("class_org_unit_id"),
                "learning_plan_scope": OUT_OF_SCOPE,
                "actual_open_year_month": row.get("actual_open_year_month"),
                "cohort_month": None,
                "template_label": None,
                "learning_cycle_count": 0,
                "plan_match_status": OUT_OF_SCOPE,
                "exclusion_reason": row.get("exclusion_reason"),
                "cycle_projections": [],
                "checkpoints": [],
                "planned_projection": {
                    "projection_type": PLANNED_PROJECTION,
                    "cycle_count": 0,
                    "checkpoint_cycle_indexes": [],
                },
                "current_projection": _current_projection(
                    row=row, actual_states=actual_states
                ),
                "findings": [],
            },
            [],
        )
    if scope != IN_SCOPE:
        raise ValueError(f"{class_name} 使用了未知学习计划范围: {scope}")

    actual_open_year_month = normalize_year_month(row.get("actual_open_year_month"))
    cohort_month = cohort_month_from_open_year_month(actual_open_year_month)
    if cohort_month not in COHORT_TEMPLATE_MONTHS:
        raise ValueError(
            f"{class_name} 的实际开班月份 {cohort_month} 不属于 1、4、7、10 月模板"
        )
    declared_cohort = row.get("cohort_month")
    if declared_cohort is not None and int(declared_cohort) != cohort_month:
        raise ValueError(
            f"{class_name} 的 cohort_month 与实际开班年月不一致"
        )

    cycles: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for learning_cycle_index in range(1, duration_cycles + 1):
        year_index = year_index_for_learning_cycle(learning_cycle_index)
        plan_cycle = plan_cycles.get((cohort_month, learning_cycle_index))
        mapping = mappings.get((cohort_month, learning_cycle_index, year_index))
        projection = _cycle_projection(
            class_name=class_name,
            actual_open_year_month=actual_open_year_month,
            cohort_month=cohort_month,
            learning_cycle_index=learning_cycle_index,
            plan_cycle=plan_cycle,
            mapping=mapping,
        )
        cycles.append(projection)
        if projection["findings"]:
            findings.append(projection)

    checkpoints: list[dict[str, Any]] = []
    for offset in _checkpoint_offsets(row, duration_cycles):
        if not 0 <= offset < duration_cycles:
            raise ValueError(f"{class_name} 的指定投影日期超出 36 学习周期")
        checkpoints.append(cycles[offset])

    return (
        {
            "class_name": class_name,
            "class_org_unit_id": row.get("class_org_unit_id"),
            "learning_plan_scope": IN_SCOPE,
            "actual_open_year_month": actual_open_year_month,
            "cohort_month": cohort_month,
            "template_label": cohort_template_label(cohort_month),
            "learning_cycle_count": duration_cycles,
            "plan_match_status": "MATCHED" if not findings else "REVIEW_REQUIRED",
            "cycle_projections": cycles,
            "checkpoints": checkpoints,
            "planned_projection": {
                "projection_type": PLANNED_PROJECTION,
                "basis": "actual_open_year_month + planned month offset; not runtime cycle clock",
                "cycle_count": duration_cycles,
                "checkpoint_cycle_indexes": [
                    item["learning_cycle_index"] for item in checkpoints
                ],
            },
            "current_projection": _current_projection(
                row=row, actual_states=actual_states
            ),
            "findings": [
                {
                    "specified_date": item["specified_date"],
                    "learning_cycle_index": item["learning_cycle_index"],
                    "plan_match_status": item["plan_match_status"],
                    "flow_mapping_status": item["flow_mapping_status"],
                }
                for item in findings
            ],
        },
        findings,
    )


def build_audit(
    *,
    baseline_path: Path,
    plan_path: Path,
    mapping_path: Path,
    actual_state_path: Path | None = None,
) -> dict[str, Any]:
    baseline = _read(baseline_path)
    plan = _read(plan_path)
    mapping = _read(mapping_path)
    actual_states: dict[str, Any] = {}
    if actual_state_path is not None:
        actual_payload = _read(actual_state_path)
        if not isinstance(actual_payload, dict):
            raise ValueError("实际周期状态必须是 JSON 对象")
        actual_states = actual_payload.get("classes", actual_payload)
        if not isinstance(actual_states, dict):
            raise ValueError("实际周期状态必须是 classes 对象映射")
    duration_cycles = int(plan.get("duration_cycles") or 36)
    if duration_cycles != 36:
        raise ValueError("班级投影审计要求学习计划为 36 个周期")

    plan_cycles = _plan_index(plan)
    mappings = _mapping_index(mapping)
    classes = baseline.get("classes") or []
    seen_names: set[str] = set()
    projections: list[dict[str, Any]] = []
    projection_findings: list[dict[str, Any]] = []
    for row in classes:
        class_name = str(row.get("class_name") or "").strip()
        if class_name in seen_names:
            raise ValueError(f"班级投影基线重复: {class_name}")
        seen_names.add(class_name)
        projection, findings = _project_class(
            row=row,
            plan_cycles=plan_cycles,
            mappings=mappings,
            duration_cycles=duration_cycles,
            actual_states=actual_states,
        )
        projections.append(projection)
        projection_findings.extend(findings)

    in_scope = [item for item in projections if item["learning_plan_scope"] == IN_SCOPE]
    out_of_scope = [item for item in projections if item["learning_plan_scope"] == OUT_OF_SCOPE]
    all_projected_cycles = [
        cycle
        for item in in_scope
        for cycle in item["cycle_projections"]
    ]
    status_counts = Counter(item["plan_match_status"] for item in all_projected_cycles)
    return {
        "schema_version": 2,
        "plan_key": plan.get("plan_key"),
        "version_label": "2026.1",
        "status": "AUDIT_ONLY",
        "projection_model": (
            "PLANNED: class_open_year_month -> learning_cycle_index -> "
            "cohort_month_template -> group_meeting_flow; "
            "ACTUAL_CURRENT: class_learning_cycles runtime state"
        ),
        "scope_source": baseline.get("scope_source"),
        "summary": {
            "class_count": len(projections),
            "in_scope_class_count": len(in_scope),
            "out_of_scope_class_count": len(out_of_scope),
            "projected_cycle_count": len(all_projected_cycles),
            "mapped_projected_cycle_count": status_counts.get("MATCHED", 0),
            "mapping_conflict_projected_cycle_count": status_counts.get("MAPPING_CONFLICT", 0),
            "mapping_missing_projected_cycle_count": status_counts.get("MAPPING_MISSING", 0),
            "plan_cycle_missing_projected_cycle_count": status_counts.get("PLAN_CYCLE_MISSING", 0),
            "projection_finding_count": len(projection_findings),
            "plan_match_status_counts": dict(sorted(status_counts.items())),
            "excluded_classes_have_no_findings": all(
                not item["findings"] for item in out_of_scope
            ),
            "template_months": sorted(COHORT_TEMPLATE_MONTHS),
            "template_cycle_definition": "4个开班月份模板 × 36学习周期",
            "projection_types": {
                "planned": PLANNED_PROJECTION,
                "actual_current": ACTUAL_CURRENT_PROJECTION,
            },
            "actual_projection_status": "LOADED" if actual_state_path else "NOT_PROVIDED",
        },
        "classes": projections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--actual-state",
        type=Path,
        default=None,
        help="可选的 class_learning_cycles 当前状态 JSON；不提供时不虚构实际投影",
    )
    args = parser.parse_args()
    root = args.root
    baseline_path = args.baseline or root / DEFAULT_BASELINE.name
    output_path = args.output or root / DEFAULT_OUTPUT.name
    payload = build_audit(
        baseline_path=baseline_path,
        plan_path=root / "standard-3y-2026.json",
        mapping_path=root / "cycle-flow-mapping-2026.1.json",
        actual_state_path=args.actual_state,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
