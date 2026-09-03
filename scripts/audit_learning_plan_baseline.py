#!/usr/bin/env python3
"""Compare a read-only learning-plan health response with the V3 baseline.

The command intentionally has no database write path.  It consumes a JSON
file captured from GET /api/v1/classes/learning-plan-health and reports the
business differences that need review before any production correction.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.services.learning_plan_baseline import (  # noqa: E402
    actual_snapshot,
    baseline_summary,
    compare_expectation,
    is_learning_plan_binding_required,
    load_baseline,
    public_expectation,
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"JSON 根节点必须是对象: {path}")
    return value


def _health_payload(value: dict[str, Any]) -> dict[str, Any]:
    data = value.get("data")
    return data if isinstance(data, dict) else value


def _fallback_runtime_status(
    *, item: dict[str, Any], generated_at: str | None
) -> str:
    if item.get("runtime_status"):
        return str(item["runtime_status"])
    cycle = item.get("current_cycle")
    if isinstance(cycle, dict):
        if cycle.get("class_meeting_status") == "POSTPONED":
            return "POSTPONED"
        return "NORMAL"
    binding = item.get("binding")
    if isinstance(binding, dict) and binding.get("started_at") and generated_at:
        try:
            started = datetime.fromisoformat(
                str(binding["started_at"]).replace("Z", "+00:00")
            )
            observed = datetime.fromisoformat(
                str(generated_at).replace("Z", "+00:00")
            )
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            if started > observed:
                return "NOT_STARTED"
        except ValueError:
            pass
    return "UNKNOWN"


def _class_result(
    *,
    expected: dict[str, Any],
    observed: dict[str, Any] | None,
    generated_at: str | None,
) -> dict[str, Any]:
    class_id = str(expected.get("class_org_unit_id") or "").strip() or None
    result: dict[str, Any] = {
        "class_name": expected.get("class_name"),
        "class_org_unit_id": class_id,
        "migration_status": expected.get("migration_status"),
        "expected": public_expectation(expected),
        "action": "UNRESOLVED",
        "mismatches": [],
    }
    if not class_id:
        result["action"] = "NEEDS_ID_LOOKUP"
        result["note"] = "仅按 class_org_unit_id 对比；未提供 ID，不按班级名称匹配。"
        return result
    if observed is None:
        result["action"] = "NOT_IN_HEALTH_RESPONSE"
        result["note"] = "健康接口响应中没有该 ID；可能未在当前 scope、已停用或尚未完成当前组织清单核对。"
        return result
    if str(observed.get("class_name") or "") != str(expected.get("class_name") or ""):
        result["action"] = "BASELINE_ID_NAME_MISMATCH"
        result["actual"] = {"class_name": observed.get("class_name")}
        result["note"] = "ID 与名称不一致，停止自动比较和生产修正。"
        return result
    binding = observed.get("binding")
    current_cycle = observed.get("current_cycle")
    runtime_status = _fallback_runtime_status(
        item=observed, generated_at=generated_at
    )
    actual = actual_snapshot(
        binding=binding if isinstance(binding, dict) else None,
        current_cycle=current_cycle if isinstance(current_cycle, dict) else None,
        runtime_status=runtime_status,
    )
    result["actual"] = actual
    if not is_learning_plan_binding_required(expected):
        result["action"] = "NOT_APPLICABLE"
        result["note"] = "该班级不纳入学习计划绑定管理；不创建或修正绑定。"
        return result
    if expected.get("migration_status") == "MANUAL_REVIEW_REQUIRED":
        result["action"] = "MANUAL_CONFIRMATION"
        result["note"] = "该班级业务基线尚未确认，禁止自动绑定模板或修改生产数据。"
        return result
    if not isinstance(binding, dict):
        result["action"] = "MISSING_BINDING_REVIEW"
        return result
    mismatches = compare_expectation(expected, actual)
    result["mismatches"] = mismatches
    if not mismatches:
        result["action"] = "NO_CHANGE"
        return result
    result["before"] = actual
    result["proposed"] = public_expectation(expected)
    structural = {
        "EXPECTED_CYCLE_MISMATCH",
        "EXPECTED_TEMPLATE_MISMATCH",
        "EXPECTED_PLAN_VERSION_MISMATCH",
    }
    result["action"] = (
        "CORRECTION_AND_STATUS_REVIEW"
        if any(item["issue_type"] in structural for item in mismatches)
        and any(item["issue_type"] == "EXPECTED_STATUS_MISMATCH" for item in mismatches)
        else "CORRECTION"
        if any(item["issue_type"] in structural for item in mismatches)
        else "STATUS_ADJUSTMENT"
    )
    return result


def build_report(baseline: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    payload = _health_payload(health)
    observed_by_id = {
        str(item.get("class_org_unit_id")): item
        for item in payload.get("classes", [])
        if isinstance(item, dict) and item.get("class_org_unit_id")
    }
    generated_at = payload.get("generated_at")
    rows = [
        _class_result(
            expected=item,
            observed=observed_by_id.get(str(item.get("class_org_unit_id"))),
            generated_at=generated_at,
        )
        for item in baseline.get("classes", [])
        if isinstance(item, dict)
    ]
    counts: dict[str, int] = {}
    for row in rows:
        action = str(row["action"])
        counts[action] = counts.get(action, 0) + 1
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_health_generated_at": generated_at,
        "dry_run": True,
        "write_performed": False,
        "baseline": baseline_summary(baseline),
        "health_scope": payload.get("scope"),
        "summary": {"total_rows": len(rows), "action_counts": counts},
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--health-json", required=True, type=Path,
        help="GET /api/v1/classes/learning-plan-health 的 JSON 文件",
    )
    parser.add_argument(
        "--baseline", type=Path,
        default=REPO_ROOT / "data" / "learning-plans" / "class-learning-plan-migration-baseline-2026-09.json",
    )
    parser.add_argument("--output", type=Path, help="可选：写出 DRY-RUN JSON 报告")
    args = parser.parse_args()
    baseline = load_baseline(args.baseline)
    health = _read_json(args.health_json)
    report = build_report(baseline, health)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
