#!/usr/bin/env python3
"""Load C2A results into a disposable SQLite staging database only.

The database path must be inside ``.codex-tmp``.  This is a staging proof for
the existing 0065 tables; it deliberately has no historical-ledger writer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _safe_database_path(value: Path) -> Path:
    path = value.resolve()
    private_root = (REPO_ROOT / ".codex-tmp").resolve()
    if private_root not in path.parents or path.suffix.lower() != ".db":
        raise ValueError("隔离 staging 数据库必须位于仓库 .codex-tmp 下且扩展名为 .db")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    database = _safe_database_path(args.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        raise SystemExit(f"拒绝覆盖已有隔离数据库，请换一个新路径: {database}")

    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    os.environ["ALLOW_PRODUCTION_MUTATIONS"] = "false"
    os.environ["IDENTITY_AUTHORIZATION_ENABLED"] = "true"
    os.environ["IDENTITY_ADMIN_WRITES_ENABLED"] = "true"
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

    from app.db import execute, transaction  # noqa: E402
    from app.migrations import run_migrations  # noqa: E402
    from app.services.historical_credit_import import register_suzhou_credit_workbook  # noqa: E402
    from app.services.historical_credit_matching import (  # noqa: E402
        apply_matches_to_staging,
        build_historical_class_mappings,
        match_historical_rows,
    )
    from scripts.run_g5_4_c2a_dry_run import build_dry_run  # noqa: E402

    run_migrations()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    tables = snapshot.get("tables", snapshot)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        org_rows = sorted(
            tables.get("org_units", []),
            key=lambda row: (
                {"ROOT": 0, "REGIONAL_CENTER": 1, "OPERATING_UNIT": 2, "CLASS": 3, "GROUP": 4}.get(
                    str(row.get("unit_type")), 5
                ),
                str(row.get("id")),
            ),
        )
        inserted_org_ids: set[str] = set()
        for row in org_rows:
            parent_id = str(row.get("parent_id")) if row.get("parent_id") else None
            if parent_id not in inserted_org_ids:
                parent_id = None
            execute(
                connection,
                "INSERT OR IGNORE INTO org_units "
                "(id, unit_code, name, unit_type, parent_id, active_from, active_until, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(row["id"]),
                    f"SNAPSHOT-{row['id']}",
                    row.get("name") or str(row["id"]),
                    row.get("unit_type") or "OPERATING_UNIT",
                    parent_id,
                    row.get("active_from"),
                    row.get("active_until"),
                    int(row.get("is_active") or 0),
                    now,
                    now,
                ),
            )
            inserted_org_ids.add(str(row["id"]))
        class_by_member: dict[str, str] = {}
        for relation in tables.get("member_org_relations", []):
            if relation.get("relation_type") == "STUDY_CLASS":
                class_by_member.setdefault(str(relation["member_id"]), str(relation["org_unit_id"]))
        for row in tables.get("members", []):
            member_id = int(row["id"])
            class_id = class_by_member.get(str(member_id))
            if not class_id:
                continue
            execute(
                connection,
                "INSERT OR IGNORE INTO members "
                "(id, member_code, name, org_unit_id, status, sensitivity_level, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'INTERNAL', ?, ?)",
                (member_id, row.get("member_code") or f"SNAPSHOT-{member_id}", row.get("name") or "", class_id, row.get("status") or "ACTIVE", now, now),
            )

    parsed_result = build_dry_run(workbook_path=args.workbook, snapshot_path=args.snapshot)
    parsed = json.loads(args.snapshot.read_text(encoding="utf-8"))
    from app.services.historical_credit_import import parse_suzhou_credit_workbook  # noqa: E402
    source = parse_suzhou_credit_workbook(args.workbook.read_bytes(), source_year=2026)
    registered = register_suzhou_credit_workbook(
        content=args.workbook.read_bytes(), original_filename=args.workbook.name, actor_user_id=None
    )
    batch_id = int(registered["batch"]["id"])
    mappings = build_historical_class_mappings(snapshot=parsed, source_rows=source["rows"])
    matches = match_historical_rows(snapshot=parsed, source_rows=source["rows"], class_mappings=mappings)
    before = None
    after = None
    with transaction() as connection:
        before = int(execute(connection, "SELECT COUNT(*) AS count FROM learning_credit_entries").fetchone()["count"])
        staging = apply_matches_to_staging(
            connection,
            batch_id=batch_id,
            results=matches,
            snapshot_id=snapshot.get("snapshot_id"),
            snapshot_fingerprint=snapshot.get("fingerprint"),
        )
        after = int(execute(connection, "SELECT COUNT(*) AS count FROM learning_credit_entries").fetchone()["count"])
        row_counts = {
            row["match_status"]: int(row["count"])
            for row in execute(
                connection,
                "SELECT match_status, COUNT(*) AS count FROM learning_credit_import_rows WHERE batch_id=? GROUP BY match_status",
                (batch_id,),
            ).fetchall()
        }
        matched_items = int(
            execute(
                connection,
                "SELECT COUNT(*) AS count FROM learning_credit_import_items WHERE batch_id=? AND matched_member_id IS NOT NULL",
                (batch_id,),
            ).fetchone()["count"]
        )
    output = {
        "mode": "DRY_RUN_STAGING",
        "database": str(database),
        "batch_id": batch_id,
        "parsed_summary": source["summary"],
        "matching_summary": parsed_result["matching"]["summary"],
        "staging": {"row_match_status_counts": row_counts, "matched_item_count": matched_items, **staging},
        "safety": {
            "ledger_count_before": before,
            "ledger_count_after": after,
            "ledger_entries_delta": after - before,
            "production_writes": 0,
            "learning_credit_entries_writes": 0,
            "settlement_enabled": False,
        },
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if output["safety"]["ledger_entries_delta"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
