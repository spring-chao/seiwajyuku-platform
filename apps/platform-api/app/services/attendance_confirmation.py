"""Staff confirmation and November-onward class meeting settlement.

Confirmation is an audited snapshot of effective facts, not source finalization.
No new schema or deployment write gate is needed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.settings import get_settings
from app.db import atomic_transaction, execute
from app.services.audit import write_audit
from app.services.class_meeting_credits import _build_preview
from app.services.learning_credits import LearningCreditError, _insert_entry, _settlement_enabled, _write_allowed

START_DATE = "2026-11-01"


def _fingerprint(connection, group_id: int) -> str:
    rows = execute(connection,
        "SELECT r.id, r.member_id, r.attendance_status, r.checked_at, r.score_eligible, r.participant_type, "
        "s.id AS session_id, s.session_code, s.scheduled_start_at, sr.rule_id, sr.final_points, "
        "sr.late_deduction, sr.early_leave_deduction FROM attendance_records r "
        "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "LEFT JOIN attendance_score_records sr ON sr.attendance_record_id=r.id "
        "WHERE s.event_group_id=? ORDER BY r.id", (group_id,)).fetchall()
    return hashlib.sha256(json.dumps([dict(row) for row in rows], sort_keys=True, default=str).encode()).hexdigest()


def confirmation_state(connection, group_id: int) -> dict:
    row = execute(connection,
        "SELECT after_json, created_at FROM audit_logs WHERE action='attendance.confirm' "
        "AND resource_type='attendance_event_group' AND resource_id=? ORDER BY id DESC LIMIT 1",
        (str(group_id),)).fetchone()
    if not row:
        return {"confirmed": False}
    result = json.loads(row["after_json"])
    return {"confirmed": True, "confirmed_at": str(row["created_at"]),
            "facts_changed": result.get("fingerprint") != _fingerprint(connection, group_id), **result}


def lock_group(connection, group_id: int):
    # Serialize staff writes and confirmation on the same parent row on MySQL.
    import sqlite3
    suffix = "" if isinstance(connection, sqlite3.Connection) else " FOR UPDATE"
    return execute(connection, "SELECT * FROM attendance_event_groups WHERE id=?" + suffix,
                   (group_id,)).fetchone()


def ensure_finished(connection, group: dict, *, current_month: bool = False):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    event_date = str(group["event_date"])[:10]
    if current_month and event_date[:7] != today[:7]:
        raise LearningCreditError("仅可补签当月活动")
    if event_date > today:
        raise LearningCreditError("活动尚未结束")
    sessions = execute(connection,
        "SELECT scheduled_end_at, checkin_end_at, status FROM attendance_sessions WHERE event_group_id=?",
        (group["id"],)).fetchall()
    if not sessions:
        raise LearningCreditError("活动没有签到场次")
    if event_date == today:
        from app.services.class_meeting_credits import _parse_timestamp
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        for session in sessions:
            end = _parse_timestamp(session["scheduled_end_at"] or session["checkin_end_at"])
            if (end and end > now) or (not end and session["status"] not in {"CLOSED", "FINALIZED"}):
                raise LearningCreditError("请在全部场次结束后操作")
    if str(group["status"]).upper() in {"CANCELLED", "CANCELED", "VOID", "INVALIDATED", "DELETED"}:
        raise LearningCreditError("活动已取消或作废")


def confirm_attendance(*, group_id: int, user: dict, reason: str, expected_fingerprint: str | None = None) -> dict:
    _write_allowed()
    with atomic_transaction() as connection:
        group = lock_group(connection, group_id)
        if not group:
            raise LearningCreditError("活动不存在")
        from app.services.iam import accessible_org_ids
        allowed = accessible_org_ids(user["id"])
        if allowed is not None and not {group["org_unit_id"], group["study_org_unit_id"]}.intersection(allowed):
            raise PermissionError("不在组织授权范围内")
        ensure_finished(connection, dict(group))
        if expected_fingerprint is not None and expected_fingerprint != _fingerprint(connection, group_id):
            raise LearningCreditError("签到数据已变化，请刷新并重新核对全部场次")
        if group["activity_type"] != "CLASS_MEETING":
            raise LearningCreditError("仅班级学习会支持此结算流程")
        unmatched = execute(connection,
            "SELECT COUNT(*) n FROM attendance_records r JOIN attendance_sessions s ON s.id=r.attendance_session_id "
            "WHERE s.event_group_id=? AND r.participant_type='MEMBER' AND r.member_id IS NULL", (group_id,)).fetchone()
        if unmatched["n"]:
            raise LearningCreditError("存在待匹配学长，请先核对身份再最终确认")
        # Score computation remains the existing attendance subsystem's job.
        from app.services.attendance_scoring import recalculate_event_group
        recalculate_event_group(group_id)
        preview = _build_preview(connection, group_id)
        if preview["blocking_reasons"]:
            raise LearningCreditError("；".join(preview["blocking_reasons"]))
        fingerprint = _fingerprint(connection, group_id)
        previous = confirmation_state(connection, group_id)
        if previous.get("settlement_status") == "POSTED":
            if previous.get("fingerprint") != fingerprint:
                raise LearningCreditError("已入账后签到事实变化，请先由学分管理员核查冲正")
            return previous
        result = {"fingerprint": fingerprint, "settlement_status": "BEFORE_START_DATE",
                  "automatic_start_date": START_DATE, "total_points": preview["totals"]["proposed_points"]}
        if str(group["event_date"])[:10] >= START_DATE:
            result["settlement_status"] = "PENDING_ENABLEMENT"
            if get_settings().learning_credit_settlement_enabled:
                _settlement_enabled()
                if "plans:credit_settlement_manage" not in user["permissions"]:
                    raise PermissionError("无权正式结算学分，请由学分管理员确认")
                credit_scope = accessible_org_ids(user["id"], permission="plans:credit_settlement_manage")
                if credit_scope is not None and group["study_org_unit_id"] not in credit_scope:
                    raise PermissionError("班级不在学分结算授权范围内")
                posted = [_insert_entry(connection, item, status="POSTED", actor_user_id=user["id"])
                          for item in preview["entries"] if item["postable"]]
                result.update(settlement_status="POSTED", posted_count=len(posted))
        write_audit(connection, actor_user_id=user["id"], action="attendance.confirm",
                    resource_type="attendance_event_group", resource_id=str(group_id),
                    org_unit_id=group["org_unit_id"], purpose=reason, after=result)
        return {"confirmed": True, **result}
