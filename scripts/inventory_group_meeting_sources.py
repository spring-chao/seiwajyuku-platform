"""Inventory the frozen 2026 group-meeting source folder.

This is intentionally a read-only inventory step.  It records the files that
are eligible for the L1.2-C parser and the files kept outside the parser's
whitelist.  No database or source document is modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SOURCE_ROOT = Path(r"E:\班级运营资料\6.班会+小组会学习会流程")
DEFAULT_OUTPUT = Path("data/learning-plans/group-meeting-source-inventory-2026.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(relative_path: str) -> tuple[bool, str]:
    """Return (included, reason) using the frozen 2026 whitelist.

    The 2026 folders contain combined class/group meeting workbooks.  They are
    included because the parser extracts only the group-meeting section.  The
    2025 folders, support spreadsheets/images and explicitly incomplete drafts
    remain visible in the inventory but cannot become runtime source files.
    """

    parts = Path(relative_path).parts
    top = parts[0] if parts else ""
    if "【2026版】" not in top:
        return False, "HISTORICAL_OR_NON_2026_FOLDER"
    if "待完善" in relative_path or "草稿" in relative_path:
        return False, "DRAFT_OR_INCOMPLETE"
    suffix = Path(relative_path).suffix.lower()
    if suffix != ".docx":
        return False, "UNSUPPORTED_SOURCE_TYPE"
    if "行动计划表" in relative_path:
        return False, "SUPPORTING_DOCUMENT_NOT_MONTHLY_FLOW"
    return True, "FORMAL_2026_FLOW_DOCUMENT"


def build_inventory(source_root: Path) -> dict:
    if not source_root.is_dir():
        raise FileNotFoundError(f"source folder not found: {source_root}")
    files = sorted(path for path in source_root.rglob("*") if path.is_file())
    included: list[dict] = []
    excluded: list[dict] = []
    for path in files:
        relative = path.relative_to(source_root).as_posix()
        allowed, reason = classify(relative)
        item = {
            "relative_path": relative,
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "classification": reason,
        }
        (included if allowed else excluded).append(item)
    return {
        "schema_version": 1,
        "source_folder": str(source_root),
        "whitelist": {
            "top_level_marker": "【2026版】",
            "extensions": [".docx"],
            "excluded_reasons": [
                "HISTORICAL_OR_NON_2026_FOLDER",
                "DRAFT_OR_INCOMPLETE",
                "UNSUPPORTED_SOURCE_TYPE",
                "SUPPORTING_DOCUMENT_NOT_MONTHLY_FLOW",
            ],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_files": len(files),
            "included_files": len(included),
            "excluded_files": len(excluded),
            "included_extensions": dict(Counter(item["extension"] for item in included)),
            "excluded_by_reason": dict(Counter(item["classification"] for item in excluded)),
        },
        "included_files": included,
        "excluded_files": excluded,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_inventory(args.source_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
