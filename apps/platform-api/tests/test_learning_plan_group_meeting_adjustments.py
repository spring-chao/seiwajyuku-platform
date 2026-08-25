from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


SCRIPTS_ROOT = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from apply_learning_plan_group_meeting_adjustments import (  # noqa: E402
    build_candidate_plan,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
BASE_JSON = REPO_ROOT / "data" / "learning-plans" / "standard-3y-2026.json"
REVIEW_JSON = REPO_ROOT / "data" / "learning-plans" / "standard-3y-2026.review.json"


def _fixtures() -> tuple[dict, dict, str]:
    base = json.loads(BASE_JSON.read_text(encoding="utf-8"))
    review = json.loads(REVIEW_JSON.read_text(encoding="utf-8"))
    for track in base["cohort_tracks"]:
        for cycle in track["cycles"]:
            for index, task in enumerate(cycle["tasks"], start=1):
                if task["task_type"] == "GROUP_MEETING":
                    task_key = f"{track['cohort_month']}-{cycle['cycle_index']}-{index}"
                    adjustment = {
                        "adjustment_schema_version": 1,
                        "plan_key": "standard-3y",
                        "version_label": "2026",
                        "scope": "GROUP_MEETING",
                        "status": "DRAFT",
                        "base_source_commit": review["source_commit"],
                        "base_source_json": BASE_JSON.name,
                        "base_source_json_sha256": review["source_json_sha256"],
                        "base_source_workbooks": {
                            f"year{year}": review["source_workbooks"][str(year)]
                            for year in (1, 2, 3)
                        },
                        "credit_policy_snapshot": {
                            "mode": "CYCLE_ATTENDANCE_ONCE",
                            "credit_points_per_person": 4,
                            "task_level_credit_points": None,
                            "task_level_credit_editable": False,
                        },
                        "candidate_plan": {
                            "version_label": "2026.1",
                            "status": "DRAFT",
                            "overwrite_confirmed": False,
                            "requires_new_review_manifest": True,
                            "requires_source_fingerprint_refresh": True,
                        },
                        "changes": [
                            {
                                "task_key": task_key,
                                "title": "调整后的小组学习会",
                                "description": "调整后的流程内容",
                                "is_required": False,
                                "notes": "业务调整说明",
                            }
                        ],
                    }
                    return base, review, json.dumps(adjustment, ensure_ascii=False)
    raise AssertionError("fixture has no GROUP_MEETING task")


def test_build_candidate_plan_keeps_confirmed_base_immutable() -> None:
    base, review, adjustment_text = _fixtures()
    adjustments = json.loads(adjustment_text)
    original = copy.deepcopy(base)

    candidate = build_candidate_plan(
        base,
        adjustments,
        base_json=BASE_JSON,
        review_manifest=review,
    )

    assert base == original
    assert candidate["version_label"] == "2026.1"
    assert candidate["status"] == "DRAFT"
    assert candidate["source"]["adjustment_lineage"]["overwrite_confirmed"] is False
    assert candidate["source"]["adjustment_lineage"]["change_count"] == 1
    assert candidate["quality_report"]["cycle_count"] == 144


def test_candidate_accepts_git_lf_checkout_of_windows_confirmed_json(tmp_path: Path) -> None:
    base, review, adjustment_text = _fixtures()
    lf_base = tmp_path / BASE_JSON.name
    lf_base.write_bytes(BASE_JSON.read_bytes().replace(b"\r\n", b"\n"))

    candidate = build_candidate_plan(
        base,
        json.loads(adjustment_text),
        base_json=lf_base,
        review_manifest=review,
    )

    assert candidate["version_label"] == "2026.1"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(base_source_json_sha256="bad"), "JSON SHA-256"),
        (lambda data: data["changes"][0].update(credit_points=4), "credit_points"),
        (lambda data: data["changes"][0].update(task_key="1-1-999"), "任务序号"),
    ],
)
def test_adjustment_gate_rejects_unbound_or_task_credit_changes(mutation, message: str) -> None:
    base, review, adjustment_text = _fixtures()
    adjustments = json.loads(adjustment_text)
    mutation(adjustments)

    with pytest.raises(ValueError, match=message):
        build_candidate_plan(
            base,
            adjustments,
            base_json=BASE_JSON,
            review_manifest=review,
        )
