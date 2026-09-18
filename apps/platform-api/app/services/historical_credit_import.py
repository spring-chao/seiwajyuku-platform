"""C1 historical credit workbook staging and dry-run services.

The workbook is treated as a source document, not as a score calculator.  The
parser preserves every non-zero credit cell as an auditable import item,
separates confirmed source totals from rows that need review, and never writes
``learning_credit_entries``.  Member matching is deliberately an interface:
without an authoritative historical roster, rows remain unmatched.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, Protocol

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit


HISTORICAL_IMPORT_TYPE = "LEGACY_SUZHOU_CREDIT_WORKBOOK"
HISTORICAL_SOURCE_RULE_VERSION = "LEGACY_SUZHOU_2026_V1"
HISTORICAL_SOURCE_NAME = "苏州分中心2026年学分"

VALIDATION_PASS = "PASS"
VALIDATION_TOTAL_MISSING = "TOTAL_MISSING"
VALIDATION_DETAIL_MISSING = "DETAIL_MISSING"
VALIDATION_MISMATCH = "MISMATCH"
VALIDATION_ZERO = "ZERO"

PERIOD_RESOLVED = "PERIOD_RESOLVED"
YEAR_ONLY_CONFIRMED = "YEAR_ONLY_CONFIRMED"
PERIOD_REVIEW_REQUIRED = "PERIOD_REVIEW_REQUIRED"
PERIOD_CONFLICT = "CONFLICT"

LEGACY_TYPES = {
    "每日读书": ("STANDARD_LEARNING", "LEGACY_READING_AND_SHARE"),
    "班级学习日": ("STANDARD_LEARNING", "LEGACY_CLASS_MEETING"),
    "小组学习会": ("STANDARD_LEARNING", "LEGACY_GROUP_MEETING"),
    "线上课程": ("STANDARD_LEARNING", "LEGACY_ONLINE_COURSE"),
    "线下课程": ("EXTENSION_ACTIVITY", "LEGACY_OFFLINE_COURSE"),
    "报告会": ("EXTENSION_ACTIVITY", "LEGACY_REPORT_EVENT"),
    "游学": ("EXTENSION_ACTIVITY", "LEGACY_STUDY_TOUR"),
}


class MemberMatchingService(Protocol):
    """Future matching boundary; implementations must require authority."""

    def match_rows(self, *, batch_id: int) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.startswith("="):
        text = text[1:].strip()
        if not re.fullmatch(r"[0-9+\-*/().\s]+", text):
            return None
        try:
            tree = ast.parse(text, mode="eval")
            allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub,
                       ast.Mult, ast.Div, ast.UAdd, ast.USub, ast.Constant,
                       ast.Load, ast.Pow)
            if any(not isinstance(node, allowed) for node in ast.walk(tree)):
                return None
            result = eval(compile(tree, "<excel-number>", "eval"), {"__builtins__": {}}, {})
            return float(result) if isinstance(result, (int, float)) else None
        except (SyntaxError, ValueError, ZeroDivisionError, TypeError):
            return None
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return float(text)
    return None


def _clean_points(value: float | None) -> int | float | None:
    if value is None:
        return None
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


def _cell_value(ws: Any, row: int, column: int, merged: dict[tuple[int, int], Any]) -> Any:
    value = ws.cell(row, column).value
    if value is not None:
        return value
    return merged.get((row, column))


def _merged_values(ws: Any, data_ws: Any) -> dict[tuple[int, int], Any]:
    values: dict[tuple[int, int], Any] = {}
    for merged_range in ws.merged_cells.ranges:
        min_col, min_row, max_col, max_row = merged_range.bounds
        value = data_ws.cell(min_row, min_col).value
        for row in range(min_row, max_row + 1):
            for column in range(min_col, max_col + 1):
                values[(row, column)] = value
    return values


def _header_column(ws: Any, label: str) -> int | None:
    for column in range(1, ws.max_column + 1):
        if _text(ws.cell(2, column).value) == label:
            return column
    return None


def _total_column(ws: Any) -> int | None:
    for column in range(1, ws.max_column + 1):
        value = _text(ws.cell(3, column).value)
        if value == "总分值":
            return column
    return None


def _sum_bounds(formula: Any) -> tuple[int, int] | None:
    if not isinstance(formula, str):
        return None
    match = re.search(r"SUM\(\$?([A-Z]+)\d+:\$?([A-Z]+)\d+\)", formula.upper())
    if not match:
        return None
    return column_index_from_string(match.group(1)), column_index_from_string(match.group(2))


def _section_kind(section: str | None) -> tuple[str, str]:
    value = section or ""
    for key, result in LEGACY_TYPES.items():
        if key in value:
            return result
    raise ValueError(f"无法识别历史学分栏目：{section!r}")


def _source_month(header: str | None) -> int | None:
    if not header:
        return None
    match = re.search(r"(\d{1,2})\s*月", header)
    if not match:
        return None
    month = int(match.group(1))
    return month if 1 <= month <= 12 else None


def _period_track(month: int | None) -> str:
    if month is None:
        return "UNCLASSIFIED_PERIOD_REVIEW"
    if 1 <= month <= 8:
        return "HISTORICAL_BASELINE"
    if month == 9:
        return "DUAL_TRACK_PENDING"
    return "FUTURE_OR_UNCLASSIFIED"


def classify_period(*, month: int | None, source_year: int) -> dict[str, Any]:
    """Classify the evidence without manufacturing a calendar date.

    A workbook with a trusted source year proves the year for an unlabelled
    item, but it does not prove a month.  Such items remain blocked for any
    ledger operation that requires ``occurred_on``.
    """

    if month is not None:
        return {
            "period_resolution_status": PERIOD_RESOLVED,
            "period_review_status": "READY",
            "source_year": source_year,
        }
    return {
        "period_resolution_status": YEAR_ONLY_CONFIRMED,
        "period_review_status": PERIOD_REVIEW_REQUIRED,
        "source_year": source_year,
    }


def summarize_period_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate every parsed item for a reviewable period report."""

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        metadata = item.get("metadata") or {}
        if metadata.get("period_track") != "UNCLASSIFIED_PERIOD_REVIEW":
            continue
        key = (
            str(item.get("legacy_credit_type") or ""),
            str(item.get("source_sheet") or ""),
            str(item.get("source_column_name") or ""),
        )
        current = grouped.setdefault(
            key,
            {
                "legacy_credit_type": key[0],
                "source_sheet": key[1],
                "source_column_name": key[2],
                "count": 0,
                "points_total": 0,
                "period_resolution_statuses": set(),
                "period_review_statuses": set(),
            },
        )
        current["count"] += 1
        current["points_total"] += float(item.get("points") or 0)
        current["period_resolution_statuses"].add(
            metadata.get("period_resolution_status", YEAR_ONLY_CONFIRMED)
        )
        current["period_review_statuses"].add(
            metadata.get("period_review_status", PERIOD_REVIEW_REQUIRED)
        )
    result = []
    for value in grouped.values():
        value["points_total"] = _clean_points(value["points_total"])
        value["period_resolution_statuses"] = sorted(value["period_resolution_statuses"])
        value["period_review_statuses"] = sorted(value["period_review_statuses"])
        result.append(value)
    return sorted(result, key=lambda item: (item["legacy_credit_type"], item["source_sheet"], item["source_column_name"]))


