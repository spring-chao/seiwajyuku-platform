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
        "mapped_projected_cycle_count": 129,
        "mapping_conflict_projected_cycle_count": 4,
        "mapping_missing_projected_cycle_count": 11,
        "plan_cycle_missing_projected_cycle_count": 0,
        "projection_finding_count": 15,
        "plan_match_status_counts": {
            "MATCHED": 129,
            "MAPPING_CONFLICT": 4,
            "MAPPING_MISSING": 11,
        },
        "excluded_classes_have_no_findings": True,
        "template_months": [1, 4, 7, 10],
        "template_cycle_definition": "4个开班月份模板 × 36学习周期",
    }


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
