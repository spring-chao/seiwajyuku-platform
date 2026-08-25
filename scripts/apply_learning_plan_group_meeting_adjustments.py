"""Create a new candidate learning-plan JSON from a local group-meeting draft.

This utility is intentionally file-only.  It never opens a database connection
and never mutates the confirmed 2026 source artifact.  The adjustment draft is
accepted only when all five baseline fingerprints still match the confirmed
review manifest.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from learning_plan_2026 import (
    COHORT_MONTHS,
    SUPPORTED_VERSION_LABEL_RE,
    assert_valid_plan,
    summarize_plan,
)
from review_learning_plan_2026 import sha256_file


GROUP_MEETING_CREDIT_POLICY = {
    "mode": "CYCLE_ATTENDANCE_ONCE",
    "credit_points_per_person": 4,
    "task_level_credit_points": None,
    "task_level_credit_editable": False,
}
ALLOWED_CHANGE_FIELDS = {"task_key", "title", "description", "is_required", "notes"}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _task_index(plan: dict[str, Any], task_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    match = re.fullmatch(r"(\d+)-(\d+)-(\d+)", task_key)
    if not match:
        raise ValueError(f"调整项 task_key 格式无效: {task_key!r}")
    cohort_month, cycle_index, task_index = (int(item) for item in match.groups())
    if cohort_month not in COHORT_MONTHS or not 1 <= cycle_index <= 36 or task_index < 1:
        raise ValueError(f"调整项 task_key 超出计划范围: {task_key!r}")
    track = next(
        (item for item in plan.get("cohort_tracks", []) if item.get("cohort_month") == cohort_month),
        None,
    )
    if track is None:
        raise ValueError(f"调整项找不到开班轨道: {task_key!r}")
    cycle = next(
        (item for item in track.get("cycles", []) if item.get("cycle_index") == cycle_index),
        None,
    )
    if cycle is None:
        raise ValueError(f"调整项找不到学习周期: {task_key!r}")
    tasks = cycle.get("tasks") or []
    if task_index > len(tasks):
        raise ValueError(f"调整项找不到任务序号: {task_key!r}")
    task = tasks[task_index - 1]
    if task.get("task_type") != "GROUP_MEETING":
        raise ValueError(f"调整项必须指向 GROUP_MEETING 任务: {task_key!r}")
    return cycle, task


def validate_adjustments(
    adjustments: dict[str, Any],
    *,
    base_plan: dict[str, Any],
    base_json: Path,
    review_manifest: dict[str, Any],
    version_label: str | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Validate a browser export and return candidate label, changes, SHA."""

    assert_valid_plan(base_plan)
    if review_manifest.get("status") != "CONFIRMED":
        raise ValueError("基线审核清单不是 CONFIRMED，禁止生成候选版本")
    if review_manifest.get("plan_key") != base_plan.get("plan_key"):
        raise ValueError("调整草稿与基线 plan_key 不一致")
    if review_manifest.get("version_label") != base_plan.get("version_label"):
        raise ValueError("调整草稿基线与审核清单版本不一致")
    if adjustments.get("adjustment_schema_version") != 1:
        raise ValueError("调整草稿 adjustment_schema_version 必须为 1")
    if adjustments.get("plan_key") != base_plan.get("plan_key"):
        raise ValueError("调整草稿 plan_key 与基线不一致")
    if adjustments.get("version_label") != base_plan.get("version_label"):
        raise ValueError("调整草稿 version_label 必须指向已确认的 2026 基线")
    if adjustments.get("scope") != "GROUP_MEETING":
        raise ValueError("调整草稿 scope 必须为 GROUP_MEETING")
    if adjustments.get("status") != "DRAFT":
        raise ValueError("调整草稿 status 必须为 DRAFT")

    if adjustments.get("base_source_commit") != review_manifest.get("source_commit"):
        raise ValueError("调整草稿 source_commit 与已确认审核指纹不一致")
    if adjustments.get("base_source_json") != base_json.name:
        raise ValueError("调整草稿 base_source_json 文件名不一致")
    expected_json_sha = sha256_file(base_json)
    if adjustments.get("base_source_json_sha256") != expected_json_sha:
        raise ValueError("调整草稿 JSON SHA-256 与当前确认基线不一致")
    if review_manifest.get("source_json_sha256") != expected_json_sha:
        raise ValueError("审核清单 JSON SHA-256 与当前确认基线不一致")

    expected_workbooks = review_manifest.get("source_workbooks") or {}
    actual_workbooks = adjustments.get("base_source_workbooks")
    if not isinstance(actual_workbooks, dict) or set(actual_workbooks) != {"year1", "year2", "year3"}:
        raise ValueError("调整草稿必须包含 year1/year2/year3 三份原始 Excel 指纹")
    for year in (1, 2, 3):
        expected = expected_workbooks.get(str(year))
        actual = actual_workbooks.get(f"year{year}")
        if not isinstance(expected, dict) or actual != expected:
            raise ValueError(f"调整草稿第{year}年原始 Excel 指纹不一致")

    policy = adjustments.get("credit_policy_snapshot") or {}
    for key, expected in GROUP_MEETING_CREDIT_POLICY.items():
        if policy.get(key) != expected:
            raise ValueError("调整草稿的小组会周期级学分规则指纹不一致")

    candidate = adjustments.get("candidate_plan") or {}
    candidate_label = version_label or candidate.get("version_label")
    if not isinstance(candidate_label, str) or not SUPPORTED_VERSION_LABEL_RE.fullmatch(candidate_label):
        raise ValueError("候选版本必须为 2026 或 2026.N")
    if candidate_label == base_plan.get("version_label"):
        raise ValueError("候选版本不得覆盖已确认的 2026")
    if candidate.get("status") != "DRAFT":
        raise ValueError("candidate_plan.status 必须为 DRAFT")
    if candidate.get("overwrite_confirmed") is not False:
        raise ValueError("候选版本必须明确禁止覆盖已确认版本")
    if candidate.get("requires_new_review_manifest") is not True or candidate.get("requires_source_fingerprint_refresh") is not True:
        raise ValueError("候选版本必须要求重新生成指纹和审核清单")

    changes = adjustments.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("调整草稿必须包含至少一项 changes")
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("调整项必须为对象")
        unexpected = set(change) - ALLOWED_CHANGE_FIELDS
        if unexpected:
            if "credit_points" in unexpected:
                raise ValueError("GROUP_MEETING 调整禁止携带任务级 credit_points")
            raise ValueError(f"调整项包含不支持字段: {', '.join(sorted(unexpected))}")
        task_key = change.get("task_key")
        if not isinstance(task_key, str) or task_key in seen:
            raise ValueError(f"调整项 task_key 缺失或重复: {task_key!r}")
        seen.add(task_key)
        _task_index(base_plan, task_key)
        if "title" in change and change["title"] is not None and not isinstance(change["title"], str):
            raise ValueError(f"调整项 title 必须为字符串: {task_key!r}")
        if "description" in change and change["description"] is not None and not isinstance(change["description"], str):
            raise ValueError(f"调整项 description 必须为字符串: {task_key!r}")
        if "is_required" in change and not isinstance(change["is_required"], bool):
            raise ValueError(f"调整项 is_required 必须为布尔值: {task_key!r}")
        if "notes" in change and change["notes"] is not None and not isinstance(change["notes"], str):
            raise ValueError(f"调整项 notes 必须为字符串: {task_key!r}")
    return candidate_label, changes, _canonical_sha256(adjustments)


