"""Advance learning cycles from confirmed, platform-created class meetings."""
import logging
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.core.settings import get_settings
from app.db import connect, execute, fetch_all, transaction
from app.services import learning_cycles as cycles
from app.services.attendance_sync import _upsert_event_group
from app.services.learning_cycle_schedule import parse_utc_datetime

CHINA = ZoneInfo("Asia/Shanghai")
RECOVERY_DAYS = 2
AUDIT_DAYS = 7
logger = logging.getLogger("uvicorn.error")


def _load_groups(settings):
    groups = defaultdict(list)
    cursor = None
    seen = {None}
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for _ in range(50):
            response = client.get(
                settings.signin_api_base_url.rstrip("/") + "/ops/v1/attendance/sessions",
                headers={"X-API-Key": settings.signin_service_api_key},
                params={"limit": 200, **({"cursor": cursor} if cursor else {})},
            )
            response.raise_for_status()
            page = response.json()
            for row in page["items"]:
                group = row.get("event_group") or {}
                group_id = group.get("external_group_id")
                if group_id and str(group.get("activity_type", "")).upper() == "CLASS_MEETING":
                    groups[group_id].append(row)
            cursor = page.get("next_cursor")
            if not cursor:
                return groups
            if cursor in seen:
                break
            seen.add(cursor)
    raise ValueError("班会活动分页未完整返回")


def _due_group(rows, today, *, window=RECOVERY_DAYS):
    if not rows or any(
        row.get("source_system") != "PLATFORM_MANAGEMENT" or row.get("lifecycle_status") != "CONFIRMED"
        for row in rows
    ):
        return None
    codes = {row.get("session_code") for row in rows}
    if not {"MORNING", "AFTERNOON"}.issubset(codes) or len(codes) != len(rows):
        return None
    group = rows[0]["event_group"]
    class_id = group.get("study_org_unit_id")
    if not class_id or any(row["event_group"].get("study_org_unit_id") != class_id for row in rows):
        return None
    try:
        intervals = [(parse_utc_datetime(row["scheduled_start_at"]),
                      parse_utc_datetime(row["scheduled_end_at"])) for row in rows]
    except (KeyError, ValueError):
        return None
    if any(start >= end for start, end in intervals):
        return None
    end_day = max(end for _, end in intervals).astimezone(CHINA).date()
    effective_day = end_day + timedelta(days=1)
    if not today - timedelta(days=window) <= effective_day <= today:
        return None
    boundary = datetime.combine(effective_day, time.min, CHINA).astimezone(UTC)
    return group, min(start for start, _ in intervals), boundary


def _has_later_facts(connection, cycle, boundary):
    cycle_id = cycle["id"]
    effective_day = boundary.astimezone(CHINA).date().isoformat()
    timestamp = cycles._storage_datetime(connection, boundary.isoformat())
    return any(
        execute(connection, sql, params).fetchone()
        for sql, params in (
            ("SELECT 1 FROM study_meeting_sessions WHERE class_org_unit_id=? "
             "AND learning_cycle_id=? AND status<>'CANCELLED' AND meeting_date>=? LIMIT 1",
             (cycle["class_org_unit_id"], cycle_id, effective_day)),
            ("SELECT 1 FROM learning_credit_entries WHERE learning_cycle_id=? AND status<>'VOID' "
             "AND (occurred_at>=? OR (occurred_at IS NULL AND created_at>=?)) LIMIT 1",
             (cycle_id, timestamp, timestamp)),
            ("SELECT 1 FROM group_learning_cycle_tasks WHERE class_learning_cycle_id=? "
             "AND status<>'PENDING' AND updated_at>=? LIMIT 1",
             (cycle_id, timestamp)),
        )
    )


class RecoveryReviewRequired(ValueError):
    """A late transition needs an operator to reconcile existing facts."""


