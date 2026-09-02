from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.learning_plan_baseline import (
    actual_snapshot,
    baseline_by_class_id,
    compare_expectation,
    load_baseline,
)
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.audit_learning_plan_baseline import build_report


BASELINE_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "learning-plans"
    / "class-learning-plan-migration-baseline-2026-09.json"
)


def test_v3_baseline_keeps_unresolved_ids_explicit() -> None:
    baseline = load_baseline(BASELINE_PATH)
    assert baseline["status"] == "DRY_RUN_ONLY"
    assert len(baseline["classes"]) == 29
    indexed = baseline_by_class_id(baseline)
    assert indexed["f03fd28f-53f3-4e51-84bd-decb7bbb5c3b"]["expected_current_cycle"] == 9
    assert indexed["cbac5025-93a8-4e4e-a14a-91af9c697f99"]["meeting_status"] == "POSTPONED"
    assert indexed["cbac5025-93a8-4e4e-a14a-91af9c697f99"]["group_meeting_policy"] == "REQUIRED"
    assert indexed["org-yanwu-1"]["migration_status"] == "MANUAL_REVIEW_REQUIRED"
    assert any(
        item["class_name"] == "不一班" and not item["class_org_unit_id"]
        for item in baseline["classes"]
    )


def test_baseline_comparison_emits_cycle_template_plan_and_status_differences() -> None:
    expected = {
        "class_name": "测试班",
        "expected_plan_version": "2026",
        "expected_cohort_month": 4,
        "expected_current_cycle": 5,
        "meeting_status": "POSTPONED",
        "group_meeting_policy": "REQUIRED",
        "expected_runtime_status": "POSTPONED",
        "migration_status": "READY_FOR_DRY_RUN",
    }
    actual = actual_snapshot(
        binding={"version_label": "2025", "cohort_month": 7},
        current_cycle={
            "learning_cycle_index": 4,
            "class_meeting_status": "PLANNED",
            "group_meeting_policy": "SUSPENDED",
        },
        runtime_status="NORMAL",
    )
    mismatches = compare_expectation(expected, actual)
    assert {item["issue_type"] for item in mismatches} == {
        "EXPECTED_CYCLE_MISMATCH",
        "EXPECTED_TEMPLATE_MISMATCH",
        "EXPECTED_PLAN_VERSION_MISMATCH",
        "EXPECTED_STATUS_MISMATCH",
    }


def test_manual_review_baseline_never_becomes_an_auto_fix() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    expected = next(item for item in baseline["classes"] if item["class_name"] == "真干三班")
    actual = actual_snapshot(
        binding={"version_label": "2026", "cohort_month": 7},
        current_cycle={"learning_cycle_index": 4},
        runtime_status="NORMAL",
    )
    assert expected["migration_status"] == "MANUAL_REVIEW_REQUIRED"
    assert compare_expectation(expected, actual) == []


def test_dry_run_report_has_no_write_result() -> None:
    baseline = {
        "schema_version": 1,
        "status": "DRY_RUN_ONLY",
        "classes": [
            {
                "class_name": "测试班",
                "class_org_unit_id": "class-1",
                "expected_plan_version": "2026",
                "expected_cohort_month": 4,
                "expected_current_cycle": 5,
                "meeting_status": "PLANNED",
                "group_meeting_policy": None,
                "expected_runtime_status": "NORMAL",
                "migration_status": "READY_FOR_DRY_RUN",
            }
        ],
    }
    report = build_report(
        baseline,
        {
            "generated_at": "2026-09-03T00:00:00+00:00",
            "classes": [
                {
                    "class_org_unit_id": "class-1",
                    "class_name": "测试班",
                    "binding": {"version_label": "2025", "cohort_month": 7},
                    "current_cycle": {
                        "learning_cycle_index": 4,
                        "class_meeting_status": "PLANNED",
                        "group_meeting_policy": "REQUIRED",
                    },
                    "runtime_status": "NORMAL",
                }
            ],
        },
    )
    assert report["dry_run"] is True
    assert report["write_performed"] is False
    assert report["summary"]["action_counts"] == {"CORRECTION": 1}
