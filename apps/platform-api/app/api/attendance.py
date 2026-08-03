"""attendance API - endpoints for attendance management and scoring.

Endpoints:
- POST /attendance/sync: Trigger incremental sync from signin system
- GET /attendance/event-groups: List event groups
- GET /attendance/event-groups/{id}: Get event group detail with sessions
- GET /attendance/records: List attendance records with filters
- POST /attendance/records/{id}/adjudications: Create adjudication
- POST /attendance/event-groups/{id}/recalculate: Recalculate scores
"""
from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.core.settings import get_settings
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.attendance_scoring import (
    get_active_rule,
    normalize_activity_type,
    recalculate_event_group,
    score_is_applicable,
    upsert_score_record,
)
from app.services.attendance_sync import sync_from_signin
from app.services.iam import accessible_org_ids


router = APIRouter(prefix="/api/v1/attendance", tags=["attendance"])


class AdjudicationPayload(BaseModel):
    adjudication_type: str = Field(pattern="^(EARLY_LEAVE|CANCEL_EARLY_LEAVE|MANUAL_CHECKIN|INVALIDATE_CHECKIN|LEAVE|CANCEL_LEAVE|MEMBER_RELINK)$")
    occurred_at: str | None = None
    reason: str = Field(min_length=4, max_length=500)
    member_id: int | None = None


def _attendance_sync_health() -> dict:
    rows = fetch_all(
        "SELECT id, status, started_at, finished_at, received_sessions, "
        "received_records, error_count, error_summary "
        "FROM attendance_sync_runs WHERE source_key=? "
        "ORDER BY id DESC LIMIT 20",
        ("signin",),
    )
    if not rows:
        return {
            "state": "NO_RUNS",
            "alert_threshold": 3,
            "consecutive_failure_count": 0,
            "last_run": None,
        }

    latest = rows[0]
    consecutive_failures = 0
    for row in rows:
        status = str(row["status"]).upper()
        if status == "RUNNING":
            continue
        if status == "SUCCESS":
            break
        if status in {"ERROR", "PARTIAL"}:
            consecutive_failures += 1

    latest_status = str(latest["status"]).upper()
    if consecutive_failures >= 3:
        state = "CRITICAL"
    elif consecutive_failures:
        state = "WARNING"
    elif latest_status == "RUNNING":
        state = "RUNNING"
    else:
        state = "HEALTHY"
    return {
        "state": state,
        "alert_threshold": 3,
        "consecutive_failure_count": consecutive_failures,
        "last_run": {
            "status": latest_status,
            "started_at": latest["started_at"],
            "finished_at": latest["finished_at"],
            "received_sessions": latest["received_sessions"] or 0,
            "received_records": latest["received_records"] or 0,
            "error_count": latest["error_count"] or 0,
            "has_error_summary": bool(latest["error_summary"]),
        },
    }


def _write_failure_alert_if_threshold_reached() -> None:
    health = _attendance_sync_health()
    if health["consecutive_failure_count"] != health["alert_threshold"]:
        return
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=None,
            action="attendance.sync.failure_alert",
            resource_type="attendance_sync",
            resource_id="signin",
            after={
                "state": health["state"],
                "consecutive_failure_count": health[
                    "consecutive_failure_count"
                ],
                "alert_threshold": health["alert_threshold"],
            },
        )


def _attendance_reconciliation_summary() -> dict:
    """Return only review workload counts; never expose attendance snapshots."""
    queries = {
        "unmatched_attendance_records": (
            "SELECT COUNT(*) AS count FROM attendance_records "
            "WHERE attendance_status='UNMATCHED' OR member_id IS NULL"
        ),
        "active_members_missing_phone_hash": (
            "SELECT COUNT(*) AS count FROM members "
            "WHERE status='ACTIVE' AND (phone_hash IS NULL OR phone_hash='')"
        ),
        "active_members_missing_primary_region": (
            "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM member_org_relations r "
            "WHERE r.member_id=m.id AND r.relation_type='PRIMARY_REGION')"
        ),
        "active_members_missing_study_class": (
            "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM member_org_relations r "
            "WHERE r.member_id=m.id AND r.relation_type='STUDY_CLASS')"
        ),
        "active_members_missing_study_group": (
            "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM member_org_relations r "
            "WHERE r.member_id=m.id AND r.relation_type='STUDY_GROUP')"
        ),
    }
    return {
        "scope": "AGGREGATE_ONLY",
        "write_enabled": False,
        "items": [
            {
                "key": key,
                "count": int((fetch_one(statement) or {"count": 0})["count"] or 0),
            }
            for key, statement in queries.items()
        ],
    }