def _transition_state(connection, group_id, group, start, boundary, *, recovering):
    class_id = group["study_org_unit_id"]
    event = execute(connection,
        "SELECT id, status FROM attendance_event_groups WHERE source_key='signin' AND external_group_id=?",
        (group_id,)).fetchone()
    binding = cycles._active_binding(connection, class_id)
    cycle = cycles._cycle_at(connection, int(binding["id"]), boundary.isoformat()) if binding else None
    cycle_index = cycle["learning_cycle_index"] if cycle else None
    if event and execute(connection,
        "SELECT id FROM class_learning_cycles WHERE source_event_group_id=? AND cycle_status='CLOSED'",
        (event["id"],)).fetchone():
        return "ALREADY_ADVANCED", cycle, cycle_index
    if not binding:
        return "NO_ACTIVE_PLAN", cycle, cycle_index
    if not cycle or cycle["cycle_status"] != "OPEN":
        return "NO_OPEN_CYCLE_AT_BOUNDARY", cycle, cycle_index
    if start <= parse_utc_datetime(cycle["opened_at"]):
        return "MEETING_PRECEDES_CYCLE", cycle, cycle_index
    if event and event["status"] in {"CANCELLED", "DELETED", "INACTIVE"}:
        return "SOURCE_EVENT_INACTIVE", cycle, cycle_index
    if cycle.get("actual_class_meeting_at") or (
        cycle.get("source_event_group_id") and (not event or cycle["source_event_group_id"] != event["id"])
    ):
        return "CYCLE_HAS_OTHER_MEETING", cycle, cycle_index
    if recovering and _has_later_facts(connection, cycle, boundary):
        return "LATER_LEARNING_FACTS", cycle, cycle_index
    return "DUE", cycle, cycle_index


def _advance(group_id, group, start, boundary, *, recovering=False):
    class_id = group["study_org_unit_id"]
    with transaction() as connection:
        cycles._lock_class_for_update(connection, class_id)
        state, cycle, _ = _transition_state(connection, group_id, group, start, boundary, recovering=recovering)
        if state == "ALREADY_ADVANCED":
            return False
        if state != "DUE":
            raise RecoveryReviewRequired(state)
        group_pk = _upsert_event_group(connection, source_key="signin",
            external_group_id=group_id, group_data={**group, "status": "ACTIVE"}, now=cycles._now())
        cycles.confirm_class_meeting(
            actor_user_id=None, class_org_unit_id=class_id, actual_class_meeting_at=start.isoformat(),
            source_event_group_id=group_pk, confirmation_reason="已确认的班会活动结束后次日推进",
            learning_cycle_id=cycle["id"], _connection=connection, _scheduled_boundary=boundary.isoformat(),
        )
        return True


def _candidates(settings, today, *, window=RECOVERY_DAYS):
    by_class = defaultdict(list)
    for group_id, rows in _load_groups(settings).items():
        candidate = _due_group(rows, today, window=window)
        if candidate:
            group, _, _ = candidate
            by_class[group["study_org_unit_id"]].append((group_id, *candidate))
    return by_class


