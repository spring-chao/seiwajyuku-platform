from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from learning_plan_2026 import COHORT_MONTHS, assert_valid_plan


CHECKPOINT_CYCLES = (1, 6, 12, 13, 18, 24, 25, 30, 36)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(repo_root: Path, revision: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo_root), "rev-parse", f"{revision}^{{commit}}"],
        text=True,
    ).strip()


def _checkpoint(plan: dict[str, Any], cohort_month: int, cycle_index: int) -> dict[str, Any]:
    track = next(track for track in plan["cohort_tracks"] if track["cohort_month"] == cohort_month)
    cycle = next(cycle for cycle in track["cycles"] if cycle["cycle_index"] == cycle_index)
    tasks = cycle.get("tasks", [])
    source_refs: dict[tuple[str, str], dict[str, Any]] = {}
    for task in tasks:
        metadata = task.get("metadata") or {}
        key = (str(metadata.get("source_sheet") or ""), str(metadata.get("source_cell") or ""))
        if not all(key):
            continue
        source_refs.setdefault(key, {"source_sheet": key[0], "source_cell": key[1]})
    task_types = Counter(str(task.get("task_type")) for task in tasks)
    confirmed_credits = [
        {
            "title": task.get("title"),
            "credit_points": task.get("credit_points"),
            "source_sheet": (task.get("metadata") or {}).get("source_sheet"),
            "source_cell": (task.get("metadata") or {}).get("source_cell"),
        }
        for task in tasks
        if task.get("credit_points") is not None
    ]
    return {
        "checkpoint_id": f"{cohort_month}-{cycle_index}",
        "cohort_month": cohort_month,
        "cycle_index": cycle_index,
        "year_index": cycle.get("year_index"),
        "year_cycle_index": cycle.get("year_cycle_index"),
        "nominal_calendar_month": cycle.get("nominal_calendar_month"),
        "source_refs": sorted(source_refs.values(), key=lambda item: (item["source_sheet"], item["source_cell"])),
        "task_count": len(tasks),
        "task_type_counts": dict(sorted(task_types.items())),
        "confirmed_credits": confirmed_credits,
        "review_fields": [
            "class_meeting_content",
            "group_meeting_content",
            "online_course_split",
            "credit_points",
            "nominal_calendar_month",
        ],
        "status": "PENDING",
        "reviewed_by": None,
        "reviewed_at": None,
        "notes": None,
    }


def build_review_manifest(plan: dict[str, Any], *, source_commit: str, source_json: Path) -> dict[str, Any]:
    assert_valid_plan(plan)
    checkpoints = [
        _checkpoint(plan, cohort_month, cycle_index)
        for cohort_month in COHORT_MONTHS
        for cycle_index in CHECKPOINT_CYCLES
    ]
    return {
        "review_schema_version": 1,
        "plan_key": plan["plan_key"],
        "version_label": plan["version_label"],
        "source_commit": source_commit,
        "source_json": source_json.name,
        "source_json_sha256": sha256_file(source_json),
        "status": "PENDING",
        "required_checkpoint_count": len(checkpoints),
        "created_at": datetime.now(UTC).isoformat(),
        "confirmed_at": None,
        "confirmed_by": None,
        "checkpoints": checkpoints,
    }


def verify_review_manifest(
    manifest: dict[str, Any],
    *,
    plan: dict[str, Any],
    source_json: Path,
    expected_source_commit: str | None = None,
    require_confirmed: bool = False,
) -> None:
    assert_valid_plan(plan)
    if manifest.get("plan_key") != plan.get("plan_key") or manifest.get("version_label") != plan.get("version_label"):
        raise ValueError("审核清单与学习计划版本不一致")
    if manifest.get("source_json") != source_json.name:
        raise ValueError("审核清单的 source_json 文件名不一致")
    if manifest.get("source_json_sha256") != sha256_file(source_json):
        raise ValueError("审核清单的 JSON SHA-256 与当前导入源不一致")
    if expected_source_commit and manifest.get("source_commit") != expected_source_commit:
        raise ValueError("审核清单的 source_commit 与固定审核提交不一致")
    checkpoints = manifest.get("checkpoints")
    expected_count = len(COHORT_MONTHS) * len(CHECKPOINT_CYCLES)
    if manifest.get("required_checkpoint_count") != expected_count or not isinstance(checkpoints, list) or len(checkpoints) != expected_count:
        raise ValueError("审核清单必须包含36个固定抽查点")
    if require_confirmed:
        if manifest.get("status") != "CONFIRMED":
            raise ValueError("36项人工抽查尚未确认，禁止 B2 导入")
        if any(item.get("status") != "CONFIRMED" for item in checkpoints):
            raise ValueError("仍有抽查项不是 CONFIRMED，禁止 B2 导入")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="生成并核验 2026 学习计划的审核指纹与36点抽查清单")
    parser.add_argument(
        "--input",
        type=Path,
        default=repo_root / "data" / "learning-plans" / "standard-3y-2026.json",
    )
    parser.add_argument(
        "--source-commit",
        required=True,
        help="固定审核提交，例如 370dc94 或完整提交 SHA",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "data" / "learning-plans" / "standard-3y-2026.review.json",
    )
    parser.add_argument("--verify", action="store_true", help="只核验已有审核清单，不重新生成")
    args = parser.parse_args()
    plan = json.loads(args.input.read_text(encoding="utf-8"))
    resolved_commit = git_commit(repo_root, args.source_commit)
    if args.verify:
        manifest = json.loads(args.output.read_text(encoding="utf-8"))
        verify_review_manifest(
            manifest,
            plan=plan,
            source_json=args.input,
            expected_source_commit=resolved_commit,
        )
        print(json.dumps({"status": manifest.get("status"), "source_commit": resolved_commit, "source_json_sha256": sha256_file(args.input)}, ensure_ascii=False, indent=2))
        return 0
    manifest = build_review_manifest(plan, source_commit=resolved_commit, source_json=args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "source_commit": resolved_commit, "source_json_sha256": manifest["source_json_sha256"], "checkpoint_count": manifest["required_checkpoint_count"], "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
