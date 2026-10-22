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
        fingerprint = _fingerprints(connection, (session_id,))[session_id]
        if fingerprint != expected_fingerprint:
            raise StudyMeetingError("学习会内容已变化，请刷新详情并重新核实")
        last = execute(connection, "SELECT after_json FROM audit_logs WHERE action='study_meeting.review_confirm' "
            "AND resource_type='study_meeting_session' AND resource_id=? AND result='SUCCESS' ORDER BY id DESC LIMIT 1",
            (str(session_id),)).fetchone()
        if not last or json.loads(last["after_json"] or "{}").get("fingerprint") != fingerprint:
            _require_correctable_session(connection, session)
            write_audit(connection, actor_user_id=actor_user_id, action="study_meeting.review_confirm",
                resource_type="study_meeting_session", resource_id=str(session_id),
                org_unit_id=session["class_org_unit_id"], purpose="人工核实本场参加人员及观看情况",
                after={"fingerprint": fingerprint, "review_status": "CONFIRMED"})
    return get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)


def settlement_metadata(connection, session_id: int) -> dict:
    from app.services.credit_settlement_batches import _batch_tables_available
    if not _batch_tables_available(connection):
        return {"status": "PENDING", "posted_points": "0.00", "proposed_points": "0.00", "blocked_count": 0}
    rows = execute(connection,
        "SELECT status,posted_points,proposed_points,blocked_count,source_snapshot_json FROM learning_credit_settlement_batches "
        "WHERE source_type='STUDY_MEETING' AND batch_type='REGULAR' AND batch_no LIKE ? ORDER BY id DESC",
        (f"LC-STUDY-{int(session_id)}-%",)).fetchall()
    for row in rows:
        if int(json.loads(row['source_snapshot_json']).get('session_id',0))==session_id:
            return {key: str(row[key]) if key.endswith('points') else row[key]
                for key in ('status','posted_points','proposed_points','blocked_count')}
    return {"status": "PENDING", "posted_points": "0.00", "proposed_points": "0.00", "blocked_count": 0}


def assert_settlement_confirmation(connection, actor: int, batch, confirmation: dict) -> None:
    """A reviewed meeting is the sole authority for this private posting path."""
    from app.services import learning_credits as credits
    if batch['source_type']!='STUDY_MEETING' or batch['batch_type']!='REGULAR':
        raise credits.LearningCreditError('学习会自动结算来源不匹配')
    session_id=int(confirmation['session_id'])
    if int(json.loads(batch['source_snapshot_json']).get('session_id',0))!=session_id:
        raise credits.LearningCreditError('学习会与结算来源不一致')
    if not can_edit_attendees(actor):
        raise StudyMeetingPermissionError('无学习会核实权限或人工核实功能未开启')
    for permission in ('study_meetings:attendees_edit','plans:credit_settlement_manage','plans:credit_settlement_post'):
        scope=accessible_org_ids(actor,permission)
        if scope is not None and batch['class_org_unit_id'] not in scope:
            raise StudyMeetingPermissionError('学习会结算不在当前组织授权范围内')
    session=_lock_session(connection,session_id)
    if session['status']!='SUBMITTED' or session['class_org_unit_id']!=batch['class_org_unit_id']:
        raise credits.LearningCreditError('学习会状态或班级已变化')
    fingerprint=_fingerprints(connection,(session_id,))[session_id]
    audit=execute(connection,"SELECT after_json FROM audit_logs WHERE action='study_meeting.review_confirm' "
        "AND resource_type='study_meeting_session' AND resource_id=? AND result='SUCCESS' ORDER BY id DESC LIMIT 1",(str(session_id),)).fetchone()
    if (fingerprint!=confirmation['review_fingerprint'] or not audit
        or json.loads(audit['after_json'] or '{}').get('fingerprint')!=fingerprint):
        raise credits.LearningCreditError('学习会尚未核实或内容已变化，请刷新重新核实')


def confirm_and_settle_meeting_review(*, actor_user_id: int, session_id: int, expected_fingerprint: str) -> dict:
    from app.services import credit_settlement_batches as batches
    from app.services import learning_credits as credits
    from app.services.study_meeting_operations import permission_context
    # Confirmation remains durable if a posting attempt fails. The next explicit
    # confirmation resumes the same frozen batch rather than creating new facts.
    detail=confirm_meeting_review(actor_user_id=actor_user_id,session_id=session_id,expected_fingerprint=expected_fingerprint)
    failure=None
    try:
        with permission_context('plans:credit_settlement_post'):
            batch=batches.dry_run_study_meeting_batch(actor_user_id=actor_user_id,session_id=session_id)
            with transaction() as connection:
                row=execute(connection,'SELECT * FROM learning_credit_settlement_batches WHERE id=?',(batch['id'],)).fetchone()
                items=execute(connection,"SELECT * FROM learning_credit_settlement_batch_items WHERE batch_id=? AND status<>'BLOCKED' ORDER BY id",(batch['id'],)).fetchall()
                fingerprint=batches._approval_fingerprint(row,items)
            if batch['status']=='POSTING':
                batches.reconcile_stale_posting_batch(actor_user_id=actor_user_id,batch_id=batch['id'])
            posted=batches._post_batch(actor_user_id=actor_user_id,batch_id=batch['id'],source_type='STUDY_MEETING',
                resume_partial_failure=True,meeting_confirmation={'session_id':session_id,
                    'review_fingerprint':expected_fingerprint,'fingerprint':fingerprint})
            if posted['status']!='POSTED':failure='本场已核实，学分尚未全部入账，请在本页重试结算。'
    except (credits.LearningCreditError,PermissionError) as exc:
        failure=f'本场已核实，学分尚未入账完成：{exc}'
    except Exception:
        failure='本场已核实，学分入账暂未完成，请刷新查看实际结果后在本页重试。'
    detail=get_study_meeting_record_for_operations(actor_user_id=actor_user_id,session_id=session_id)
    if failure:detail.setdefault('credit_settlement',{'status':'PENDING'})['message']=failure
    return detail
