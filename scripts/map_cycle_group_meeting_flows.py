"""Map learning-plan template cycles to formal group-meeting flow documents.

The resolver uses ``cohort_month`` as an opening-month template and
``learning_cycle_index`` as the class-relative cycle number.  The source
``nominal_calendar_month`` is copied as evidence only and is never a lookup
key, so a delayed class meeting cannot silently switch a cohort's flow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from learning_plan_semantics import (
    COHORT_TEMPLATE_MONTHS,
    cohort_template_label,
    learning_cycle_label,
)


DEFAULT_PLAN = Path("data/learning-plans/standard-3y-2026.json")
DEFAULT_FLOWS = Path("data/learning-plans/group-meeting-flows-2026.1.json")
DEFAULT_OUTPUT = Path("data/learning-plans/cycle-flow-mapping-2026.1.json")
IGNORED_FLOW_STATUSES = {"DUPLICATE_SUPERSEDED"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_mapping(plan_path: Path, flows_path: Path) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    flows_catalog = json.loads(flows_path.read_text(encoding="utf-8"))
    flows = flows_catalog.get("flows", [])
    entries: list[dict[str, Any]] = []
    for track in plan.get("cohort_tracks", []):
        cohort_month = int(track["cohort_month"])
        if cohort_month not in COHORT_TEMPLATE_MONTHS:
            raise ValueError(f"非法开班月份模板: {cohort_month}")
        for cycle in track.get("cycles", []):
            cycle_index = int(cycle["cycle_index"])
            year_index = int(cycle["year_index"])
            candidates = [
                flow
                for flow in flows
                if flow.get("year_index") == year_index
                and flow.get("cycle_index") == cycle_index
                and cohort_month in (flow.get("eligible_cohort_months") or [])
                and flow.get("status") not in IGNORED_FLOW_STATUSES
            ]
            status = "MAPPED" if len(candidates) == 1 else (
                "MAPPING_CONFLICT" if candidates else "MAPPING_MISSING"
            )
            entries.append(
                {
                    "mapping_key": f"{cohort_month}-{cycle_index}",
                    "template_key": f"COHORT_MONTH_{cohort_month:02d}",
                    "template_label": cohort_template_label(cohort_month),
                    "cohort_month": cohort_month,
                    "cycle_index": cycle_index,
                    "learning_cycle_index": cycle_index,
                    "learning_cycle_label": learning_cycle_label(cohort_month, cycle_index),
                    "year_index": year_index,
                    "year_cycle_index": cycle.get("year_cycle_index"),
                    "nominal_calendar_month": cycle.get("nominal_calendar_month"),
                    "status": status,
                    "flow_key": candidates[0].get("flow_key") if len(candidates) == 1 else None,
                    "candidate_flow_keys": [flow.get("flow_key") for flow in candidates],
                    "candidate_source_files": [
                        flow.get("source", {}).get("relative_path") for flow in candidates
                    ],
                    "lookup_key": {
                        "plan_version": plan.get("version_label"),
                        "template_key": f"COHORT_MONTH_{cohort_month:02d}",
                        "cohort_month": cohort_month,
                        "learning_cycle_index": cycle_index,
                        "year_index": year_index,
                    },
                    "calendar_month_used_as_primary_key": False,
                }
            )
    quality = {
        "entry_count": len(entries),
        "template_count": len(plan.get("cohort_tracks", [])),
        "template_cycle_definition": "4个开班月份模板 × 36学习周期",
        "mapped_count": sum(entry["status"] == "MAPPED" for entry in entries),
        "missing_count": sum(entry["status"] == "MAPPING_MISSING" for entry in entries),
        "conflict_count": sum(entry["status"] == "MAPPING_CONFLICT" for entry in entries),
        "calendar_month_used_as_primary_key": False,
    }
    return {
        "schema_version": 1,
        "plan_key": plan.get("plan_key"),
        "version_label": "2026.1",
        "status": "DRAFT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_plan": {
            "filename": plan_path.name,
            "sha256": sha256_file(plan_path),
        },
        "source_flows": {
            "filename": flows_path.name,
            "sha256": sha256_file(flows_path),
            "source_inventory_sha256": flows_catalog.get("source_inventory_sha256"),
        },
        "quality_report": quality,
        "mappings": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--flows", type=Path, default=DEFAULT_FLOWS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_mapping(args.plan, args.flows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["quality_report"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
