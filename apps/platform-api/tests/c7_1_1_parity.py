"""Stable C7.1.1 semantics used to compare SQLite and MySQL previews.

Database-generated ids are deliberately removed from the comparison.  The
business result must be identical across dialects even though excellent-share
idempotency keys contain a database-generated fact id.
"""

from __future__ import annotations

from typing import Any


def _stable_idempotency_key(value: Any) -> str | None:
    if not value:
        return None
    parts = str(value).split(":")
    if parts[0] == "DAILY_READING" and len(parts) == 3:
        return f"DAILY_READING:<member>:{parts[2]}"
    if parts[0] == "EXCELLENT_SHARE" and len(parts) == 3:
        return "EXCELLENT_SHARE:<member>:<fact_id>"
    return str(value)


def preview_parity_signature(preview: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Return only deterministic business semantics from a C7 preview."""

    result = []
    for entry in preview["entries"]:
        calendar = entry.get("calendar") or {}
        result.append(
            (
                entry.get("occurred_on"),
                entry.get("status"),
                float(entry.get("points") or 0),
                tuple(entry.get("reasons") or []),
                calendar.get("calendar_year"),
                calendar.get("day_type"),
                entry.get("rule_key"),
                entry.get("rule_version"),
                entry.get("period_month"),
                _stable_idempotency_key(entry.get("idempotency_key")),
            )
        )
    return tuple(result)


EXPECTED_DAILY_READING_SIGNATURE = (
    (
        "2026-01-01",
        "NO_CREDIT",
        0.0,
        ("NOT_BUSINESS_WORKDAY",),
        2026,
        "HOLIDAY",
        "DAILY_READING",
        "2026.1",
        None,
        "DAILY_READING:<member>:2026-01-01",
    ),
    (
        "2026-01-04",
        "READY",
        1.0,
        (),
        2026,
        "ADJUSTED_WORKDAY",
        "DAILY_READING",
        "2026.1",
        None,
        "DAILY_READING:<member>:2026-01-04",
    ),
    (
        "2026-01-05",
        "READY",
        1.0,
        (),
        2026,
        "NORMAL_WORKDAY",
        "DAILY_READING",
        "2026.1",
        None,
        "DAILY_READING:<member>:2026-01-05",
    ),
    (
        "2026-01-11",
        "NO_CREDIT",
        0.0,
        ("NOT_BUSINESS_WORKDAY",),
        2026,
        "WEEKEND",
        "DAILY_READING",
        "2026.1",
        None,
        "DAILY_READING:<member>:2026-01-11",
    ),
    (
        "2026-02-14",
        "READY",
        1.0,
        (),
        2026,
        "ADJUSTED_WORKDAY",
        "DAILY_READING",
        "2026.1",
        None,
        "DAILY_READING:<member>:2026-02-14",
    ),
    (
        "2027-01-01",
        "BLOCKED",
        0.0,
        ("BUSINESS_CALENDAR_MISSING",),
        None,
        None,
        "DAILY_READING",
        "2026.1",
        None,
        "DAILY_READING:<member>:2027-01-01",
    ),
)


EXPECTED_EXCELLENT_SHARE_SIGNATURE = tuple(
    [
        (
            f"2026-05-{day:02d}",
            "READY" if day <= 5 else "NO_CREDIT",
            1.0 if day <= 5 else 0.0,
            () if day <= 5 else ("MONTHLY_CAP_REACHED",),
            None,
            None,
            "EXCELLENT_SHARE",
            "2026.1",
            "2026-05",
            "EXCELLENT_SHARE:<member>:<fact_id>",
        )
        for day in range(1, 7)
    ]
)
