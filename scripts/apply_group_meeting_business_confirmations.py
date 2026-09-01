"""Apply the business confirmations for the V1.3-C0 group-meeting review.

The source flow catalog is generated from the original documents.  This
script adds only the separately confirmed business overlays (duplicate
selection and screenshot-confirmed flows), so a future parser run can be
followed by the same deterministic step again.  It also keeps the confirmed
course-credit reference in the two checked-in credit artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("data/learning-plans")
DEFAULT_CONFIRMATIONS = DEFAULT_ROOT / "group-meeting-business-confirmations-2026.1.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _step_texts(flow: dict[str, Any]) -> list[str]:
    return [
        str(step.get("content") or step.get("title") or "").strip()
        for step in flow.get("steps", [])
    ]


def _manual_flow(definition: dict[str, Any], *, confirmed_at: str, source: str) -> dict[str, Any]:
    steps = []
    for step_no, content in enumerate(definition["steps"], start=1):
        steps.append(
            {
                "step_no": step_no,
                "title": content,
                "content": content,
                "is_required": True,
                "source_paragraph_index": None,
                "qr_refs": [],
                "is_terminal": step_no == len(definition["steps"]),
            }
        )
    return {
        "flow_key": definition["flow_key"],
        "status": "MANUALLY_CONFIRMED",
        "year_index": int(definition["year_index"]),
        "cycle_index": int(definition["cycle_index"]),
        "year_cycle_index": int(definition["year_cycle_index"]),
        "eligible_cohort_months": [int(month) for month in definition["eligible_cohort_months"]],
        "title": definition["title"],
        "source": {
            "kind": "BUSINESS_CONFIRMATION",
            "relative_path": f"business-confirmations/2026.1/{definition['flow_key']}",
            "filename": definition["evidence_label"],
            "sha256": None,
        },
        "boundary": {
            "starts_at": "business-confirmed first 小组学习会 item",
            "ends_at": "business-confirmed final 空巴 item",
            "terminal_step_count": 1,
            "qr_after_terminal_excluded": True,
        },
        "steps": steps,
        "course_nodes": [],
        "learning_content_nodes": [],
        "quality": {
            "group_marker_found": True,
            "konpa_found": True,
            "course_node_count": 0,
            "unknown_course_node_count": 0,
            "pending_images_before_first_step": 0,
            "learning_content_node_count": 0,
            "video_learning_node_count": 0,
            "required_video_without_qr_count": 0,
            "learning_content_without_credit_rule_count": 0,
        },
        "business_confirmation": {
            "confirmed_at": confirmed_at,
            "source": source,
            "attendance_credit_points": 4,
            "task_level_course_credit_points": None,
            "no_video_step_in_confirmation": True,
            "course_credit_decision": "NO_EXPLICIT_COURSE_MATCH_KEEP_UNSET",
        },
    }


def _refresh_flow_quality(payload: dict[str, Any]) -> None:
    flows = payload.get("flows", [])
    quality = dict(payload.get("quality_report") or {})
    quality.update(
        {
            "flow_count": len(flows),
            "parsed_flow_count": sum(flow.get("status") == "PARSED" for flow in flows),
            "manually_confirmed_flow_count": sum(
                flow.get("status") == "MANUALLY_CONFIRMED" for flow in flows
            ),
            "superseded_flow_count": sum(
                flow.get("status") == "DUPLICATE_SUPERSEDED" for flow in flows
            ),
            "review_required_flow_count": sum(
                flow.get("status") == "REVIEW_REQUIRED" for flow in flows
            ),
            "course_node_count": sum(len(flow.get("course_nodes", [])) for flow in flows),
            "learning_content_node_count": sum(
                len(flow.get("learning_content_nodes", [])) for flow in flows
            ),
            "video_learning_node_count": sum(
                sum(node.get("task_type") == "VIDEO_LEARNING" for node in flow.get("learning_content_nodes", []))
                for flow in flows
            ),
            "required_video_without_qr_count": sum(
                sum(
                    node.get("task_type") == "VIDEO_LEARNING"
                    and node.get("is_required")
                    and not node.get("qr_refs")
                    for node in flow.get("learning_content_nodes", [])
                )
                for flow in flows
            ),
            "learning_content_without_credit_rule_count": sum(
                sum(node.get("credit_rule_key") is None for node in flow.get("learning_content_nodes", []))
                for flow in flows
            ),
            "qr_review_required_count": sum(
                node.get("credit_status") == "QR_REVIEW_REQUIRED"
                for flow in flows
                for node in flow.get("course_nodes", [])
            ),
            "missing_konpa_count": sum(
                not flow.get("quality", {}).get("konpa_found", False) for flow in flows
            ),
        }
    )
    payload["quality_report"] = quality


def apply_flow_confirmations(
    payload: dict[str, Any], confirmations: dict[str, Any]
) -> dict[str, Any]:
    flows = list(payload.get("flows", []))
    by_key = {str(flow.get("flow_key")): flow for flow in flows}
    duplicate_decisions = confirmations.get("duplicate_flow_decisions", [])
    for decision in duplicate_decisions:
        selected_key = str(decision["selected_flow_key"])
        selected = by_key.get(selected_key)
        if selected is None:
            raise ValueError(f"找不到要保留的重复流程: {selected_key}")
        for duplicate_key in decision.get("superseded_flow_keys", []):
            duplicate = by_key.get(str(duplicate_key))
            if duplicate is None:
                raise ValueError(f"找不到要标记为重复的流程: {duplicate_key}")
            if _step_texts(duplicate) != _step_texts(selected):
                raise ValueError(f"重复流程步骤并不相同，拒绝自动合并: {duplicate_key}")
            duplicate["status"] = "DUPLICATE_SUPERSEDED"
            duplicate["superseded_by"] = selected_key
            duplicate["business_confirmation"] = {
                "confirmed_at": confirmations["confirmed_at"],
                "source": confirmations["confirmation_source"],
                "decision": "DUPLICATE_SUPERSEDED",
                "selected_flow_key": selected_key,
                "note": decision.get("note"),
            }
        selected.setdefault("business_confirmation", {})
        selected["business_confirmation"].update(
            {
                "confirmed_at": confirmations["confirmed_at"],
                "source": confirmations["confirmation_source"],
                "decision": "SELECTED_FROM_DUPLICATES",
            }
        )

    for definition in confirmations.get("manual_flows", []):
        flow = _manual_flow(
            definition,
            confirmed_at=confirmations["confirmed_at"],
            source=confirmations["confirmation_source"],
        )
        existing = by_key.get(flow["flow_key"])
        if existing is None:
            flows.append(flow)
            by_key[flow["flow_key"]] = flow
        else:
            if existing.get("status") != "MANUALLY_CONFIRMED":
                raise ValueError(f"业务确认流程 key 已被其他类型占用: {flow['flow_key']}")
            existing.clear()
            existing.update(flow)

    payload["flows"] = flows
    payload["business_confirmation"] = {
        "confirmed_at": confirmations["confirmed_at"],
        "source": confirmations["confirmation_source"],
        "manual_flow_count": len(confirmations.get("manual_flows", [])),
        "duplicate_decision_count": len(duplicate_decisions),
        "attendance_credit_points": confirmations["group_meeting_policy"]["attendance_credit_points"],
        "task_level_course_credit": None,
        "explicit_no_credit": confirmations.get("explicit_no_credit", []),
    }
    _refresh_flow_quality(payload)
    return payload


def _apply_reference_rows(
    rows: list[dict[str, Any]], references: list[dict[str, Any]], *, catalog: bool
) -> list[dict[str, Any]]:
    by_key = {str(row.get("course_key")): row for row in rows}
    for reference in references:
        key = str(reference["course_key"])
        row = by_key.get(key)
        if row is None:
            row = {"course_key": key}
            rows.append(row)
            by_key[key] = row
        row.update(
            {
                "course_name": reference["course_name"],
                "year_index": reference.get("year_index"),
                "credit_points": int(reference["credit_points"]),
                "aliases": list(reference.get("aliases", [])),
                "status": "CONFIGURED" if catalog else "CONFIRMED",
                "source": "USER_CONFIRMED_REFERENCE",
            }
        )
    return rows


def apply_credit_confirmations(
    rules_payload: dict[str, Any], catalog_payload: dict[str, Any], confirmations: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    references = list(confirmations.get("course_credit_reference", []))
    rules_payload["rules"] = _apply_reference_rows(
        list(rules_payload.get("rules", [])), references, catalog=False
    )
    rules_payload["business_confirmation"] = {
        "confirmed_at": confirmations["confirmed_at"],
        "source": confirmations["confirmation_source"],
        "reference_count": len(references),
        "explicit_no_credit": confirmations.get("explicit_no_credit", []),
    }
    catalog_payload["entries"] = _apply_reference_rows(
        list(catalog_payload.get("entries", [])), references, catalog=True
    )
    catalog_payload["description"] = (
        "小组学习会课程积分配置目录。课程名称和分值以业务负责人确认的课程积分参考表为准；"
        "未明确对应的流程暂不设置课程学分。"
    )
    catalog_payload["business_confirmation"] = {
        "confirmed_at": confirmations["confirmed_at"],
        "source": confirmations["confirmation_source"],
        "reference_count": len(references),
        "explicit_no_credit": confirmations.get("explicit_no_credit", []),
    }
    return rules_payload, catalog_payload


def render_summary(confirmations: dict[str, Any]) -> str:
    policy = confirmations["group_meeting_policy"]
    lines = [
        "# 小组学习会已确认内容（业务版）",
        "",
        "> 本页只写已经确认的内容；没有把不确定的项目硬猜成课程或学分。",
        "",
        "## 先记住这三条",
        "",
        f"- 参加小组学习会：每个学习周期基础出席分 **{policy['attendance_credit_points']} 分**。",
        "- 图片中的第28、29、32、33、34次流程没有观看视频，所以不增加视频课程学分。",
        "- `成功方程式49天讲解` 视频+研讨：总部不设置课程学分，保持不计课程分。",
        "",
        "## 第26周期（1、4、7月开班模板）",
        "",
        "1、4、7月开班模板的两个候选流程内容重复，已保留 `Y3-C26-COHORT-1-4-7-c5846a2908`；另一个只保留作历史证据并标记为重复，不再参与匹配。10月开班模板第26周期的候选仍待确认，暂不自动选择。",
        "",
        "## 已按图片确认的流程",
        "",
        "| 开班批次 | 学习周期 | 流程内容 | 课程学分 |",
        "|---|---:|---|---|",
    ]
    for definition in confirmations.get("manual_flows", []):
        lines.append(
            f"| {'、'.join(f'{month}月' for month in definition['eligible_cohort_months'])} | "
            f"{definition['cycle_index']} | {definition['title']}（共{len(definition['steps'])}项，无视频） | "
            "不设置课程分；参加小组会按基础出席分4分 |"
        )
    lines.extend(
        [
            "",
            "## 课程积分表",
            "",
            "下面的课程只有在当前小组学习会内容明确对应时才使用对应分值；没有明确对应的内容先不设课程分。",
            "",
            "| 课程 | 分值 |",
            "|---|---:|",
        ]
    )
    for reference in confirmations.get("course_credit_reference", []):
        lines.append(f"| {reference['course_name']} | {reference['credit_points']} |")
    lines.extend(
        [
            "",
            "## 还没有确认的项目",
            "",
            "10月开班模板的第25、26、30、31、32周期仍保留待确认；以后有明确资料时再补，不影响本轮已经确认的内容。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="应用小组学习会业务确认配置")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--confirmations", type=Path, default=None)
    args = parser.parse_args()
    root = args.root
    confirmations_path = args.confirmations or root / DEFAULT_CONFIRMATIONS.name
    confirmations = _read(confirmations_path)

    flows_path = root / "group-meeting-flows-2026.1.json"
    rules_path = root / "course-credit-rules-2026.json"
    catalog_path = root / "course-credit-catalog-2026.json"
    flows = apply_flow_confirmations(_read(flows_path), confirmations)
    rules, catalog = apply_credit_confirmations(
        _read(rules_path), _read(catalog_path), confirmations
    )
    _write(flows_path, flows)
    _write(rules_path, rules)
    _write(catalog_path, catalog)
    summary_path = root / "group-meeting-business-confirmations-2026.1.md"
    summary_path.write_text(render_summary(confirmations), encoding="utf-8")
    print(
        json.dumps(
            {
                "manual_flow_count": len(confirmations.get("manual_flows", [])),
                "course_credit_reference_count": len(confirmations.get("course_credit_reference", [])),
                "flow_quality": flows["quality_report"],
                "summary_path": str(summary_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
