"""Remove unused activities from normal views while retaining audit evidence."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.db import execute, fetch_all, fetch_one, transaction
from app.services import signin_engine
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids


def delete_event_group(event_group_id: int, *, expected_title: str, user: dict,
                       confirm_test_activity: bool = False) -> dict:
    if not {"attendance:manage", "attendance:view"}.issubset(user.get("permissions", [])):
        raise signin_engine.SigninEngineError("无此操作权限", 403)
    signin_engine.enabled(write=True)
    group = fetch_one("SELECT * FROM attendance_event_groups WHERE id=?", (event_group_id,))
    if not group:
        raise signin_engine.SigninEngineError("活动不存在", 404)
    owner = group.get("study_org_unit_id") or group["org_unit_id"]
    allowed = accessible_org_ids(user["id"], "attendance:manage")
    if allowed is not None and owner not in allowed:
        raise signin_engine.SigninEngineError("活动不在可操作的组织范围内", 403)
    if group["status"] == "DELETED":
        return {"event_group_id": event_group_id, "already_deleted": True}
    if expected_title != (group["title"] or ""):
        raise signin_engine.SigninEngineError("活动名称已变化，请刷新后重新确认", 409)
    if group["source_key"] != "signin":
        raise signin_engine.SigninEngineError("此活动由学习数据导入，请在对应数据管理入口处理", 409)
    sessions = fetch_all("SELECT external_session_id FROM attendance_sessions WHERE event_group_id=?", (event_group_id,))
    if not sessions or any(not row["external_session_id"] for row in sessions):
        raise signin_engine.SigninEngineError("活动缺少签到源场次编号，无法安全删除", 409)
    source_ids = [row["external_session_id"] for row in sessions]
    facts = fetch_one(
        "SELECT COUNT(*) AS count FROM attendance_records r "
        "JOIN attendance_sessions s ON s.id=r.attendance_session_id "
        "WHERE s.event_group_id=? AND (EXISTS (SELECT 1 FROM attendance_adjudications a WHERE a.attendance_record_id=r.id) "
        "OR (?=0 AND (r.checked_at IS NOT NULL OR r.attendance_status IN ('PRESENT','MANUAL_PRESENT') "
        "OR EXISTS (SELECT 1 FROM attendance_score_records sr WHERE sr.attendance_record_id=r.id))))",
        (event_group_id, int(confirm_test_activity)),
    )
    credits = fetch_one("SELECT COUNT(*) AS count FROM learning_credit_entries "
                        "WHERE source_type='ATTENDANCE_CLASS_MEETING' AND source_id=?", (str(event_group_id),))
    if facts["count"] or credits["count"]:
        raise signin_engine.SigninEngineError("活动已有签到、人工裁定或学分记录，请使用取消或数据复核流程", 409)
    # The source may be newer than the platform snapshot. Check each source
    # session before cancelling; do not rely on the displayed participant count.
    for source_id in source_ids:
        stats = signin_engine.management_request("stats", {"event_id": source_id}, user=user)
        checked = stats.get("onsite_total", stats.get("checked"))
        if not isinstance(checked, int) or isinstance(checked, bool) or checked < 0 or (checked and not confirm_test_activity):
            raise signin_engine.SigninEngineError("签到源已有到场记录或统计不完整，暂不能删除", 409)
    request_id = str(uuid4())
    before = {key: group[key] for key in ("id", "title", "status", "source_key", "external_group_id")}
    with transaction() as connection:
        write_audit(connection, actor_user_id=user["id"], action="attendance.event.delete",
                    resource_type="attendance_event_group", resource_id=str(event_group_id),
                    request_id=request_id, result="PENDING", before=before,
                    after={"source_session_ids": source_ids, "mode": "RETAIN_EVIDENCE",
                           "confirmed_test_activity": confirm_test_activity})
    try:
        # Cancelling the authoritative activity removes it from public check-in
        # candidates. Existing lifecycle rules and remote organization checks apply.
        for source_id in source_ids:
            cancelled = signin_engine.management_request("event_lifecycle_update", {
                "event_id": source_id, "lifecycle_status": "CANCELLED", "reason": "管理员确认删除测试或无效活动"
            }, user=user)
            if cancelled.get("lifecycle_status") != "CANCELLED":
                raise signin_engine.SigninEngineError("签到源未确认取消，请刷新核验后重试", 502)
        now = datetime.now(UTC).isoformat()
        with transaction() as connection:
            execute(connection, "UPDATE attendance_event_groups SET status='DELETED', updated_at=? WHERE id=?", (now, event_group_id))
            execute(connection, "UPDATE attendance_sessions SET status='DELETED', updated_at=? WHERE event_group_id=?", (now, event_group_id))
            write_audit(connection, actor_user_id=user["id"], action="attendance.event.delete",
                        resource_type="attendance_event_group", resource_id=str(event_group_id),
                        request_id=request_id, before=before, after={"status": "DELETED", "session_count": len(sessions)})
    except Exception:
        with transaction() as connection:
            write_audit(connection, actor_user_id=user["id"], action="attendance.event.delete",
                        resource_type="attendance_event_group", resource_id=str(event_group_id),
                        request_id=request_id, result="FAILED", before=before,
                        after={"source_outcome": "VERIFY_BEFORE_RETRY"})
        raise
    return {"event_group_id": event_group_id, "session_count": len(sessions), "status": "DELETED"}
