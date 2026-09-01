"""Shared names and month arithmetic for learning-plan audits.

``cohort_month`` identifies one of the four opening-month templates.  It is
not a learning-cycle number and it is not the current calendar month.
``learning_cycle_index`` is the one-based cycle offset from a class's actual
opening year-month.
"""

from __future__ import annotations

import re
from typing import Any


COHORT_TEMPLATE_MONTHS = (1, 4, 7, 10)
_YEAR_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})(?:-\d{1,2})?(?:[T ].*)?$")


def cohort_template_label(cohort_month: int) -> str:
    month = int(cohort_month)
    if month not in COHORT_TEMPLATE_MONTHS:
        raise ValueError("cohort_month 必须是 1、4、7 或 10 月开班模板")
    return f"{month}月开班模板"


def learning_cycle_label(cohort_month: int, learning_cycle_index: int) -> str:
    return f"{cohort_template_label(cohort_month)} · 第{int(learning_cycle_index)}学习周期"


def normalize_year_month(value: Any) -> str:
    text = str(value or "").strip()
    match = _YEAR_MONTH_RE.fullmatch(text)
    if not match:
        raise ValueError("开班年月必须是 YYYY-MM 或 YYYY-MM-DD")
    year = int(match.group("year"))
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        raise ValueError("开班年月中的月份无效")
    return f"{year:04d}-{month:02d}"


def cohort_month_from_open_year_month(value: Any) -> int:
    return int(normalize_year_month(value)[5:7])


def add_months(year_month: Any, offset: int) -> str:
    normalized = normalize_year_month(year_month)
    year = int(normalized[:4])
    month = int(normalized[5:7])
    absolute_month = year * 12 + (month - 1) + int(offset)
    result_year, result_month_index = divmod(absolute_month, 12)
    return f"{result_year:04d}-{result_month_index + 1:02d}"


def learning_cycle_index_for_month(open_year_month: Any, specified_year_month: Any) -> int:
    opened = normalize_year_month(open_year_month)
    specified = normalize_year_month(specified_year_month)
    opened_absolute = int(opened[:4]) * 12 + int(opened[5:7]) - 1
    specified_absolute = int(specified[:4]) * 12 + int(specified[5:7]) - 1
    index = specified_absolute - opened_absolute + 1
    if index < 1:
        raise ValueError("指定年月早于班级实际开班年月")
    return index


def year_index_for_learning_cycle(learning_cycle_index: int) -> int:
    index = int(learning_cycle_index)
    if not 1 <= index <= 36:
        raise ValueError("learning_cycle_index 必须在 1 到 36 之间")
    return ((index - 1) // 12) + 1


def year_cycle_index_for_learning_cycle(learning_cycle_index: int) -> int:
    index = int(learning_cycle_index)
    if not 1 <= index <= 36:
        raise ValueError("learning_cycle_index 必须在 1 到 36 之间")
    return ((index - 1) % 12) + 1
