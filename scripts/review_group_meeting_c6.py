"""Build and verify the L1.2-C C6 exception review manifest.

The C6 manifest is an audit boundary, not a database import.  It is generated
from the already confirmed 2026 plan and the frozen L1.2-C source artifacts.
Every exception and every course node must be reviewed individually.  The
top-level ``CONFIRMED_CANDIDATE`` state is derived by :func:`verify_review`;
it cannot be set by editing the JSON directly.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("data/learning-plans")
DEFAULT_OUTPUT = DEFAULT_ROOT / "standard-3y-2026.1.review.json"
FINAL_MAPPING_STATUSES = {"MAPPED", "EXEMPTED", "SOURCE_MISSING"}
FINAL_QR_STATUSES = {
    "COURSE_CONFIRMED",
    "COURSE_CONFIRMED_CREDIT_PENDING",
    "NON_COURSE_QR",
    "EXCLUDED_AFTER_KONPA",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_files(inventory: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "filename": str(item.get("filename")),
            "relative_path": str(item.get("relative_path")),
            "sha256": str(item.get("sha256")),
        }
        for item in inventory.get("included_files", [])
    ]


def _review_item(kind: str, review_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "kind": kind,
        "review_status": "PENDING",
        "resolution_status": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "notes": None,
        **fields,
    }


def _sample_flows(flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select three representative flows per year for end-to-end review."""

    selected: list[dict[str, Any]] = []
    by_year: dict[int, list[dict[str, Any]]] = {}
    for flow in flows:
        by_year.setdefault(int(flow.get("year_index") or 0), []).append(flow)
    for year_index in (1, 2, 3):
        year_flows = sorted(
            by_year.get(year_index, []),
            key=lambda flow: (int(flow.get("cycle_index") or 0), flow.get("flow_key", "")),
        )
        if not year_flows:
            continue
        indexes = sorted({0, len(year_flows) // 2, len(year_flows) - 1})
        for index in indexes:
            flow = year_flows[index]
            selected.append(
                _review_item(
                    "FLOW_SAMPLE",
                    f"flow-sample-y{year_index}-{flow.get('flow_key')}",
                    year_index=year_index,
                    cycle_index=flow.get("cycle_index"),
                    flow_key=flow.get("flow_key"),
                    source=copy.deepcopy(flow.get("source", {})),
                    boundary=copy.deepcopy(flow.get("boundary", {})),
                    review_prompt="确认流程从小组学习会开始，到首个空巴结束；空巴后的班会二维码不在流程内。",
                )
            )
    return selected


def build_review(
    *,
    base_plan: Path,
    base_review: Path,
    flows_path: Path,
    mapping_path: Path,
    rules_path: Path,
    inventory_path: Path,
) -> dict[str, Any]:
    base = _read(base_plan)
    manifest = _read(base_review)
    flows_catalog = _read(flows_path)
    mapping_catalog = _read(mapping_path)
    rules = _read(rules_path)
    inventory = _read(inventory_path)
    flows = flows_catalog.get("flows", [])

    mapping_conflicts: list[dict[str, Any]] = []
    mapping_missing: list[dict[str, Any]] = []
    for mapping in mapping_catalog.get("mappings", []):
        status = mapping.get("status")
        fields = {
            "mapping_key": mapping.get("mapping_key"),
            "cohort_month": mapping.get("cohort_month"),
            "cycle_index": mapping.get("cycle_index"),
            "year_index": mapping.get("year_index"),
            "year_cycle_index": mapping.get("year_cycle_index"),
            "candidate_flow_keys": mapping.get("candidate_flow_keys", []),
            "candidate_source_files": mapping.get("candidate_source_files", []),
            "lookup_key": mapping.get("lookup_key", {}),
            "review_prompt": (
                "选择正确的流程；不得依据自然月自动猜选。"
                if status == "MAPPING_CONFLICT"
                else "确认本周期映射流程，或明确 EXEMPTED/NO_GROUP_MEETING；若应有流程但源资料缺失，标记 SOURCE_MISSING。"
            ),
        }
        if status == "MAPPING_CONFLICT":
            mapping_conflicts.append(_review_item("MAPPING_CONFLICT", f"mapping-conflict-{mapping.get('mapping_key')}", **fields))
        elif status == "MAPPING_MISSING":
            mapping_missing.append(_review_item("MAPPING_MISSING", f"mapping-missing-{mapping.get('mapping_key')}", **fields))

    qr_review_required: list[dict[str, Any]] = []
    course_nodes: list[dict[str, Any]] = []
    for flow in flows:
        for node_index, node in enumerate(flow.get("course_nodes", []), start=1):
            node_fields = {
                "flow_key": flow.get("flow_key"),
                "year_index": flow.get("year_index"),
                "cycle_index": flow.get("cycle_index"),
                "source": copy.deepcopy(flow.get("source", {})),
                "node_index": node_index,
                "node_type": node.get("node_type"),
                "media_target": node.get("media_target"),
                "relationship_id": node.get("relationship_id"),
                "source_paragraph_index": node.get("source_paragraph_index"),
                "context_step_no": node.get("context_step_no"),
                "context_text": node.get("context_text"),
                "source_course_key": node.get("course_key"),
                "source_credit_points": node.get("credit_points"),
                "source_credit_status": node.get("credit_status"),
                "review_prompt": "确认课程名称、是否属于课程积分二维码以及积分；非课程或空巴后二维码要明确排除。",
            }
            course_item = _review_item(
                "COURSE_NODE",
                f"course-node-{flow.get('flow_key')}-{node_index}",
                **node_fields,
            )
            course_nodes.append(course_item)
            if node.get("credit_status") == "QR_REVIEW_REQUIRED":
                qr_review_required.append(
                    _review_item(
                        "QR_REVIEW_REQUIRED",
                        f"qr-review-{flow.get('flow_key')}-{node_index}",
                        **node_fields,
                    )
                )

    now = datetime.now(timezone.utc).isoformat()
    source_files = _source_files(inventory)
    fingerprint = {
        "base_source_commit": manifest.get("source_commit"),
        "base_source_json": manifest.get("source_json"),
        "base_source_json_sha256": manifest.get("source_json_sha256"),
        "base_source_workbooks": copy.deepcopy(manifest.get("source_workbooks", {})),
        "base_flow_catalog": {"filename": flows_path.name, "sha256": sha256_file(flows_path)},
        "base_mapping": {"filename": mapping_path.name, "sha256": sha256_file(mapping_path)},
        "base_course_credit_rules": {"filename": rules_path.name, "sha256": sha256_file(rules_path)},
        "base_source_inventory": {
            "filename": inventory_path.name,
            "sha256": sha256_file(inventory_path),
            "included_file_count": len(source_files),
        },
        "base_group_flow_source_files": source_files,
    }
    review = {
        "review_schema_version": 1,
        "review_type": "L1.2-C6_EXCEPTION_AND_QR_REVIEW",
        "plan_key": manifest.get("plan_key", base.get("plan_key")),
        "base_version_label": manifest.get("version_label", "2026"),
        "candidate_version_label": "2026.1",
        "candidate_status": "DRAFT",
        "status": "PENDING",
        "created_at": now,
        "updated_at": now,
        "base_confirmed_review_status": manifest.get("status"),
        "base_plan_cycle_count": sum(len(track.get("cycles", [])) for track in base.get("cohort_tracks", [])),
        "source_fingerprint": fingerprint,
        "review_rules": {
            "mapping_conflict_final_statuses": ["MAPPED"],
            "mapping_missing_final_statuses": ["MAPPED", "EXEMPTED", "SOURCE_MISSING"],
            "qr_final_statuses": sorted(FINAL_QR_STATUSES),
            "source_missing_blocks_candidate": True,
            "requires_all_course_nodes": True,
            "requires_flow_samples": True,
        },
        "mapping_conflicts": mapping_conflicts,
        "mapping_missing": mapping_missing,
        "qr_review_required": qr_review_required,
        "course_nodes": course_nodes,
        "flow_samples": _sample_flows(flows),
        "summary": {
            "cycle_count": 144,
            "mapping_conflict_count": len(mapping_conflicts),
            "mapping_missing_count": len(mapping_missing),
            "unresolved_mapping_count": len(mapping_conflicts) + len(mapping_missing),
            "qr_review_required_count": len(qr_review_required),
            "course_node_review_count": len(course_nodes),
            "course_node_confirmed_count": 0,
            "flow_sample_count": len(_sample_flows(flows)),
            "flow_sample_confirmed_count": 0,
            "source_missing_count": 0,
            "status": "PENDING",
        },
        "rules_snapshot": {
            "group_meeting_base_credit": rules.get("group_meeting_base_credit"),
            "course_rules": copy.deepcopy(rules.get("rules", [])),
        },
    }
    return review


def _all_items(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for key in ("mapping_conflicts", "mapping_missing", "qr_review_required", "course_nodes", "flow_samples")
        for item in review.get(key, [])
    ]


def _assert_fingerprint(review: dict[str, Any], paths: dict[str, Path]) -> None:
    fingerprint = review.get("source_fingerprint") or {}
    base_review = _read(paths["base_review"])
    expected_workbooks = base_review.get("source_workbooks", {})
    checks = {
        "base_source_commit": base_review.get("source_commit"),
        "base_source_json": base_review.get("source_json"),
        "base_source_json_sha256": base_review.get("source_json_sha256"),
        "base_flow_catalog": {"filename": paths["flows"].name, "sha256": sha256_file(paths["flows"])},
        "base_mapping": {"filename": paths["mapping"].name, "sha256": sha256_file(paths["mapping"])},
        "base_course_credit_rules": {"filename": paths["rules"].name, "sha256": sha256_file(paths["rules"])},
        "base_source_inventory": {
            "filename": paths["inventory"].name,
            "sha256": sha256_file(paths["inventory"]),
            "included_file_count": len(_read(paths["inventory"]).get("included_files", [])),
        },
    }
    if sha256_file(paths["base_plan"]) != base_review.get("source_json_sha256"):
        raise ValueError("2026 CONFIRMED 标准 JSON 文件指纹不一致，请重新生成审核清单")
    if any(fingerprint.get(key) != value for key, value in checks.items()):
        raise ValueError("C6 审核指纹不一致，请重新生成审核清单")
    if fingerprint.get("base_source_workbooks") != expected_workbooks:
        raise ValueError("C6 原始 Excel 指纹不一致，请重新生成审核清单")
    expected_files = _source_files(_read(paths["inventory"]))
    if fingerprint.get("base_group_flow_source_files") != expected_files:
        raise ValueError("C6 78份流程源文件指纹不一致，请重新生成审核清单")


def _validate_item(item: dict[str, Any], *, allow_pending: bool = False) -> None:
    status = item.get("resolution_status")
    if status is None:
        if allow_pending:
            return
        raise ValueError(f"{item.get('review_id')} 尚未完成业务确认")
    kind = item.get("kind")
    if kind == "MAPPING_CONFLICT" and status != "MAPPED":
        raise ValueError(f"{item.get('review_id')} 冲突只能确认 MAPPED")
    if kind == "MAPPING_CONFLICT" and not item.get("resolved_flow_key"):
        raise ValueError(f"{item.get('review_id')} MAPPED 必须选择 flow_key")
    if kind == "MAPPING_MISSING" and status not in FINAL_MAPPING_STATUSES:
        raise ValueError(f"{item.get('review_id')} 缺失结论无效")
    if kind == "MAPPING_MISSING" and status == "MAPPED" and not item.get("resolved_flow_key"):
        raise ValueError(f"{item.get('review_id')} MAPPED 必须选择 flow_key")
    if kind in {"QR_REVIEW_REQUIRED", "COURSE_NODE"}:
        if status not in FINAL_QR_STATUSES:
            raise ValueError(f"{item.get('review_id')} 二维码结论无效")
        course_key = item.get("resolved_course_key")
        credit_points = item.get("resolved_credit_points")
        if status in {"COURSE_CONFIRMED", "COURSE_CONFIRMED_CREDIT_PENDING"} and not course_key:
            raise ValueError(f"{item.get('review_id')} 已确认课程必须填写 course_key")
        if status == "COURSE_CONFIRMED" and credit_points is None:
            raise ValueError(f"{item.get('review_id')} COURSE_CONFIRMED 必须填写积分")
        if status == "COURSE_CONFIRMED" and (not isinstance(credit_points, int) or credit_points < 0):
            raise ValueError(f"{item.get('review_id')} 课程积分必须是非负整数")
        if status != "COURSE_CONFIRMED" and status != "COURSE_CONFIRMED_CREDIT_PENDING" and (course_key or credit_points is not None):
            raise ValueError(f"{item.get('review_id')} 非课程/排除项不得带课程或积分")
        if status == "COURSE_CONFIRMED_CREDIT_PENDING" and credit_points is not None:
            raise ValueError(f"{item.get('review_id')} 积分待定项的 credit_points 必须为 null")
    if kind == "FLOW_SAMPLE" and status != "CONFIRMED":
        raise ValueError(f"{item.get('review_id')} 流程抽查必须 CONFIRMED")
    if item.get("review_status") != "CONFIRMED":
        raise ValueError(f"{item.get('review_id')} 缺少逐项 CONFIRMED 记录")
    if not item.get("reviewed_by") or not item.get("reviewed_at"):
        raise ValueError(f"{item.get('review_id')} 缺少 reviewed_by/reviewed_at")


def verify_review(
    review: dict[str, Any],
    *,
    paths: dict[str, Path],
    mutate_status: bool = False,
) -> dict[str, Any]:
    _assert_fingerprint(review, paths)
    expected_counts = {
        "mapping_conflicts": 5,
        "mapping_missing": 10,
        "qr_review_required": 15,
        "course_nodes": 32,
        "flow_samples": 9,
    }
    for key, expected in expected_counts.items():
        if len(review.get(key, [])) != expected:
            raise ValueError(f"C6 {key} 数量应为 {expected}")
    if review.get("base_plan_cycle_count") != 144:
        raise ValueError("C6 基线周期数应为 144")
    for item in _all_items(review):
        _validate_item(item)
    source_missing = sum(
        item.get("resolution_status") == "SOURCE_MISSING" for item in review.get("mapping_missing", [])
    )
    if source_missing:
        raise ValueError("仍有 SOURCE_MISSING，不能进入 CONFIRMED_CANDIDATE")
    summary = review.setdefault("summary", {})
    summary.update(
        {
            "cycle_count": 144,
            "mapping_conflict_count": len(review.get("mapping_conflicts", [])),
            "mapping_missing_count": len(review.get("mapping_missing", [])),
            "unresolved_mapping_count": 0,
            "qr_review_required_count": 0,
            "course_node_review_count": len(review.get("course_nodes", [])),
            "course_node_confirmed_count": len(review.get("course_nodes", [])),
            "flow_sample_count": len(review.get("flow_samples", [])),
            "flow_sample_confirmed_count": len(review.get("flow_samples", [])),
            "source_missing_count": 0,
            "status": "CONFIRMED_CANDIDATE",
        }
    )
    if mutate_status:
        review["status"] = "CONFIRMED_CANDIDATE"
        review["candidate_status"] = "CONFIRMED_CANDIDATE"
        review["updated_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def apply_confirmation(
    review: dict[str, Any],
    *,
    review_id: str,
    status: str,
    reviewed_by: str,
    notes: str | None = None,
    flow_key: str | None = None,
    course_key: str | None = None,
    credit_points: int | None = None,
) -> dict[str, Any]:
    if not reviewed_by.strip():
        raise ValueError("reviewed_by 不能为空")
    matches = [item for item in _all_items(review) if item.get("review_id") == review_id]
    if len(matches) != 1:
        raise ValueError(f"找不到唯一审核项: {review_id}")
    item = matches[0]
    item["review_status"] = "CONFIRMED"
    item["resolution_status"] = status
    item["reviewed_by"] = reviewed_by.strip()
    item["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    item["notes"] = notes.strip() if notes else None
    if item.get("kind") == "MAPPING_CONFLICT":
        if status != "MAPPED" or not flow_key:
            raise ValueError("映射冲突必须选择 MAPPED 和 flow_key")
        item["resolved_flow_key"] = flow_key
    elif item.get("kind") == "MAPPING_MISSING":
        if status not in FINAL_MAPPING_STATUSES:
            raise ValueError("映射缺失必须选择 MAPPED、EXEMPTED 或 SOURCE_MISSING")
        item["resolved_flow_key"] = flow_key if status == "MAPPED" else None
        if status == "MAPPED" and not flow_key:
            raise ValueError("MAPPED 必须填写 flow_key")
    elif item.get("kind") in {"QR_REVIEW_REQUIRED", "COURSE_NODE"}:
        item["resolved_course_key"] = course_key if status in {"COURSE_CONFIRMED", "COURSE_CONFIRMED_CREDIT_PENDING"} else None
        item["resolved_credit_points"] = credit_points if status == "COURSE_CONFIRMED" else None
    elif item.get("kind") == "FLOW_SAMPLE" and status != "CONFIRMED":
        raise ValueError("流程抽查只能使用 CONFIRMED")
    return item


def _paths(root: Path) -> dict[str, Path]:
    return {
        "base_plan": root / "standard-3y-2026.json",
        "base_review": root / "standard-3y-2026.review.json",
        "flows": root / "group-meeting-flows-2026.1.json",
        "mapping": root / "cycle-flow-mapping-2026.1.json",
        "rules": root / "course-credit-rules-2026.json",
        "inventory": root / "group-meeting-source-inventory-2026.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="L1.2-C C6 异常与二维码人工复核清单")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build", action="store_true", help="从冻结工件生成 PENDING 清单")
    parser.add_argument("--confirm", metavar="REVIEW_ID")
    parser.add_argument("--status")
    parser.add_argument("--reviewed-by")
    parser.add_argument("--notes")
    parser.add_argument("--flow-key")
    parser.add_argument("--course-key")
    parser.add_argument("--credit-points", type=int)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    paths = _paths(args.root)
    if args.build:
        payload = build_review(
            base_plan=paths["base_plan"], base_review=paths["base_review"], flows_path=paths["flows"],
            mapping_path=paths["mapping"], rules_path=paths["rules"], inventory_path=paths["inventory"],
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
        return 0
    if not args.output.is_file():
        raise SystemExit(f"审核清单不存在: {args.output}; 先执行 --build")
    review = _read(args.output)
    if args.confirm:
        if not args.status or not args.reviewed_by:
            raise SystemExit("--confirm 需要同时提供 --status 和 --reviewed-by")
        _assert_fingerprint(review, paths)
        apply_confirmation(
            review, review_id=args.confirm, status=args.status, reviewed_by=args.reviewed_by,
            notes=args.notes, flow_key=args.flow_key, course_key=args.course_key,
            credit_points=args.credit_points,
        )
        # This intentionally leaves the top-level status PENDING until every
        # item has been confirmed and verify_review succeeds.
        review["status"] = "PENDING"
        review["candidate_status"] = "DRAFT"
        review["updated_at"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"review_id": args.confirm, "status": "CONFIRMED"}, ensure_ascii=False))
        return 0
    if args.verify:
        summary = verify_review(review, paths=paths, mutate_status=True)
        args.output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    raise SystemExit("请指定 --build、--confirm 或 --verify")


if __name__ == "__main__":
    raise SystemExit(main())
