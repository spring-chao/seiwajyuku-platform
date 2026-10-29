"""V1.2 MVP-B group study meeting fact capture.

This module intentionally has no relationship with the legacy attendance
tables and never advances a class learning cycle.  It stores the people,
course snapshot, and per-member course completion fact exactly as submitted by
an authorized group leader/counsellor. B2.1 adds course detail snapshots and
private photo evidence; formal credit posting remains separately gated.
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
from app.services.credit_rule_mapping import bound_course_rule_version
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


def _refresh_monthly_content(class_org_unit_id: str) -> None:
    from app.services.learning_cycle_monthly import refresh_class
    try:
        result = refresh_class(class_org_unit_id)
        if result.get("status") in {"REPAIR_REQUIRED", "REPAIR_REVIEW_REQUIRED"}:
            raise StudyMeetingError("该班级学习次数正在校准，请校准完成后再登记，避免记入错误周期")
    except ValueError as exc:
        raise StudyMeetingError(str(exc)) from exc


def _active_group_members(connection, group_org_unit_id: str) -> list[dict[str, Any]]:
    from app.services.study_membership import group_roster
    return [{"member_id": int(row["member_id"]), "name": row["name"],
             "phone_masked": row["phone_masked"] or ""}
            for row in group_roster(connection, group_id=group_org_unit_id)]


def _course_rule_directory_for_binding(
    connection, binding: dict[str, Any]
) -> dict[str, Any]:
    """Resolve course metadata through the binding's frozen policy id.

    When a binding has not been mapped yet, the immutable default catalog is
    used only to keep the submitted course fact readable.  Its points are
    deliberately discarded and the snapshot is marked PENDING so settlement
    cannot mistake that catalog lookup for a bound course policy.
    """

    course_rule_version_id = binding.get("course_credit_rule_version_id")
    course_version = bound_course_rule_version(
        connection,
        int(course_rule_version_id) if course_rule_version_id is not None else None,
    )
    if course_version:
        directory = list_course_credit_rules(
            str(course_version["plan_key"]), str(course_version["version_label"])
        )
        return {
            **directory,
            "mapping_status": "BOUND",
            "course_rule_version_id": int(course_version["id"]),
            "course_rule_plan_key": course_version["plan_key"],
            "course_rule_version_label": course_version["version_label"],
            "course_rule_version_status": course_version["status"],
        }

    # This is an explicit missing-mapping path, not a pricing fallback.  The
    # returned directory is metadata-only; _course_snapshots() sets every
    # captured point to NULL/PENDING below.
    directory = list_course_credit_rules()
    metadata_rules = [
        {
            **rule,
            "credit_points": 0,
            "status": "PENDING",
            "persisted": False,
        }
        for rule in directory.get("rules", [])
    ]
    return {
        **directory,
        "rules": metadata_rules,
        "mapping_status": "RULE_MAPPING_MISSING",
        "course_rule_version_id": None,
        "course_rule_plan_key": None,
        "course_rule_version_label": None,
        "course_rule_version_status": "MISSING",
    }


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
        result = []
        for item in details:
            payload = {key: _serialize(value) for key, value in item.items()}
            if item.get("id"):
                payload["completions"] = [
                    {key: _serialize(value) for key, value in dict(completion).items()}
                    for completion in execute(
                        connection,
                        "SELECT r.*, m.name AS member_name FROM study_meeting_course_completions r "
                        "JOIN members m ON m.id=r.member_id "
                        "WHERE r.study_meeting_course_id=? ORDER BY r.member_id",
                        (item["id"],),
                    ).fetchall()
                ]
            result.append(payload)
        return result
    if not row.get("course_details_initialized") and row.get("course_key"):
        return [{
            "course_key": row["course_key"], "course_name_snapshot": row["course_name_snapshot"],
            "course_credit_snapshot": row["course_credit_snapshot"],
            "course_rule_status": "CONFIGURED" if row["course_credit_snapshot"] is not None else "PENDING",
            "rule_reference_json": None, "completion_status": "UNCONFIRMED", "legacy": True,
        }]
    return []


def _course_snapshots(
    connection, keys: list[str], *, binding: dict[str, Any]
) -> list[dict]:
    if any(not isinstance(key, str) or not key or len(key) > 128 for key in keys):
        raise StudyMeetingError("课程编号无效")
    if len(keys) != len(set(keys)):
        raise StudyMeetingError("同一课程不能重复添加")
    directory = _course_rule_directory_for_binding(connection, binding)
    mapping_missing = directory["mapping_status"] != "BOUND"
    rules = {item["course_key"]: item for item in directory["rules"]}
    snapshots = []
    for key in keys:
        rule = rules.get(key)
        if not rule:
            raise StudyMeetingError("课程不在当前课程积分目录中")
        rule_status = "PENDING" if mapping_missing else rule["status"]
        item = {
            "course_key": key, "course_name_snapshot": rule["course_name"],
            "course_credit_snapshot": (
                rule["credit_points"]
                if rule_status == "CONFIGURED" and not mapping_missing
                else None
            ),
            "course_rule_status": rule_status,
            "credit_rule_version_id": directory["course_rule_version_id"],
            "plan_key": binding["plan_key"],
            "plan_version_label": binding["version_label"],
        }
        reference = {
            "mapping_status": directory["mapping_status"],
            # These legacy aliases describe the course-policy namespace.  The
            # binding learning-plan identity is kept separately below.
            "plan_key": directory.get("plan_key"),
            "version_label": directory.get("version_label"),
            "binding_plan_key": binding["plan_key"],
            "binding_version_label": binding["version_label"],
            "course_rule_version_id": directory["course_rule_version_id"],
            "course_rule_plan_key": directory["course_rule_plan_key"],
            "course_rule_version_label": directory["course_rule_version_label"],
            "rule_id": rule.get("id"), "rule_updated_at": _serialize(rule.get("updated_at")),
            "source": rule.get("source"),
            "version_status": directory["course_rule_version_status"],
            "source_catalog_plan_key": directory.get("plan_key"),
            "unresolved_plan": mapping_missing,
            "snapshot_sha256": hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        }
        item["rule_reference_json"] = json.dumps(reference, ensure_ascii=False, sort_keys=True)
        snapshots.append(item)
    return snapshots


def _write_courses(
    connection, session_id: int, snapshots: list[dict], *,
    attendee_member_ids: list[int] | None = None,
    confirmed_by_member_id: int | None = None,
    confirmed_by_user_id: int | None = None,
    confirmation_source: str = "MEMBER_SUBMISSION",
) -> None:
    now = _db_timestamp(connection)
    execute(connection, "DELETE FROM study_meeting_courses WHERE study_meeting_session_id=?", (session_id,))
    for item in snapshots:
        completion_status = item.get("completion_status") or "CONFIRMED"
        completed_at = now if completion_status == "CONFIRMED" else None
        execute(connection,
            "INSERT INTO study_meeting_courses(study_meeting_session_id, course_key, course_name_snapshot, "
            "course_credit_snapshot, course_rule_status, rule_reference_json, completion_status, completed_at, "
            "confirmed_by_member_id, confirmed_by_user_id, completion_note, credit_rule_version_id, plan_key, "
            "plan_version_label, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, item["course_key"], item["course_name_snapshot"], item["course_credit_snapshot"],
             item["course_rule_status"], item["rule_reference_json"], completion_status, completed_at,
             confirmed_by_member_id, confirmed_by_user_id, item.get("completion_note"),
             item.get("credit_rule_version_id"), item.get("plan_key"), item.get("plan_version_label"), now, now))
        if isinstance(connection, sqlite3.Connection):
            course_id = int(execute(connection, "SELECT last_insert_rowid() AS id").fetchone()["id"])
        else:
            course_id = int(execute(connection, "SELECT LAST_INSERT_ID() AS id").fetchone()["id"])
        completion_facts = {
            int(fact["member_id"]): fact
            for fact in item.get("completions", [])
            if fact.get("member_id") is not None
        }
        for member_id in attendee_member_ids or []:
            fact = completion_facts.get(int(member_id))
            member_status = fact.get("completion_status") if fact else completion_status
            member_completed_at = fact.get("completed_at") if fact else completed_at
            member_confirmed_by_member_id = (
                fact.get("confirmed_by_member_id") if fact else confirmed_by_member_id
            )
            member_confirmed_by_user_id = (
                fact.get("confirmed_by_user_id") if fact else confirmed_by_user_id
            )
            member_source = fact.get("confirmation_source") if fact else confirmation_source
            member_note = fact.get("note") if fact else item.get("completion_note")
            execute(
                connection,
                "INSERT INTO study_meeting_course_completions "
                "(study_meeting_course_id, member_id, completion_status, completed_at, "
                "confirmed_by_member_id, confirmed_by_user_id, confirmation_source, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (course_id, member_id, member_status, member_completed_at,
                 member_confirmed_by_member_id, member_confirmed_by_user_id,
                 member_source, member_note, now, now),
            )
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
    confirmation = execute(
        connection,
        "SELECT after_json FROM audit_logs WHERE resource_type='study_meeting_session' "
        "AND resource_id=? AND action IN ('study_meeting.create', 'study_meeting.draft_content_update') "
        "ORDER BY id DESC LIMIT 1", (str(session_id),),
    ).fetchone()
    result["learning_content_results"] = (
        json.loads(confirmation["after_json"]).get("learning_content_results")
        if confirmation and confirmation["after_json"] else None
    )
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
    refresh_errors: dict[str, str] = {}
    for class_id in sorted({target["class_org_unit_id"] for target in targets}):
        try:
            _refresh_monthly_content(class_id)
        except StudyMeetingError as exc:
            refresh_errors[class_id] = str(exc)
    connection = connect()
    try:
        for target in targets:
            cycle_data: dict[str, Any] | None = None
            try:
                if target["class_org_unit_id"] in refresh_errors:
                    raise StudyMeetingError(refresh_errors[target["class_org_unit_id"]])
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


def search_cross_group_members(*, member_id: int, group_org_unit_id: str, query: str | None = None) -> list[dict[str, Any]]:
    from app.services.study_membership import group_roster
    _require_enabled()
    target = _target_for_member(member_id, group_org_unit_id)
    term = (query or "").strip()
    if len(term) > 80:
        raise StudyMeetingError("搜索条件过长")
    connection = connect()
    try:
        rows = group_roster(connection, class_id=target["class_org_unit_id"])
    finally:
        connection.close()
    return [{key: row[key] for key in ('member_id','name','class_name','group_name')}
            for row in rows if row['group_org_unit_id'] != group_org_unit_id
            and (not term or term.casefold() in row['name'].casefold())][:50]


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

    from app.services.study_membership import group_roster
    current = {int(item['member_id']): item['group_org_unit_id']
               for item in group_roster(connection, class_id=class_id, member_ids=member_ids)}
    if any(current.get(mid) != target_group_id for mid in home_ids):
        raise StudyMeetingError("本组名单已不再是当前有效关系")
    current_cross = {mid: current[mid] for mid in cross_ids if mid in current and current[mid] != target_group_id}
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


def _session_plan(connection, session_row: dict[str, Any]) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT b.id AS binding_id, b.credit_rule_version_id, "
        "b.course_credit_rule_version_id, "
        "p.plan_key, p.version_label, p.status AS plan_status "
        "FROM class_learning_cycles lc "
        "JOIN class_learning_bindings b ON b.id=lc.binding_id "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE lc.id=? LIMIT 1",
        (session_row["learning_cycle_id"],),
    ).fetchone()
    if not row:
        raise StudyMeetingError("学习会缺少有效学习计划版本")
    return dict(row)


def _validate_course_completions(connection, session_row: dict[str, Any]) -> None:
    courses = execute(
        connection,
        "SELECT id, course_key FROM study_meeting_courses "
        "WHERE study_meeting_session_id=? ORDER BY id",
        (session_row["id"],),
    ).fetchall()
    if not courses:
        if session_row.get("course_key") and not session_row.get("course_details_initialized"):
            raise StudyMeetingError("课程缺少逐人实际完成事实，暂不能生成课程学分")
        return
    attendees = [
        int(item["member_id"])
        for item in execute(
            connection,
            "SELECT member_id FROM study_meeting_attendances WHERE study_meeting_session_id=?",
            (session_row["id"],),
        ).fetchall()
    ]
    for course in courses:
        confirmed = {
            int(item["member_id"])
            for item in execute(
                connection,
                "SELECT member_id FROM study_meeting_course_completions "
                "WHERE study_meeting_course_id=? AND completion_status='CONFIRMED'",
                (course["id"],),
            ).fetchall()
        }
        if set(attendees) != confirmed:
            raise StudyMeetingError(
                f"课程 {course['course_key']} 缺少全部参加学员的实际完成确认"
            )


def _learning_content_course_keys(connection, cycle_data: dict, results: list[dict],
                                  learning_cycle_id: int | None) -> list[str]:
    cycle = cycle_data["cycle"]
    if learning_cycle_id != int(cycle["id"]):
        raise StudyMeetingError("当前学习周期已变化，请重新加载学习内容后提交")
    plan_cycle = _plan_cycle_payload(connection, int(cycle["plan_cycle_id"]))
    if not plan_cycle:
        raise StudyMeetingError("当前学习周期缺少对应的学习计划内容")
    try:
        plan = build_group_meeting_plan(
            plan_cycle=plan_cycle, cohort_month=cycle_data["binding"].get("cohort_month"),
            learning_cycle_index=int(cycle["learning_cycle_index"]),
        )
    except GroupMeetingPlanConfigError as exc:
        raise StudyMeetingError(str(exc)) from exc
    visible = set()
    terminal_found = False
    for step in plan["steps"]:
        visible.update(step.get("learning_content_keys", []))
        if step.get("is_terminal"):
            terminal_found = True
            break
    if not terminal_found:
        raise StudyMeetingError("当前小组学习会流程配置不完整")
    contents = {item["content_key"]: item for item in plan["learning_contents"]
                if item["content_key"] in visible}
    submitted = [item.get("content_key") for item in results]
    if len(set(submitted)) != len(submitted) or set(submitted) != set(contents):
        raise StudyMeetingError("学习内容与当前周期不一致，请重新加载后提交")
    keys = []
    for item in results:
        if type(item.get("completed")) is not bool or type(item.get("confirmed")) is not bool:
            raise StudyMeetingError("学习内容完成选项无效")
        if item["completed"] and not item["confirmed"]:
            raise StudyMeetingError("已完成的学习内容需要明确确认")
        key = contents[item["content_key"]].get("credit_rule_key")
        if item["completed"] and key and key not in keys:
            keys.append(key)
    return keys


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
    learning_cycle_id: int | None = None,
    learning_content_results: list[dict] | None = None,
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
    if len(set(keys)) != len(keys):
        raise StudyMeetingError("同一课程不能重复添加")
    if bool(keys) != has_course:
        raise StudyMeetingError("观看课程选项与课程列表不一致")
    if learning_content_results is not None and (has_course or course_key is not None or course_keys is not None):
        raise StudyMeetingError("学习内容确认不能同时传入旧课程字段")
    meeting_day = _parse_meeting_date(meeting_date)
    now = _now()

    _refresh_monthly_content(target["class_org_unit_id"])
    with transaction() as connection:
        # Serialize creation on a stable parent row, including the first insert.
        # A child-only lock cannot prevent two concurrent first submissions.
        execute(connection, "UPDATE org_units SET id=id WHERE id=?", (target["group_org_unit_id"],))
        cycle_data = _current_cycle(connection, target["class_org_unit_id"])
        cycle = cycle_data["cycle"]
        binding = cycle_data["binding"]
        if learning_content_results is not None:
            keys = _learning_content_course_keys(connection, cycle_data, learning_content_results, learning_cycle_id)
        existing = execute(connection,
            "SELECT * FROM study_meeting_sessions WHERE study_group_org_unit_id=? "
            "AND learning_cycle_id=? AND status<>'CANCELLED' ORDER BY id DESC LIMIT 1",
            (target["group_org_unit_id"], cycle["id"])).fetchone()
        if existing:
            # Submit and operator corrections lock this same parent session.
            # Recheck status under that lock before changing a draft's courses.
            existing = _lock_session(connection, int(existing['id']))
            if existing['status'] == 'DRAFT':
                payload = _session_payload(connection, int(existing['id']))
                same_home = {r['member_id'] for r in payload['home_attendees']} == set(home_ids)
                same_cross = {r['member_id'] for r in payload['cross_group_attendees']} == set(cross_ids)
                same_courses = {r['course_key'] for r in payload['courses']} == set(keys)
                if (learning_content_results is not None and same_home and same_cross
                        and int(existing['created_by_member_id']) == member_id
                        and str(existing['meeting_date']) == meeting_day):
                    # Only the creator may sync the same unsubmitted draft.
                    # Keep photo/attendees and retain existing pricing snapshots.
                    retained = {item['course_key']: item for item in payload['courses']}
                    additions = _course_snapshots(connection, [key for key in keys if key not in retained], binding=binding)
                    by_key = {**retained, **{item['course_key']: item for item in additions}}
                    snapshots = [by_key[key] for key in keys]
                    if not same_courses:
                        _write_courses(connection, int(existing['id']), snapshots,
                                       attendee_member_ids=[*home_ids, *cross_ids],
                                       confirmed_by_member_id=member_id)
                    if not same_courses or payload.get('learning_content_results') != learning_content_results:
                        write_audit(connection, actor_user_id=None, action="study_meeting.draft_content_update",
                                    resource_type="study_meeting_session", resource_id=str(existing['id']),
                                    org_unit_id=target['class_org_unit_id'],
                                    before={"courses": payload['courses'], "learning_content_results": payload.get('learning_content_results')},
                                    after={"courses": snapshots, "learning_content_results": learning_content_results,
                                           "created_by_member_id": member_id})
                    return _session_payload(connection, int(existing['id']))
                if same_home and same_cross and same_courses:
                    # A lost creation response or restarted photo upload retries
                    # the original draft instead of creating another fact.
                    return payload
            raise StudyMeetingError(f"本小组在当前学习周期已有学习会（{existing['session_code']}），请查看原记录；已提交内容请联系运营人员更正，不能重复上传")
        snapshots = _course_snapshots(
            connection,
            keys,
            binding=binding,
        )
        from app.services.study_membership import group_roster
        current = {int(item['member_id']): item['group_org_unit_id'] for item in group_roster(
            connection, class_id=target['class_org_unit_id'], member_ids=[*home_ids, *cross_ids])}
        if any(current.get(mid) != target['group_org_unit_id'] for mid in home_ids):
            raise StudyMeetingError("本组名单包含不在当前有效关系中的学长")
        cross_home_groups = {mid: current[mid] for mid in cross_ids if mid in current and current[mid] != target['group_org_unit_id']}
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
        _write_courses(
            connection,
            session_id,
            snapshots,
            attendee_member_ids=[*home_ids, *cross_ids],
            confirmed_by_member_id=member_id,
            confirmation_source="MEMBER_SUBMISSION",
        )
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
                "learning_content_results": learning_content_results,
                "created_by_member_id": member_id,
            },
        )
        payload = _session_payload(connection, session_id)
    if not payload:
        raise StudyMeetingError("学习会保存失败")
    return payload


def _validate_session_before_submit(connection, row) -> None:
    current_cycle = _current_cycle(connection, row["class_org_unit_id"])["cycle"]
    if int(current_cycle["id"]) != int(row["learning_cycle_id"]):
        raise StudyMeetingError("当前学习周期已变化，请重新登记本场学习会")
    _validate_session_attendees(connection, row)
    plan = _session_plan(connection, row)
    course_rule_directory = _course_rule_directory_for_binding(connection, plan)
    course_rules = {
        str(rule["course_key"]): rule
        for rule in course_rule_directory["rules"]
    }
    if any(item["course_key"] not in course_rules for item in _read_courses(connection, row)):
        raise StudyMeetingError("课程不在当前课程积分目录中")
    _validate_course_completions(connection, row)
    from app.services.study_meeting_evidence import evidence_metadata
    if not evidence_metadata(connection, int(row["id"])):
        raise StudyMeetingError("请先上传一张学习合影")


def submit_study_meeting(*, member_id: int, session_id: int) -> dict[str, Any]:
    _require_enabled()
    _require_write()
    session = fetch_one("SELECT class_org_unit_id, study_group_org_unit_id, status FROM study_meeting_sessions WHERE id=?", (session_id,))
    if session and session["status"] == "DRAFT":
        if not role_for_target(member_id, session["class_org_unit_id"], session["study_group_org_unit_id"]):
            raise StudyMeetingPermissionError("当前没有该小组的有效登记任职")
        _refresh_monthly_content(session["class_org_unit_id"])
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

        _validate_session_before_submit(connection, row)

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


def study_meeting_filter_options(*, actor_user_id: int) -> dict:
    from app.services.learning_org_catalog import class_catalog
    from app.db import connect
    allowed = accessible_org_ids(actor_user_id)
    c = connect()
    try:
        catalog = class_catalog(c, allowed)
    finally:
        c.close()
    organizations = {r['id']: r for r in fetch_all('SELECT id,name,parent_id,unit_type FROM org_units')}
    classes=[];regions={}
    for item in catalog:
        parent=item;visited=set();region=None
        while parent and parent['id'] not in visited:
            visited.add(parent['id'])
            if parent['unit_type'] in {'REGIONAL_CENTER','OPERATING_UNIT'}:
                region=parent;break
            parent=organizations.get(parent['parent_id'])
        classes.append({'id':item['id'],'name':item['name'],
                        'related_class_ids':item['related_class_ids'],
                        'management_org_unit_id':region['id'] if region else None})
        if region:regions[region['id']]={'id':region['id'],'name':region['name']}
    return {'classes':classes, 'regions':sorted(regions.values(),key=lambda v:(v['name'],v['id']))}



def list_study_meeting_records(
    *,
    actor_user_id: int,
    status: str | None = None,
    meeting_date_from: str | None = None,
    meeting_date_to: str | None = None,
    management_org_unit_id: str | None = None,
    class_org_unit_id: str | None = None,
    limit: int = 500,
    scope_permissions: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Formal records only; retryable drafts are not completed registrations."""

    allowed = accessible_org_ids(actor_user_id)
    for permission in scope_permissions:
        scope = accessible_org_ids(actor_user_id, permission)
        if scope is not None:
            allowed = scope if allowed is None else allowed & scope
    if allowed == set():
        return []
    if management_org_unit_id or class_org_unit_id:
        options=study_meeting_filter_options(actor_user_id=actor_user_id)
        selected={cid for r in options['classes'] for cid in r['related_class_ids']
                  if (not class_org_unit_id or class_org_unit_id in r['related_class_ids'])
                  and (not management_org_unit_id or r['management_org_unit_id']==management_org_unit_id)}
        allowed=selected if allowed is None else allowed & selected
        if not allowed:return []
    conditions = ["s.status<>'DRAFT'"]
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
        "ORDER BY s.meeting_date DESC, s.id DESC LIMIT ?",
        (*params, limit),
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
        from app.services.study_meeting_review import add_operation_metadata
        add_operation_metadata(connection, rows)
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
    course_options: list[dict[str, Any]] = []
    try:
        # Keep the displayed facts and their review fingerprint in one snapshot.
        # MySQL already starts a consistent transaction on the first SELECT;
        # SQLite needs an explicit read transaction across these SELECTs.
        if isinstance(connection, sqlite3.Connection):
            execute(connection, "BEGIN")
        result = _session_payload(connection, session_id)
        if result:
            from app.services.study_meeting_review import add_operation_metadata
            add_operation_metadata(connection, [result])
            from app.services.study_meeting_review import settlement_metadata
            if {"plans:credit_settlement_preview", "plans:credit_settlement_manage", "plans:credit_settlement_post"} & set((user_context(actor_user_id) or {}).get("permissions", [])):
                result["credit_settlement"] = settlement_metadata(connection,session_id)
        correction_lock_reason = _correction_lock_reason(connection, result) if result else None
        can_edit_courses = (
            bool(result)
            and can_edit_meeting_courses(actor_user_id)
            and result["status"] == "SUBMITTED"
            and not correction_lock_reason
        )
        if can_edit_courses:
            plan = _session_plan(connection, result)
            course_options = list(
                _course_rule_directory_for_binding(connection, plan)["rules"]
            )
    finally:
        connection.close()
    if not result:
        raise StudyMeetingError("学习会记录不存在")
    result["can_edit_courses"] = can_edit_courses
    from app.services.study_meeting_attendees import can_edit_attendees
    result["can_edit_attendees"] = can_edit_attendees(actor_user_id) and result["status"] == "SUBMITTED" and not correction_lock_reason
    review_scope = accessible_org_ids(actor_user_id, "study_meetings:attendees_edit")
    result["draft_submission_message"] = (
        "合影尚未上传成功，本次登记未提交；请登记人补传合影后重试。" if not result.get("evidence") else
        "登记尚未完成最后提交；核对合影和参加人员后，可在本页完成提交并自动结算。"
    ) if result["status"] == "DRAFT" else None
    result["can_confirm_review"] = ((result["status"] == "DRAFT" and bool(result.get("evidence")) and can_edit_attendees(actor_user_id) and not correction_lock_reason) or result["can_edit_attendees"] or (result["review_status"] == "CONFIRMED" and can_edit_attendees(actor_user_id) and result.get("credit_settlement", {}).get("status") not in {"POSTED","CLOSED"})) and (review_scope is None or result["class_org_unit_id"] in review_scope)
    result["correction_lock_reason"] = correction_lock_reason
    result["course_options"] = course_options
    return result