def preview_due_class_meetings():
    """Read current cycle receipts and the reason each recent meeting is pending."""
    now = datetime.now(CHINA)
    today = now.date()
    due = _candidates(get_settings(), today, window=AUDIT_DAYS)
    connection = connect()
    try:
        report = []
        for class_id, candidates in due.items():
            active = cycles._active_binding(connection, class_id)
            current = cycles._cycle_at(connection, int(active["id"]), now.isoformat()) if active else None
            pending = []
            for group_id, group, start, boundary in candidates:
                effective_day = boundary.astimezone(CHINA).date()
                state, _, cycle_index = _transition_state(
                    connection, group_id, group, start, boundary, recovering=effective_day < today)
                if state == "DUE" and effective_day < today - timedelta(days=RECOVERY_DAYS):
                    state = "RECOVERY_WINDOW_CLOSED"
                item = {"class_id": class_id, "meeting_date": group["event_date"],
                        "next_cycle_due": effective_day.isoformat(), "cycle_index": cycle_index, "status": state,
                        "current_cycle_index": current["learning_cycle_index"] if current else None,
                        "current_cycle_status": current["cycle_status"] if current else None,
                        "current_cycle_opened_at": parse_utc_datetime(current["opened_at"]).isoformat() if current else None}
                if state == "ALREADY_ADVANCED":
                    closed = execute(
                        connection,
                        "SELECT c.id, c.learning_cycle_index, c.closed_at FROM class_learning_cycles c "
                        "JOIN attendance_event_groups e ON e.id=c.source_event_group_id "
                        "WHERE c.class_org_unit_id=? AND c.cycle_status='CLOSED' "
                        "AND e.source_key='signin' AND e.external_group_id=? ORDER BY c.id DESC LIMIT 1",
                        (class_id, group_id),
                    ).fetchone()
                    if closed:
                        item["completed_cycle_index"] = closed["learning_cycle_index"]
                        item["completed_cycle_closed_at"] = parse_utc_datetime(closed["closed_at"]).isoformat()
                        recorded = execute(
                            connection,
                            "SELECT created_at FROM audit_logs WHERE action='learning.cycle.confirm_class_meeting' "
                            "AND resource_type='class_learning_cycle' AND resource_id=? "
                            "AND org_unit_id=? ORDER BY id LIMIT 1",
                            (str(closed["id"]), class_id),
                        ).fetchone()
                        if recorded:
                            item["advancement_recorded_at"] = parse_utc_datetime(recorded["created_at"]).isoformat()
                report.append(item)
                if state != "ALREADY_ADVANCED":
                    pending.append(item)
            if len(pending) > 1:
                for item in pending:
                    item["status"] = "MULTIPLE_PENDING_MEETINGS"
        return report
    finally:
        connection.close()


def refresh_due_class_meetings():
    settings = get_settings()
    report = {"enabled": False, "scanned": 0, "updated": 0, "failed": 0,
              "repair_required": 0, "review_required": 0,
              "progression_mode": "CLASS_MEETING_NEXT_DAY"}
    if settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations):
        return report
    if not settings.signin_api_base_url or not settings.signin_service_api_key:
        raise ValueError("签到服务未配置")
    report["enabled"] = True
    today = datetime.now(CHINA).date()
    due = _candidates(settings, today)
    report["scanned"] = len(due)
    for class_id, candidates in due.items():
        if len({candidate[3] for candidate in candidates}) > 1:
            # An earlier, completed event stays visible throughout the recovery window.
            group_ids = tuple(candidate[0] for candidate in candidates)
            placeholders = ",".join("?" for _ in group_ids)
            closed = fetch_all(
                "SELECT e.external_group_id FROM class_learning_cycles c "
                "JOIN attendance_event_groups e ON e.id=c.source_event_group_id "
                "WHERE c.class_org_unit_id=? AND c.cycle_status='CLOSED' AND e.source_key='signin' "
                f"AND e.external_group_id IN ({placeholders})",
                (class_id, *group_ids),
            )
            completed = {row["external_group_id"] for row in closed}
            candidates = [candidate for candidate in candidates if candidate[0] not in completed]
        if not candidates:
            continue
        if len(candidates) != 1:
            report["review_required"] += 1
            logger.warning("CLASS_MEETING_ADVANCE_REVIEW class_id=%s reason=multiple_candidates", class_id)
            continue
        try:
            group_id, group, start, boundary = candidates[0]
            recovering = boundary.astimezone(CHINA).date() < today
            report["updated"] += int(_advance(group_id, group, start, boundary, recovering=recovering))
        except RecoveryReviewRequired as error:
            report["review_required"] += 1
            logger.warning("CLASS_MEETING_ADVANCE_REVIEW class_id=%s reason=%s", class_id, error)
        except Exception as error:
            report["failed"] += 1
            logger.warning("CLASS_MEETING_ADVANCE_FAILED class_id=%s error_type=%s", class_id, type(error).__name__)
    return report
