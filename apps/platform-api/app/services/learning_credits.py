"""G5 learning-credit ledger and read-only settlement preview.

The ledger is append-only from the business point of view.  A correction is a
new reversal/adjustment entry; an existing posted entry is never repriced in
place.  This module deliberately keeps the first release narrow: it computes
group-meeting and course proposals and only posts them when the explicit
settlement flag is enabled.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from app.core.settings import get_settings
from app.db import connect, execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.course_credit_rules import (
    DEFAULT_PLAN_KEY,
    DEFAULT_VERSION_LABEL,
    get_group_meeting_credit_policy,
    list_course_credit_rules,
)
from app.services.iam import accessible_org_ids, user_context


STANDARD_LEARNING = "STANDARD_LEARNING"
EXTENSION_ACTIVITY = "EXTENSION_ACTIVITY"
GROUP_MEETING_ATTENDANCE = "GROUP_MEETING_ATTENDANCE"
COURSE_COMPLETION = "COURSE_COMPLETION"


class LearningCreditError(ValueError):
    """A learning-credit request failed a business validation."""


class LearningCreditFeatureDisabled(LearningCreditError):
    """Formal posting is closed by the explicit deployment feature flag."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_timestamp(connection, value: datetime | None = None) -> str:
    parsed = value or datetime.now(UTC)
    if isinstance(connection, sqlite3.Connection):
        return parsed.astimezone(UTC).isoformat()
    return parsed.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _write_allowed() -> None:
    settings = get_settings()
    if settings.deployment_read_only or (
        settings.is_production and not settings.allow_production_mutations
    ):
        raise LearningCreditError("当前环境不允许写入")


def _settlement_enabled() -> None:
    if not get_settings().learning_credit_settlement_enabled:
        raise LearningCreditFeatureDisabled("学分正式结算尚未开启")
    _write_allowed()


def _scope_allows(actor_user_id: int, class_org_unit_id: str | None) -> bool:
    if not class_org_unit_id:
        return True
    allowed = accessible_org_ids(actor_user_id)
    return allowed is None or class_org_unit_id in allowed


def _require_preview_permission(actor_user_id: int) -> None:
    user = user_context(actor_user_id) or {}
    if "plans:credit_settlement_preview" not in user.get("permissions", []):
        raise PermissionError("无权预览学分结算")


def _rule_version(
    connection, *, rule_set_key: str, version_label: str, rule_key: str | None = None
) -> dict[str, Any] | None:
    version = execute(
        connection,
        "SELECT * FROM learning_credit_rule_versions "
        "WHERE rule_set_key=? AND version_label=? LIMIT 1",
        (rule_set_key, version_label),
    ).fetchone()
    if not version:
        return None
    result = dict(version)
    if rule_key:
        rule = execute(
            connection,
            "SELECT * FROM learning_credit_rules "
            "WHERE rule_version_id=? AND rule_key=? AND status='ACTIVE' LIMIT 1",
            (result["id"], rule_key),
        ).fetchone()
        result["rule"] = dict(rule) if rule else None
    return result


def _session_context(connection, session_id: int) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT s.*, c.name AS class_name, g.name AS group_name, "
        "lc.learning_cycle_index AS cycle_index, lc.group_meeting_policy, "
        "b.id AS binding_id, b.credit_rule_version_id, "
        "p.plan_key, p.version_label, p.status AS plan_status, "
        "v.status AS generic_rule_version_status, v.version_label AS generic_rule_version_label "
        "FROM study_meeting_sessions s "
        "JOIN org_units c ON c.id=s.class_org_unit_id "
        "JOIN org_units g ON g.id=s.study_group_org_unit_id "
        "JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id "
        "JOIN class_learning_bindings b ON b.id=lc.binding_id "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "LEFT JOIN learning_credit_rule_versions v ON v.id=b.credit_rule_version_id "
        "WHERE s.id=? LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def _course_rule_version_status(connection, plan_key: str, version_label: str) -> str:
    row = execute(
        connection,
        "SELECT status FROM learning_plan_credit_rule_versions "
        "WHERE plan_key=? AND version_label=? LIMIT 1",
        (plan_key, version_label),
    ).fetchone()
    return str(row["status"]) if row else "DRAFT"


