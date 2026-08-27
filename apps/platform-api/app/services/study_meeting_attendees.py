"""Operator-only correction of submitted meeting facts, never enrollment or credits."""
from __future__ import annotations

from app.core.settings import get_settings
from app.db import connect, execute, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import user_context
from app.services.study_meetings import (
    StudyMeetingError, StudyMeetingPermissionError, _db_timestamp, _lock_session,
    _operation_scope_allows, _require_write, _serialize, get_study_meeting_record_for_operations,
)


def can_edit_attendees(actor_user_id: int) -> bool:
    settings = get_settings()
    user = user_context(actor_user_id) or {}
    return (
        settings.study_meeting_attendee_edit_enabled
        and not settings.learning_credit_settlement_enabled
        and not settings.deployment_read_only
        and (not settings.is_production or settings.allow_production_mutations)
        and "study_meetings:attendees_edit" in user.get("permissions", [])
    )


def _require_editor(actor_user_id: int, session: dict) -> None:
    if not can_edit_attendees(actor_user_id):
        raise StudyMeetingPermissionError("参加人员修正功能未开启或无此权限；积分结算开启时禁止直接修正")
    if not _operation_scope_allows(actor_user_id, session["class_org_unit_id"]):
        raise StudyMeetingPermissionError("学习会记录不在当前组织授权范围内")
    if session["status"] != "SUBMITTED":
        raise StudyMeetingError("仅能修正已提交学习会的参加人员")


def _roster(connection, session: dict, member_ids: list[int] | None = None) -> list[dict]:
    """Resolve unique current GROUP relations; never choose an ambiguous group."""
    now = _db_timestamp(connection)
    params = [now, now]
    filter_sql = ""
    if member_ids is not None:
        filter_sql = " AND m.id IN (" + ",".join("?" for _ in member_ids) + ")"
        params.extend(member_ids)
    else:
        # First bound candidates to this class; the outer query still sees ALL
        # active group relations to reject cross-class or ambiguous membership.
        filter_sql = (
            " AND EXISTS(SELECT 1 FROM member_org_relations cr JOIN org_units cg ON cg.id=cr.org_unit_id "
            "WHERE cr.member_id=m.id AND cr.relation_type='STUDY_GROUP' "
            "AND cg.parent_id=? AND cg.unit_type='GROUP' AND cg.is_active=1 "
            "AND (cr.valid_from IS NULL OR cr.valid_from<=?) AND (cr.valid_until IS NULL OR cr.valid_until>=?))"
        )
        params.extend([session["class_org_unit_id"], now, now])
    rows = execute(connection,
        "SELECT DISTINCT m.id AS member_id, m.name, g.id AS group_org_unit_id, g.name AS group_name, "
        "g.parent_id AS class_org_unit_id FROM members m "
        "JOIN member_org_relations r ON r.member_id=m.id AND r.relation_type='STUDY_GROUP' "
        "JOIN org_units g ON g.id=r.org_unit_id AND g.unit_type='GROUP' AND g.is_active=1 "
        "JOIN org_units c ON c.id=g.parent_id AND c.unit_type='CLASS' AND c.is_active=1 "
        "WHERE m.status='ACTIVE' AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?)" + filter_sql + " ORDER BY m.name, m.id",
        tuple(params)).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["member_id"]), []).append(dict(row))
    result = []
    for member_id, groups in grouped.items():
        if len(groups) != 1 or groups[0]["class_org_unit_id"] != session["class_org_unit_id"]:
            continue
        row = groups[0]
        row["member_id"] = member_id
        row["attendance_type"] = "HOME_GROUP" if row["group_org_unit_id"] == session["study_group_org_unit_id"] else "CROSS_GROUP"
        result.append(row)
    return result


def _snapshot(connection, session_id: int) -> dict:
    rows = execute(connection,
        "SELECT a.*, m.name FROM study_meeting_attendances a JOIN members m ON m.id=a.member_id "
        "WHERE a.study_meeting_session_id=? ORDER BY a.member_id", (session_id,)).fetchall()
    items = [{key: _serialize(value) for key, value in dict(row).items()} for row in rows]
    return {
        "home_attendees": [item for item in items if item["attendance_type"] == "HOME_GROUP"],
        "cross_group_attendees": [item for item in items if item["attendance_type"] == "CROSS_GROUP"],
    }


