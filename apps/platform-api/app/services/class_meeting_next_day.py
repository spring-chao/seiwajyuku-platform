"""Advance learning cycles from confirmed, platform-created class meetings."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.core.settings import get_settings
from app.db import execute, transaction
from app.services import learning_cycles as cycles
from app.services.attendance_sync import _upsert_event_group
from app.services.learning_cycle_schedule import parse_utc_datetime

CHINA = ZoneInfo("Asia/Shanghai")


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


def _due_group(rows, today):
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
    if end_day + timedelta(days=1) != today:
        return None
    return group, min(start for start, _ in intervals)


def _advance(group_id, group, start, boundary):
    class_id = group["study_org_unit_id"]
    with transaction() as connection:
        cycles._lock_class_for_update(connection, class_id)
        binding = cycles._active_binding(connection, class_id)
        if not binding:
            return False
        cycle = cycles._cycle_at(connection, int(binding["id"]), boundary.isoformat())
        if (not cycle or cycle["cycle_status"] != "OPEN"
                or start <= parse_utc_datetime(cycle["opened_at"])):
            return False
        event = execute(connection,
            "SELECT id FROM attendance_event_groups WHERE source_key='signin' AND external_group_id=?",
            (group_id,)).fetchone()
        if event and execute(connection,
            "SELECT id FROM class_learning_cycles WHERE source_event_group_id=? AND cycle_status='CLOSED'",
            (event["id"],)).fetchone():
            return False
        if cycle.get("source_event_group_id") and (
            not event or cycle["source_event_group_id"] != event["id"]
        ):
            raise ValueError("本期已关联不同的班会活动")
        group_pk = _upsert_event_group(connection, source_key="signin",
            external_group_id=group_id, group_data={**group, "status": "ACTIVE"}, now=cycles._now())
        cycles.confirm_class_meeting(
            actor_user_id=None, class_org_unit_id=class_id, actual_class_meeting_at=start.isoformat(),
            source_event_group_id=group_pk, confirmation_reason="已确认的班会活动结束后次日推进",
            learning_cycle_id=cycle["id"], _connection=connection, _scheduled_boundary=boundary.isoformat(),
        )
        return True


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
    boundary = datetime.combine(today, time.min, CHINA)
    due = defaultdict(list)
    for group_id, rows in _load_groups(settings).items():
        candidate = _due_group(rows, today)
        if candidate:
            group, start = candidate
            due[group["study_org_unit_id"]].append((group_id, group, start))
    report["scanned"] = len(due)
    for candidates in due.values():
        if len(candidates) != 1:
            report["review_required"] += 1
            continue
        try:
            report["updated"] += int(_advance(*candidates[0], boundary))
        except ValueError:
            report["failed"] += 1
    return report