def _existing_entry(connection, idempotency_key: str) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT * FROM learning_credit_entries WHERE idempotency_key=? LIMIT 1",
        (idempotency_key,),
    ).fetchone()
    return dict(row) if row else None


def _entry_payload(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["id"] = int(result["id"])
    if result.get("member_id") is not None:
        result["member_id"] = int(result["member_id"])
    if result.get("learning_cycle_id") is not None:
        result["learning_cycle_id"] = int(result["learning_cycle_id"])
    return result


def _attendance_proposals(connection, session: dict[str, Any]) -> list[dict[str, Any]]:
    attendees = execute(
        connection,
        "SELECT a.member_id, m.name AS member_name "
        "FROM study_meeting_attendances a JOIN members m ON m.id=a.member_id "
        "WHERE a.study_meeting_session_id=? ORDER BY a.member_id",
        (session["id"],),
    ).fetchall()
    policy = get_group_meeting_credit_policy()
    exact_version = _rule_version(
        connection,
        rule_set_key=str(session["plan_key"]),
        version_label=str(session["version_label"]),
        rule_key=GROUP_MEETING_ATTENDANCE,
    )
    if not exact_version or int(exact_version["id"]) != int(session.get("credit_rule_version_id") or 0):
        exact_version = None
    rule = (exact_version or {}).get("rule") or {}
    points = rule.get("points") if rule.get("points") is not None else policy["credit_points_per_person"]
    rule_status = (exact_version or {}).get("status") if rule else "UNRESOLVED"
    rule_snapshot = {
        "rule_key": GROUP_MEETING_ATTENDANCE,
        "credit_category": STANDARD_LEARNING,
        "settlement_model": "CYCLE_ONCE",
        "points": points,
        "plan_key": session["plan_key"],
        "plan_version": session["version_label"],
        "rule_version_status": rule_status,
        "source": "course-credit-rules-2026.json",
    }
    result: list[dict[str, Any]] = []
    for attendee in attendees:
        member_id = int(attendee["member_id"])
        key = f"GROUP_MEETING:{member_id}:{int(session['learning_cycle_id'])}"
        existing = _existing_entry(connection, key)
        reasons: list[str] = []
        if session["status"] != "SUBMITTED":
            reasons.append("学习会尚未提交")
        if session.get("group_meeting_policy") != "REQUIRED":
            reasons.append(f"当前周期小组会政策为{session.get('group_meeting_policy')}")
        if rule_status != "PUBLISHED":
            reasons.append("小组会规则版本未发布或未绑定")
        if existing:
            reasons.append("该学员在本学习周期已有账本记录")
        result.append({
            "entry_kind": "GROUP_MEETING_ATTENDANCE",
            "member_id": member_id,
            "member_name": attendee["member_name"],
            "points": float(points),
            "credit_category": STANDARD_LEARNING,
            "credit_type": GROUP_MEETING_ATTENDANCE,
            "source_type": "STUDY_MEETING_ATTENDANCE",
            "source_id": str(session["id"]),
            "learning_cycle_id": int(session["learning_cycle_id"]),
            "class_org_unit_id": session["class_org_unit_id"],
            "rule_key": GROUP_MEETING_ATTENDANCE,
            "rule_version": str(session["version_label"]),
            "rule_version_id": exact_version["id"] if exact_version else None,
            "rule_snapshot": rule_snapshot,
            "idempotency_key": key,
            "existing_entry": _entry_payload(existing) if existing else None,
            "eligible": not reasons or reasons == ["该学员在本学习周期已有账本记录"],
            "postable": not reasons,
            "status": "SKIPPED_DUPLICATE" if existing else ("READY" if not reasons else "BLOCKED"),
            "reasons": reasons,
        })
    return result


def _course_rows(connection, session: dict[str, Any]) -> list[dict[str, Any]]:
    rows = execute(
        connection,
        "SELECT c.*, r.member_id, r.completion_status, r.completed_at AS member_completed_at, "
        "r.confirmed_by_user_id, r.confirmation_source, r.note AS completion_note, "
        "m.name AS member_name "
        "FROM study_meeting_courses c "
        "JOIN study_meeting_course_completions r ON r.study_meeting_course_id=c.id "
        "JOIN members m ON m.id=r.member_id "
        "WHERE c.study_meeting_session_id=? ORDER BY c.id, r.member_id",
        (session["id"],),
    ).fetchall()
    return [dict(row) for row in rows]


def _course_proposals(connection, session: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _course_rows(connection, session)
    if not rows:
        # Legacy single-course rows have no authoritative per-member completion
        # fact.  Keep them visible to DRY-RUN, but never manufacture points.
        if session.get("course_key"):
            attendees = execute(
                connection,
                "SELECT a.member_id, m.name AS member_name "
                "FROM study_meeting_attendances a JOIN members m ON m.id=a.member_id "
                "WHERE a.study_meeting_session_id=? ORDER BY a.member_id",
                (session["id"],),
            ).fetchall()
            return [{
                "entry_kind": "COURSE_COMPLETION",
                "member_id": int(item["member_id"]),
                "member_name": item["member_name"],
                "course_key": session["course_key"],
                "course_name": session.get("course_name_snapshot"),
                "points": 0,
                "credit_category": STANDARD_LEARNING,
                "credit_type": COURSE_COMPLETION,
                "idempotency_key": f"COURSE:{int(item['member_id'])}:{session['course_key']}:{int(session['binding_id'])}",
                "eligible": False,
                "postable": False,
                "status": "BLOCKED",
                "reasons": ["旧课程记录缺少逐人课程完成事实"],
            } for item in attendees]
        return []

    result: list[dict[str, Any]] = []
    plan_rule_status = _course_rule_version_status(
        connection, str(session["plan_key"]), str(session["version_label"])
    )
    generic = _rule_version(
        connection,
        rule_set_key=str(session["plan_key"]),
        version_label=str(session["version_label"]),
        rule_key=COURSE_COMPLETION,
    )
    if not generic or int(generic["id"]) != int(session.get("credit_rule_version_id") or 0):
        generic = None
    generic_status = (generic or {}).get("status") if (generic or {}).get("rule") else "UNRESOLVED"
    for row in rows:
        points = row.get("course_credit_snapshot")
        points_value = float(points) if points is not None else 0.0
        reference = _decode(row.get("rule_reference_json"))
        for_key = f"COURSE:{int(row['member_id'])}:{row['course_key']}:{int(session['binding_id'])}"
        existing = _existing_entry(connection, for_key)
        reasons: list[str] = []
        if session["status"] != "SUBMITTED":
            reasons.append("学习会尚未提交")
        if row.get("completion_status") != "CONFIRMED":
            reasons.append("课程实际完成尚未确认")
        if points is None or row.get("course_rule_status") != "CONFIGURED":
            reasons.append("该课程当前没有已确认总部学分")
        if plan_rule_status != "PUBLISHED":
            reasons.append("该学习计划对应的课程积分版本未发布")
        if generic_status != "PUBLISHED":
            reasons.append("课程完成规则版本未发布或未绑定")
        if reference.get("plan_key") and reference.get("plan_key") != session["plan_key"]:
            reasons.append("课程快照与班级学习计划版本不一致")
        if reference.get("version_label") and reference.get("version_label") != session["version_label"]:
            reasons.append("课程快照与班级学习计划版本不一致")
        if existing:
            reasons.append("该学员在本学习轮次已有课程账本记录")
        no_credit = points is not None and float(points) == 0 and row.get("course_rule_status") == "CONFIGURED"
        if no_credit:
            reasons = [reason for reason in reasons if "没有已确认总部学分" not in reason]
        result.append({
            "entry_kind": "COURSE_COMPLETION",
            "member_id": int(row["member_id"]),
            "member_name": row.get("member_name"),
            "course_key": row["course_key"],
            "course_name": row.get("course_name_snapshot"),
            "points": points_value,
            "credit_category": STANDARD_LEARNING,
            "credit_type": COURSE_COMPLETION,
            "source_type": "STUDY_MEETING_COURSE",
            "source_id": str(row["id"]),
            "learning_cycle_id": int(session["learning_cycle_id"]),
            "class_org_unit_id": session["class_org_unit_id"],
            "rule_key": row["course_key"],
            "rule_version": str(row.get("plan_version_label") or session["version_label"]),
            # The ledger FK points to the generic rule version.  The course
            # row's credit_rule_version_id is the plan-specific course-rule
            # version and is retained inside the frozen snapshot separately.
            "rule_version_id": session.get("credit_rule_version_id"),
            "rule_snapshot": {
                "rule_key": row["course_key"],
                "credit_type": COURSE_COMPLETION,
                "settlement_model": "COURSE_COMPLETION",
                "points": points,
                "course_name": row.get("course_name_snapshot"),
                "course_rule_status": row.get("course_rule_status"),
                "course_rule_version_status": plan_rule_status,
                "course_rule_version_id": row.get("credit_rule_version_id"),
                "rule_version_status": generic_status,
                "generic_rule_version_id": session.get("credit_rule_version_id"),
                "plan_key": session["plan_key"],
                "plan_version": session["version_label"],
                "source_reference": reference,
            },
            "idempotency_key": for_key,
            "existing_entry": _entry_payload(existing) if existing else None,
            "eligible": not reasons or reasons == ["该学员在本学习轮次已有课程账本记录"],
            "postable": not reasons and not no_credit,
            "status": (
                "NO_CREDIT" if no_credit and not existing else
                "SKIPPED_DUPLICATE" if existing else
                "READY" if not reasons else "BLOCKED"
            ),
            "reasons": reasons,
        })
    return result


def _build_study_meeting_preview(connection, session_id: int) -> dict[str, Any]:
    session = _session_context(connection, session_id)
    if not session:
        raise LearningCreditError("学习会记录不存在")
    entries = _attendance_proposals(connection, session) + _course_proposals(connection, session)
    proposed = [item for item in entries if item["postable"]]
    blocked_reasons: list[str] = []
    for item in entries:
        if item["status"] == "BLOCKED":
            blocked_reasons.extend(item.get("reasons", []))
    unique_reasons = list(dict.fromkeys(blocked_reasons))
    group_points = sum(item["points"] for item in proposed if item["entry_kind"] == "GROUP_MEETING_ATTENDANCE")
    course_points = sum(item["points"] for item in proposed if item["entry_kind"] == "COURSE_COMPLETION")
    return {
        "mode": "DRY_RUN",
        "persisted": False,
        "settlement_enabled": get_settings().learning_credit_settlement_enabled,
        # A blocked course must not prevent an otherwise valid group
        # attendance entry from being settled. Settlement remains item-level:
        # only proposals marked postable are written.
        "formal_settlement_allowed": bool(proposed),
        "session": {
            "id": int(session["id"]),
            "session_code": session["session_code"],
            "status": session["status"],
            "class_org_unit_id": session["class_org_unit_id"],
            "class_name": session["class_name"],
            "study_group_org_unit_id": session["study_group_org_unit_id"],
            "group_name": session["group_name"],
            "learning_cycle_id": int(session["learning_cycle_id"]),
            "cycle_index": int(session["cycle_index"]),
            "meeting_date": session["meeting_date"],
            "plan_key": session["plan_key"],
            "plan_version": session["version_label"],
        },
        "entries": entries,
        "totals": {
            "group_meeting_points": group_points,
            "course_points": course_points,
            "proposed_points": group_points + course_points,
            "proposed_entry_count": len(proposed),
            "blocked_entry_count": sum(item["status"] == "BLOCKED" for item in entries),
            "duplicate_entry_count": sum(item["status"] == "SKIPPED_DUPLICATE" for item in entries),
            "no_credit_entry_count": sum(item["status"] == "NO_CREDIT" for item in entries),
        },
        "blocking_reasons": unique_reasons,
    }


def dry_run_study_meeting_settlement(*, actor_user_id: int, session_id: int) -> dict[str, Any]:
    _require_preview_permission(actor_user_id)
    connection = connect()
    try:
        session = _session_context(connection, session_id)
        if not session:
            raise LearningCreditError("学习会记录不存在")
        if not _scope_allows(actor_user_id, session["class_org_unit_id"]):
            raise PermissionError("学习会记录不在当前组织授权范围内")
        return _build_study_meeting_preview(connection, session_id)
    finally:
        connection.close()


def dry_run_study_meetings(
    *, actor_user_id: int, class_org_unit_id: str | None = None,
    learning_cycle_id: int | None = None, limit: int = 30,
) -> dict[str, Any]:
    """Build an ephemeral reconciliation batch for submitted meetings.

    The result is intentionally bounded so operations can compare a 20-30
    person sample with the existing manual workbook without creating staging
    rows or ledger entries.
    """

    _require_preview_permission(actor_user_id)
    if limit < 1 or limit > 500:
        raise LearningCreditError("DRY-RUN数量必须在1到500之间")
    connection = connect()
    try:
        conditions = ["s.status='SUBMITTED'"]
        params: list[Any] = []
        if class_org_unit_id:
            conditions.append("s.class_org_unit_id=?")
            params.append(class_org_unit_id)
        if learning_cycle_id is not None:
            conditions.append("s.learning_cycle_id=?")
            params.append(learning_cycle_id)
        rows = execute(
            connection,
            "SELECT s.id, s.class_org_unit_id FROM study_meeting_sessions s "
            "WHERE " + " AND ".join(conditions) + " ORDER BY s.meeting_date, s.id LIMIT ?",
            (*params, limit),
        ).fetchall()
        sessions: list[dict[str, Any]] = []
        for row in rows:
            if not _scope_allows(actor_user_id, row["class_org_unit_id"]):
                continue
            sessions.append(_build_study_meeting_preview(connection, int(row["id"])))
        totals = {
            "session_count": len(sessions),
            "proposed_points": sum(item["totals"]["proposed_points"] for item in sessions),
            "proposed_entry_count": sum(item["totals"]["proposed_entry_count"] for item in sessions),
            "blocked_entry_count": sum(item["totals"]["blocked_entry_count"] for item in sessions),
            "duplicate_entry_count": sum(item["totals"]["duplicate_entry_count"] for item in sessions),
        }
        return {
            "mode": "DRY_RUN",
            "persisted": False,
            "class_org_unit_id": class_org_unit_id,
            "learning_cycle_id": learning_cycle_id,
            "limit": limit,
            "sessions": sessions,
            "totals": totals,
        }
    finally:
        connection.close()


def list_credit_entries(
    *, actor_user_id: int, member_id: int | None = None,
    occurred_from: str | None = None, occurred_to: str | None = None,
    credit_category: str | None = None, source_type: str | None = None,
) -> list[dict[str, Any]]:
    _require_preview_permission(actor_user_id)
    if credit_category and credit_category not in {STANDARD_LEARNING, EXTENSION_ACTIVITY}:
        raise LearningCreditError("学分类别无效")
    conditions = ["1=1"]
    params: list[Any] = []
    if member_id is not None:
        conditions.append("e.member_id=?")
        params.append(member_id)
    if occurred_from:
        conditions.append("e.occurred_at>=?")
        params.append(occurred_from)
    if occurred_to:
        conditions.append("e.occurred_at<=?")
        params.append(occurred_to)
    if credit_category:
        conditions.append("e.credit_category=?")
        params.append(credit_category)
    if source_type:
        conditions.append("e.source_type=?")
        params.append(source_type)
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None:
        if not allowed:
            return []
        placeholders = ",".join("?" for _ in allowed)
        conditions.append(f"e.class_org_unit_id IN ({placeholders})")
        params.extend(sorted(allowed))
    rows = fetch_all(
        "SELECT e.*, m.name AS member_name FROM learning_credit_entries e "
        "JOIN members m ON m.id=e.member_id WHERE " + " AND ".join(conditions) +
        " ORDER BY e.occurred_at DESC, e.id DESC LIMIT 1000",
        tuple(params),
    )
    return [_entry_payload(row) | {"member_name": row.get("member_name")} for row in rows]


def member_credit_summary(*, actor_user_id: int, member_id: int) -> dict[str, Any]:
    _require_preview_permission(actor_user_id)
    allowed = accessible_org_ids(actor_user_id)
    if allowed is None:
        rows = fetch_all(
            "SELECT credit_category, COALESCE(SUM(points), 0) AS points "
            "FROM learning_credit_entries WHERE member_id=? AND status IN ('POSTED', 'REVERSED') "
            "GROUP BY credit_category",
            (member_id,),
        )
    elif allowed:
        placeholders = ",".join("?" for _ in allowed)
        rows = fetch_all(
            "SELECT credit_category, COALESCE(SUM(points), 0) AS points "
            "FROM learning_credit_entries WHERE member_id=? AND status IN ('POSTED', 'REVERSED') "
            f"AND class_org_unit_id IN ({placeholders}) GROUP BY credit_category",
            (member_id, *sorted(allowed)),
        )
    else:
        rows = []
    values = {str(row["credit_category"]): float(row["points"] or 0) for row in rows}
    standard = values.get(STANDARD_LEARNING, 0)
    extension = values.get(EXTENSION_ACTIVITY, 0)
    return {
        "member_id": member_id,
        "standard_learning_points": standard,
        "extension_activity_points": extension,
        "total_points": standard + extension,
    }


def _insert_entry(connection, item: dict[str, Any], *, status: str, actor_user_id: int | None) -> dict[str, Any]:
    now = _db_timestamp(connection)
    posted_at = now if status == "POSTED" else None
    try:
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_entries "
            "(member_id, credit_category, credit_type, points, source_type, source_id, "
            "class_org_unit_id, learning_cycle_id, rule_key, rule_version, rule_version_id, "
            "rule_snapshot_json, occurred_at, posted_at, status, idempotency_key, "
            "reversal_of_entry_id, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(item["member_id"]), item["credit_category"], item["credit_type"], item["points"],
                item["source_type"], str(item["source_id"]), item.get("class_org_unit_id"),
                item.get("learning_cycle_id"), item["rule_key"], str(item["rule_version"]),
                item.get("rule_version_id"), _json(item["rule_snapshot"]), item["occurred_at"],
                posted_at, status, item["idempotency_key"], item.get("reversal_of_entry_id"),
                actor_user_id, now, now,
            ),
        )
        row = execute(connection, "SELECT * FROM learning_credit_entries WHERE id=?", (cursor.lastrowid,)).fetchone()
        return _entry_payload(row)
    except Exception as exc:
        # The unique idempotency key is the concurrency boundary.  A duplicate
        # request returns the original entry; unrelated integrity failures are
        # still raised and roll back the transaction.
        if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
            raise
        existing = _existing_entry(connection, item["idempotency_key"])
        if not existing:
            raise
        return _entry_payload(existing)


