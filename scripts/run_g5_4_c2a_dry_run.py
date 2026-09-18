#!/usr/bin/env python3
"""Run the private C2A workbook-to-snapshot matching dry-run.

Inputs are a real source workbook and an authorized, sanitized read-only
platform snapshot.  The command emits a private JSON result; it never opens a
production connection and never writes a ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.services.historical_credit_import import (  # noqa: E402
    parse_suzhou_credit_workbook,
    summarize_period_items,
)
from app.services.historical_credit_matching import (  # noqa: E402
    build_historical_class_mappings,
    match_historical_rows,
    summarize_matching,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return value


def build_dry_run(*, workbook_path: Path, snapshot_path: Path) -> dict[str, Any]:
    workbook_bytes = workbook_path.read_bytes()
    parsed = parse_suzhou_credit_workbook(workbook_bytes, source_year=2026)
    snapshot = _read_json(snapshot_path)
    mappings = build_historical_class_mappings(snapshot=snapshot, source_rows=parsed["rows"])
    matches = match_historical_rows(
        snapshot=snapshot,
        source_rows=parsed["rows"],
        class_mappings=mappings,
    )
    class_mapping_counts: dict[str, int] = {}
    for mapping in mappings.values():
        status = str(mapping["mapping_status"])
        class_mapping_counts[status] = class_mapping_counts.get(status, 0) + 1

    by_center: dict[str, dict[str, int]] = {}
    tables = snapshot.get("tables", snapshot)
    class_by_id = {str(item.get("id")): item for item in tables.get("class_candidates", [])}
    for result in matches:
        mapping = result["class_mapping"]
        class_id = str(mapping.get("org_unit_id") or "")
        class_row = class_by_id.get(class_id, {})
        center = str(class_row.get("parent_id") or result.get("source_sheet") or "UNKNOWN")
        class_name = str(class_row.get("name") or result.get("raw_class_name") or "UNCLASSIFIED")
        center_counts = by_center.setdefault(center, {})
        key = f"{class_name}:{result['identity_status']}"
        center_counts[key] = center_counts.get(key, 0) + 1

    match_by_row = {
        (item["source_sheet"], item["source_row_number"]): item for item in matches
    }
    baseline_ready_items = 0
    dual_track_pending_items = 0
    period_unresolved_items = 0
    proposed_points = 0.0
    all_item_points = 0.0
    for source_row in parsed["rows"]:
        match = match_by_row[(source_row["source_sheet"], source_row["source_row_number"])]
        for item in source_row["items"]:
            points = float(item.get("points") or 0)
            all_item_points += points
            period_track = item["metadata"].get("period_track")
            if period_track == "HISTORICAL_BASELINE":
                if match["identity_ready"] and match["credit_ready"]:
                    baseline_ready_items += 1
                    proposed_points += points
            elif period_track == "DUAL_TRACK_PENDING":
                dual_track_pending_items += 1
            elif period_track == "UNCLASSIFIED_PERIOD_REVIEW":
                period_unresolved_items += 1

    result = {
        "mode": "DRY_RUN",
        "workbook": {
            "path": str(workbook_path),
            "sha256": hashlib.sha256(workbook_bytes).hexdigest(),
            "summary": parsed["summary"],
        },
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "captured_at": snapshot.get("captured_at"),
            "source_revision": snapshot.get("source_revision"),
            "fingerprint": snapshot.get("fingerprint"),
            "row_counts": snapshot.get("row_counts"),
        },
        "class_mapping": {
            "counts": class_mapping_counts,
            "rows": sorted(mappings.values(), key=lambda item: (item["source_sheet"], item["raw_class_name"] or "")),
        },
        "matching": {
            "summary": summarize_matching(matches),
            "identity_blocked_rows": sum(not item["identity_ready"] for item in matches),
            "credit_validation_blocked_rows": sum(not item["credit_ready"] for item in matches),
            "historical_baseline_ready_items": baseline_ready_items,
            "dual_track_pending_items": dual_track_pending_items,
            "period_unresolved_items": period_unresolved_items,
            "proposed_points": int(proposed_points) if proposed_points.is_integer() else proposed_points,
            "blocked_points": int(all_item_points - proposed_points)
            if (all_item_points - proposed_points).is_integer()
            else all_item_points - proposed_points,
            "by_center_and_class": by_center,
            "rows": matches,
        },
        "period_analysis": {
            "unclassified_item_count": parsed["summary"]["period_track_counts"]["UNCLASSIFIED_PERIOD_REVIEW"],
            "groups": summarize_period_items(parsed["items"]),
            "classification": {
                "period_resolution": "YEAR_ONLY_CONFIRMED",
                "ledger_review": "PERIOD_REVIEW_REQUIRED",
                "occurred_on_generated": False,
            },
        },
        "safety": {
            "production_queries": 0,
            "production_writes": 0,
            "learning_credit_entries_writes": 0,
            "ledger_entries_delta": 0,
            "settlement_enabled": False,
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_dry_run(workbook_path=args.workbook, snapshot_path=args.snapshot)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({
            "output": str(args.output),
            "summary": result["matching"]["summary"],
            "period_item_count": result["period_analysis"]["unclassified_item_count"],
            "ledger_entries_delta": result["safety"]["ledger_entries_delta"],
        }, ensure_ascii=False, indent=2))
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
