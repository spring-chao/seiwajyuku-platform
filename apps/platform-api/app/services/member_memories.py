from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from app.db import fetch_all


SPECIAL_ACTIVITY_TYPES = {
    "COURSE",
    "STUDY_COURSE",
    "SEMINAR",
    "STUDY_TOUR",
    "ENTERPRISE_VISIT",
    "CENTER_MONTHLY_REPORT",
    "ANNUAL_REPORT",
    "REPORT_MEETING",
    "CONFERENCE",
}
LONG_TERM_ACTIVITY_TYPES = {
    "CLASS_MEETING",
    "GROUP_MEETING",
    "READING_CHECKIN",
    "READING",
    "STUDY_GROUP",
}
SPECIAL_TITLE_HINTS = ("游学", "参访", "标杆", "报告会", "专题", "课程", "大会")


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalise_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _activity_title(activity_type: str, title: Any) -> str:
    normalised = _normalise_title(title)
    if normalised:
        return normalised
    return {
        "CLASS_MEETING": "班会",
        "GROUP_MEETING": "小组学习",
        "READING_CHECKIN": "读书打卡",
        "COURSE": "课程学习",
        "STUDY_COURSE": "专题学习",
    }.get(activity_type, "学习活动")


def _classify_memory(activity_type: str, title: str) -> tuple[str, str, int]:
    activity_type = activity_type.upper()
    if activity_type in SPECIAL_ACTIVITY_TYPES or any(
        hint in title for hint in SPECIAL_TITLE_HINTS
    ):
        return "SPECIAL_EXPERIENCE", "特别经历", 300
    if activity_type in LONG_TERM_ACTIVITY_TYPES:
        return "LONG_TERM_COMPANIONSHIP", "长期同行", 100
    return "LEARNING_ACTIVITY", "学习活动", 80


def _memory_candidate(
    *,
    memory_id: str,
    occurred_on: Any,
    activity_type: Any,
    title: Any,
    source_type: str,
    evidence_status: str,
    as_of: date,
) -> dict[str, Any] | None:
    parsed_date = _as_date(occurred_on)
    if not parsed_date:
        return None
    normalised_type = str(activity_type or "LEARNING_ACTIVITY").upper()
    normalised_title = _activity_title(normalised_type, title)
    category, category_label, score = _classify_memory(normalised_type, normalised_title)
    days_ago = (as_of - parsed_date).days
    if 0 <= days_ago <= 180:
        score += 40
    return {
        "id": memory_id,
        "occurred_on": parsed_date.isoformat(),
        "year": parsed_date.year,
        "month": parsed_date.month,
        "title": normalised_title,
        "activity_type": normalised_type,
        "category": category,
        "category_label": category_label,
        "source_type": source_type,
        "evidence_status": evidence_status,
        "verified": True,
        "selection_score": score,
    }


def verified_member_memories(
    member_id: int,
    *,
    limit: int = 20,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Return deduplicated positive participation facts from verifiable sources."""
    current_date = as_of or datetime.now(UTC).date()
    candidates: list[dict[str, Any]] = []
    attendance_rows = fetch_all(
        "SELECT eg.id AS event_id, eg.event_date, eg.activity_type, eg.title "
        "FROM attendance_records ar "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "WHERE ar.member_id=? AND ar.participant_type='MEMBER' "
        "AND ar.attendance_status IN ('PRESENT','MANUAL_PRESENT') "
        "AND eg.status NOT IN ('CANCELLED','INACTIVE') "
        "GROUP BY eg.id, eg.event_date, eg.activity_type, eg.title",
        (member_id,),
    )
    for row in attendance_rows:
        candidate = _memory_candidate(
            memory_id=f"attendance-event-{row['event_id']}",
            occurred_on=row["event_date"],
            activity_type=row["activity_type"],
            title=row["title"],
            source_type="ATTENDANCE",
            evidence_status="PRESENT",
            as_of=current_date,
        )
        if candidate:
            candidates.append(candidate)

    legacy_rows = fetch_all(
        "SELECT id, activity_type, occurred_on, participation_status, title "
        "FROM member_activity_facts WHERE member_id=? "
        "AND participation_status IN ('PRESENT','COMPLETED','RECORDED') "
        "ORDER BY occurred_on DESC, id DESC",
        (member_id,),
    )
    for row in legacy_rows:
        candidate = _memory_candidate(
            memory_id=f"legacy-activity-{row['id']}",
            occurred_on=row["occurred_on"],
            activity_type=row["activity_type"],
            title=row["title"],
            source_type="LEGACY_ACTIVITY_FACT",
            evidence_status=str(row["participation_status"]),
            as_of=current_date,
        )
        if candidate:
            candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -int(item["selection_score"]),
            -date.fromisoformat(item["occurred_on"]).toordinal(),
            item["id"],
        )
    )
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate["occurred_on"], candidate["title"])
        if key in seen:
            continue
        seen.add(key)
        candidate.pop("selection_score", None)
        candidate["selected_by_default"] = len(deduplicated) < 4
        deduplicated.append(candidate)
    return deduplicated[:limit]
