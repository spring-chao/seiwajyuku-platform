from __future__ import annotations

import base64
import io
import hashlib
import json
import sys
from pathlib import Path

import pytest
from docx import Document


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from apply_group_meeting_flow_adjustments import build_candidate_catalog  # noqa: E402
from parse_group_meeting_flows import load_course_rules, parse_flow  # noqa: E402
from app.api.learning_plans import (  # noqa: E402
    GROUP_MEETING_CREDIT_POLICY,
    _learning_plan_group_meeting_flow_payload,
)


INVENTORY = REPO_ROOT / "data/learning-plans/group-meeting-source-inventory-2026.json"
FLOWS = REPO_ROOT / "data/learning-plans/group-meeting-flows-2026.1.json"
MAPPING = REPO_ROOT / "data/learning-plans/cycle-flow-mapping-2026.1.json"
RULES = REPO_ROOT / "data/learning-plans/course-credit-rules-2026.json"
PLAN = REPO_ROOT / "data/learning-plans/standard-3y-2026.json"
REVIEW = REPO_ROOT / "data/learning-plans/standard-3y-2026.review.json"


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _sample_docx(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    path = source_root / "【2026版】第1学年" / "【第1个月】2026版1、4、7、10月开班.docx"
    path.parent.mkdir()
    document = Document()
    document.add_paragraph("第1个月小组学习会流程")
    for text, with_image in (
        ("1. 观看《如何制作核算表》视频并进行整体核算表研讨", True),
        ("2. 幸福测评表结果检视与改善", True),
        ("3. 班级学习会发表稿编写讲解", True),
        ("4. 空巴", True),
    ):
        paragraph = document.add_paragraph(text)
        if with_image:
            paragraph.add_run().add_picture(io.BytesIO(_ONE_PIXEL_PNG), width=None)
    document.add_paragraph("空巴后班会二维码（应排除）")
    document.save(path)
    return path, source_root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_flow_parser_stops_at_first_konpa(tmp_path: Path) -> None:
    path, source_root = _sample_docx(tmp_path)
    flow = parse_flow(path, source_root, load_course_rules(RULES))

    assert flow["status"] == "PARSED"
    assert flow["boundary"]["terminal_step_count"] == 1
    assert flow["steps"][-1]["is_terminal"] is True
    assert all("空巴" in step["content"] or "空吧" in step["content"] for step in flow["steps"][-1:])


def test_qr_after_konpa_is_excluded(tmp_path: Path) -> None:
    path, source_root = _sample_docx(tmp_path)
    flow = parse_flow(path, source_root, load_course_rules(RULES))

    assert flow["boundary"]["qr_after_terminal_excluded"] is True
    assert all(
        node["source_paragraph_index"] <= flow["steps"][-1]["source_paragraph_index"]
        for node in flow["course_nodes"]
    )


def test_first_group_meeting_has_three_course_nodes(tmp_path: Path) -> None:
    path, source_root = _sample_docx(tmp_path)
    flow = parse_flow(path, source_root, load_course_rules(RULES))

    assert len(flow["course_nodes"]) == 3
    assert [node["course_key"] for node in flow["course_nodes"]] == [
        "Y1-INTEGRATED-ACCOUNTING",
        "Y1-HAPPINESS-ASSESSMENT",
        "Y1-CLASS-SPEECH-DRAFT",
    ]


def test_first_year_credit_rules_match_confirmed_values() -> None:
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    points = {rule["course_key"]: rule["credit_points"] for rule in payload["rules"]}

    assert points["Y1-HAPPINESS-ASSESSMENT"] == 20
    assert points["Y1-CLASS-SPEECH-DRAFT"] == 20
    assert points["Y1-SIX-DILIGENCES"] == 20
    assert points["Y1-TWELVE-MANAGEMENT"] == 20
    assert points["Y1-INTEGRATED-ACCOUNTING"] == 40
    assert points["Y1-ACCOUNTING-ANALYSIS-TASK"] == 40
    assert points["Y1-SEVEN-ACCOUNTING-PRINCIPLES"] == 20


def test_kyocera_annual_plan_credit_is_30_not_40() -> None:
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    rule = next(item for item in payload["rules"] if item["course_key"] == "Y1-KYOCERA-ANNUAL-PLAN")
    assert rule["credit_points"] == 30


def test_annual_monthly_management_credit_is_15() -> None:
    payload = json.loads(RULES.read_text(encoding="utf-8"))
    rule = next(item for item in payload["rules"] if item["course_key"] == "Y1-ANNUAL-MONTHLY-MGMT")
    assert rule["credit_points"] == 15


def test_group_meeting_base_credit_is_cycle_level_not_step_level() -> None:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    assert GROUP_MEETING_CREDIT_POLICY["credit_points_per_person"] == 4
    assert GROUP_MEETING_CREDIT_POLICY["task_level_credit_points"] is None
    assert all(
        task["credit_points"] is None
        for track in plan["cohort_tracks"]
        for cycle in track["cycles"]
        for task in cycle["tasks"]
        if task["task_type"] == "GROUP_MEETING"
    )


def test_unknown_course_credit_stays_null() -> None:
    payload = json.loads(FLOWS.read_text(encoding="utf-8"))
    unknown = [
        node
        for flow in payload["flows"]
        for node in flow["course_nodes"]
        if node["credit_status"] == "QR_REVIEW_REQUIRED"
    ]
    assert unknown
    assert all(node["credit_points"] is None for node in unknown)


def test_cycle_flow_mapping_does_not_use_calendar_month_as_primary_key() -> None:
    payload = json.loads(MAPPING.read_text(encoding="utf-8"))
    assert payload["quality_report"]["entry_count"] == 144
    assert payload["quality_report"]["calendar_month_used_as_primary_key"] is False
    assert all(not item["calendar_month_used_as_primary_key"] for item in payload["mappings"])


def _valid_adjustment() -> tuple[dict, dict]:
    base_catalog = json.loads(FLOWS.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    flow = base_catalog["flows"][0]
    adjustment = {
        "status": "DRAFT",
        "candidate_version_label": "2026.1",
        "overwrite_confirmed": False,
        "base_source_commit": review["source_commit"],
        "base_source_json_sha256": review["source_json_sha256"],
        "base_source_workbooks": review["source_workbooks"],
        "base_group_flow_source_files": [
            {key: item[key] for key in ("filename", "relative_path", "sha256")}
            for item in inventory["included_files"]
        ],
        "base_course_credit_rules_sha256": _sha256(RULES),
        "changes": [{
            "flow_key": flow["flow_key"],
            "steps": [{
                "title": step["title"],
                "content": step["content"],
                "is_required": step["is_required"],
                "notes": None,
            } for step in flow["steps"]],
            "notes": "测试调整",
        }],
    }
    return base_catalog, adjustment


def test_adjustment_export_binds_all_fingerprints() -> None:
    base_catalog, adjustment = _valid_adjustment()
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    candidate = build_candidate_catalog(
        base_catalog,
        adjustment,
        review_manifest=review,
        base_json=PLAN,
        rules_json=RULES,
        inventory_json=INVENTORY,
    )
    lineage = candidate["source"]
    assert lineage["base_source_commit"] == review["source_commit"]
    assert lineage["base_source_json_sha256"] == review["source_json_sha256"]
    assert lineage["base_source_workbooks"] == review["source_workbooks"]
    assert lineage["base_group_flow_source_files"]
    assert lineage["base_course_credit_rules_sha256"] == _sha256(RULES)


def test_confirmed_2026_cannot_be_overwritten() -> None:
    base_catalog, adjustment = _valid_adjustment()
    adjustment["overwrite_confirmed"] = True
    with pytest.raises(ValueError, match="覆盖"):
        build_candidate_catalog(
            base_catalog,
            adjustment,
            review_manifest=json.loads(REVIEW.read_text(encoding="utf-8")),
            base_json=PLAN,
            rules_json=RULES,
            inventory_json=INVENTORY,
        )


def test_group_flow_catalog_contains_read_only_source_evidence() -> None:
    payload = _learning_plan_group_meeting_flow_payload()
    assert payload["source_fragment_count"] == 575
    assert payload["flow_count"] == 78
    assert len(payload["base_group_flow_source_files"]) == 78
    assert payload["base_course_credit_rules_sha256"] == _sha256(RULES)
