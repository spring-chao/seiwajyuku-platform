"""Shared birthday recurrence rules for member care and operations."""

import calendar
import re
from datetime import date
from functools import lru_cache
from typing import Any

from lunar_python import LunarMonth, LunarYear, Solar

LUNAR_BIRTHDAY_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|30)L?$"


def lunar_month_options(year: int) -> list[dict[str, int]]:
    return [
        {"month": month.getMonth(), "days": month.getDayCount()}
        for month in LunarYear.fromYear(year).getMonths()
        if month.getYear() == year
    ]


def normalize_lunar_birthday(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(LUNAR_BIRTHDAY_PATTERN, value):
        raise ValueError("请填写有效的农历月份和日期")
    year, month, day = (int(part) for part in value.removesuffix("L").split("-"))
    if not 1900 <= year <= 2100:
        raise ValueError("农历出生年份须在1900至2100年之间")
    lunar_month = LunarMonth.fromYm(year, -month if value.endswith("L") else month)
    if lunar_month is None or day > lunar_month.getDayCount():
        raise ValueError("该年没有所选的农历月份或日期")
    return value


def lunar_birthday_label(value: str | None) -> str | None:
    if not value:
        return None
    leap = "闰" if value.endswith("L") else ""
    return f"农历{leap}{int(value[5:7])}月{int(value[8:10])}日"


@lru_cache(maxsize=4096)
def _lunar_occurrence(value: str, lunar_year: int) -> date | None:
    month = int(value[:2])
    day = int(value[3:5])
    lunar_month = LunarMonth.fromYm(lunar_year, -month) if value.endswith("L") else None
    if lunar_month is None:
        lunar_month = LunarMonth.fromYm(lunar_year, month)
    solar = Solar.fromJulianDay(
        lunar_month.getFirstJulianDay() + min(day, lunar_month.getDayCount()) - 1
    )
    if 1 <= solar.getYear() <= 9999:
        return date(solar.getYear(), solar.getMonth(), solar.getDay())
    return None


def birthday_occurrences(member: dict[str, Any], year: int) -> tuple[date, ...]:
    """Dates in the requested Gregorian year, in chronological order."""
    if not 1900 <= year <= 9999:
        return ()
    lunar = member.get("birthday_lunar")
    if lunar:
        return tuple(
            due for lunar_year in (year - 1, year)
            if (due := _lunar_occurrence(lunar[5:], lunar_year)) is not None and due.year == year
        )
    try:
        birthday = date.fromisoformat(str(member.get("birthday"))[:10])
    except ValueError:
        return ()
    day = min(birthday.day, calendar.monthrange(year, birthday.month)[1])
    return (date(year, birthday.month, day),)


def birthday_completion_year(member: dict[str, Any], due: date) -> int:
    # A Gregorian year can contain two occurrences of the same lunar month/day.
    # The existing member/year uniqueness rule applies to the chosen calendar.
    lunar = member.get("birthday_lunar")
    if lunar and due.year > 1900 and _lunar_occurrence(lunar[5:], due.year - 1) == due:
        return due.year - 1
    return due.year