def _parse_sheet(ws: Any, data_ws: Any, *, source_year: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    name_column = _header_column(ws, "姓名")
    if name_column is None:
        raise ValueError(f"工作表 {ws.title} 缺少姓名列")
    group_column = _header_column(ws, "组别")
    class_column = _header_column(ws, "班级")
    total_column = _total_column(ws)
    if total_column is None:
        raise ValueError(f"工作表 {ws.title} 缺少总分值列")

    merged = _merged_values(ws, data_ws)
    first_data_row = 5
    sum_bounds = _sum_bounds(ws.cell(first_data_row, total_column).value)
    detail_start, detail_end = sum_bounds or (total_column + 1, ws.max_column)
    detail_end = min(detail_end, ws.max_column)

    section_by_column: dict[int, str | None] = {}
    current_section: str | None = None
    for column in range(detail_start, detail_end + 1):
        candidate = _text(_cell_value(ws, 2, column, merged))
        if candidate:
            current_section = candidate
        section_by_column[column] = current_section

    rows: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for row_number in range(first_data_row, ws.max_row + 1):
        raw_name = _text(_cell_value(ws, row_number, name_column, merged))
        if not raw_name:
            continue
        raw_class_name = _text(_cell_value(ws, row_number, class_column, merged)) if class_column else ws.title
        raw_group_name = _text(_cell_value(ws, row_number, group_column, merged)) if group_column else None
        raw_total = _number(data_ws.cell(row_number, total_column).value)
        total_formula = ws.cell(row_number, total_column).value
        if raw_total is None and _sum_bounds(total_formula):
            # Formula caches can be absent in a workbook saved without a full
            # recalculation.  The row remains traceable while its total is
            # derived only from the parsed detail cells.
            total_from_formula = True
        else:
            total_from_formula = False

        calculated = 0.0
        unparsed_cells: list[str] = []
        row_items: list[dict[str, Any]] = []
        for column in range(detail_start, detail_end + 1):
            formula_value = ws.cell(row_number, column).value
            raw_value = data_ws.cell(row_number, column).value
            value = raw_value if raw_value is not None else formula_value
            numeric = _number(value)
            if numeric is None:
                if _text(value):
                    unparsed_cells.append(get_column_letter(column))
                continue
            calculated += numeric
            if numeric == 0:
                continue
            section = section_by_column.get(column)
            credit_category, legacy_type = _section_kind(section)
            header = _text(_cell_value(ws, 3, column, merged)) or get_column_letter(column)
            month = _source_month(header)
            row_items.append(
                {
                    "source_sheet": ws.title,
                    "source_column_index": column,
                    "source_column_name": header,
                    "credit_category": credit_category,
                    "legacy_credit_type": legacy_type,
                    "source_month": month,
                    "accounting_month": f"{source_year}-{month:02d}" if month else None,
                    "points": _clean_points(numeric),
                    "metadata": {
                        "section": section,
                        "source_cell": f"{get_column_letter(column)}{row_number}",
                        "formula": formula_value if isinstance(formula_value, str) and formula_value.startswith("=") else None,
                        "period_track": _period_track(month),
                        **classify_period(month=month, source_year=source_year),
                    },
                }
            )
        calculated = round(calculated, 2)
        if raw_total is None and total_from_formula:
            raw_total = calculated
        if raw_total is None:
            validation_status = VALIDATION_TOTAL_MISSING if calculated > 0 else VALIDATION_ZERO
        elif calculated == 0 and raw_total > 0:
            validation_status = VALIDATION_DETAIL_MISSING
        elif abs(raw_total - calculated) > 0.001:
            validation_status = VALIDATION_MISMATCH
        else:
            validation_status = VALIDATION_PASS
        review_status = "NO_CREDIT" if validation_status == VALIDATION_ZERO else (
            "REVIEW_REQUIRED" if validation_status != VALIDATION_PASS else "PENDING"
        )
        rows.append(
            {
                "source_sheet": ws.title,
                "source_row_number": row_number,
                "raw_name": raw_name,
                "raw_class_name": raw_class_name,
                "raw_group_name": raw_group_name,
                "raw_total_points": _clean_points(raw_total),
                "calculated_total_points": _clean_points(calculated),
                "match_status": "NOT_RUN",
                "validation_status": validation_status,
                "review_status": review_status,
                "match_reason": "PLATFORM_MEMBER_SNAPSHOT_REQUIRED",
                "metadata": {
                    "total_formula": total_formula if isinstance(total_formula, str) and total_formula.startswith("=") else None,
                    "unparsed_detail_columns": unparsed_cells,
                    "source_rule_version": HISTORICAL_SOURCE_RULE_VERSION,
                },
                "items": row_items,
            }
        )
        items.extend(row_items)
    return rows, items


def parse_suzhou_credit_workbook(
    content: bytes, *, source_year: int = 2026, source_name: str = HISTORICAL_SOURCE_NAME
) -> dict[str, Any]:
    """Parse a workbook without opening a database connection or writing data."""

    if not content:
        raise ValueError("历史学分工作簿不能为空")
    workbook = load_workbook(BytesIO(content), data_only=False, read_only=False)
    data_workbook = load_workbook(BytesIO(content), data_only=True, read_only=False)
    rows: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for ws in workbook.worksheets:
        data_ws = data_workbook[ws.title]
        sheet_rows, sheet_items = _parse_sheet(ws, data_ws, source_year=source_year)
        rows.extend(sheet_rows)
        items.extend(sheet_items)

    validation_counts = {status: sum(row["validation_status"] == status for row in rows)
                         for status in (VALIDATION_PASS, VALIDATION_TOTAL_MISSING, VALIDATION_DETAIL_MISSING, VALIDATION_MISMATCH, VALIDATION_ZERO)}
    confirmed_source_total = sum(float(row["raw_total_points"] or 0) for row in rows if row["validation_status"] == VALIDATION_PASS)
    review_suggested_total = sum(float(row["calculated_total_points"] or 0) for row in rows if row["validation_status"] != VALIDATION_PASS)
    summary = {
        "source_name": source_name,
        "source_year": source_year,
        "source_rule_version": HISTORICAL_SOURCE_RULE_VERSION,
        "sheet_count": len(workbook.sheetnames),
        "source_row_count": len(rows),
        "nonblank_total_row_count": sum(row["raw_total_points"] is not None for row in rows),
        "blank_total_row_count": sum(row["raw_total_points"] is None for row in rows),
        "nonzero_item_count": len(items),
        "validation_counts": validation_counts,
        "confirmed_source_total": _clean_points(confirmed_source_total),
        "review_suggested_total": _clean_points(review_suggested_total),
        "september_item_count": sum(item["source_month"] == 9 for item in items),
        "period_track_counts": {
            track: sum(item["metadata"].get("period_track") == track for item in items)
            for track in ("HISTORICAL_BASELINE", "DUAL_TRACK_PENDING", "FUTURE_OR_UNCLASSIFIED", "UNCLASSIFIED_PERIOD_REVIEW")
        },
        "period_resolution_counts": {
            status: sum(
                item["metadata"].get("period_resolution_status") == status
                for item in items
            )
            for status in (PERIOD_RESOLVED, YEAR_ONLY_CONFIRMED, PERIOD_CONFLICT)
        },
        "period_review_required_count": sum(
            item["metadata"].get("period_review_status") == PERIOD_REVIEW_REQUIRED
            for item in items
        ),
        "status": "NEEDS_REVIEW",
        "ledger_entries_delta": 0,
    }
    return {"summary": summary, "rows": rows, "items": items}


def _batch_summary(connection: Any, batch_id: int) -> dict[str, Any]:
    batch = execute(connection, "SELECT * FROM learning_credit_import_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise ValueError("历史学分导入批次不存在")
    counts = execute(
        connection,
        "SELECT validation_status, COUNT(*) AS count FROM learning_credit_import_rows "
        "WHERE batch_id=? GROUP BY validation_status ORDER BY validation_status",
        (batch_id,),
    ).fetchall()
    item_count = execute(
        connection, "SELECT COUNT(*) AS count FROM learning_credit_import_items WHERE batch_id=?", (batch_id,)
    ).fetchone()["count"]
    period_counts = execute(
        connection,
        "SELECT occurred_precision, period_review_status, COUNT(*) AS count, COALESCE(SUM(points), 0) AS points "
        "FROM learning_credit_import_items WHERE batch_id=? "
        "GROUP BY occurred_precision, period_review_status ORDER BY occurred_precision, period_review_status",
        (batch_id,),
    ).fetchall()
    decision_count = execute(
        connection, "SELECT COUNT(*) AS count FROM learning_credit_import_decisions WHERE batch_id=?", (batch_id,)
    ).fetchone()["count"]
    data = dict(batch)
    data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
    data["validation_counts"] = {row["validation_status"]: int(row["count"]) for row in counts}
    data["item_count"] = int(item_count)
    data["period_counts"] = [
        {
            "occurred_precision": str(row["occurred_precision"]),
            "period_review_status": str(row["period_review_status"]),
            "count": int(row["count"]),
            "points": _clean_points(float(row["points"] or 0)),
        }
        for row in period_counts
    ]
    data["decision_count"] = int(decision_count)
    data["ledger_entries_delta"] = 0
    return data


def register_suzhou_credit_workbook(
    *, content: bytes, original_filename: str, actor_user_id: int | None = None,
    source_year: int = 2026, source_name: str = HISTORICAL_SOURCE_NAME,
) -> dict[str, Any]:
    parsed = parse_suzhou_credit_workbook(content, source_year=source_year, source_name=source_name)
    digest = hashlib.sha256(content).hexdigest()
    with transaction() as connection:
        existing = execute(
            connection,
            "SELECT id FROM learning_credit_import_batches WHERE file_sha256=? AND import_type=?",
            (digest, HISTORICAL_IMPORT_TYPE),
        ).fetchone()
        if existing:
            return {"batch": _batch_summary(connection, int(existing["id"])), "idempotent": True}
        now = _now()
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_import_batches "
            "(import_type, source_year, source_name, original_filename, file_sha256, source_rule_version, status, metadata_json, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'NEEDS_REVIEW', ?, ?, ?, ?)",
            (HISTORICAL_IMPORT_TYPE, source_year, source_name, original_filename, digest,
             HISTORICAL_SOURCE_RULE_VERSION, json.dumps(parsed["summary"], ensure_ascii=False), actor_user_id, now, now),
        )
        batch_id = int(cursor.lastrowid)
        for row in parsed["rows"]:
            row_cursor = execute(
                connection,
                "INSERT INTO learning_credit_import_rows "
                "(batch_id, source_sheet, source_row_number, raw_name, raw_class_name, raw_group_name, raw_total_points, calculated_total_points, "
                "match_status, validation_status, review_status, match_reason, metadata_json, credit_review_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, row["source_sheet"], row["source_row_number"], row["raw_name"], row["raw_class_name"],
                 row["raw_group_name"], row["raw_total_points"], row["calculated_total_points"], row["match_status"],
                 row["validation_status"], row["review_status"], row["match_reason"], json.dumps(row["metadata"], ensure_ascii=False),
                 "NOT_REQUIRED" if row["validation_status"] == VALIDATION_PASS else "PENDING", now, now),
            )
            for item in row["items"]:
                period = classify_period(month=item["source_month"], source_year=source_year)
                period_track = item["metadata"].get("period_track")
                period_review_status = (
                    "DUAL_TRACK_PENDING" if period_track == "DUAL_TRACK_PENDING"
                    else "READY" if period["period_review_status"] == "READY"
                    else "PERIOD_REVIEW_REQUIRED"
                )
                occurred_precision = "MONTH" if item["source_month"] is not None else "YEAR"
                execute(
                    connection,
                    "INSERT INTO learning_credit_import_items "
                    "(batch_id, import_row_id, source_sheet, source_row_number, source_column_index, source_column_name, credit_category, legacy_credit_type, "
                    "source_month, accounting_month, points, source_rule_version, status, metadata_json, "
                    "occurred_precision, occurred_year, occurred_month, period_review_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_REVIEW', ?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, int(row_cursor.lastrowid), item["source_sheet"], row["source_row_number"], item["source_column_index"], item["source_column_name"],
                     item["credit_category"], item["legacy_credit_type"], item["source_month"], item["accounting_month"],
                     item["points"], HISTORICAL_SOURCE_RULE_VERSION, json.dumps(item["metadata"], ensure_ascii=False),
                     occurred_precision, source_year, item["source_month"], period_review_status, now, now),
                )
        if actor_user_id is not None:
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="learning.historical_credit_import.register",
                resource_type="learning_credit_import_batch",
                resource_id=str(batch_id),
                purpose="登记历史学分来源文件并生成待审核解析结果",
                after={"file_sha256": digest, "source_rule_version": HISTORICAL_SOURCE_RULE_VERSION, "summary": parsed["summary"]},
            )
        return {"batch": _batch_summary(connection, batch_id), "idempotent": False}


