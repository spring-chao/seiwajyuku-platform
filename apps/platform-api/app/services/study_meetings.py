"""V1.2 MVP-B group study meeting fact capture.

This module intentionally has no relationship with the legacy attendance
tables and never advances a class learning cycle or settles credits.  It
stores the people and course fact exactly as submitted by an authorized group
leader/counsellor. B2.1 adds course detail snapshots and private photo evidence;
review and credit settlement remain separate, disabled phases.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from app.core.settings import get_settings
from app.db import connect, execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.course_credit_rules import (
    get_group_meeting_credit_policy,
    list_course_credit_rules,
)
from app.services.learning_cycles import _active_binding, _cycle_at
from app.services.learning_cycles import _plan_cycle_payload
from app.services.group_meeting_plan import (
    GroupMeetingPlanConfigError,
    build_group_meeting_plan,
)
from app.services.wechat_identity import (
    WeChatIdentityError,
    authorized_group_targets,
    role_for_target,
    resolve_member_session,
)
from app.services.iam import accessible_org_ids, user_context


class StudyMeetingError(ValueError):
    """A request failed a business validation."""


class StudyMeetingFeatureDisabled(StudyMeetingError):
    """The capability is deliberately closed by the deployment feature flag."""


class StudyMeetingPermissionError(PermissionError):
    """The bound member is not currently authorized for the target."""


BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_timestamp(connection, value: datetime | None = None) -> str:
    parsed = value or datetime.now(UTC)
    if isinstance(connection, sqlite3.Connection):
        return parsed.astimezone(UTC).isoformat()
    return parsed.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _serialize(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _business_today() -> str:
    """Return the operating date used by the China-based mini-program."""

    return datetime.now(UTC).astimezone(BUSINESS_TIMEZONE).date().isoformat()


def _require_enabled() -> None:
    if not get_settings().study_meeting_submission_enabled:
        raise StudyMeetingFeatureDisabled("小组学习会登记功能尚未开启")


def _target_for_member(member_id: int, group_org_unit_id: str | None) -> dict[str, Any]:
    if not fetch_one(
        "SELECT id FROM members WHERE id=? AND status='ACTIVE' LIMIT 1",
        (member_id,),
    ):
        raise StudyMeetingPermissionError("当前学员身份不可用")
    targets = authorized_group_targets(member_id)
    if not targets:
        raise StudyMeetingPermissionError("当前没有有效的小组学习会登记任职")
    if group_org_unit_id:
        target = next(
            (item for item in targets if item["group_org_unit_id"] == group_org_unit_id),
            None,
        )
        if not target:
            raise StudyMeetingPermissionError("小组不在当前任职范围内")
        return target
    if len(targets) != 1:
        raise StudyMeetingError("当前负责多个小组，请先选择本次学习会小组")
    return targets[0]


def _current_cycle(connection, class_org_unit_id: str) -> dict[str, Any]:
    binding = _active_binding(connection, class_org_unit_id)
    if not binding:
        raise StudyMeetingError("该班级尚未绑定学习计划")
    cycle = _cycle_at(connection, int(binding["id"]), _now())
    if not cycle:
        raise StudyMeetingError("该班级尚未生成学习周期")
    if cycle.get("cycle_status") == "CLOSED":
        raise StudyMeetingError("当前学习周期已关闭，暂不能登记")
    return {"binding": dict(binding), "cycle": dict(cycle)}


def _active_group_members(connection, group_org_unit_id: str) -> list[dict[str, Any]]:
    now = _now()
    rows = execute(
        connection,
        "SELECT DISTINCT m.id, m.name, m.phone_masked "
        "FROM members m JOIN member_org_relations r ON r.member_id=m.id "
        "JOIN org_units g ON g.id=r.org_unit_id "
        "WHERE m.status='ACTIVE' AND g.id=? AND g.unit_type='GROUP' AND g.is_active=1 "
        "AND r.relation_type='STUDY_GROUP' "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "ORDER BY m.name, m.id",
        (group_org_unit_id, now, now),
    ).fetchall()
    return [
        {
            "member_id": int(row["id"]),
            "name": row["name"],
            "phone_masked": row["phone_masked"] or "",
        }
        for row in rows
    ]


def _course_rules() -> dict[str, dict[str, Any]]:
    payload = list_course_credit_rules()
    return {str(rule["course_key"]): rule for rule in payload.get("rules", [])}


def _require_write() -> None:
    settings = get_settings()
    if settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations):
        raise StudyMeetingPermissionError("当前环境不允许写入")


def _lock_session(connection, session_id: int) -> dict:
    # Serialize submit, evidence replacement and operator correction on both DBs.
    execute(connection, "UPDATE study_meeting_sessions SET id=id WHERE id=?", (session_id,))
    row = execute(connection, "SELECT * FROM study_meeting_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise StudyMeetingError("学习会记录不存在")
    return dict(row)


def _read_courses(connection, row: dict, details: list[dict] | None = None) -> list[dict]:
    if details is None:
        details = [dict(item) for item in execute(
            connection, "SELECT * FROM study_meeting_courses WHERE study_meeting_session_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()]
    if details:
        return [{key: _serialize(value) for key, value in item.items()} for item in details]
    if not row.get("course_details_initialized") and row.get("course_key"):
        return [{
            "course_key": row["course_key"], "course_name_snapshot": row["course_name_snapshot"],
            "course_credit_snapshot": row["course_credit_snapshot"],
            "course_rule_status": "CONFIGURED" if row["course_credit_snapshot"] is not None else "PENDING",
            "rule_reference_json": None, "legacy": True,
        }]
    return []


def _course_snapshots(keys: list[str]) -> list[dict]:
    if any(not isinstance(key, str) or not key or len(key) > 128 for key in keys):
        raise StudyMeetingError("课程编号无效")
    if len(keys) != len(set(keys)):
        raise StudyMeetingError("同一课程不能重复添加")
    directory = list_course_credit_rules()
    rules = {item["course_key"]: item for item in directory["rules"]}
    snapshots = []
    for key in keys:
        rule = rules.get(key)
        if not rule:
            raise StudyMeetingError("课程不在当前课程积分目录中")
        item = {
            "course_key": key, "course_name_snapshot": rule["course_name"],
            "course_credit_snapshot": rule["credit_points"] if rule["status"] == "CONFIGURED" else None,
            "course_rule_status": rule["status"],
        }
        reference = {
            "plan_key": directory["plan_key"], "version_label": directory["version_label"],
            "rule_id": rule.get("id"), "rule_updated_at": _serialize(rule.get("updated_at")),
            "source": rule.get("source"), "version_status": directory["version_status"],
            "snapshot_sha256": hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        }
        item["rule_reference_json"] = json.dumps(reference, ensure_ascii=False, sort_keys=True)
        snapshots.append(item)
    return snapshots


def _write_courses(connection, session_id: int, snapshots: list[dict]) -> None:
    now = _db_timestamp(connection)
    execute(connection, "DELETE FROM study_meeting_courses WHERE study_meeting_session_id=?", (session_id,))
    for item in snapshots:
        execute(connection,
            "INSERT INTO study_meeting_courses(study_meeting_session_id, course_key, course_name_snapshot, "
            "course_credit_snapshot, course_rule_status, rule_reference_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, item["course_key"], item["course_name_snapshot"], item["course_credit_snapshot"],
             item["course_rule_status"], item["rule_reference_json"], now, now))
    # Includes the empty-list case, so clearing a legacy course cannot resurrect it.
    execute(connection, "UPDATE study_meeting_sessions SET course_details_initialized=1, updated_at=? WHERE id=?",
            (now, session_id))


def _session_payload(connection, session_id: int) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT s.*, c.name AS class_name, g.name AS group_name, "
        "lc.learning_cycle_index AS cycle_index, m.name AS creator_name "
        "FROM study_meeting_sessions s "
        "JOIN org_units c ON c.id=s.class_org_unit_id "
        "JOIN org_units g ON g.id=s.study_group_org_unit_id "
        "JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id "
        "JOIN members m ON m.id=s.created_by_member_id "
        "WHERE s.id=? LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        return None
    result = {key: _serialize(value) for key, value in dict(row).items()}
    result["id"] = int(result["id"])
    result["courses"] = _read_courses(connection, result)
    result["has_course"] = bool(result["courses"])
    from app.services.study_meeting_evidence import evidence_metadata
    result["evidence"] = evidence_metadata(connection, session_id)
    result["course_rule_status"] = (
        "CONFIGURED" if result.get("course_key") and result.get("course_credit_snapshot") is not None else
        "PENDING" if result.get("course_key") else None
    )
    attendees = execute(
        connection,
        "SELECT a.id, a.member_id, a.home_study_group_org_unit_id, "
        "a.attended_study_group_org_unit_id, a.attendance_type, a.added_by_member_id, a.added_by_user_id, "
        "m.name, m.phone_masked, home.name AS home_group_name, attended.name AS attended_group_name "
        "FROM study_meeting_attendances a JOIN members m ON m.id=a.member_id "
        "JOIN org_units home ON home.id=a.home_study_group_org_unit_id "
        "JOIN org_units attended ON attended.id=a.attended_study_group_org_unit_id "
        "WHERE a.study_meeting_session_id=? ORDER BY a.attendance_type, m.name, a.member_id",
        (session_id,),
    ).fetchall()
    result["attendees"] = [
        {
            "id": int(item["id"]),
            "member_id": int(item["member_id"]),
            "name": item["name"],
            "phone_masked": item["phone_masked"] or "",
            "home_study_group_org_unit_id": item["home_study_group_org_unit_id"],
            "attended_study_group_org_unit_id": item["attended_study_group_org_unit_id"],
            "home_group_name": item["home_group_name"],
            "attended_group_name": item["attended_group_name"],
            "attendance_type": item["attendance_type"],
            "added_by_member_id": int(item["added_by_member_id"]) if item["added_by_member_id"] is not None else None,
            "added_by_user_id": int(item["added_by_user_id"]) if item["added_by_user_id"] is not None else None,
        }
        for item in attendees
    ]
    result["home_attendees"] = [
        item for item in result["attendees"] if item["attendance_type"] == "HOME_GROUP"
    ]
    result["cross_group_attendees"] = [
        item for item in result["attendees"] if item["attendance_type"] == "CROSS_GROUP"
    ]
    return result


def get_study_meeting_context(
    *, member_id: int, group_org_unit_id: str | None = None
) -> dict[str, Any]:
    _require_enabled()
    targets = authorized_group_targets(member_id)
    if not targets:
        raise StudyMeetingPermissionError("当前没有有效的小组学习会登记任职")
    selected = None
    if group_org_unit_id:
        selected = next(
            (item for item in targets if item["group_org_unit_id"] == group_org_unit_id),
            None,
        )
        if not selected:
            raise StudyMeetingPermissionError("小组不在当前任职范围内")
    elif len(targets) == 1:
        selected = targets[0]

    assignments: list[dict[str, Any]] = []
    connection = connect()
    try:
        for target in targets:
            cycle_data: dict[str, Any] | None = None
            try:
                cycle_data = _current_cycle(connection, target["class_org_unit_id"])
            except StudyMeetingError as exc:
                cycle_data = {"error": str(exc)}
            cycle = cycle_data.get("cycle") if cycle_data else None
            meeting_plan = None
            meeting_plan_error = None
            if cycle:
                plan_cycle = _plan_cycle_payload(connection, int(cycle["plan_cycle_id"]))
                if not plan_cycle:
                    meeting_plan_error = "当前学习周期缺少对应的学习计划内容，请联系运营人员检查学习计划"
                    logger.warning(
                        "Study meeting plan cycle is missing",
                        extra={"plan_cycle_id": cycle.get("plan_cycle_id")},
                    )
                else:
                    try:
                        meeting_plan = build_group_meeting_plan(
                            plan_cycle=plan_cycle,
                            cohort_month=cycle_data["binding"].get("cohort_month"),
                            learning_cycle_index=int(cycle["learning_cycle_index"]),
                        )
                    except GroupMeetingPlanConfigError as exc:
                        meeting_plan_error = str(exc)
                        logger.warning(
                            "Study meeting plan configuration is unavailable",
                            extra={
                                "plan_cycle_id": plan_cycle.get("id"),
                                "cycle_index": plan_cycle.get("cycle_index"),
                            },
                        )
            assignment = {
                **target,
                "current_cycle": {
                    "id": int(cycle["id"]),
                    "learning_cycle_index": int(cycle["learning_cycle_index"]),
                    "plan_cycle_id": int(cycle["plan_cycle_id"]),
                    "group_meeting_policy": cycle["group_meeting_policy"],
                    "cycle_status": cycle["cycle_status"],
                }
                if cycle
                else None,
                "cycle_error": cycle_data.get("error") if cycle_data else None,
                "member_count": len(_active_group_members(connection, target["group_org_unit_id"])),
                "meeting_plan": meeting_plan,
                "meeting_plan_error": meeting_plan_error,
            }
            if selected and selected["group_org_unit_id"] == target["group_org_unit_id"]:
                assignment["members"] = _active_group_members(
                    connection, target["group_org_unit_id"]
                )
            assignments.append(assignment)
        selected_assignment = next(
            (
                item
                for item in assignments
                if selected and item["group_org_unit_id"] == selected["group_org_unit_id"]
            ),
            None,
        )
    finally:
        connection.close()
    role_keys = sorted({item["role_key"] for item in targets})
    credit_policy = {
        **get_group_meeting_credit_policy(),
        "settlement_enabled": get_settings().learning_credit_settlement_enabled,
        "note": "本阶段只保存事实，不正式结算积分",
    }
    return {
        "member_id": member_id,
        "roles": role_keys,
        "selection_required": selected is None,
        "selected_group_org_unit_id": selected["group_org_unit_id"] if selected else None,
        "assignment": selected_assignment,
        "assignments": assignments,
        "credit_policy": credit_policy,
        "evidence_enabled": get_settings().study_meeting_evidence_enabled,
        "evidence_required": True,
    }


def search_cross_group_members(
    *, member_id: int, group_org_unit_id: str, query: str | None = None
) -> list[dict[str, Any]]:
    _require_enabled()
    target = _target_for_member(member_id, group_org_unit_id)
    now = _now()
    term = (query or "").strip()
    if len(term) > 80:
        raise StudyMeetingError("搜索条件过长")
    statement = (
        "SELECT DISTINCT m.id, m.name, c.name AS class_name, g.name AS group_name "
        "FROM members m JOIN member_org_relations r ON r.member_id=m.id "
        "JOIN org_units g ON g.id=r.org_unit_id AND g.unit_type='GROUP' AND g.is_active=1 "
        "JOIN org_units c ON c.id=g.parent_id AND c.id=? "
        "WHERE m.status='ACTIVE' AND c.is_active=1 AND r.relation_type='STUDY_GROUP' AND g.id<>? "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?) "
    )
    params: list[Any] = [target["class_org_unit_id"], group_org_unit_id, now, now]
    if term:
        statement += "AND m.name LIKE ? "
        params.append(f"%{term}%")
    statement += "ORDER BY m.name, m.id LIMIT 50"
    return [
        {
            "member_id": int(row["id"]),
            "name": row["name"],
            "class_name": row["class_name"],
            "group_name": row["group_name"],
        }
        for row in fetch_all(statement, tuple(params))
    ]


def _parse_meeting_date(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return _business_today()
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise StudyMeetingError("学习会日期必须是 YYYY-MM-DD") from exc


def _validate_session_attendees(connection, session_row: Any) -> None:
    """Re-check the immutable attendance facts immediately before submission.

    A draft can remain open while an organization relation changes.  Creation
    validates the submitted ids, but submission must validate them again so a
    stale draft cannot turn into a fact record for a departed member or a
    cross-class attendee.
    """

    rows = execute(
        connection,
        "SELECT member_id, home_study_group_org_unit_id, "
        "attended_study_group_org_unit_id, attendance_type "
        "FROM study_meeting_attendances WHERE study_meeting_session_id=?",
        (int(session_row["id"]),),
    ).fetchall()
    if not rows:
        raise StudyMeetingError("至少需要一名实际参加学长")
    member_ids = [int(item["member_id"]) for item in rows]
    if len(set(member_ids)) != len(member_ids):
        raise StudyMeetingError("同一学长不能重复添加")

    target_group_id = session_row["study_group_org_unit_id"]
    class_id = session_row["class_org_unit_id"]
    home_ids = [
        int(item["member_id"])
        for item in rows
        if item["attendance_type"] == "HOME_GROUP"
    ]
    cross_ids = [
        int(item["member_id"])
        for item in rows
        if item["attendance_type"] == "CROSS_GROUP"
    ]
    if len(home_ids) + len(cross_ids) != len(rows):
        raise StudyMeetingError("学习会名单类型无效")

    now = _now()
    if home_ids:
        placeholders = ",".join("?" for _ in home_ids)
        home_rows = execute(
            connection,
            "SELECT DISTINCT m.id FROM members m "
            "JOIN member_org_relations r ON r.member_id=m.id "
            "JOIN org_units g ON g.id=r.org_unit_id "
            "WHERE m.status='ACTIVE' AND g.id=? AND g.unit_type='GROUP' AND g.is_active=1 "
            "AND r.relation_type='STUDY_GROUP' "
            "AND r.member_id IN (" + placeholders + ") "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?)",
            (target_group_id, *home_ids, now, now),
        ).fetchall()
        if {int(item["id"]) for item in home_rows} != set(home_ids):
            raise StudyMeetingError("本组名单已不再是当前有效关系")

    current_cross: dict[int, str] = {}
    if cross_ids:
        placeholders = ",".join("?" for _ in cross_ids)
        cross_rows = execute(
            connection,
            "SELECT DISTINCT m.id, r.org_unit_id AS home_group_id "
            "FROM members m JOIN member_org_relations r ON r.member_id=m.id "
            "JOIN org_units g ON g.id=r.org_unit_id AND g.unit_type='GROUP' AND g.is_active=1 "
            "WHERE m.status='ACTIVE' AND g.parent_id=? AND g.id<>? "
            "AND g.is_active=1 AND r.relation_type='STUDY_GROUP' "
            "AND r.member_id IN (" + placeholders + ") "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?)",
            (class_id, target_group_id, *cross_ids, now, now),
        ).fetchall()
        grouped_cross: dict[int, set[str]] = {}
        for item in cross_rows:
            grouped_cross.setdefault(int(item["id"]), set()).add(item["home_group_id"])
        if any(len(groups) != 1 for groups in grouped_cross.values()):
            raise StudyMeetingError("跨组学长存在多个当前有效小组，暂不能提交")
        current_cross = {
            member_id: next(iter(groups))
            for member_id, groups in grouped_cross.items()
        }
        if set(current_cross) != set(cross_ids):
            raise StudyMeetingError("跨组学长已不再是同班其他小组的有效学员")

    for item in rows:
        if item["attended_study_group_org_unit_id"] != target_group_id:
            raise StudyMeetingError("学习会参加小组与登记小组不一致")
        if item["attendance_type"] == "HOME_GROUP":
            if item["home_study_group_org_unit_id"] != target_group_id:
                raise StudyMeetingError("本组参加记录不一致")
        elif item["attendance_type"] == "CROSS_GROUP":
            expected_home_group = current_cross.get(int(item["member_id"]))
            if not expected_home_group or item["home_study_group_org_unit_id"] != expected_home_group:
                raise StudyMeetingError("跨组参加记录与当前正式小组不一致")


def create_study_meeting(
    *,
    member_id: int,
    group_org_unit_id: str,
    meeting_date: str | None,
    member_ids: list[int],
    cross_group_member_ids: list[int],
    has_course: bool,
    course_key: str | None = None,
    course_keys: list[str] | None = None,
) -> dict[str, Any]:
    _require_enabled()
    _require_write()
    target = _target_for_member(member_id, group_org_unit_id)
    role = role_for_target(
        member_id, target["class_org_unit_id"], target["group_org_unit_id"]
    )
    if not role:
        raise StudyMeetingPermissionError("当前没有该小组的有效登记任职")
    home_ids = [int(item) for item in member_ids]
    cross_ids = [int(item) for item in cross_group_member_ids]
    if not home_ids and not cross_ids:
        raise StudyMeetingError("至少选择一名实际参加学长")
    if len(set(home_ids)) != len(home_ids) or len(set(cross_ids)) != len(cross_ids):
        raise StudyMeetingError("同一学长不能重复添加")
    if set(home_ids) & set(cross_ids):
        raise StudyMeetingError("本组与跨组名单不能重复")
    if course_keys is not None and course_key is not None:
        raise StudyMeetingError("请使用课程列表，不要同时传入旧课程字段")
    keys = course_keys if course_keys is not None else ([course_key] if course_key else [])
    if bool(keys) != has_course:
        raise StudyMeetingError("观看课程选项与课程列表不一致")
    snapshots = _course_snapshots(keys)
    meeting_day = _parse_meeting_date(meeting_date)
    now = _now()

    with transaction() as connection:
        cycle_data = _current_cycle(connection, target["class_org_unit_id"])
        cycle = cycle_data["cycle"]
        home_placeholders = ",".join("?" for _ in home_ids)
        home_rows = execute(
            connection,
            "SELECT DISTINCT m.id FROM members m JOIN member_org_relations r ON r.member_id=m.id "
            "WHERE m.status='ACTIVE' AND r.org_unit_id=? AND r.relation_type='STUDY_GROUP' "
            "AND r.member_id IN (" + home_placeholders + ") "
            "AND (r.valid_from IS NULL OR r.valid_from<=?) "
            "AND (r.valid_until IS NULL OR r.valid_until>=?)",
            (target["group_org_unit_id"], *home_ids, now, now),
        ).fetchall()
        valid_home = {int(row["id"]) for row in home_rows}
        if valid_home != set(home_ids):
            raise StudyMeetingError("本组名单包含不在当前有效关系中的学长")

        cross_home_groups: dict[int, str] = {}
        if cross_ids:
            cross_placeholders = ",".join("?" for _ in cross_ids)
            cross_rows = execute(
                connection,
                "SELECT DISTINCT m.id, r.org_unit_id AS home_group_id "
                "FROM members m JOIN member_org_relations r ON r.member_id=m.id "
                "JOIN org_units g ON g.id=r.org_unit_id AND g.unit_type='GROUP' "
                "WHERE m.status='ACTIVE' AND g.parent_id=? AND g.id<>? "
                "AND r.relation_type='STUDY_GROUP' AND r.member_id IN (" + cross_placeholders + ") "
                "AND (r.valid_from IS NULL OR r.valid_from<=?) "
                "AND (r.valid_until IS NULL OR r.valid_until>=?)",
                (target["class_org_unit_id"], target["group_org_unit_id"], *cross_ids, now, now),
            ).fetchall()
            grouped_cross: dict[int, set[str]] = {}
            for row in cross_rows:
                grouped_cross.setdefault(int(row["id"]), set()).add(row["home_group_id"])
            if any(len(groups) != 1 for groups in grouped_cross.values()):
                raise StudyMeetingError("跨组学长存在多个当前有效小组，暂不能登记")
            for cross_member_id, groups in grouped_cross.items():
                cross_home_groups[cross_member_id] = next(iter(groups))
            if set(cross_home_groups) != set(cross_ids):
                raise StudyMeetingError("跨组学长必须是同一班级其他小组的有效学员")

        session_code = f"SM-{datetime.now(UTC):%Y%m%d%H%M%S}-{secrets.token_hex(4).upper()}"
        db_now = _db_timestamp(connection)
        cursor = execute(
            connection,
            "INSERT INTO study_meeting_sessions "
            "(session_code, class_org_unit_id, study_group_org_unit_id, learning_cycle_id, "
            "meeting_date, created_by_member_id, created_by_role, has_course, course_key, "
            "course_name_snapshot, course_credit_snapshot, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)",
            (
                session_code,
                target["class_org_unit_id"],
                target["group_org_unit_id"],
                cycle["id"],
                meeting_day,
                member_id,
                role,
                0,  # Legacy single-course fields are no longer written.
                None,
                None,
                None,
                db_now,
                db_now,
            ),
        )
        session_id = int(cursor.lastrowid)
        _write_courses(connection, session_id, snapshots)
        for item in home_ids:
            execute(
                connection,
                "INSERT INTO study_meeting_attendances "
                "(study_meeting_session_id, member_id, home_study_group_org_unit_id, "
                "attended_study_group_org_unit_id, attendance_type, added_by_member_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'HOME_GROUP', ?, ?, ?)",
                (session_id, item, target["group_org_unit_id"], target["group_org_unit_id"], member_id, db_now, db_now),
            )
        for item in cross_ids:
            execute(
                connection,
                "INSERT INTO study_meeting_attendances "
                "(study_meeting_session_id, member_id, home_study_group_org_unit_id, "
                "attended_study_group_org_unit_id, attendance_type, added_by_member_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'CROSS_GROUP', ?, ?, ?)",
                (session_id, item, cross_home_groups[item], target["group_org_unit_id"], member_id, db_now, db_now),
            )
        write_audit(
            connection,
            actor_user_id=None,
            action="study_meeting.create",
            resource_type="study_meeting_session",
            resource_id=str(session_id),
            org_unit_id=target["class_org_unit_id"],
            after={
                "session_code": session_code,
                "class_org_unit_id": target["class_org_unit_id"],
                "study_group_org_unit_id": target["group_org_unit_id"],
                "learning_cycle_id": int(cycle["id"]),
                "home_count": len(home_ids),
                "cross_group_count": len(cross_ids),
                "courses": snapshots,
                "created_by_member_id": member_id,
            },
        )
        payload = _session_payload(connection, session_id)
    if not payload:
        raise StudyMeetingError("学习会保存失败")
    return payload


def submit_study_meeting(*, member_id: int, session_id: int) -> dict[str, Any]:
    _require_enabled()
    _require_write()
    with transaction() as connection:
        row = _lock_session(connection, session_id)
        if not row:
            raise StudyMeetingError("学习会记录不存在")
        role = role_for_target(
            member_id, row["class_org_unit_id"], row["study_group_org_unit_id"]
        )
        if not role:
            raise StudyMeetingPermissionError("当前没有该小组的有效登记任职")
        if row["status"] == "SUBMITTED":
            payload = _session_payload(connection, session_id)
            if not payload:
                raise StudyMeetingError("学习会记录不存在")
            return payload
        if row["status"] != "DRAFT":
            raise StudyMeetingError("当前学习会记录不能提交")

        current_cycle = _current_cycle(connection, row["class_org_unit_id"])["cycle"]
        if int(current_cycle["id"]) != int(row["learning_cycle_id"]):
            raise StudyMeetingError("当前学习周期已变化，请重新登记本场学习会")
        _validate_session_attendees(connection, row)
        course_rules = _course_rules()
        if any(item["course_key"] not in course_rules for item in _read_courses(connection, row)):
            raise StudyMeetingError("课程不在当前课程积分目录中")
        from app.services.study_meeting_evidence import evidence_metadata
        if not evidence_metadata(connection, session_id):
            raise StudyMeetingError("请先上传一张学习合影")

        now = _db_timestamp(connection)
        execute(
            connection,
            "UPDATE study_meeting_sessions SET status='SUBMITTED', submitted_at=?, updated_at=? WHERE id=?",
            (now, now, session_id),
        )
        write_audit(
            connection,
            actor_user_id=None,
            action="study_meeting.submit",
            resource_type="study_meeting_session",
            resource_id=str(session_id),
            org_unit_id=row["class_org_unit_id"],
            after={"status": "SUBMITTED", "submitted_by_member_id": member_id},
        )
        payload = _session_payload(connection, session_id)
    if not payload:
        raise StudyMeetingError("学习会提交失败")
    return payload


def get_study_meeting(*, member_id: int, session_id: int) -> dict[str, Any]:
    _require_enabled()
    payload = fetch_one(
        "SELECT class_org_unit_id, study_group_org_unit_id, created_by_member_id "
        "FROM study_meeting_sessions WHERE id=? LIMIT 1",
        (session_id,),
    )
    if not payload:
        raise StudyMeetingError("学习会记录不存在")
    role = role_for_target(
        member_id, payload["class_org_unit_id"], payload["study_group_org_unit_id"]
    )
    if not role and int(payload["created_by_member_id"]) != member_id:
        raise StudyMeetingPermissionError("无权查看该学习会记录")
    connection = connect()
    try:
        result = _session_payload(connection, session_id)
    finally:
        connection.close()
    if not result:
        raise StudyMeetingError("学习会记录不存在")
    return result


def _operation_scope_allows(actor_user_id: int, class_org_unit_id: str) -> bool:
    allowed = accessible_org_ids(actor_user_id)
    return allowed is None or class_org_unit_id in allowed


def list_study_meeting_records(
    *,
    actor_user_id: int,
    status: str | None = None,
    meeting_date_from: str | None = None,
    meeting_date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Read-only operations view for submitted/draft fact records."""

    allowed = accessible_org_ids(actor_user_id)
    if allowed == set():
        return []
    conditions = ["1=1"]
    params: list[Any] = []
    if status:
        normalized = status.upper()
        if normalized not in {"DRAFT", "SUBMITTED", "CANCELLED"}:
            raise StudyMeetingError("学习会状态无效")
        conditions.append("s.status=?")
        params.append(normalized)
    if meeting_date_from:
        _parse_meeting_date(meeting_date_from)
        conditions.append("s.meeting_date>=?")
        params.append(meeting_date_from)
    if meeting_date_to:
        _parse_meeting_date(meeting_date_to)
        conditions.append("s.meeting_date<=?")
        params.append(meeting_date_to)
    if allowed is not None:
        placeholders = ",".join("?" for _ in allowed)
        conditions.append(f"s.class_org_unit_id IN ({placeholders})")
        params.extend(sorted(allowed))
    rows = fetch_all(
        "SELECT s.id, s.session_code, s.class_org_unit_id, c.name AS class_name, "
        "s.study_group_org_unit_id, g.name AS group_name, s.learning_cycle_id, "
        "lc.learning_cycle_index AS cycle_index, s.meeting_date, "
        "s.created_by_member_id, creator.name AS creator_name, s.has_course, "
        "s.course_key, s.course_name_snapshot, s.course_credit_snapshot, s.status, s.course_details_initialized, "
        "s.submitted_at, s.created_at, s.updated_at, "
        "SUM(CASE WHEN a.attendance_type='HOME_GROUP' THEN 1 ELSE 0 END) AS home_count, "
        "SUM(CASE WHEN a.attendance_type='CROSS_GROUP' THEN 1 ELSE 0 END) AS cross_group_count, "
        "COUNT(a.id) AS total_count "
        "FROM study_meeting_sessions s "
        "JOIN org_units c ON c.id=s.class_org_unit_id "
        "JOIN org_units g ON g.id=s.study_group_org_unit_id "
        "JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id "
        "JOIN members creator ON creator.id=s.created_by_member_id "
        "LEFT JOIN study_meeting_attendances a ON a.study_meeting_session_id=s.id "
        "WHERE " + " AND ".join(conditions) + " "
        "GROUP BY s.id, s.session_code, s.class_org_unit_id, c.name, s.study_group_org_unit_id, g.name, "
        "s.learning_cycle_id, lc.learning_cycle_index, s.meeting_date, s.created_by_member_id, "
        "creator.name, s.has_course, s.course_key, s.course_name_snapshot, s.course_credit_snapshot, "
        "s.status, s.submitted_at, s.created_at, s.updated_at, s.course_details_initialized "
        "ORDER BY s.meeting_date DESC, s.id DESC LIMIT 500",
        tuple(params),
    )
    connection = connect()
    try:
        grouped: dict[int, list[dict]] = {}
        if rows:
            ids = tuple(row["id"] for row in rows)
            placeholders = ",".join("?" for _ in ids)
            for item in execute(connection,
                f"SELECT * FROM study_meeting_courses WHERE study_meeting_session_id IN ({placeholders}) ORDER BY id",
                ids).fetchall():
                grouped.setdefault(item["study_meeting_session_id"], []).append(dict(item))
        for row in rows:
            row["courses"] = _read_courses(connection, row, grouped.get(row["id"], []))
    finally:
        connection.close()
    return [
        {
            **{key: _serialize(value) for key, value in dict(row).items()},
            "id": int(row["id"]),
            "has_course": bool(row["courses"]),
            "home_count": int(row["home_count"] or 0),
            "cross_group_count": int(row["cross_group_count"] or 0),
            "total_count": int(row["total_count"] or 0),
            "course_rule_status": (
                "CONFIGURED" if row["course_key"] and row["course_credit_snapshot"] is not None
                else "PENDING" if row["course_key"] else None
            ),
        }
        for row in rows
    ]