def post_credit_entry(*, actor_user_id: int, item: dict[str, Any]) -> dict[str, Any]:
    _settlement_enabled()
    user = user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权正式入账学分")
    if not item.get("rule_version") or not item.get("rule_snapshot"):
        raise LearningCreditError("正式入账必须冻结学分规则快照")
    if not item.get("rule_version_id") or not item.get("rule_key"):
        raise LearningCreditError("正式入账必须绑定学分规则版本和规则键")
    with transaction() as connection:
        rule = execute(
            connection,
            "SELECT v.id AS version_id, v.version_label, v.status AS version_status, "
            "r.rule_key, r.credit_category, r.credit_type, r.status AS rule_status "
            "FROM learning_credit_rule_versions v "
            "JOIN learning_credit_rules r ON r.rule_version_id=v.id "
            "WHERE v.id=? AND r.rule_key=? LIMIT 1",
            (item["rule_version_id"], item["rule_key"]),
        ).fetchone()
        if not rule or rule["version_status"] != "PUBLISHED" or rule["rule_status"] != "ACTIVE":
            raise LearningCreditError("只有数据库中已发布且启用的规则才能正式入账")
        if str(rule["version_label"]) != str(item["rule_version"]):
            raise LearningCreditError("学分规则版本标签与数据库不一致")
        if (
            str(rule["credit_category"]) != str(item["credit_category"])
            or str(rule["credit_type"]) != str(item["credit_type"])
        ):
            raise LearningCreditError("学分规则类别或类型与数据库不一致")
        return _insert_entry(connection, item, status="POSTED", actor_user_id=actor_user_id)