def _org_is_allowed(
    primary_org_id: str,
    study_org_unit_id: str | None,
    allowed: set[str] | None,
) -> bool:
    return allowed is None or bool(
        {primary_org_id, study_org_unit_id}.intersection(allowed)
    )


def _verify_signin_service_key(x_api_key: str | None) -> None:
    expected = get_settings().signin_service_api_key
    if not expected:
        raise HTTPException(503, "签到服务密钥未配置")
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key 请求头")
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(403, "API Key 无效")


def _run_sync(cursor: str | None, actor_user_id: int | None, action: str) -> dict:
    try:
        result = sync_from_signin(cursor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"同步失败: {exc}") from exc
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action=action,
            resource_type="attendance_sync_run",
            resource_id=str(result["run_id"]),
            after={
                "status": result["status"],
                "received_sessions": result["received_sessions"],
                "received_records": result["received_records"],
                "errors": result["errors"],
            },
        )
    return {"success": True, "data": result}


@router.post("/sync")
def sync(
    cursor: str | None = None,
    user: dict = Depends(require_permission("attendance:sync")),
) -> dict:
    """Trigger incremental sync from signin system."""
    return _run_sync(cursor, user["id"], "attendance.sync.trigger")


@router.post("/sync/scheduled")
def scheduled_sync(
    cursor: str | None = None,
    x_api_key: str | None = Header(default=None),
) -> dict:
    """Run the weekday timer pull using the signin service credential."""
    _verify_signin_service_key(x_api_key)
    try:
        result = _run_sync(cursor, None, "attendance.sync.scheduled")
    except HTTPException:
        _write_failure_alert_if_threshold_reached()
        raise
    if result["data"]["status"] == "PARTIAL":
        _write_failure_alert_if_threshold_reached()
    return result