def get_study_meeting_record_for_operations(
    *, actor_user_id: int, session_id: int
) -> dict[str, Any]:
    row = fetch_one(
        "SELECT class_org_unit_id FROM study_meeting_sessions WHERE id=? LIMIT 1",
        (session_id,),
    )
    if not row:
        raise StudyMeetingError("学习会记录不存在")
    if not _operation_scope_allows(actor_user_id, row["class_org_unit_id"]):
        raise StudyMeetingPermissionError("学习会记录不在当前组织授权范围内")
    connection = connect()
    try:
        result = _session_payload(connection, session_id)
    finally:
        connection.close()
    if not result:
        raise StudyMeetingError("学习会记录不存在")
    result["can_edit_courses"] = can_edit_meeting_courses(actor_user_id) and result["status"] == "SUBMITTED"
    from app.services.study_meeting_attendees import can_edit_attendees
    result["can_edit_attendees"] = can_edit_attendees(actor_user_id) and result["status"] == "SUBMITTED"
    result["course_options"] = list(_course_rules().values()) if result["can_edit_courses"] else []
    return result


def can_edit_meeting_courses(actor_user_id: int) -> bool:
    settings = get_settings()
    user = user_context(actor_user_id) or {}
    return (
        settings.study_meeting_course_edit_enabled
        and not settings.deployment_read_only
        and (not settings.is_production or settings.allow_production_mutations)
        and "study_meetings:courses_edit" in user.get("permissions", [])
    )


