"""attendance_scoring service - calculate individual attendance scores.

Scoring rules:
- Morning/AFTERNOON: 7 base points, -1 for late, -1 for early leave
- KONPA: 4 base points, -1 for late, -1 for early leave
- Not score_eligible: 0 points
- Not PRESENT/MANUAL_PRESENT: 0 points
- Minimum: 0 points (no negative)
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def normalize_activity_type(value: str | None) -> str:
    """Normalize external activity identifiers to the platform contract."""
    normalized = (value or "CLASS_MEETING").strip().upper()
    aliases = {
        "CLASS_MEETING": "CLASS_MEETING",
        "CLASS-MEETING": "CLASS_MEETING",
    }
    return aliases.get(normalized, normalized)


def get_active_rule(session_code: str, activity_type: str = "CLASS_MEETING") -> dict | None:
    """Get the active score rule for a session code."""
    now = _now()
    activity_type = normalize_activity_type(activity_type)
    return fetch_one(
        "SELECT * FROM attendance_score_rules "
        "WHERE session_code=? AND activity_type=? AND status='ACTIVE' "
        "AND effective_from<=? "
        "AND (effective_until IS NULL OR effective_until>=?) "
        "ORDER BY rule_version DESC LIMIT 1",
        (session_code, activity_type, now, now),
    )


def has_active_early_leave(attendance_record_id: int) -> bool:
    """Check if a record has an active (non-superseded) early leave adjudication."""
    row = fetch_one(
        "SELECT COUNT(*) AS cnt FROM attendance_adjudications "
        "WHERE attendance_record_id=? AND adjudication_type='EARLY_LEAVE' "
        "AND superseded_at IS NULL",
        (attendance_record_id,),
    )
    return bool(row and row["cnt"] > 0)


def calculate_score(
    *,
    attendance_record_id: int,
    member_id: int | None,
    session_code: str,
    attendance_status: str,
    checked_at: str | None,
    scheduled_start_at: str | None,
    score_eligible: bool,
    activity_type: str = "CLASS_MEETING",
) -> dict[str, Any]:
    """Calculate score for a single attendance record.

    Returns dict with base_points, late_deduction, early_leave_deduction,
    final_points, is_late, is_early_leave.
    """
    rule = get_active_rule(session_code, activity_type)
    if not rule:
        raise ValueError(f"没有找到 {session_code} 的有效积分规则")

    # MySQL DECIMAL values are returned as Decimal while SQLite returns float.
    # Normalize rule values before arithmetic and JSON serialization so both
    # database backends produce the same scoring contract.
    base_points = float(rule["base_points"])
    rule_late_deduction = float(rule["late_deduction"])
    rule_early_leave_deduction = float(rule["early_leave_deduction"])

    if not score_eligible:
        final_points = 0
        is_late = False
        is_early_leave = False
        late_deduction = 0
        early_leave_deduction = 0
    elif attendance_status not in {"PRESENT", "MANUAL_PRESENT"}:
        final_points = 0
        is_late = False
        is_early_leave = False
        late_deduction = 0
        early_leave_deduction = 0
    else:
        # Check late
        checked_dt = _parse_dt(checked_at)
        scheduled_dt = _parse_dt(scheduled_start_at)
        is_late = bool(
            checked_dt
            and scheduled_dt
            and checked_dt > scheduled_dt
        )
        late_deduction = rule_late_deduction if is_late else 0

        # Check early leave
        is_early_leave = has_active_early_leave(attendance_record_id)
        early_leave_deduction = (
            rule_early_leave_deduction if is_early_leave else 0
        )

        final_points = max(base_points - late_deduction - early_leave_deduction, 0)

    detail = {
        "rule_id": rule["id"],
        "rule_version": rule["rule_version"],
        "base_points": base_points,
        "late_deduction": late_deduction,
        "early_leave_deduction": early_leave_deduction,
        "is_late": is_late,
        "is_early_leave": is_early_leave,
        "attendance_status": attendance_status,
        "score_eligible": score_eligible,
    }

    return {
        "attendance_record_id": attendance_record_id,
        "member_id": member_id,
        "rule_id": rule["id"],
        "rule_version": rule["rule_version"],
        "base_points": base_points,
        "late_deduction": late_deduction,
        "early_leave_deduction": early_leave_deduction,
        "other_adjustment": 0,
        "final_points": final_points,
        "is_late": is_late,
        "is_early_leave": is_early_leave,
        "calculation_detail_json": json.dumps(detail, ensure_ascii=False),
    }


def upsert_score_record(
    *,
    attendance_record_id: int,
    member_id: int | None,
    session_code: str,
    attendance_status: str,
    checked_at: str | None,
    scheduled_start_at: str | None,
    score_eligible: bool,
    activity_type: str = "CLASS_MEETING",
    source_updated_at: str | None = None,
) -> dict[str, Any]:
    """Calculate and upsert a score record for an attendance record."""
    result = calculate_score(
        attendance_record_id=attendance_record_id,
        member_id=member_id,
        session_code=session_code,
        attendance_status=attendance_status,
        checked_at=checked_at,
        scheduled_start_at=scheduled_start_at,
        score_eligible=score_eligible,
        activity_type=normalize_activity_type(activity_type),
    )
    now = _now()
    with transaction() as connection:
        existing = execute(
            connection,
            "SELECT id FROM attendance_score_records WHERE attendance_record_id=?",
            (attendance_record_id,),
        ).fetchone()
        if existing:
            score_id = existing["id"] if hasattr(existing, "keys") else existing[0]
            execute(
                connection,
                "UPDATE attendance_score_records SET member_id=?, rule_id=?, rule_version=?, "
                "base_points=?, late_deduction=?, early_leave_deduction=?, other_adjustment=?, "
                "final_points=?, is_late=?, is_early_leave=?, calculation_detail_json=?, "
                "source_updated_at=?, calculated_at=?, updated_at=? WHERE id=?",
                (
                    member_id, result["rule_id"], result["rule_version"],
                    result["base_points"], result["late_deduction"],
                    result["early_leave_deduction"], result["other_adjustment"],
                    result["final_points"], 1 if result["is_late"] else 0,
                    1 if result["is_early_leave"] else 0,
                    result["calculation_detail_json"],
                    source_updated_at, now, now, score_id,
                ),
            )
        else:
            cursor = execute(
                connection,
                "INSERT INTO attendance_score_records"
                "(attendance_record_id, member_id, rule_id, rule_version, base_points, "
                "late_deduction, early_leave_deduction, other_adjustment, final_points, "
                "is_late, is_early_leave, calculation_detail_json, source_updated_at, "
                "calculated_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attendance_record_id, member_id, result["rule_id"],
                    result["rule_version"], result["base_points"],
                    result["late_deduction"], result["early_leave_deduction"],
                    result["other_adjustment"], result["final_points"],
                    1 if result["is_late"] else 0,
                    1 if result["is_early_leave"] else 0,
                    result["calculation_detail_json"],
                    source_updated_at, now, now, now,
                ),
            )
            score_id = cursor.lastrowid
    return {"score_id": score_id, **result}


def recalculate_event_group(event_group_id: int) -> dict[str, Any]:
    """Recalculate all scores for an event group."""
    records = fetch_all(
        "SELECT ar.id, ar.member_id, ar.attendance_status, ar.checked_at, "
        "ar.score_eligible, ar.participant_type, s.session_code, s.scheduled_start_at, "
        "s.event_group_id, eg.activity_type "
        "FROM attendance_records ar "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "WHERE s.event_group_id=?",
        (event_group_id,),
    )
    recalculated = 0
    for rec in records:
        upsert_score_record(
            attendance_record_id=rec["id"],
            member_id=rec["member_id"],
            session_code=rec["session_code"],
            attendance_status=rec["attendance_status"],
            checked_at=rec["checked_at"],
            scheduled_start_at=rec["scheduled_start_at"],
            score_eligible=bool(rec["score_eligible"]),
            activity_type=rec["activity_type"],
        )
        recalculated += 1
    return {"event_group_id": event_group_id, "recalculated": recalculated}


def member_scores(member_id: int) -> list[dict[str, Any]]:
    """Get all score records for a member."""
    return fetch_all(
        "SELECT sr.*, ar.attendance_session_id, s.session_code, s.session_name, "
        "eg.title, eg.event_date, eg.org_unit_id "
        "FROM attendance_score_records sr "
        "JOIN attendance_records ar ON ar.id=sr.attendance_record_id "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "WHERE sr.member_id=? "
        "ORDER BY eg.event_date DESC, s.session_order",
        (member_id,),
    )