def get_historical_credit_import_batch(batch_id: int) -> dict[str, Any]:
    with transaction() as connection:
        return _batch_summary(connection, batch_id)


def list_historical_credit_import_anomalies(batch_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id, source_sheet, source_row_number, raw_name, raw_class_name, raw_group_name, "
        "raw_total_points, calculated_total_points, validation_status, review_status, credit_review_status, "
        "resolved_total_points, credit_review_reason, match_status, match_reason "
        "FROM learning_credit_import_rows WHERE batch_id=? AND validation_status<>'PASS' "
        "ORDER BY source_sheet, source_row_number",
        (batch_id,),
    )


def get_historical_credit_import_row(batch_id: int, row_id: int) -> dict[str, Any]:
    row = fetch_one(
        "SELECT * FROM learning_credit_import_rows WHERE batch_id=? AND id=?", (batch_id, row_id)
    )
    if not row:
        raise ValueError("历史学分导入行不存在")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    row["items"] = fetch_all(
        "SELECT * FROM learning_credit_import_items WHERE batch_id=? AND import_row_id=? ORDER BY source_column_index, id",
        (batch_id, row_id),
    )
    for item in row["items"]:
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return row


def dry_run_historical_credit_import(batch_id: int) -> dict[str, Any]:
    """Return a complete historical proposal without writing any table."""

    batch = get_historical_credit_import_batch(batch_id)
    rows = fetch_all(
        "SELECT i.*, r.raw_name, r.raw_class_name, r.raw_group_name, r.match_status, "
        "r.validation_status, r.credit_review_status, r.resolved_total_points, r.review_status, r.match_reason, "
        "r.id AS source_row_id, r.metadata_json AS row_metadata_json, b.source_year "
        "FROM learning_credit_import_items i "
        "JOIN learning_credit_import_rows r ON r.id=i.import_row_id "
        "JOIN learning_credit_import_batches b ON b.id=i.batch_id "
        "WHERE i.batch_id=? ORDER BY i.occurred_year DESC, (i.occurred_month IS NULL) ASC, i.occurred_month DESC, i.id",
        (batch_id,),
    )
    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    reason_counts: dict[str, dict[str, Any]] = {}

    def period_display(item: dict[str, Any]) -> str | None:
        precision = str(item.get("occurred_precision") or "")
        year = item.get("occurred_year")
        month = item.get("occurred_month")
        if precision == "MONTH" and year is not None and month is not None:
            return f"{int(year):04d}年{int(month)}月"
        if precision == "YEAR" and year is not None:
            return f"{int(year):04d}年"
        return None

    for raw in rows:
        item = dict(raw)
        item.pop("row_metadata_json", None)
        item["period_display"] = period_display(item)
        item["points"] = float(item["points"] or 0)
        reason: str | None = None
        if item.get("matched_member_id") is None or item.get("match_status") not in {"AUTO_MATCHED", "CONFIRMED"}:
            reason = item.get("match_reason") or "MEMBER_MAPPING_REQUIRED"
        elif item.get("validation_status") == VALIDATION_PASS:
            reason = None
        elif item.get("validation_status") == VALIDATION_TOTAL_MISSING and item.get("credit_review_status") == "APPROVED":
            reason = None
        elif item.get("validation_status") == VALIDATION_ZERO:
            reason = "NO_CREDIT_CONFIRMED"
        elif item.get("credit_review_status") == "REJECTED":
            reason = "CREDIT_REVIEW_REJECTED"
        elif item.get("credit_review_status") == "NEEDS_SOURCE_CORRECTION":
            reason = "SOURCE_CORRECTION_REQUIRED"
        else:
            reason = "CREDIT_REVIEW_REQUIRED"

        if reason is None:
            if item.get("period_review_status") == "DUAL_TRACK_PENDING" or item.get("source_month") == 9:
                reason = "DUAL_TRACK_PENDING"
            elif item.get("occurred_precision") == "YEAR" and item.get("period_review_status") != "YEAR_ACCEPTED":
                reason = "PERIOD_YEAR_ACCEPTANCE_REQUIRED"
            elif item.get("occurred_precision") == "MONTH" and item.get("period_review_status") not in {"READY", "MONTH_CONFIRMED"}:
                reason = "PERIOD_MONTH_CONFIRMATION_REQUIRED"
        if reason is None and item.get("status") not in {"PENDING_REVIEW", "READY"}:
            reason = "ITEM_NOT_READY"

        if reason is None:
            ready.append(item)
        else:
            item["blocked_reason"] = reason
            blocked.append(item)
            current = reason_counts.setdefault(reason, {"count": 0, "points": 0})
            current["count"] += 1
            current["points"] += item["points"]

    for value in reason_counts.values():
        value["points"] = _clean_points(value["points"])
    track_counts: dict[str, dict[str, Any]] = {}
    for item in ready:
        track = "SEPTEMBER_DUAL_TRACK" if item.get("source_month") == 9 else (
            "YEAR_ACCEPTED" if item.get("occurred_precision") == "YEAR" else "HISTORICAL_BASELINE"
        )
        current = track_counts.setdefault(track, {"count": 0, "points": 0})
        current["count"] += 1
        current["points"] += item["points"]
    for value in track_counts.values():
        value["points"] = _clean_points(value["points"])
    september_dual_track_items = [item for item in rows if item.get("source_month") == 9]
    identity_rows = fetch_all(
        "SELECT match_status, COUNT(*) AS count FROM learning_credit_import_rows WHERE batch_id=? GROUP BY match_status",
        (batch_id,),
    )
    credit_rows = fetch_all(
        "SELECT validation_status, credit_review_status, COUNT(*) AS count FROM learning_credit_import_rows "
        "WHERE batch_id=? GROUP BY validation_status, credit_review_status",
        (batch_id,),
    )
    blocked_reason = None
    if not ready:
        blocked_reason = "PLATFORM_MEMBER_SNAPSHOT_REQUIRED" if not rows or all(
            item.get("blocked_reason") == "MEMBER_MAPPING_REQUIRED" for item in blocked
        ) else next(iter(reason_counts), "HISTORICAL_REVIEW_REQUIRED")
    return {
        "mode": "DRY_RUN",
        "batch": batch,
        "source_rule_version": HISTORICAL_SOURCE_RULE_VERSION,
        "total_item_count": len(rows),
        "proposed_item_count": len(ready),
        "proposed_points": _clean_points(sum(item["points"] for item in ready)),
        "blocked_item_count": len(blocked),
        "blocked_points": _clean_points(sum(item["points"] for item in blocked)),
        "blocked_reason": blocked_reason,
        "blocked_reason_counts": reason_counts,
        "track_counts": track_counts,
        "september_dual_track_items": len(september_dual_track_items),
        "september_dual_track_points": _clean_points(sum(float(item.get("points") or 0) for item in september_dual_track_items)),
        "identity_match_counts": {str(row["match_status"]): int(row["count"]) for row in identity_rows},
        "credit_review_counts": [dict(row) for row in credit_rows],
        "details": ready,
        "blocked_details": blocked,
        "learning_credit_entries_delta": 0,
        "ledger_write": False,
        "staging_write": False,
    }
