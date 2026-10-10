"""Read-only credit years: twelve confirmed class learning days per year."""
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.db import execute
from app.services.learning_cycle_schedule import parse_utc_datetime
from app.services.learning_cycles import _active_binding, _cycle_at
from app.services.wechat_learning import _current_relations, _optional_rows, _now_for_database


def learning_year_summary(connection, member_id, opening):
    now = datetime.now(UTC)
    cache = {}
    progress_cache = {}

    def complete_progress(binding_id):
        if binding_id not in progress_cache:
            row = execute(connection,
                "SELECT MIN(learning_cycle_index) AS first_index FROM class_learning_cycles WHERE binding_id=?",
                (binding_id,)).fetchone()
            progress_cache[binding_id] = row['first_index'] == 1
        return progress_cache[binding_id]

    def held_days(binding_id):
        if binding_id not in cache:
            rows = _optional_rows(connection,
                "SELECT id,actual_class_meeting_at FROM class_learning_cycles "
                "WHERE binding_id=? AND class_meeting_status='HELD' "
                "AND cycle_status IN ('OPEN','CLOSED') AND actual_class_meeting_at IS NOT NULL",
                (binding_id,))
            cache[binding_id] = sorted(
                (parse_utc_datetime(r["actual_class_meeting_at"]), int(r["id"])) for r in rows
                if parse_utc_datetime(r["actual_class_meeting_at"]) <= now)
        return cache[binding_id]

    try:
        relations = _current_relations(connection, member_id)
    except Exception as exc:
        # Match the existing read-only legacy table compatibility, not SQL errors.
        if not any(text in str(exc).lower() for text in ("no such table", "doesn't exist", "does not exist")):
            raise
        relations = []
    classes = {str(r["org_unit_id"] if r["relation_type"] == "STUDY_CLASS" else r["parent_id"])
               for r in relations if r["relation_type"] == "STUDY_CLASS" or r.get("parent_id")}
    current_years = set()
    current_days = set()
    for class_id in classes:
        binding = _active_binding(connection, class_id)
        cycle = _cycle_at(connection, int(binding["id"]), _now_for_database(connection)) if binding else None
        if not cycle or not complete_progress(int(binding['id'])):
            current_years.add(None)
            continue
        count = len(held_days(int(binding["id"])))
        current_years.add(count // 12 + 1)
        current_days.add(count % 12)
    current = next(iter(current_years)) if len(current_years) == 1 else None

    columns = {c[0] for c in execute(connection, "SELECT * FROM learning_credit_entries WHERE 1=0").description}
    rows = []
    if {"learning_cycle_id", "reversal_of_entry_id"} <= columns:
        rows = _optional_rows(connection,
            "SELECT lc.id AS cycle_id,lc.binding_id,lc.opened_at,lc.actual_class_meeting_at, "
            "SUM(e.points) AS points,COUNT(*) AS entry_count FROM learning_credit_entries e "
            "LEFT JOIN learning_credit_entries original ON original.id=e.reversal_of_entry_id "
            "AND original.member_id=e.member_id AND original.status IN ('POSTED','REVERSED') "
            "LEFT JOIN class_learning_cycles lc ON lc.id=COALESCE(e.learning_cycle_id,original.learning_cycle_id) "
            "WHERE e.member_id=? AND e.status IN ('POSTED','REVERSED') "
            "GROUP BY lc.id,lc.binding_id,lc.opened_at,lc.actual_class_meeting_at", (member_id,))
    totals = {1: Decimal(0), 2: Decimal(0), 3: Decimal(0)}
    allocated_count = 0
    for row in rows:
        if not row["cycle_id"] or not row["binding_id"] or not complete_progress(int(row['binding_id'])):
            continue
        days = held_days(int(row["binding_id"]))
        position = next((i for i, (_, cid) in enumerate(days) if cid == int(row["cycle_id"])), None)
        if position is not None:
            # Learning day 12 is retained in year 1; day 13 belongs to year 2.
            year = position // 12 + 1
        elif row["opened_at"]:
            opened = parse_utc_datetime(row["opened_at"])
            if opened > now:
                continue
            year = sum(at <= opened for at, _ in days) // 12 + 1
        else:
            continue
        totals[year] = totals.get(year, Decimal(0)) + Decimal(str(row["points"]))
        allocated_count += int(row["entry_count"])
    if current:
        totals.setdefault(current, Decimal(0))
    # Reading/share facts record a frozen binding and a business date, rather
    # than a cycle FK. Attribute only periods wholly within one learning year.
    if {'learning_cycle_id', 'reversal_of_entry_id', 'rule_snapshot_json', 'class_org_unit_id'} <= columns:
        precision = 'COALESCE(original.occurred_precision,e.occurred_precision)' if 'occurred_precision' in columns else "'EXACT_DATE'"
        dated = execute(connection,
            "SELECT e.points,COALESCE(original.rule_snapshot_json,e.rule_snapshot_json) AS snapshot, "
            "COALESCE(original.class_org_unit_id,e.class_org_unit_id) AS class_id, "
            "COALESCE(original.occurred_at,e.occurred_at) AS occurred_at," + precision + " AS time_precision "
            "FROM learning_credit_entries e LEFT JOIN learning_credit_entries original "
            "ON original.id=e.reversal_of_entry_id AND original.member_id=e.member_id "
            "AND original.status IN ('POSTED','REVERSED') "
            "WHERE e.member_id=? AND e.status IN ('POSTED','REVERSED') "
            "AND COALESCE(e.learning_cycle_id,original.learning_cycle_id) IS NULL", (member_id,)).fetchall()
        for row in dated:
            try:
                snapshot = json.loads(row['snapshot'] or '{}')
            except (ValueError, TypeError, RecursionError):
                continue
            if not isinstance(snapshot, dict) or type(snapshot.get('binding_id')) is not int:
                continue
            binding = execute(connection, 'SELECT * FROM class_learning_bindings WHERE id=? AND class_org_unit_id=?',
                              (snapshot['binding_id'], row['class_id'])).fetchone()
            if not binding or not complete_progress(int(binding['id'])) or row['time_precision'] != 'EXACT_DATE':
                continue
            try:
                # These facts explicitly retain the original Shanghai business
                # date. A bare calendar year/month never fabricates a date.
                occurred = date.fromisoformat(str(snapshot.get('occurred_on') or '')[:10])
            except ValueError:
                continue
            start = datetime.combine(occurred, time.min, ZoneInfo('Asia/Shanghai')).astimezone(UTC)
            end = start + timedelta(days=1)
            if start > now or parse_utc_datetime(binding['started_at']) >= end:
                continue
            days = held_days(int(binding['id']))
            year = sum(at <= start for at, _ in days) // 12 + 1
            last_year = sum(at < end for at, _ in days) // 12 + 1
            if year != last_year:
                continue  # Date-only fact straddles an annual boundary: unknown.
            totals[year] = totals.get(year, Decimal(0)) + Decimal(str(row['points']))
            allocated_count += 1
    ledger = execute(connection,
        "SELECT COUNT(*) AS entry_count,COALESCE(SUM(points),0) AS points "
        "FROM learning_credit_entries WHERE member_id=? AND status IN ('POSTED','REVERSED')",
        (member_id,)).fetchone()
    unallocated_count = int(ledger["entry_count"]) - allocated_count + int(opening["entry_count"])
    unallocated_points = Decimal(str(ledger["points"])) - sum(totals.values()) + Decimal(opening["total_points"])
    return {
        "current_learning_year": current,
        "current_learning_year_points": format(totals[current], ".2f") if current else None,
        "current_learning_year_completed_days": next(iter(current_days)) if current and len(current_days) == 1 else None,
        "learning_days_per_year": 12,
        "learning_years": [{"year_index": i, "points": format(totals[i], ".2f")} for i in sorted(totals)],
        "has_unallocated_learning_year_credits": unallocated_count > 0,
        "unallocated_learning_year_points": format(unallocated_points, ".2f"),
    }