def settle_study_meeting(*, actor_user_id: int, session_id: int) -> dict[str, Any]:
    """Post a previously previewed session only behind the explicit flag."""

    _settlement_enabled()
    user = user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权正式结算学分")
    with transaction() as connection:
        session = _session_context(connection, session_id)
        if not session:
            raise LearningCreditError("学习会记录不存在")
        if not _scope_allows(actor_user_id, session["class_org_unit_id"]):
            raise PermissionError("学习会记录不在当前组织授权范围内")
        preview = _build_study_meeting_preview(connection, session_id)
        if not preview["formal_settlement_allowed"]:
            existing = [
                item["existing_entry"]
                for item in preview["entries"]
                if item["status"] == "SKIPPED_DUPLICATE" and item.get("existing_entry")
            ]
            if existing and not any(item["status"] == "READY" for item in preview["entries"]):
                return {
                    "mode": "POSTED",
                    "persisted": True,
                    "idempotent": True,
                    "entries": existing,
                    "total_points": sum(float(item["points"]) for item in existing),
                    "blocking_reasons": preview["blocking_reasons"],
                }
            raise LearningCreditError(
                "；".join(preview["blocking_reasons"])
                or "当前学习会没有可正式入账的学分"
            )
        posted: list[dict[str, Any]] = []
        occurred_at = session["meeting_date"]
        for item in preview["entries"]:
            if not item["postable"]:
                continue
            item = {**item, "occurred_at": occurred_at}
            posted.append(_insert_entry(connection, item, status="POSTED", actor_user_id=actor_user_id))
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning_credit.settle_study_meeting",
            resource_type="study_meeting_session",
            resource_id=str(session_id),
            org_unit_id=session["class_org_unit_id"],
            purpose="正式结算学习会学分",
            after={"posted_entry_ids": [item["id"] for item in posted]},
        )
        return {"mode": "POSTED", "persisted": True, "entries": posted, "total_points": sum(item["points"] for item in posted)}


