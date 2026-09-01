"""Pure helpers for class-level learning-cycle planned dates.

Planned dates are only an operational reference.  They never advance a
learning cycle.  The runtime cycle clock remains ``class_learning_cycles``:
the next cycle opens only after the current class meeting is confirmed.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from typing import Any


def parse_utc_datetime(value: Any, field_name: str = "日期时间") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            raise ValueError(f"{field_name}不能为空")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name}必须是 ISO 日期时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def add_calendar_months(value: Any, month_offset: int) -> str:
    """Add calendar months while preserving time and clamping month-end days."""

    parsed = parse_utc_datetime(value)
    absolute_month = parsed.year * 12 + parsed.month - 1 + int(month_offset)
    year, month_index = divmod(absolute_month, 12)
    month = month_index + 1
    day = min(parsed.day, calendar.monthrange(year, month)[1])
    return parsed.replace(year=year, month=month, day=day).isoformat()


def planned_class_meeting_at_for_cycle(binding_started_at: Any, learning_cycle_index: int) -> str:
    index = int(learning_cycle_index)
    if index < 1:
        raise ValueError("learning_cycle_index 必须从 1 开始")
    return add_calendar_months(binding_started_at, index - 1)


def year_month(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return parse_utc_datetime(value).strftime("%Y-%m")
