from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = ROOT / "data/learning-plans"
sys.path.insert(0, str(ROOT / "scripts"))

from audit_group_meeting_class_projections import build_audit  # noqa: E402
from learning_plan_semantics import (  # noqa: E402
    cohort_month_from_open_year_month,
    learning_cycle_index_for_month,
)


def _audit() -> dict:
    return build_audit(
        baseline_path=DATA_ROOT / "group-meeting-class-projection-baseline-2026.1.json",
        plan_path=DATA_ROOT / "standard-3y-2026.json",
        mapping_path=DATA_ROOT / "cycle-flow-mapping-2026.1.json",
    )


def test_opening_month_is_template_and_cycle_is_month_offset() -> None:
    assert cohort_month_from_open_year_month("2026-04") == 4
    assert learning_cycle_index_for_month("2026-04", "2026-04") == 1
    assert learning_cycle_index_for_month("2026-04", "2026-05") == 2
    assert learning_cycle_index_for_month("2026-04", "2027-04") == 13


def test_class_projection_audits_four_in_scope_classes_and_two_exclusions() -> None:
    audit = _audit()
    assert audit["summary"] == {
        "class_count": 6,
        "in_scope_class_count": 4,
        "out_of_scope_class_count": 2,
        "projected_cycle_count": 144,
        "mapped_projected_cycle_count": 144,
        "mapping_conflict_projected_cycle_count": 0,
        "mapping_missing_projected_cycle_count": 0,
        "plan_cycle_missing_projected_cycle_count": 0,
        "projection_finding_count": 0,
        "plan_match_status_counts": {
            "MATCHED": 144,
        },
        "excluded_classes_have_no_findings": True,
        "template_months": [1, 4, 7, 10],
        "template_cycle_definition": "4个开班月份模板 × 36学习周期",
        "projection_types": {
            "planned": "PLANNED",
            "actual_current": "ACTUAL_CURRENT",
        },
        "actual_projection_status": "NOT_PROVIDED",
    }


def test_planned_projection_is_explicitly_not_actual_state() -> None:
    audit = _audit()
    july = next(item for item in audit["classes"] if item["class_name"] == "吴越一班")
    assert july["planned_projection"]["projection_type"] == "PLANNED"
    assert july["current_projection"] == {
        "projection_type": "ACTUAL_CURRENT",
        "class_name": "吴越一班",
        "status": "NOT_PROVIDED",
        "source": "class_learning_cycles (runtime state required)",
        "current_open_cycle": None,
        "planned_month": None,
        "actual_status": "NOT_LOADED",
        "schedule_override": None,
    }
    assert all(
        cycle["projection_type"] == "PLANNED"
        and cycle["planned_month"] == cycle["specified_date"]
        for cycle in july["cycle_projections"]
    )


def test_actual_current_projection_can_be_loaded_without_changing_planned_audit(
    tmp_path: Path,
) -> None:
    actual_state = tmp_path / "actual-state.json"
    actual_state.write_text(
        json.dumps(
            {
                "classes": {
                    "吴越一班": {
                        "current_open_cycle": 8,
                        "planned_month": "2027-03",
                        "actual_status": "OPEN",
                        "schedule_override": {
                            "planned_class_meeting_at": "2027-03-20T19:00:00+00:00",
                            "adjustment_reason": "春节期间暂停班会",
                        },
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    audit = build_audit(
        baseline_path=DATA_ROOT / "group-meeting-class-projection-baseline-2026.1.json",
        plan_path=DATA_ROOT / "standard-3y-2026.json",
        mapping_path=DATA_ROOT / "cycle-flow-mapping-2026.1.json",
        actual_state_path=actual_state,
    )
    july = next(item for item in audit["classes"] if item["class_name"] == "吴越一班")
    assert audit["summary"]["actual_projection_status"] == "LOADED"
    assert july["current_projection"]["current_open_cycle"] == 8
    assert july["current_projection"]["planned_month"] == "2027-03"
    assert july["current_projection"]["actual_status"] == "OPEN"
    assert july["cycle_projections"][7]["planned_month"] == "2027-02"
    assert july["cycle_projections"][35]["planned_month"] == "2029-06"


def test_july_baseline_projects_all_three_classes_from_cycle_one() -> None:
    audit = _audit()
    july_names = {"吴越一班", "圆融五班", "吴越三班"}
    july = [item for item in audit["classes"] if item["class_name"] in july_names]
    assert len(july) == 3
    for item in july:
        assert item["actual_open_year_month"] == "2026-07"
        assert item["cohort_month"] == 7
        assert item["template_label"] == "7月开班模板"
        checkpoints = {
            row["specified_date"]: row
            for row in item["checkpoints"]
        }
        assert checkpoints["2026-07"]["learning_cycle_index"] == 1
        assert checkpoints["2026-08"]["learning_cycle_index"] == 2
        assert checkpoints["2027-07"]["learning_cycle_index"] == 13
        assert checkpoints["2029-06"]["learning_cycle_index"] == 36
        assert checkpoints["2026-07"]["plan_match_status"] == "MATCHED"


def test_out_of_scope_classes_do_not_create_missing_configuration_findings() -> None:
    audit = _audit()
    excluded = {
        item["class_name"]: item
        for item in audit["classes"]
        if item["learning_plan_scope"] == "OUT_OF_SCOPE"
    }
    assert set(excluded) == {"神仙班", "先锋班"}
    assert all(item["plan_match_status"] == "OUT_OF_SCOPE" for item in excluded.values())
    assert all(item["cycle_projections"] == [] for item in excluded.values())
    assert all(item["findings"] == [] for item in excluded.values())


def test_declared_cohort_month_must_match_actual_opening_month(tmp_path: Path) -> None:
    baseline = json.loads(
        (DATA_ROOT / "group-meeting-class-projection-baseline-2026.1.json").read_text(
            encoding="utf-8"
        )
    )
    baseline["classes"][0]["cohort_month"] = 7
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="cohort_month"):
        build_audit(
            baseline_path=path,
            plan_path=DATA_ROOT / "standard-3y-2026.json",
            mapping_path=DATA_ROOT / "cycle-flow-mapping-2026.1.json",
        )
