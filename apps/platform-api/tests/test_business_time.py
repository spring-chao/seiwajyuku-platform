from datetime import UTC, date, datetime

import pytest

from app.core.business_time import business_today, calendar_date, instant_date, business_date_sql
from app.db import fetch_one
from app.services.renewals import determine_calendar_stage


@pytest.mark.parametrize("instant, expected", [
    ("2026-10-14T15:59:59+00:00", "2026-10-14"),
    ("2026-10-14T16:00:00+00:00", "2026-10-15"),
    ("2026-10-15T15:59:59+00:00", "2026-10-15"),
    ("2026-10-15T16:00:00+00:00", "2026-10-16"),
    ("2026-12-31T16:00:00+00:00", "2027-01-01"),
    ("2028-02-28T16:00:00+00:00", "2028-02-29"),
])
def test_business_date_uses_beijing_boundaries(instant, expected):
    assert business_today(datetime.fromisoformat(instant)).isoformat() == expected
    assert instant_date(instant).isoformat() == expected


def test_date_facts_do_not_shift_but_utc_audit_instants_do():
    assert calendar_date("1980-10-14") == date(1980, 10, 14)
    assert calendar_date("2026-10-14 23:30:00") == date(2026, 10, 14)
    assert calendar_date("2026-10-14T17:30:00Z") == date(2026, 10, 15)
    assert instant_date("2026-10-14 17:30:00") == date(2026, 10, 15)
    assert business_today(date(2026, 10, 15)) == date(2026, 10, 15)


@pytest.mark.parametrize("stored, expected", [
    ("2026-10-14 15:59:59", "2026-10-14"),
    ("2026-10-14 16:00:00", "2026-10-15"),
    ("2026-10-15 15:59:59", "2026-10-15"),
    ("2026-10-15 16:00:00", "2026-10-16"),
])
def test_database_completed_day_matches_beijing(stored, expected):
    row = fetch_one(f"SELECT {business_date_sql('occurred_at')} AS day FROM (SELECT ? AS occurred_at) AS sample", (stored,))
    assert str(row["day"]) == expected


def test_renewal_stage_rolls_over_at_beijing_month_boundary():
    before = determine_calendar_stage(2026, 10, as_of=datetime(2026, 9, 30, 15, 59, tzinfo=UTC))
    after = determine_calendar_stage(2026, 10, as_of=datetime(2026, 9, 30, 16, tzinfo=UTC))
    assert before["code"] == "FOLLOW_1"
    assert after["code"] == "DUE_NOW"


def test_rhythm_today_rolls_over_at_beijing_midnight(monkeypatch):
    from app.services import operation_rhythm as rhythm
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 14, 16, tzinfo=UTC)
    monkeypatch.setattr(rhythm, "datetime", FrozenClock)
    monkeypatch.setattr(rhythm, "_require_permission", lambda *args: None)
    monkeypatch.setattr(rhythm, "_item_rows", lambda *args, **kwargs: [
        {"id": day, "due_date": f"2026-10-{day}", "status": "PENDING", "business_type": "CARE", "item_key": "CARE"}
        for day in (14, 15, 16)
    ])
    snapshot = rhythm.rhythm_snapshot(1, 2026, 10)
    assert [item["id"] for item in snapshot["views"]["today"]] == [15]
    assert [item["id"] for item in snapshot["views"]["attention"]] == [14]