def correct_meeting_courses(*, actor_user_id: int, session_id: int,
                            course_keys: list[str], expected_course_keys: list[str],
                            note: str | None = None) -> dict:
    _require_write()
    if not can_edit_meeting_courses(actor_user_id):
        raise StudyMeetingPermissionError("课程修正功能未开启或无此权限")
    with transaction() as connection:
        row = _lock_session(connection, session_id)
        if not _operation_scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise StudyMeetingPermissionError("学习会记录不在当前组织授权范围内")
        if row["status"] != "SUBMITTED":
            raise StudyMeetingError("仅能修正已提交学习会的课程")
        before = _read_courses(connection, row)
        if sorted(item["course_key"] for item in before) != sorted(expected_course_keys):
            raise StudyMeetingError("课程已被其他人修改，请刷新后重试")
        snapshots = _course_snapshots(course_keys)
        # A correction adds/removes courses, not a hidden re-pricing of retained facts.
        old = {item["course_key"]: dict(item) for item in before}
        for previous in old.values():
            if not previous.get("rule_reference_json"):
                previous["rule_reference_json"] = json.dumps({
                    "source": "LEGACY_SINGLE_COURSE", "version_label": None,
                    "note": "保留旧单课程快照，不推断历史积分规则版本",
                }, ensure_ascii=False)
        snapshots = [old[item["course_key"]] if item["course_key"] in old
                     else item for item in snapshots]
        _write_courses(connection, session_id, snapshots)
        write_audit(connection, actor_user_id=actor_user_id, action="study_meeting.courses_correct",
                    resource_type="study_meeting_session", resource_id=str(session_id),
                    org_unit_id=row["class_org_unit_id"], purpose=(note or "").strip() or None,
                    before={"courses": before}, after={"courses": snapshots, "status": "SUBMITTED"})
    return get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)


def member_from_session_token(token: str) -> dict[str, Any]:
    try:
        return resolve_member_session(token)
    except WeChatIdentityError as exc:
        raise StudyMeetingPermissionError(str(exc)) from exc
