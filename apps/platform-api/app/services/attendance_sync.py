"""attendance_sync service - incremental pull from signin system.

The platform pulls attendance data from the signin system (cloud function)
via machine-to-machine API using SIGNIN_SERVICE_API_KEY.

Flow:
1. GET /ops/v1/attendance/sessions?cursor=... → sessions
2. GET /ops/v1/attendance/records?cursor=... → records
3. Upsert into attendance_event_groups, attendance_sessions, attendance_records
4. Calculate scores for each record
5. Track sync state in attendance_sync_runs
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.settings import get_settings
from app.db import execute, fetch_one, transaction
from app.services.attendance_scoring import normalize_activity_type, upsert_score_record


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sync_run_start(source_key: str, cursor_before: str | None) -> int:
    now = _now()
    with transaction() as connection:
        cursor = execute(
            connection,
            "INSERT INTO attendance_sync_runs"
            "(source_key, cursor_before, status, started_at) "
            "VALUES (?, ?, 'RUNNING', ?)",
            (source_key, cursor_before, now),
        )
        return cursor.lastrowid


def _sync_run_finish(
    run_id: int,
    *,
    cursor_after: str | None,
    received_sessions: int,
    received_records: int,
    inserted: int,
    updated: int,
    ignored: int,
    errors: int,
    status: str = "SUCCESS",
    error_summary: str | None = None,
) -> None:
    now = _now()
    with transaction() as connection:
        execute(
            connection,
            "UPDATE attendance_sync_runs SET cursor_after=?, received_sessions=?, "
            "received_records=?, inserted_count=?, updated_count=?, ignored_count=?, "
            "error_count=?, status=?, finished_at=?, error_summary=? WHERE id=?",
            (
                cursor_after, received_sessions, received_records,
                inserted, updated, ignored, errors,
                status, now, error_summary, run_id,
            ),
        )


def _upsert_event_group(
    connection, *, source_key: str, external_group_id: str, group_data: dict, now: str
) -> int:
    org_unit_id = str(group_data.get("org_unit_id") or "").strip()
    event_date = group_data.get("event_date")
    if not external_group_id:
        raise ValueError("签到场次缺少 external_group_id")
    if not org_unit_id:
        raise ValueError(f"签到活动 {external_group_id} 缺少 org_unit_id")
    if not event_date:
        raise ValueError(f"签到活动 {external_group_id} 缺少 event_date")
    org_exists = execute(
        connection, "SELECT id FROM org_units WHERE id=?", (org_unit_id,)
    ).fetchone()
    if not org_exists:
        raise ValueError(f"签到活动组织不存在: {org_unit_id}")
    existing = execute(
        connection,
        "SELECT id FROM attendance_event_groups "
        "WHERE source_key=? AND external_group_id=?",
        (source_key, external_group_id),
    ).fetchone()
    fields = {
        "org_unit_id": org_unit_id,
        "study_org_unit_id": group_data.get("study_org_unit_id"),
        "title": group_data.get("title"),
        "activity_type": normalize_activity_type(group_data.get("activity_type")),
        "event_date": event_date,
        "status": str(group_data.get("status") or "ACTIVE").upper(),
        "source_updated_at": group_data.get("updated_at"),
        "updated_at": now,
    }
    if existing:
        group_id = existing["id"] if hasattr(existing, "keys") else existing[0]
        set_clause = ", ".join(f"{k}=?" for k in fields)
        execute(
            connection,
            f"UPDATE attendance_event_groups SET {set_clause} WHERE id=?",
            (*fields.values(), group_id),
        )
        return group_id
    cursor = execute(
        connection,
        "INSERT INTO attendance_event_groups"
        "(source_key, external_group_id, org_unit_id, study_org_unit_id, title, "
        "activity_type, event_date, status, source_updated_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_key, external_group_id, fields["org_unit_id"],
            fields["study_org_unit_id"], fields["title"],
            fields["activity_type"], fields["event_date"],
            fields["status"], fields["source_updated_at"],
            now, now,
        ),
    )
    return cursor.lastrowid


def _upsert_session(
    connection, *, event_group_id: int, session_data: dict, now: str
) -> int:
    external_session_id = session_data.get("external_session_id", session_data.get("session_id"))
    existing = execute(
        connection,
        "SELECT id FROM attendance_sessions "
        "WHERE event_group_id=? AND external_session_id=?",
        (event_group_id, external_session_id),
    ).fetchone() if external_session_id else None

    fields = {
        "session_code": str(session_data.get("session_code") or "MORNING").upper(),
        "session_name": session_data.get("session_name"),
        "session_order": session_data.get("session_order", 0),
        "checkin_start_at": session_data.get("checkin_start_at"),
        "scheduled_start_at": session_data.get("scheduled_start_at"),
        "scheduled_end_at": session_data.get("scheduled_end_at"),
        "checkin_end_at": session_data.get("checkin_end_at"),
        "status": str(session_data.get("status") or "ACTIVE").upper(),
        "source_revision": session_data.get("revision"),
        "source_updated_at": session_data.get("updated_at"),
        "finalized_at": session_data.get("finalized_at"),
        "updated_at": now,
    }
    if existing:
        session_id = existing["id"] if hasattr(existing, "keys") else existing[0]
        set_clause = ", ".join(f"{k}=?" for k in fields)
        execute(
            connection,
            f"UPDATE attendance_sessions SET {set_clause} WHERE id=?",
            (*fields.values(), session_id),
        )
        return session_id
    cursor = execute(
        connection,
        "INSERT INTO attendance_sessions"
        "(event_group_id, external_session_id, session_code, session_name, session_order, "
        "checkin_start_at, scheduled_start_at, scheduled_end_at, checkin_end_at, "
        "status, source_revision, source_updated_at, finalized_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_group_id, external_session_id, fields["session_code"],
            fields["session_name"], fields["session_order"],
            fields["checkin_start_at"], fields["scheduled_start_at"],
            fields["scheduled_end_at"], fields["checkin_end_at"],
            fields["status"], fields["source_revision"],
            fields["source_updated_at"], fields["finalized_at"],
            now, now,
        ),
    )
    return cursor.lastrowid


def _upsert_record(
    connection, *, session_id: int, record_data: dict, now: str
) -> tuple[int, bool, int | None, str, bool]:
    """Upsert a record and return its effective scoring identity and status."""
    external_record_id = record_data.get("external_record_id") or record_data.get("record_id")
    existing = None
    if external_record_id:
        existing = execute(
            connection,
            "SELECT id, source_revision, member_id, attendance_status, score_eligible "
            "FROM attendance_records "
            "WHERE attendance_session_id=? AND external_record_id=?",
            (session_id, external_record_id),
        ).fetchone()

    # Check revision: skip if incoming revision is older
    incoming_revision = record_data.get("revision")
    if existing and incoming_revision is not None:
        existing_rev = existing["source_revision"]
        if existing_rev is not None and int(incoming_revision) < int(existing_rev):
            return (
                existing["id"],
                False,
                existing["member_id"],
                existing["attendance_status"],
                bool(existing["score_eligible"]),
            )

    member_id = record_data.get("member_id")
    member_code = str(record_data.get("member_code") or "").strip()
    if member_id:
        matched = execute(
            connection, "SELECT id FROM members WHERE id=?", (member_id,)
        ).fetchone()
        member_id = matched["id"] if matched else None
    elif member_code:
        matched = execute(
            connection, "SELECT id FROM members WHERE member_code=?", (member_code,)
        ).fetchone()
        member_id = matched["id"] if matched else None

    participant_type = str(record_data.get("participant_type") or "MEMBER").upper()
    attendance_status = str(
        record_data.get("attendance_status") or "ABSENT"
    ).upper()
    score_eligible = bool(record_data.get("score_eligible", True))
    if participant_type == "MEMBER" and member_id is None:
        attendance_status = "UNMATCHED"
        score_eligible = False

    fields = {
        "external_registration_id": record_data.get("external_registration_id"),
        "member_id": member_id,
        "member_code_snapshot": member_code or None,
        "name_snapshot": record_data.get("name"),
        "participant_type": participant_type,
        "score_eligible": 1 if score_eligible else 0,
        "attendance_status": attendance_status,
        "checked_at": record_data.get("checked_at"),
        "checkin_source": record_data.get("checkin_source"),
        "source_revision": incoming_revision,
        "source_updated_at": record_data.get("updated_at"),
        "updated_at": now,
    }

    if existing:
        record_id = existing["id"] if hasattr(existing, "keys") else existing[0]
        set_clause = ", ".join(f"{k}=?" for k in fields)
        execute(
            connection,
            f"UPDATE attendance_records SET {set_clause} WHERE id=?",
            (*fields.values(), record_id),
        )
        return (record_id, False, member_id, attendance_status, score_eligible)

    cursor = execute(
        connection,
        "INSERT INTO attendance_records"
        "(attendance_session_id, external_record_id, external_registration_id, member_id, "
        "member_code_snapshot, name_snapshot, participant_type, score_eligible, "
        "attendance_status, checked_at, checkin_source, source_revision, source_updated_at, "
        "received_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id, external_record_id, fields["external_registration_id"],
            fields["member_id"], fields["member_code_snapshot"],
            fields["name_snapshot"], fields["participant_type"],
            fields["score_eligible"], fields["attendance_status"],
            fields["checked_at"], fields["checkin_source"],
            fields["source_revision"], fields["source_updated_at"],
            now, now, now,
        ),
    )
    return (
        cursor.lastrowid,
        True,
        member_id,
        attendance_status,
        score_eligible,
    )


def sync_from_signin(cursor: str | None = None) -> dict[str, Any]:
    """Pull attendance data from the signin system.

    Returns sync summary with next cursor.
    """
    settings = get_settings()
    if not settings.signin_api_base_url or not settings.signin_service_api_key:
        raise ValueError("签到服务地址或密钥未配置 (SIGNIN_API_BASE_URL / SIGNIN_SERVICE_API_KEY)")

    source_key = "signin"
    run_id = _sync_run_start(source_key, cursor)
    base_url = settings.signin_api_base_url.rstrip("/")
    headers = {"X-API-Key": settings.signin_service_api_key}

    try:
        # Step 1: Pull sessions
        sessions_url = f"{base_url}/ops/v1/attendance/sessions"
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor

        received_sessions = 0
        received_records = 0
        inserted = 0
        updated = 0
        ignored = 0
        errors = 0
        error_messages: list[str] = []
        next_cursor = None

        with httpx.Client(timeout=30.0) as client:
            # Pull sessions
            resp = client.get(sessions_url, params=params, headers=headers)
            resp.raise_for_status()
            sessions_data = resp.json()

            session_items = sessions_data.get("items", [])
            next_cursor = sessions_data.get("next_cursor")

            # Pull records for each session
            records_url = f"{base_url}/ops/v1/attendance/records"
            now = _now()

            for session_item in session_items:
                received_sessions += 1
                try:
                    with transaction() as connection:
                        # Upsert event group
                        group_data = session_item.get("event_group", {})
                        external_group_id = group_data.get("external_group_id") or session_item.get("event_id")
                        group_id = _upsert_event_group(
                            connection,
                            source_key=source_key,
                            external_group_id=external_group_id,
                            group_data=group_data,
                            now=now,
                        )
                        # Upsert session
                        session_id = _upsert_session(
                            connection,
                            event_group_id=group_id,
                            session_data=session_item,
                            now=now,
                        )

                    # Pull every records page for this session.
                    records_cursor: str | None = None
                    while True:
                        rec_params = {
                            "session_id": session_item.get("session_id"),
                            "limit": 500,
                        }
                        if records_cursor:
                            rec_params["cursor"] = records_cursor
                        rec_resp = client.get(
                            records_url, params=rec_params, headers=headers
                        )
                        rec_resp.raise_for_status()
                        records_data = rec_resp.json()

                        for record_data in records_data.get("items", []):
                            received_records += 1
                            try:
                                with transaction() as connection:
                                    (
                                        record_id,
                                        was_inserted,
                                        member_id,
                                        attendance_status,
                                        score_eligible,
                                    ) = _upsert_record(
                                        connection,
                                        session_id=session_id,
                                        record_data=record_data,
                                        now=now,
                                    )
                                if was_inserted:
                                    inserted += 1
                                else:
                                    updated += 1

                                upsert_score_record(
                                    attendance_record_id=record_id,
                                    member_id=member_id,
                                    session_code=session_item.get(
                                        "session_code", "MORNING"
                                    ),
                                    attendance_status=attendance_status,
                                    checked_at=record_data.get("checked_at"),
                                    scheduled_start_at=session_item.get(
                                        "scheduled_start_at"
                                    ),
                                    score_eligible=score_eligible,
                                    activity_type=normalize_activity_type(
                                        group_data.get("activity_type")
                                    ),
                                    source_updated_at=record_data.get("updated_at"),
                                )
                            except Exception as exc:
                                errors += 1
                                error_messages.append(
                                    f"record {record_data.get('external_record_id')}: {exc}"
                                )
                        records_cursor = records_data.get("next_cursor")
                        if not records_cursor:
                            break
                except Exception as exc:
                    errors += 1
                    error_messages.append(
                        f"session {session_item.get('session_id')}: {exc}"
                    )

        status = "PARTIAL" if errors else "SUCCESS"
        _sync_run_finish(
            run_id,
            cursor_after=next_cursor,
            received_sessions=received_sessions,
            received_records=received_records,
            inserted=inserted,
            updated=updated,
            ignored=ignored,
            errors=errors,
            status=status,
            error_summary="; ".join(error_messages[:20]) or None,
        )

        return {
            "run_id": run_id,
            "received_sessions": received_sessions,
            "received_records": received_records,
            "inserted": inserted,
            "updated": updated,
            "ignored": ignored,
            "errors": errors,
            "status": status,
            "next_cursor": next_cursor,
            "has_more": bool(next_cursor),
        }

    except Exception as exc:
        _sync_run_finish(
            run_id,
            cursor_after=None,
            received_sessions=0,
            received_records=0,
            inserted=0,
            updated=0,
            ignored=0,
            errors=1,
            status="ERROR",
            error_summary=str(exc),
        )
        raise