def build_candidate_plan(
    base_plan: dict[str, Any],
    adjustments: dict[str, Any],
    *,
    base_json: Path,
    review_manifest: dict[str, Any],
    version_label: str | None = None,
) -> dict[str, Any]:
    candidate_label, changes, adjustment_sha = validate_adjustments(
        adjustments,
        base_plan=base_plan,
        base_json=base_json,
        review_manifest=review_manifest,
        version_label=version_label,
    )
    candidate = copy.deepcopy(base_plan)
    candidate["version_label"] = candidate_label
    candidate["status"] = "DRAFT"
    for change in changes:
        _, task = _task_index(candidate, change["task_key"])
        for field in ("title", "description", "is_required"):
            if field in change:
                task[field] = change[field]
    lineage = {
        "parent_version_label": base_plan["version_label"],
        "parent_source_json_sha256": sha256_file(base_json),
        "adjustment_sha256": adjustment_sha,
        "change_count": len(changes),
        "overwrite_confirmed": False,
        "changes": copy.deepcopy(changes),
    }
    source = copy.deepcopy(candidate.get("source") or {})
    source["adjustment_lineage"] = lineage
    candidate["source"] = source
    candidate["quality_report"] = summarize_plan(candidate)
    assert_valid_plan(candidate)
    return candidate


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="从小组学习会调整草稿生成新的 DRAFT 候选计划")
    parser.add_argument("--base-plan", type=Path, default=repo_root / "data/learning-plans/standard-3y-2026.json")
    parser.add_argument("--adjustments", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, default=repo_root / "data/learning-plans/standard-3y-2026.review.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version-label", help="候选版本，例如 2026.1；默认使用调整草稿声明")
    args = parser.parse_args()
    if args.output.resolve() == args.base_plan.resolve():
        raise ValueError("输出路径不得覆盖已确认基线 JSON")
    base_plan = json.loads(args.base_plan.read_text(encoding="utf-8"))
    adjustments = json.loads(args.adjustments.read_text(encoding="utf-8"))
    review_manifest = json.loads(args.review_manifest.read_text(encoding="utf-8"))
    candidate = build_candidate_plan(
        base_plan,
        adjustments,
        base_json=args.base_plan,
        review_manifest=review_manifest,
        version_label=args.version_label,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": candidate["status"], "version_label": candidate["version_label"], "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