@router.get("/sync/status")
def sync_status(
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    """Return privacy-safe health details for the signin synchronization."""
    return {"success": True, "data": _attendance_sync_health()}


@router.get("/reconciliation-summary")
def reconciliation_summary(
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    """Return privacy-safe aggregate workload for manual data review."""
    return {"success": True, "data": _attendance_reconciliation_summary()}


@router.get("/reconciliation-queue")
def reconciliation_queue(
    issue: Literal[
        "unmatched_attendance_records",
        "active_members_missing_phone_hash",
        "active_members_missing_primary_region",
        "active_members_missing_study_class",
        "active_members_missing_study_group",
    ] = "unmatched_attendance_records",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_permission("members:detail_view")),
) -> dict:
    """Return a paged read-only queue for authorized manual review."""
    queries = {
        "unmatched_attendance_records": {
            "count": "SELECT COUNT(*) AS count FROM attendance_records "
            "WHERE attendance_status='UNMATCHED' OR member_id IS NULL",
            "rows": "SELECT r.id, r.member_code_snapshot, r.name_snapshot, "
            "r.attendance_status, r.checked_at, s.session_name, "
            "eg.title, eg.event_date, eg.org_unit_id, eg.study_org_unit_id "
            "FROM attendance_records r JOIN attendance_sessions s "
            "ON s.id=r.attendance_session_id JOIN attendance_event_groups eg "
            "ON eg.id=s.event_group_id WHERE r.attendance_status='UNMATCHED' "
            "OR r.member_id IS NULL ORDER BY eg.event_date DESC, r.id DESC",
        },
        "active_members_missing_phone_hash": {
            "count": "SELECT COUNT(*) AS count FROM members "
            "WHERE status='ACTIVE' AND (phone_hash IS NULL OR phone_hash='')",
            "rows": "SELECT id, member_code AS member_code_snapshot, name AS name_snapshot, "
            "status AS attendance_status, NULL AS checked_at, NULL AS session_name, "
            "NULL AS title, NULL AS event_date, org_unit_id, development_org_unit_id AS study_org_unit_id "
            "FROM members WHERE status='ACTIVE' AND (phone_hash IS NULL OR phone_hash='') "
            "ORDER BY id DESC",
        },
        "active_members_missing_primary_region": {
            "count": "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM member_org_relations r WHERE r.member_id=m.id "
            "AND r.relation_type='PRIMARY_REGION')",
            "rows": "SELECT m.id, m.member_code AS member_code_snapshot, m.name AS name_snapshot, "
            "m.status AS attendance_status, NULL AS checked_at, NULL AS session_name, NULL AS title, "
            "NULL AS event_date, m.org_unit_id, m.development_org_unit_id AS study_org_unit_id "
            "FROM members m WHERE m.status='ACTIVE' AND NOT EXISTS (SELECT 1 FROM member_org_relations r "
            "WHERE r.member_id=m.id AND r.relation_type='PRIMARY_REGION') ORDER BY m.id DESC",
        },
        "active_members_missing_study_class": {
            "count": "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM member_org_relations r WHERE r.member_id=m.id "
            "AND r.relation_type='STUDY_CLASS')",
            "rows": "SELECT m.id, m.member_code AS member_code_snapshot, m.name AS name_snapshot, "
            "m.status AS attendance_status, NULL AS checked_at, NULL AS session_name, NULL AS title, "
            "NULL AS event_date, m.org_unit_id, m.development_org_unit_id AS study_org_unit_id "
            "FROM members m WHERE m.status='ACTIVE' AND NOT EXISTS (SELECT 1 FROM member_org_relations r "
            "WHERE r.member_id=m.id AND r.relation_type='STUDY_CLASS') ORDER BY m.id DESC",
        },
        "active_members_missing_study_group": {
            "count": "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
            "AND NOT EXISTS (SELECT 1 FROM member_org_relations r WHERE r.member_id=m.id "
            "AND r.relation_type='STUDY_GROUP')",
            "rows": "SELECT m.id, m.member_code AS member_code_snapshot, m.name AS name_snapshot, "
            "m.status AS attendance_status, NULL AS checked_at, NULL AS session_name, NULL AS title, "
            "NULL AS event_date, m.org_unit_id, m.development_org_unit_id AS study_org_unit_id "
            "FROM members m WHERE m.status='ACTIVE' AND NOT EXISTS (SELECT 1 FROM member_org_relations r "
            "WHERE r.member_id=m.id AND r.relation_type='STUDY_GROUP') ORDER BY m.id DESC",
        },
    }[issue]
    total = int((fetch_one(queries["count"]) or {"count": 0})["count"] or 0)
    rows = fetch_all(
        f"{queries['rows']} LIMIT ? OFFSET ?", (limit, offset)
    )
    return {
        "success": True,
        "data": {
            "scope": "MANUAL_REVIEW_READ_ONLY",
            "issue": issue,
            "write_enabled": False,
            "total": total,
            "limit": limit,
            "offset": offset,
            "rows": rows,
        },
    }


@router.get("/event-groups")
def list_event_groups(
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    org_unit_id: str | None = None,
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    """List attendance event groups."""
    params: list = []
    sql = (
        "SELECT eg.id, eg.source_key, eg.title, eg.event_date, "
        "eg.activity_type, eg.status, "
        "eg.org_unit_id, o.name AS org_name, eg.study_org_unit_id, "
        "(SELECT COUNT(*) FROM attendance_sessions s "
        "WHERE s.event_group_id=eg.id) AS session_count, "
        "(SELECT COUNT(*) FROM attendance_records r "
        "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "WHERE s.event_group_id=eg.id) AS record_count, "
        "(SELECT COUNT(*) FROM attendance_records r "
        "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "WHERE s.event_group_id=eg.id "
        "AND r.attendance_status IN ('PRESENT','MANUAL_PRESENT')) AS present_count "
        "FROM attendance_event_groups eg "
        "JOIN org_units o ON o.id=eg.org_unit_id "
        "WHERE 1=1"
    )
    if month:
        sql += " AND substr(eg.event_date, 1, 7)=?"
        params.append(month)
    if org_unit_id:
        sql += " AND eg.org_unit_id=?"
        params.append(org_unit_id)
    allowed = accessible_org_ids(user["id"])
    if allowed is not None:
        if not allowed:
            return {"success": True, "data": []}
        placeholders = ",".join("?" for _ in allowed)
        sql += (
            f" AND (eg.org_unit_id IN ({placeholders}) "
            f"OR eg.study_org_unit_id IN ({placeholders}))"
        )
        params.extend(sorted(allowed))
        params.extend(sorted(allowed))
    sql += " ORDER BY eg.event_date DESC"
    rows = fetch_all(sql, tuple(params))
    return {"success": True, "data": rows}


@router.get("/event-groups/{group_id}")
def event_group_detail(
    group_id: int,
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    """Get event group detail with sessions and score summary."""
    group = fetch_one(
        "SELECT eg.*, o.name AS org_name "
        "FROM attendance_event_groups eg "
        "JOIN org_units o ON o.id=eg.org_unit_id "
        "WHERE eg.id=?",
        (group_id,),
    )
    if not group:
        raise HTTPException(404, "活动组不存在")
    allowed = accessible_org_ids(user["id"])
    if not _org_is_allowed(
        group["org_unit_id"], group.get("study_org_unit_id"), allowed
    ):
        raise HTTPException(403, "不在组织授权范围内")
    sessions = fetch_all(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM attendance_records r WHERE r.attendance_session_id=s.id) AS record_count, "
        "(SELECT COUNT(*) FROM attendance_records r WHERE r.attendance_session_id=s.id "
        "AND r.attendance_status IN ('PRESENT','MANUAL_PRESENT')) AS present_count, "
        "(SELECT SUM(sr.final_points) FROM attendance_score_records sr "
        "JOIN attendance_records r ON r.id=sr.attendance_record_id "
        "WHERE r.attendance_session_id=s.id) AS total_points "
        "FROM attendance_sessions s WHERE s.event_group_id=? "
        "ORDER BY s.session_order",
        (group_id,),
    )
    return {"success": True, "data": {"group": group, "sessions": sessions}}


@router.get("/records")
def list_records(
    event_group_id: int | None = None,
    session_id: int | None = None,
    member_id: int | None = None,
    session_code: str | None = None,
    attendance_status: str | None = None,
    is_late: bool | None = None,
    user: dict = Depends(require_permission("members:read")),
) -> dict:
    """List attendance records with filters."""
    params: list = []
    sql = (
        "SELECT r.id, r.attendance_session_id, r.member_id, r.member_code_snapshot, "
        "r.name_snapshot, r.participant_type, r.score_eligible, r.attendance_status, "
        "r.checked_at, r.checkin_source, s.session_code, s.session_name, "
        "eg.title, eg.event_date, eg.org_unit_id, eg.study_org_unit_id, "
        "sr.final_points, sr.is_late, sr.is_early_leave "
        "FROM attendance_records r "
        "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "LEFT JOIN attendance_score_records sr ON sr.attendance_record_id=r.id "
        "WHERE 1=1"
    )
    if event_group_id:
        sql += " AND s.event_group_id=?"
        params.append(event_group_id)
    if session_id:
        sql += " AND r.attendance_session_id=?"
        params.append(session_id)
    if member_id:
        sql += " AND r.member_id=?"
        params.append(member_id)
    if session_code:
        sql += " AND s.session_code=?"
        params.append(session_code)
    if attendance_status:
        sql += " AND r.attendance_status=?"
        params.append(attendance_status)
    if is_late is not None:
        sql += " AND sr.is_late=?"
        params.append(1 if is_late else 0)

    allowed = accessible_org_ids(user["id"])
    if allowed is not None:
        if not allowed:
            return {"success": True, "data": []}
        placeholders = ",".join("?" for _ in allowed)
        sql += (
            f" AND (eg.org_unit_id IN ({placeholders}) "
            f"OR eg.study_org_unit_id IN ({placeholders}))"
        )
        params.extend(sorted(allowed))
        params.extend(sorted(allowed))

    sql += " ORDER BY eg.event_date DESC, s.session_order, r.name_snapshot"
    rows = fetch_all(sql, tuple(params))
    return {"success": True, "data": rows}


@router.post("/records/{record_id}/adjudications")
def create_adjudication(
    record_id: int,
    payload: AdjudicationPayload,
    user: dict = Depends(require_permission("attendance:adjudicate")),
) -> dict:
    """Create an adjudication for an attendance record."""
    record = fetch_one(
        "SELECT r.id, r.attendance_session_id, r.member_id, r.attendance_status, "
        "r.checked_at, eg.org_unit_id, eg.study_org_unit_id "
        "FROM attendance_records r "
        "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "WHERE r.id=?",
        (record_id,),
    )
    if not record:
        raise HTTPException(404, "出勤记录不存在")
    allowed = accessible_org_ids(user["id"])
    if not _org_is_allowed(
        record["org_unit_id"], record.get("study_org_unit_id"), allowed
    ):
        raise HTTPException(403, "不在组织授权范围内")

    if payload.adjudication_type == "MEMBER_RELINK":
        if payload.member_id is None:
            raise HTTPException(400, "MEMBER_RELINK 必须指定 member_id")
        member = fetch_one(
            "SELECT id, org_unit_id FROM members WHERE id=?", (payload.member_id,)
        )
        if not member:
            raise HTTPException(400, "目标学长不存在")
        if allowed is not None and member["org_unit_id"] not in allowed:
            raise HTTPException(403, "目标学长不在组织授权范围内")

    now = datetime.now(UTC).isoformat()
    cancel_targets = {
        "CANCEL_EARLY_LEAVE": "EARLY_LEAVE",
        "CANCEL_LEAVE": "LEAVE",
    }
    status_updates = {
        "MANUAL_CHECKIN": "MANUAL_PRESENT",
        "INVALIDATE_CHECKIN": "INVALIDATED",
        "LEAVE": "LEAVE",
        "CANCEL_LEAVE": "PRESENT" if record["checked_at"] else "ABSENT",
    }

    with transaction() as connection:
        target_type = cancel_targets.get(payload.adjudication_type)
        if target_type:
            cancelled = execute(
                connection,
                "UPDATE attendance_adjudications SET superseded_at=? "
                "WHERE attendance_record_id=? AND adjudication_type=? "
                "AND superseded_at IS NULL",
                (now, record_id, target_type),
            )
            if cancelled.rowcount == 0:
                raise HTTPException(400, f"没有可撤销的 {target_type} 裁定")
        else:
            execute(
                connection,
                "UPDATE attendance_adjudications SET superseded_at=? "
                "WHERE attendance_record_id=? AND adjudication_type=? "
                "AND superseded_at IS NULL",
                (now, record_id, payload.adjudication_type),
            )
        cursor = execute(
            connection,
            "INSERT INTO attendance_adjudications"
            "(attendance_record_id, adjudication_type, occurred_at, reason, actor_user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, payload.adjudication_type, payload.occurred_at or now,
             payload.reason, user["id"], now),
        )
        adj_id = cursor.lastrowid
        if payload.adjudication_type in status_updates:
            execute(
                connection,
                "UPDATE attendance_records SET attendance_status=?, updated_at=? WHERE id=?",
                (status_updates[payload.adjudication_type], now, record_id),
            )
        elif payload.adjudication_type == "MEMBER_RELINK":
            execute(
                connection,
                "UPDATE attendance_records SET member_id=?, "
                "attendance_status=CASE WHEN attendance_status='UNMATCHED' "
                "THEN CASE WHEN checked_at IS NULL THEN 'ABSENT' ELSE 'PRESENT' END "
                "ELSE attendance_status END, score_eligible=1, updated_at=? WHERE id=?",
                (payload.member_id, now, record_id),
            )
        write_audit(
            connection,
            actor_user_id=user["id"],
            action="attendance.adjudication.create",
            resource_type="attendance_record",
            resource_id=str(record_id),
            org_unit_id=record["org_unit_id"],
            purpose=payload.reason,
            before={
                "member_id": record["member_id"],
                "attendance_status": record["attendance_status"],
            },
            after={
                "adjudication_id": adj_id,
                "type": payload.adjudication_type,
                "member_id": payload.member_id or record["member_id"],
                "attendance_status": status_updates.get(
                    payload.adjudication_type, record["attendance_status"]
                ),
            },
        )

    # Recalculate score for this record
    session = fetch_one(
        "SELECT s.session_code, s.scheduled_start_at, eg.activity_type "
        "FROM attendance_sessions s "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "WHERE s.id=?",
        (record["attendance_session_id"],),
    )
    rec = fetch_one("SELECT * FROM attendance_records WHERE id=?", (record_id,))
    activity_type = (
        normalize_activity_type(session["activity_type"]) if session else None
    )
    if (
        session
        and rec
        and score_is_applicable(
            rec["member_id"], bool(rec["score_eligible"]), activity_type
        )
        and get_active_rule(session["session_code"], activity_type)
    ):
        upsert_score_record(
            attendance_record_id=record_id,
            member_id=rec["member_id"],
            session_code=session["session_code"],
            attendance_status=rec["attendance_status"],
            checked_at=rec["checked_at"],
            scheduled_start_at=session["scheduled_start_at"],
            score_eligible=bool(rec["score_eligible"]),
            activity_type=activity_type,
        )

    return {"success": True, "data": {"id": adj_id}}


@router.post("/event-groups/{group_id}/recalculate")
def recalculate(
    group_id: int,
    user: dict = Depends(require_permission("attendance:adjudicate")),
) -> dict:
    """Recalculate all scores for an event group."""
    group = fetch_one(
        "SELECT org_unit_id, study_org_unit_id "
        "FROM attendance_event_groups WHERE id=?",
        (group_id,),
    )
    if not group:
        raise HTTPException(404, "活动组不存在")
    allowed = accessible_org_ids(user["id"])
    if not _org_is_allowed(
        group["org_unit_id"], group.get("study_org_unit_id"), allowed
    ):
        raise HTTPException(403, "不在组织授权范围内")
    result = recalculate_event_group(group_id)
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=user["id"],
            action="attendance.score.recalculate",
            resource_type="attendance_event_group",
            resource_id=str(group_id),
            org_unit_id=group["org_unit_id"],
            after=result,
        )
    return {"success": True, "data": result}
