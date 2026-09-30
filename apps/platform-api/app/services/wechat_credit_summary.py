"""Read-only, privacy-limited learning-credit view for the bound member."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from app.db import connect, execute


BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
RECENT_CREDIT_LIMIT = 20
_POSTED_STATUSES = ("POSTED", "REVERSED")
_CATEGORY_LABELS = {
    "STANDARD_LEARNING": "标准学习",
    "EXTENSION_ACTIVITY": "拓展活动",
}
_CREDIT_TYPE_LABELS = {
    "GROUP_MEETING_ATTENDANCE": "小组学习会出席",
    "COURSE_COMPLETION": "课程完成",
    "CLASS_MEETING_SCORE": "班级学习日",
    "DAILY_READING": "每日读书",
    "EXCELLENT_SHARE": "优秀分享",
    "LEGACY_READING_AND_SHARE": "历史每日读书及分享",
    "LEGACY_CLASS_MEETING": "历史班级学习日",
}
_HISTORICAL_SOURCE_TYPE = "LEGACY_SUZHOU_2026_V1"


def _points(value: Any) -> str:
    return format(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def _period_display(row: Any) -> str:
    precision = str(row.get("occurred_precision") or "EXACT_DATE")
    year = row.get("occurred_year")
    month = row.get("occurred_month")
    if precision == "YEAR" and year is not None:
        return f"{int(year):04d}年"
    if precision == "MONTH" and year is not None and month is not None:
        return f"{int(year):04d}年{int(month)}月"
    occurred_at = row.get("occurred_at")
    return str(occurred_at)[:10].replace("-", "/") if occurred_at else "时间待确认"


def _entry_display(row: Any) -> dict[str, Any]:
    credit_type = str(row.get("credit_type") or "")
    source_type = str(row.get("source_type") or "")
    credit_type_label = _CREDIT_TYPE_LABELS.get(credit_type, "其他学习")
    is_reversal = source_type == "REVERSAL"
    if source_type == _HISTORICAL_SOURCE_TYPE:
        rule_basis = "历史学分原始记录（按原值保留，未重算）"
    elif is_reversal:
        rule_basis = "原学分记录冲销"
    else:
        rule_basis = f"{credit_type_label}规则"
    category = str(row.get("credit_category") or "")
    return {
        "period_display": _period_display(row),
        "credit_category_label": _CATEGORY_LABELS.get(category, "学分"),
        "credit_type_label": "冲销记录" if is_reversal else credit_type_label,
        "rule_basis": rule_basis,
        "points": _points(row.get("points")),
        "is_reversal": is_reversal,
    }


def get_member_credit_summary(member_id: int) -> dict[str, Any]:
    """Return totals and recent posted entries for one active member only."""

    connection = connect()
    try:
        member = execute(
            connection,
            "SELECT id FROM members WHERE id=? AND status='ACTIVE' LIMIT 1",
            (member_id,),
        ).fetchone()
        if not member:
            raise ValueError("当前学员身份不可用")

        status_sql = "status IN (?, ?)"
        category_rows = execute(
            connection,
            "SELECT credit_category,COALESCE(SUM(points),0) AS points "
            "FROM learning_credit_entries WHERE member_id=? AND " + status_sql +
            " GROUP BY credit_category",
            (member_id, *_POSTED_STATUSES),
        ).fetchall()
        totals = {str(row["credit_category"]): _points(row["points"]) for row in category_rows}
        standard = Decimal(totals.get("STANDARD_LEARNING", "0.00"))
        extension = Decimal(totals.get("EXTENSION_ACTIVITY", "0.00"))

        current_year = datetime.now(BUSINESS_TIMEZONE).year
        annual = execute(
            connection,
            "SELECT COALESCE(SUM(points),0) AS points "
            "FROM learning_credit_entries WHERE member_id=? AND " + status_sql +
            " AND occurred_year=?",
            (member_id, *_POSTED_STATUSES, current_year),
        ).fetchone()
        entries = execute(
            connection,
            "SELECT credit_category,credit_type,source_type,points,occurred_at,"
            "occurred_precision,occurred_year,occurred_month "
            "FROM learning_credit_entries WHERE member_id=? AND " + status_sql +
            " ORDER BY occurred_year DESC,(occurred_month IS NULL) ASC,occurred_month DESC,"
            "occurred_at DESC,id DESC LIMIT ?",
            (member_id, *_POSTED_STATUSES, RECENT_CREDIT_LIMIT),
        ).fetchall()
        return {
            "current_year": current_year,
            "total_points": _points(standard + extension),
            "current_year_points": _points(annual["points"]),
            "standard_learning_points": _points(standard),
            "extension_activity_points": _points(extension),
            "recent_entries": [_entry_display(dict(row)) for row in entries],
        }
    finally:
        connection.close()