def can_edit_meeting_courses(actor_user_id: int) -> bool:
    settings = get_settings()
    user = user_context(actor_user_id) or {}
    return (
        (settings.study_meeting_course_edit_enabled or settings.study_meeting_pre_settlement_review_enabled)
        and (not settings.learning_credit_settlement_enabled or settings.study_meeting_pre_settlement_review_enabled)
        and not settings.deployment_read_only
        and (not settings.is_production or settings.allow_production_mutations)
        and "study_meetings:courses_edit" in user.get("permissions", [])
    )


def _correction_lock_reason(connection, session: dict, *, lock: bool = False) -> str | None:
    """Never rewrite a source already used by the ledger or a frozen approval.

    Batch states become protected before posting starts. Locking reads see the
    latest MySQL state even under REPEATABLE READ. A stale DRY_RUN can be edited,
    but its existing fingerprint checks prevent submission/posting afterwards.
    """
    from app.services.credit_settlement_batches import _batch_tables_available
    if isinstance(connection, sqlite3.Connection):
        ledger = execute(connection, "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='learning_credit_entries'").fetchone()
    else:
        ledger = execute(connection, "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='learning_credit_entries'").fetchone()
    if not int(ledger["n"]) or not _batch_tables_available(connection):
        return "结算记录尚未就绪，暂不能人工修正"
    lock_clause = " FOR UPDATE" if lock and not isinstance(connection, sqlite3.Connection) else ""
    courses = execute(connection, "SELECT id FROM study_meeting_courses WHERE study_meeting_session_id=?", (session["id"],)).fetchall()
    source_clause = "(source_type='STUDY_MEETING_ATTENDANCE' AND source_id=?)"
    params = [str(session["id"])]
    if courses:
        placeholders = ",".join("?" for _ in courses)
        source_clause += f" OR (source_type='STUDY_MEETING_COURSE' AND source_id IN ({placeholders}))"
        params.extend(str(item["id"]) for item in courses)
    posted = execute(connection, "SELECT id FROM learning_credit_entries WHERE " + source_clause + " LIMIT 1" + lock_clause, tuple(params)).fetchone()
    if posted:
        return "本场已有学分账本记录，请到学分结算工作台按更正流程处理"
    batches = execute(connection, "SELECT id, status, source_snapshot_json FROM learning_credit_settlement_batches "
                      "WHERE class_org_unit_id=? AND source_type='STUDY_MEETING' "
                      "AND status NOT IN ('DRAFT','DRY_RUN','CANCELLED') ORDER BY id" + lock_clause,
                      (session["class_org_unit_id"],)).fetchall()
    for batch in batches:
        snapshot = json.loads(batch["source_snapshot_json"] or "{}")
        if int(snapshot.get("session_id") or 0) == int(session["id"]):
            return "本场已进入结算审批或入账流程，请先在学分结算工作台处理对应批次"
    return None