def attendee_options(*, actor_user_id: int, session_id: int) -> dict:
    session = fetch_one("SELECT * FROM study_meeting_sessions WHERE id=?", (session_id,))
    if not session:
        raise StudyMeetingError("学习会记录不存在")
    _require_editor(actor_user_id, session)
    connection = connect()
    try:
        options = _roster(connection, session)
        existing = _snapshot(connection, session_id)
    finally:
        connection.close()
    valid_ids = {item["member_id"] for item in options}
    return {
        "home_members": [item for item in options if item["attendance_type"] == "HOME_GROUP"],
        "cross_group_members": [item for item in options if item["attendance_type"] == "CROSS_GROUP"],
        "unavailable_attendees": [
            {"member_id": item["member_id"], "name": item["name"], "attendance_type": item["attendance_type"]}
            for item in existing["home_attendees"] + existing["cross_group_attendees"]
            if item["member_id"] not in valid_ids
        ],
    }


def correct_attendees(*, actor_user_id: int, session_id: int, member_ids: list[int],
                      expected_member_ids: list[int], note: str | None = None) -> dict:
    _require_write()
    for ids in (member_ids, expected_member_ids):
        if len(ids) > 500 or any(type(item) is not int or item <= 0 for item in ids):
            raise StudyMeetingError("参加人员编号或数量无效")
        if len(set(ids)) != len(ids):
            raise StudyMeetingError("同一学长不能重复添加")
    if not member_ids:
        raise StudyMeetingError("至少保留一名实际参加学长")
    if note is not None and (not isinstance(note, str) or len(note) > 1000):
        raise StudyMeetingError("修正备注最多1000字")
    with transaction() as connection:
        session = _lock_session(connection, session_id)
        _require_editor(actor_user_id, session)
        before = _snapshot(connection, session_id)
        old = {item["member_id"]: item for item in before["home_attendees"] + before["cross_group_attendees"]}
        if set(old) != set(expected_member_ids):
            raise StudyMeetingError("参加人员已被其他人修改，请刷新后重试")
        selected = _roster(connection, session, member_ids)
        if {item["member_id"] for item in selected} != set(member_ids):
            raise StudyMeetingError("参加人员必须为本班ACTIVE正式学员，且只能有一个当前有效小组；请检查离册、跨班或归属不明确的学长")
        now = _db_timestamp(connection)
        for removed_id in set(old) - set(member_ids):
            execute(connection, "DELETE FROM study_meeting_attendances WHERE study_meeting_session_id=? AND member_id=?",
                    (session_id, removed_id))
        for item in selected:
            member_id = item["member_id"]
            if member_id in old:
                # Keep original adder and timestamps unless classification changes.
                if (old[member_id]["home_study_group_org_unit_id"] != item["group_org_unit_id"]
                        or old[member_id]["attendance_type"] != item["attendance_type"]):
                    execute(connection, "UPDATE study_meeting_attendances SET home_study_group_org_unit_id=?, "
                            "attendance_type=?, updated_at=? WHERE study_meeting_session_id=? AND member_id=?",
                            (item["group_org_unit_id"], item["attendance_type"], now, session_id, member_id))
            else:
                execute(connection, "INSERT INTO study_meeting_attendances "
                        "(study_meeting_session_id, member_id, home_study_group_org_unit_id, attended_study_group_org_unit_id, "
                        "attendance_type, added_by_member_id, added_by_user_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                        (session_id, member_id, item["group_org_unit_id"], session["study_group_org_unit_id"],
                         item["attendance_type"], actor_user_id, now, now))
        after = _snapshot(connection, session_id)
        execute(connection, "UPDATE study_meeting_sessions SET updated_at=? WHERE id=?", (now, session_id))
        write_audit(connection, actor_user_id=actor_user_id, action="study_meeting.attendees_correct",
                    resource_type="study_meeting_session", resource_id=str(session_id),
                    org_unit_id=session["class_org_unit_id"], purpose=(note or "").strip() or None,
                    before=before, after=after)
    return get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)
