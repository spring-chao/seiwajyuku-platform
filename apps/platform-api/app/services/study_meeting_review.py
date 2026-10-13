"""Whole-meeting verification stored in the existing append-only audit trail."""
from __future__ import annotations

import hashlib
import json

from app.db import execute, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids
from app.services.study_meetings import (
    StudyMeetingError, StudyMeetingPermissionError, _db_timestamp, _lock_session,
    _require_correctable_session, _require_write, _serialize,
    get_study_meeting_record_for_operations,
)
from app.services.study_meeting_attendees import can_edit_attendees


def _fingerprints(connection, ids: tuple[int, ...]) -> dict[int, str]:
    placeholders = ",".join("?" for _ in ids)
    snapshots = {sid: {} for sid in ids}
    queries = {
        "session": f"SELECT * FROM study_meeting_sessions WHERE id IN ({placeholders}) ORDER BY id",
        "attendees": f"SELECT * FROM study_meeting_attendances WHERE study_meeting_session_id IN ({placeholders}) ORDER BY id",
        "courses": f"SELECT * FROM study_meeting_courses WHERE study_meeting_session_id IN ({placeholders}) ORDER BY id",
        "completions": "SELECT r.*, c.study_meeting_session_id FROM study_meeting_course_completions r "
            f"JOIN study_meeting_courses c ON c.id=r.study_meeting_course_id WHERE c.study_meeting_session_id IN ({placeholders}) ORDER BY r.id",
        # Photo retention/deletion is not a change to the originally reviewed fact.
        "evidence": "SELECT id, study_meeting_session_id, uploaded_at FROM study_meeting_evidence "
            f"WHERE study_meeting_session_id IN ({placeholders}) ORDER BY id",
    }
    for key, sql in queries.items():
        for row in execute(connection, sql, ids).fetchall():
            item = {k: _serialize(v) for k, v in dict(row).items()}
            sid = int(item["id"] if key == "session" else item["study_meeting_session_id"])
            snapshots[sid].setdefault(key, []).append(item)
    return {sid: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
            for sid, value in snapshots.items()}


def add_operation_metadata(connection, records: list[dict]) -> None:
    """Batch counts and verification reads; do not expose the underlying roster."""
    if not records:
        return
    ids = tuple(int(row["id"]) for row in records)
    fingerprints = _fingerprints(connection, ids)
    groups = tuple(sorted({row["study_group_org_unit_id"] for row in records}))
    now = _db_timestamp(connection)
    placeholders = ",".join("?" for _ in groups)
    counts = {row["org_unit_id"]: int(row["n"]) for row in execute(connection,
        "SELECT r.org_unit_id, COUNT(DISTINCT r.member_id) AS n FROM member_org_relations r "
        "JOIN members m ON m.id=r.member_id AND m.status='ACTIVE' "
        f"WHERE r.org_unit_id IN ({placeholders}) AND r.relation_type='STUDY_GROUP' "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "GROUP BY r.org_unit_id", (*groups, now, now)).fetchall()}
    placeholders = ",".join("?" for _ in ids)
    audits = execute(connection,
        "SELECT a.resource_id, a.actor_user_id, a.created_at, a.after_json, u.display_name AS reviewer_name "
        "FROM audit_logs a LEFT JOIN app_users u ON u.id=a.actor_user_id "
        "WHERE a.action='study_meeting.review_confirm' AND a.resource_type='study_meeting_session' "
        f"AND a.result='SUCCESS' AND a.resource_id IN ({placeholders}) ORDER BY a.id DESC",
        tuple(str(sid) for sid in ids)).fetchall()
    latest = {}
    for audit in audits:
        latest.setdefault(int(audit["resource_id"]), dict(audit))
    for record in records:
        sid = int(record["id"])
        home_count = int(record.get("home_count", len(record.get("home_attendees", []))) or 0)
        record["home_count"] = home_count
        record["cross_group_count"] = int(record.get("cross_group_count", len(record.get("cross_group_attendees", []))) or 0)
        record["total_count"] = home_count + record["cross_group_count"]
        count = counts.get(record["study_group_org_unit_id"], 0)
        record["home_group_member_count"] = count
        record["home_group_attendance_rate"] = round(home_count * 100 / count, 1) if count and home_count <= count else None
        record["review_fingerprint"] = fingerprints[sid]
        audit = latest.get(sid)
        confirmed = bool(audit and record["status"] == "SUBMITTED" and
            json.loads(audit["after_json"] or "{}").get("fingerprint") == fingerprints[sid])
        record["review_status"] = "CONFIRMED" if confirmed else "UNCONFIRMED"
        record["reviewed_at"] = _serialize(audit["created_at"]) if confirmed else None
        record["reviewer_name"] = audit["reviewer_name"] if confirmed else None


def confirm_meeting_review(*, actor_user_id: int, session_id: int, expected_fingerprint: str) -> dict:
    _require_write()
    if not can_edit_attendees(actor_user_id):
        raise StudyMeetingPermissionError("无学习会核实权限或人工核实功能未开启")
    allowed = accessible_org_ids(actor_user_id, "study_meetings:attendees_edit")
    with transaction() as connection:
        session = _lock_session(connection, session_id)
        if allowed is not None and session["class_org_unit_id"] not in allowed:
            raise StudyMeetingPermissionError("学习会记录不在当前组织授权范围内")
        if session["status"] != "SUBMITTED":
            raise StudyMeetingError("仅能核实已提交的学习会")
        _require_correctable_session(connection, session)
        fingerprint = _fingerprints(connection, (session_id,))[session_id]
        if fingerprint != expected_fingerprint:
            raise StudyMeetingError("学习会内容已变化，请刷新详情并重新核实")
        last = execute(connection, "SELECT after_json FROM audit_logs WHERE action='study_meeting.review_confirm' "
            "AND resource_type='study_meeting_session' AND resource_id=? AND result='SUCCESS' ORDER BY id DESC LIMIT 1",
            (str(session_id),)).fetchone()
        if not last or json.loads(last["after_json"] or "{}").get("fingerprint") != fingerprint:
            write_audit(connection, actor_user_id=actor_user_id, action="study_meeting.review_confirm",
                resource_type="study_meeting_session", resource_id=str(session_id),
                org_unit_id=session["class_org_unit_id"], purpose="人工核实本场参加人员及观看情况",
                after={"fingerprint": fingerprint, "review_status": "CONFIRMED"})
    return get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)