def _require_correctable_session(connection, session: dict) -> None:
    reason = _correction_lock_reason(connection, session, lock=True)
    if reason:
        raise StudyMeetingError(reason)


def correct_meeting_courses(*, actor_user_id: int, session_id: int,
                            course_keys: list[str], expected_course_keys: list[str],
                            note: str | None = None) -> dict:
    _require_write()
    if not can_edit_meeting_courses(actor_user_id):
        raise StudyMeetingPermissionError("课程修正功能未开启或无此权限；积分结算开启时禁止直接修正")
    with transaction() as connection:
        row = _lock_session(connection, session_id)
        if not _operation_scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise StudyMeetingPermissionError("学习会记录不在当前组织授权范围内")
        if row["status"] != "SUBMITTED":
            raise StudyMeetingError("仅能修正已提交学习会的课程")
        _require_correctable_session(connection, row)
        before = _read_courses(connection, row)
        if sorted(item["course_key"] for item in before) != sorted(expected_course_keys):
            raise StudyMeetingError("课程已被其他人修改，请刷新后重试")
        plan = _session_plan(connection, row)
        snapshots = _course_snapshots(
            connection,
            course_keys,
            binding=plan,
        )
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
        attendee_ids = [
            int(item["member_id"])
            for item in execute(
                connection,
                "SELECT member_id FROM study_meeting_attendances "
                "WHERE study_meeting_session_id=? ORDER BY member_id",
                (session_id,),
            ).fetchall()
        ]
        _write_courses(
            connection,
            session_id,
            snapshots,
            attendee_member_ids=attendee_ids,
            confirmed_by_user_id=actor_user_id,
            confirmation_source="OPERATOR_CONFIRMATION",
        )
        write_audit(connection, actor_user_id=actor_user_id, action="study_meeting.courses_correct",
                    resource_type="study_meeting_session", resource_id=str(session_id),
                    org_unit_id=row["class_org_unit_id"], purpose=(note or "").strip() or None,
                    before={"courses": before}, after={"courses": snapshots, "status": "SUBMITTED"})
    return get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)


