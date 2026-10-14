"""China business calendar; stored audit instants remain UTC."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

BUSINESS_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def business_today(reference: date | datetime | None = None) -> date:
    current = reference if reference is not None else datetime.now(UTC)
    if isinstance(current, datetime):
        return (current if current.tzinfo else current.replace(tzinfo=UTC)).astimezone(BUSINESS_TIMEZONE).date()
    return current


def calendar_date(value: Any) -> date | None:
    """Date-only and unzoned planned dates are calendar facts, not UTC instants."""
    if isinstance(value, datetime):
        return business_today(value) if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return business_today(parsed) if parsed.tzinfo else parsed.date()
    except ValueError:
        return None


def instant_date(value: Any) -> date | None:
    """MySQL naive DATETIME audit values use the existing UTC storage convention."""
    if isinstance(value, datetime):
        return business_today(value)
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        return calendar_date(text)
    try:
        return business_today(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def business_date_sql(column: str) -> str:
    """Extract a Beijing day from an audit timestamp in SQLite or MySQL."""
    from app.db import settings
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", column):
        raise ValueError("invalid timestamp column")
    if settings.database_url.startswith("mysql"):
        return f"DATE(DATE_ADD({column}, INTERVAL 8 HOUR))"
    return f"DATE({column}, '+8 hours')"