def reverse_credit_entry(*, actor_user_id: int, entry_id: int, reason: str) -> dict[str, Any]:
    _settlement_enabled()
    user = user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权冲销学分")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise LearningCreditError("冲销必须填写原因，且不超过1000字")
    with transaction() as connection:
        original = execute(connection, "SELECT * FROM learning_credit_entries WHERE id=?", (entry_id,)).fetchone()
        if not original:
            raise LearningCreditError("学分账本记录不存在")
        original = dict(original)
        if not original.get("class_org_unit_id"):
            raise PermissionError("无组织范围的学分记录不能由当前权限冲销")
        if not _scope_allows(actor_user_id, original["class_org_unit_id"]):
            raise PermissionError("学分记录不在当前组织授权范围内")
        existing = _existing_entry(connection, f"REVERSAL:{entry_id}")
        if existing:
            return _entry_payload(existing)
        if original["status"] != "POSTED":
            raise LearningCreditError("只有已正式入账记录可以冲销")
        reversal_key = f"REVERSAL:{entry_id}"
        item = {
            "member_id": original["member_id"],
            "credit_category": original["credit_category"],
            "credit_type": original["credit_type"],
            "points": -float(original["points"]),
            "source_type": "REVERSAL",
            "source_id": str(entry_id),
            "class_org_unit_id": original["class_org_unit_id"],
            "learning_cycle_id": original["learning_cycle_id"],
            "rule_key": original["rule_key"],
            "rule_version": original["rule_version"],
            "rule_version_id": original["rule_version_id"],
            "rule_snapshot": {**_decode(original["rule_snapshot_json"]), "reversal_reason": reason.strip()},
            "occurred_at": original["occurred_at"],
            "idempotency_key": reversal_key,
            "reversal_of_entry_id": entry_id,
        }
        reversal = _insert_entry(connection, item, status="POSTED", actor_user_id=actor_user_id)
        execute(connection, "UPDATE learning_credit_entries SET status='REVERSED', updated_at=? WHERE id=?", (_db_timestamp(connection), entry_id))
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning_credit.entry.reverse",
            resource_type="learning_credit_entry",
            resource_id=str(entry_id),
            org_unit_id=original["class_org_unit_id"],
            purpose=reason.strip(),
            before={"status": original["status"], "points": original["points"]},
            after={"status": "REVERSED", "reversal_entry_id": reversal["id"], "points": reversal["points"]},
        )
        return reversal
