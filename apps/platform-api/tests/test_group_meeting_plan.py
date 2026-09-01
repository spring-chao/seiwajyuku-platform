from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / "data/learning-plans/standard-3y-2026.json"
DATA_ROOT = ROOT / "data/learning-plans"
sys.path.insert(0, str(ROOT / "scripts"))

from audit_group_meeting_learning_content import build_audit  # noqa: E402
from app.services.group_meeting_plan import (  # noqa: E402
    GroupMeetingPlanConfigError,
    build_group_meeting_plan,
)


def _cycle(cohort_month: int, cycle_index: int) -> dict:
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    track = next(item for item in payload["cohort_tracks"] if item["cohort_month"] == cohort_month)
    cycle = next(item for item in track["cycles"] if item["cycle_index"] == cycle_index)
    return {"id": 9000 + cycle_index, "cohort_month": cohort_month, **cycle}


def test_cycle_five_returns_required_video_without_qr() -> None:
    meeting_plan = build_group_meeting_plan(
        plan_cycle=_cycle(cohort_month=4, cycle_index=5), cohort_month=4
    )

    assert meeting_plan["configuration_status"] == "CONFIGURED"
    assert meeting_plan["plan_cycle_id"] == 9005
    assert meeting_plan["cohort_month"] == 4
    assert meeting_plan["cohort_template_label"] == "4月开班模板"
    assert meeting_plan["learning_cycle_index"] == 5
    assert meeting_plan["learning_cycle_label"] == "4月开班模板 · 第5学习周期"
    assert meeting_plan["cycle_index"] == 5
    assert meeting_plan["year_index"] == 1
    assert meeting_plan["learning_contents"] == [
        {
            "content_key": "Y1-C05-STEP-04-CONTENT-01",
            "task_type": "VIDEO_LEARNING",
            "title": "关于核算表分析&任务单的制作",
            "description": "观看王寅清老师《关于核算表分析&任务单的制作》视频，并现场研讨；",
            "required": True,
            "sort_order": 401,
            "credit_rule_key": "Y1-ACCOUNTING-ANALYSIS-TASK",
            "credit_points": 40,
            "verification_mode": "MEETING_CONFIRM",
            "content_access": {"type": "NONE", "label": None},
            "plan_match_status": "MATCHED",
        }
    ]
    step = next(item for item in meeting_plan["steps"] if item["step_no"] == 4)
    assert step["learning_content_keys"] == ["Y1-C05-STEP-04-CONTENT-01"]


def test_business_confirmed_screenshot_flow_has_steps_but_no_video_course() -> None:
    meeting_plan = build_group_meeting_plan(
        plan_cycle=_cycle(cohort_month=4, cycle_index=28), cohort_month=4
    )

    assert meeting_plan["configuration_status"] == "CONFIGURED"
    assert meeting_plan["learning_contents"] == []
    assert [step["content"] for step in meeting_plan["steps"]] == [
        "经营分析会实操观摩；",
        "企业参访、企业经营者分享；",
        "近期读书打卡分享情况；",
        "上月班级学习会课后作业的检视、分享、辅导；",
        "人财培养体系研讨；（如线上研讨，小组学习会可改为半天）",
        "三大委落地经验和案例分享编写辅导（如线上研讨，小组会可改为半天）",
        "近期重点工作沟通交流；",
        "空巴。",
    ]


@pytest.mark.parametrize("cohort_month, cycle_index", [(10, 25), (10, 30)])
def test_ambiguous_or_missing_flow_fails_closed(cohort_month: int, cycle_index: int) -> None:
    with pytest.raises(GroupMeetingPlanConfigError, match="小组学习会"):
        build_group_meeting_plan(
            plan_cycle=_cycle(cohort_month=cohort_month, cycle_index=cycle_index),
            cohort_month=cohort_month,
        )


def test_plan_cycle_cohort_mismatch_fails_closed() -> None:
    with pytest.raises(GroupMeetingPlanConfigError, match="开班批次"):
        build_group_meeting_plan(
            plan_cycle=_cycle(cohort_month=4, cycle_index=5), cohort_month=7
        )


def test_runtime_cycle_mismatch_fails_closed() -> None:
    with pytest.raises(GroupMeetingPlanConfigError, match="学习计划周期不一致"):
        build_group_meeting_plan(
            plan_cycle=_cycle(cohort_month=4, cycle_index=5),
            cohort_month=4,
            learning_cycle_index=6,
        )


def test_36_cycle_audit_reports_missing_qr_as_a_finding() -> None:
    audit = build_audit(
        plan_path=DATA_ROOT / "standard-3y-2026.json",
        flows_path=DATA_ROOT / "group-meeting-flows-2026.1.json",
        mapping_path=DATA_ROOT / "cycle-flow-mapping-2026.1.json",
        rules_path=DATA_ROOT / "course-credit-rules-2026.json",
    )

    assert audit["summary"]["cycle_entry_count"] == 144
    assert audit["summary"]["cycles_per_track"] == {"1": 36, "4": 36, "7": 36, "10": 36}
    assert audit["summary"]["source_required_video_without_qr_count"] >= 1
    assert audit["summary"]["configuration_finding_counts"]["MAPPING_CONFLICT"] == 2
    assert audit["summary"]["configuration_finding_counts"]["MAPPING_MISSING"] == 3
    assert any(
        item["cycle_index"] == 5
        and item["title"] == "关于核算表分析&任务单的制作"
        for item in audit["findings"]["required_video_without_qr"]
    )
