from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = ROOT / "data" / "learning-plans"
sys.path.insert(0, str(ROOT / "scripts"))

from build_group_meeting_c0_manual_review import (  # noqa: E402
    build_review,
    render_markdown,
)


def _review() -> dict:
    return build_review(root=DATA_ROOT)


def test_c0_uses_real_class_projection_as_the_4_plus_11_baseline() -> None:
    review = _review()
    summary = review["summary"]

    assert summary["canonical_item_count"] == 15
    assert summary["mapping_conflict_count"] == 4
    assert summary["mapping_missing_count"] == 11
    assert summary["mapped_count"] == 129
    assert summary["safe_auto_fix_count"] == 0
    assert summary["canonical_review_required_count"] == 15
    assert summary["priority_counts"] == {"P0": 0, "P1": 15, "P2": 8}
    assert summary["template_only_supplemental_count"] == 8
    assert summary["actual_projection_status"] == "NOT_PROVIDED"
    assert len(review["items"]) == 15
    assert len(review["template_only_supplemental_items"]) == 8


def test_c0_keeps_template_only_exceptions_visible_without_changing_main_scope() -> None:
    review = _review()
    main_keys = {
        (item["cohort_month"], item["learning_cycle_index"])
        for item in review["items"]
    }
    assert main_keys == {(4, 26), (4, 28), (4, 29), (7, 26), (7, 32), (7, 33), (7, 34)}
    supplemental_keys = {
        (item["cohort_month"], item["learning_cycle_index"])
        for item in review["template_only_supplemental_items"]
    }
    assert supplemental_keys == {
        (1, 26), (1, 28), (1, 29), (10, 25), (10, 26), (10, 30), (10, 31), (10, 32)
    }
    assert all(item["priority"] == "P2" for item in review["template_only_supplemental_items"])
    assert all(item["must_be_manually_confirmed"] for item in review["items"])


def test_c0_preserves_cycle_five_no_qr_video_and_does_not_assign_new_credit() -> None:
    review = _review()
    rows = [
        row
        for row in review["appendices"]["required_video_without_qr"]
        if row["learning_cycle_index"] == 5
        and row["title"] == "关于核算表分析&任务单的制作"
    ]
    assert len(rows) == 4
    assert all(row["required"] is True for row in rows)
    assert all(row["qr_refs"] == [] for row in rows)
    assert all(row["credit_rule_key"] == "Y1-ACCOUNTING-ANALYSIS-TASK" for row in rows)
    assert all(row["credit_points"] == 40 for row in rows)
    assert all(row["review_status"] == "REVIEW_REQUIRED" for row in rows)


def test_c0_historical_qr_nodes_remain_unconfirmed_and_cannot_create_credit() -> None:
    review = _review()
    nodes = review["appendices"]["historical_qr_nodes"]
    assert len(nodes) == 15
    assert all(node["review_classification"].startswith("D.") for node in nodes)
    assert all(node["credit_rule_key"] is None for node in nodes)
    assert all(node["credit_points"] is None for node in nodes)
    assert all(node["review_status"] == "REVIEW_REQUIRED" for node in nodes)


def test_c0_markdown_contains_business_confirmation_and_b2_invariant() -> None:
    markdown = render_markdown(_review())
    assert "4 个冲突 + 11 个缺失 = 15 个审核项" in markdown
    assert "最终标准内容名称" in markdown
    assert "是否属于本周期小组学习会" in markdown
    assert "二维码定位" in markdown
    assert "班会顺延只能改变计划/实际时间" in markdown
    assert markdown.count("是否必须人工确认") == 15
