"""Create a 2026.1 group-flow candidate without touching the confirmed plan.

The command validates the five-part release fingerprint and writes a new
candidate JSON file only.  It intentionally has no database adapter and never
changes ``standard-3y-2026.json`` or its review manifest.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_file(path: Path) -> str:
    return sha256_file(path)


def _assert_workbook_fingerprints(adjustment: dict, review: dict) -> None:
    if adjustment.get("base_source_workbooks") != review.get("source_workbooks"):
        raise ValueError("原始 Excel 指纹不一致")


def _assert_flow_source_fingerprints(adjustment: dict, inventory: dict) -> None:
    expected = [
        {
            "filename": item.get("filename"),
            "relative_path": item.get("relative_path"),
            "sha256": item.get("sha256"),
        }
        for item in inventory.get("included_files", [])
    ]
    actual = adjustment.get("base_group_flow_source_files")
    if actual != expected:
        raise ValueError("小组会流程源文件指纹不一致")


def validate_adjustments(
    adjustments: dict,
    *,
    review_manifest: dict,
    base_json: Path,
    rules_json: Path,
    inventory_json: Path,
) -> None:
    if adjustments.get("status") != "DRAFT":
        raise ValueError("调整草稿必须是 DRAFT")
    if adjustments.get("candidate_version_label") != "2026.1":
        raise ValueError("候选版本必须是 2026.1")
    if adjustments.get("overwrite_confirmed") is not False:
        raise ValueError("禁止覆盖已确认版本")
    if adjustments.get("base_source_commit") != review_manifest.get("source_commit"):
        raise ValueError("审核提交指纹不一致")
    if adjustments.get("base_source_json_sha256") != review_manifest.get("source_json_sha256"):
        raise ValueError("JSON SHA-256 指纹不一致")
    _assert_workbook_fingerprints(adjustments, review_manifest)
    if adjustments.get("base_course_credit_rules_sha256") != _fingerprint_file(rules_json):
        raise ValueError("课程积分规则 SHA-256 指纹不一致")
    inventory = json.loads(inventory_json.read_text(encoding="utf-8"))
    _assert_flow_source_fingerprints(adjustments, inventory)
    if not base_json.is_file():
        raise ValueError("确认版标准计划不存在")
    for change in adjustments.get("changes", []):
        allowed = {"flow_key", "steps", "notes"}
        if set(change) - allowed:
            raise ValueError("流程调整不得修改课程积分或其他受保护字段")
        for step in change.get("steps", []):
            if set(step) - {"title", "content", "is_required", "notes"}:
                raise ValueError("流程步骤只允许调整标题、内容、必做标记和说明")


def build_candidate_catalog(
    base_catalog: dict,
    adjustments: dict,
    *,
    review_manifest: dict,
    base_json: Path,
    rules_json: Path,
    inventory_json: Path,
) -> dict:
    validate_adjustments(
        adjustments,
        review_manifest=review_manifest,
        base_json=base_json,
        rules_json=rules_json,
        inventory_json=inventory_json,
    )
    candidate = copy.deepcopy(base_catalog)
    flows_by_key = {flow.get("flow_key"): flow for flow in candidate.get("flows", [])}
    for change in adjustments.get("changes", []):
        flow = flows_by_key.get(change.get("flow_key"))
        if flow is None:
            raise ValueError(f"找不到待调整流程: {change.get('flow_key')}")
        original_qr = flow.get("course_nodes", [])
        flow["steps"] = [
            {
                **step,
                "title": str(step.get("title", "")).strip(),
                "content": str(step.get("content", "")).strip(),
                "is_required": bool(step.get("is_required", True)),
                "notes": step.get("notes"),
            }
            for step in change.get("steps", [])
        ]
        # QR/course nodes and their credit values remain source-derived and
        # cannot be edited through this adjustment channel.
        flow["course_nodes"] = original_qr
    candidate["version_label"] = "2026.1"
    candidate["status"] = "DRAFT"
    candidate["source"] = {
        **candidate.get("source", {}),
        "base_source_commit": review_manifest.get("source_commit"),
        "base_source_json": base_json.name,
        "base_source_json_sha256": review_manifest.get("source_json_sha256"),
        "base_source_workbooks": review_manifest.get("source_workbooks", {}),
        "base_group_flow_source_files": adjustments.get("base_group_flow_source_files", []),
        "base_course_credit_rules_sha256": adjustments.get("base_course_credit_rules_sha256"),
        "adjustment_lineage": {
            "candidate_version_label": "2026.1",
            "overwrite_confirmed": False,
            "change_count": len(adjustments.get("changes", [])),
        },
    }
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-catalog", type=Path, required=True)
    parser.add_argument("--adjustments", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--base-json", type=Path, required=True)
    parser.add_argument("--rules-json", type=Path, required=True)
    parser.add_argument("--inventory-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = build_candidate_catalog(
        json.loads(args.base_catalog.read_text(encoding="utf-8")),
        json.loads(args.adjustments.read_text(encoding="utf-8")),
        review_manifest=json.loads(args.review_manifest.read_text(encoding="utf-8")),
        base_json=args.base_json,
        rules_json=args.rules_json,
        inventory_json=args.inventory_json,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"version_label": candidate["version_label"], "status": candidate["status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