def confirm_study_meeting_course_completion(
    *, actor_user_id: int, session_id: int, course_key: str, member_id: int,
    completion_status: str, note: str | None = None, expected_completion_status: str | None = None,
) -> dict[str, Any]:
    """Confirm the per-member course fact used by formal settlement.

    This is intentionally an operator correction endpoint.  The member-facing
    course selection creates a persisted fact, but an operator can explicitly
    confirm, reject, or return that fact for review before C12 is opened.
    """

    _require_write()
    if completion_status not in {"UNCONFIRMED", "CONFIRMED", "NOT_COMPLETED"}:
        raise StudyMeetingError("课程完成状态无效")
    if not isinstance(member_id, int) or member_id <= 0:
        raise StudyMeetingError("学员编号无效")
    if note is not None and (not isinstance(note, str) or len(note) > 1000):
        raise StudyMeetingError("完成确认备注最多1000字")
    if not can_edit_meeting_courses(actor_user_id):
        raise StudyMeetingPermissionError("课程修正功能未开启或无此权限；积分结算开启时禁止直接修正")
    with transaction() as connection:
        session = _lock_session(connection, session_id)
        if not _operation_scope_allows(actor_user_id, session["class_org_unit_id"]):
            raise StudyMeetingPermissionError("学习会记录不在当前组织授权范围内")
        if session["status"] != "SUBMITTED":
            raise StudyMeetingError("仅能确认已提交学习会的课程完成事实")
        _require_correctable_session(connection, session)
        course = execute(
            connection,
            "SELECT * FROM study_meeting_courses "
            "WHERE study_meeting_session_id=? AND course_key=? LIMIT 1",
            (session_id, course_key),
        ).fetchone()
        if not course:
            raise StudyMeetingError("课程不在该学习会记录中")
        attendee = execute(
            connection,
            "SELECT id FROM study_meeting_attendances "
            "WHERE study_meeting_session_id=? AND member_id=? LIMIT 1",
            (session_id, member_id),
        ).fetchone()
        if not attendee:
            raise StudyMeetingError("该学员不是本场学习会参加人员")
        before = execute(
            connection,
            "SELECT * FROM study_meeting_course_completions "
            "WHERE study_meeting_course_id=? AND member_id=? LIMIT 1",
            (course["id"], member_id),
        ).fetchone()
        if not before:
            raise StudyMeetingError("课程完成事实不存在")
        if expected_completion_status is not None and before["completion_status"] != expected_completion_status:
            raise StudyMeetingError("观看确认已被其他人修改，请刷新后重试")
        now = _db_timestamp(connection)
        completed_at = now if completion_status == "CONFIRMED" else None
        execute(
            connection,
            "UPDATE study_meeting_course_completions SET completion_status=?, completed_at=?, "
            "confirmed_by_member_id=NULL, confirmed_by_user_id=?, confirmation_source='OPERATOR_CONFIRMATION', "
            "note=?, updated_at=? WHERE study_meeting_course_id=? AND member_id=?",
            (completion_status, completed_at, actor_user_id, (note or "").strip() or None,
             now, course["id"], member_id),
        )
        completion_counts = execute(
            connection,
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN completion_status='CONFIRMED' THEN 1 ELSE 0 END) AS confirmed, "
            "SUM(CASE WHEN completion_status='NOT_COMPLETED' THEN 1 ELSE 0 END) AS rejected "
            "FROM study_meeting_course_completions WHERE study_meeting_course_id=?",
            (course["id"],),
        ).fetchone()
        aggregate = "CONFIRMED" if int(completion_counts["total"] or 0) > 0 and int(completion_counts["confirmed"] or 0) == int(completion_counts["total"] or 0) else (
            "NOT_COMPLETED" if int(completion_counts["rejected"] or 0) > 0 else "UNCONFIRMED"
        )
        execute(
            connection,
            "UPDATE study_meeting_courses SET completion_status=?, completed_at=?, "
            "confirmed_by_member_id=NULL, confirmed_by_user_id=?, completion_note=?, updated_at=? WHERE id=?",
            (aggregate, now if aggregate == "CONFIRMED" else None, actor_user_id,
             (note or "").strip() or None, now, course["id"]),
        )
        after = execute(
            connection,
            "SELECT * FROM study_meeting_course_completions "
            "WHERE study_meeting_course_id=? AND member_id=? LIMIT 1",
            (course["id"], member_id),
        ).fetchone()
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="study_meeting.course_completion_confirm",
            resource_type="study_meeting_course",
            resource_id=str(course["id"]),
            org_unit_id=session["class_org_unit_id"],
            purpose=(note or "").strip() or "确认课程实际完成事实",
            before={key: _serialize(value) for key, value in dict(before).items()},
            after={key: _serialize(value) for key, value in dict(after).items()},
        )
    return get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)


def member_from_session_token(token: str) -> dict[str, Any]:
    try:
        return resolve_member_session(token)
    except WeChatIdentityError as exc:
        raise StudyMeetingPermissionError(str(exc)) from exc
