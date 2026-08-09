"""Export a privacy-safe activity bundle from the legacy operations database.

The script never exports names, phone numbers, notes, reflections, evaluations,
free-text shares, financial data, role grants, or passwords. It uses the stable
``member_code`` identifier to let the unified platform resolve each fact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text


SOURCE_SYSTEM = "seiwajyuku_system"
SOURCES = {
    "group_sessions": {
        "date": "session_date",
        "title": "theme",
        "status": "attendance",
        "duration": "duration_minutes",
    },
    "class_sessions": {
        "date": "session_date",
        "title": "theme",
        "status": "attendance",
    },
    "courses": {
        "date": "course_date",
        "title": "course_name",
        "status": "attendance",
    },
    "report_meetings": {
        "date": "meeting_date",
        "title": "meeting_name",
        "status": "attendance",
    },
    "study_tours": {
        "date": "tour_date",
        "title": "destination",
        "duration": "duration_days",
        "duration_multiplier": 1440,
    },
    "reading_checkins": {
        "date": "checkin_date",
        "title": "book_name",
        "duration": "duration_minutes",
        "where": "audio_completed=1",
        "updated": "source_updated_at",
    },
    "reading_shares": {
        "date": "share_date",
        "title": "book_name",
        "duration": "duration_minutes",
    },
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _status(value: Any, source_table: str) -> str:
    if source_table in {"study_tours", "reading_checkins", "reading_shares"}:
        return "COMPLETED"
    normalized = str(value or "").strip().lower()
    if normalized in {"present", "attended", "completed", "已签到", "出席", "参加"}:
        return "PRESENT"
    if normalized in {"absent", "未签到", "缺席"}:
        return "ABSENT"
    return "RECORDED"


def _select_sql(table: str, config: dict[str, Any]) -> str:
    duration = config.get("duration")
    updated = config.get("updated")
    multiplier = int(config.get("duration_multiplier") or 1)
    status = config.get("status")
    return (
        "SELECT a.id, i.identifier_value AS member_code, "
        f"a.{config['date']} AS occurred_on, "
        f"a.{config['title']} AS title, "
        + (f"a.{status} AS raw_status, " if status else "NULL AS raw_status, ")
        + (
            f"a.{duration} * {multiplier} AS duration_minutes, "
            if duration
            else "NULL AS duration_minutes, "
        )
        + (f"a.{updated} AS source_updated_at " if updated else "NULL AS source_updated_at ")
        + f"FROM {table} a LEFT JOIN member_identifiers i "
        "ON i.member_id=a.member_id AND i.identifier_type='member_code' "
        + (f"WHERE {config['where']} " if config.get("where") else "")
        + "ORDER BY a.id"
    )


def export_bundle(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url)
    existing_tables = set(inspect(engine).get_table_names())
    if "member_identifiers" not in existing_tables:
        raise RuntimeError("旧系统缺少 member_identifiers，不能按稳定学员编号安全合并")
    facts: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table, config in SOURCES.items():
            if table not in existing_tables:
                counts[table] = 0
                continue
            rows = connection.execute(text(_select_sql(table, config))).mappings()
            table_count = 0
            for row in rows:
                occurred_on = _json_value(row["occurred_on"])
                if not occurred_on:
                    continue
                facts.append(
                    {
                        "source_table": table,
                        "external_id": str(row["id"]),
                        "member_code": row["member_code"],
                        "occurred_on": str(occurred_on)[:10],
                        "participation_status": _status(row["raw_status"], table),
                        "title": row["title"],
                        "duration_minutes": row["duration_minutes"],
                        "source_updated_at": _json_value(row["source_updated_at"]),
                    }
                )
                table_count += 1
            counts[table] = table_count
    engine.dispose()
    return {
        "bundle_version": 1,
        "source_system": SOURCE_SYSTEM,
        "generated_at": datetime.now(UTC).isoformat(),
        "privacy_contract": {
            "matching_key": "member_code",
            "contains_names": False,
            "contains_phones": False,
            "contains_narratives": False,
        },
        "source_counts": counts,
        "facts": facts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导出旧盛和塾运营管理系统的隐私安全行为事实合并包"
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("LEGACY_DATABASE_URL", ""),
        help="旧系统数据库 URL；建议通过 LEGACY_DATABASE_URL 提供",
    )
    parser.add_argument("--output", required=True, help="输出 JSON 文件")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("缺少 --database-url 或 LEGACY_DATABASE_URL")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = export_bundle(args.database_url)
    output.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "fact_count": len(bundle["facts"]),
                "source_counts": bundle["source_counts"],
                "privacy_safe": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
