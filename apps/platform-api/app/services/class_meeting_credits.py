"""Read-only C6 projection from attendance scores to learning credits.

The attendance subsystem remains the source of truth for a class meeting's
score.  This module only assembles an ephemeral proposal from
``attendance_score_records``; it never recalculates attendance, changes an
attendance fact, or inserts a ledger entry.  A future posting flow can pass a
proposal through the append-only ledger service, where the existing
idempotency and reversal boundaries apply.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from app.core.settings import get_settings
from app.db import connect, execute
from app.services.iam import accessible_org_ids
from app.services.learning_credits import (
    LearningCreditError,
    STANDARD_LEARNING,
    _entry_payload,
    _existing_entry,
    _require_preview_permission,
)
from app.services.attendance_scoring import normalize_activity_type


CLASS_MEETING_SCORE = "CLASS_MEETING_SCORE"
CLASS_MEETING_SOURCE_TYPE = "ATTENDANCE_CLASS_MEETING"
_INVALID_STATUSES = {"CANCELLED", "CANCELED", "VOID", "INVALIDATED"}
_VALID_ATTENDANCE_STATUSES = {
    "PRESENT",
    "ABSENT",
    "LEAVE",
    "MANUAL_PRESENT",
    "INVALIDATED",
}
_SESSION_CODES = {"MORNING", "AFTERNOON", "KONPA"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _timestamp_is_after(candidate: Any, baseline: Any) -> bool:
    candidate_dt = _parse_timestamp(candidate)
    baseline_dt = _parse_timestamp(baseline)
    return bool(candidate_dt and baseline_dt and candidate_dt > baseline_dt)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _scope_allows_group(actor_user_id: int, meeting: dict[str, Any]) -> bool:
    allowed = accessible_org_ids(actor_user_id)
    if allowed is None:
        return True
    return bool(
        {
            meeting.get("org_unit_id"),
            meeting.get("study_org_unit_id"),
        }.intersection(allowed)
    )


def _meeting_context(connection, event_group_id: int) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT eg.id, eg.source_key, eg.external_group_id, eg.org_unit_id, "
        "eg.study_org_unit_id, eg.title, eg.activity_type, eg.event_date, "
        "eg.status, eg.source_updated_at, eg.created_at, eg.updated_at, "
        "o.name AS org_name, class_org.name AS class_name "
        "FROM attendance_event_groups eg "
        "JOIN org_units o ON o.id=eg.org_unit_id "
        "LEFT JOIN org_units class_org ON class_org.id=eg.study_org_unit_id "
        "WHERE eg.id=? LIMIT 1",
        (event_group_id,),
    ).fetchone()
    if not row:
        return None

    meeting = dict(row)
    cycle = execute(
        connection,
        "SELECT id, binding_id, learning_cycle_index "
        "FROM class_learning_cycles WHERE source_event_group_id=? "
        "ORDER BY id DESC LIMIT 1",
        (event_group_id,),
    ).fetchone()
    if cycle:
        meeting["learning_cycle_id"] = int(cycle["id"])
        meeting["cycle_index"] = int(cycle["learning_cycle_index"])
        binding = execute(
            connection,
            "SELECT b.id AS binding_id, b.class_org_unit_id, b.plan_version_id, "
            "b.credit_rule_version_id, b.started_at, b.status AS binding_status, "
            "p.plan_key, p.version_label, p.status AS plan_status "
            "FROM class_learning_bindings b "
            "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
            "WHERE b.id=? LIMIT 1",
            (cycle["binding_id"],),
        ).fetchone()
    else:
        meeting["learning_cycle_id"] = None
        meeting["cycle_index"] = None
        binding = None
        if meeting.get("study_org_unit_id"):
            binding = execute(
                connection,
                "SELECT b.id AS binding_id, b.class_org_unit_id, b.plan_version_id, "
                "b.credit_rule_version_id, b.started_at, b.status AS binding_status, "
                "p.plan_key, p.version_label, p.status AS plan_status "
                "FROM class_learning_bindings b "
                "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
                "WHERE b.class_org_unit_id=? AND substr(b.started_at, 1, 10)<=? "
                "ORDER BY b.started_at DESC, b.id DESC LIMIT 1",
                (meeting["study_org_unit_id"], meeting["event_date"]),
            ).fetchone()

    if binding:
        meeting.update(dict(binding))
    else:
        meeting.update(
            {
                "binding_id": None,
                "class_org_unit_id": meeting.get("study_org_unit_id"),
                "plan_version_id": None,
                "credit_rule_version_id": None,
                "started_at": None,
                "binding_status": None,
                "plan_key": None,
                "version_label": None,
                "plan_status": None,
            }
        )

    rule = None
    if meeting.get("credit_rule_version_id"):
        rule = execute(
            connection,
            "SELECT v.id AS rule_version_id, v.rule_set_key, "
            "v.version_label AS rule_version, v.status AS rule_version_status, "
            "r.id AS rule_id, r.rule_key, r.credit_category, r.credit_type, "
            "r.settlement_model, r.points, r.cap_points, r.rule_snapshot_json, "
            "r.status AS rule_status "
            "FROM learning_credit_rule_versions v "
            "JOIN learning_credit_rules r ON r.rule_version_id=v.id "
            "WHERE v.id=? AND r.rule_key=? LIMIT 1",
            (meeting["credit_rule_version_id"], CLASS_MEETING_SCORE),
        ).fetchone()
    meeting["credit_rule"] = dict(rule) if rule else None

    sessions = execute(
        connection,
        "SELECT id, event_group_id, external_session_id, session_code, "
        "session_name, session_order, scheduled_start_at, status, "
        "source_revision, source_updated_at, finalized_at "
        "FROM attendance_sessions WHERE event_group_id=? "
        "ORDER BY session_order, id",
        (event_group_id,),
    ).fetchall()
    meeting["sessions"] = [dict(item) for item in sessions]
    return meeting


def _attendance_rows(connection, event_group_id: int) -> list[dict[str, Any]]:
    rows = execute(
        connection,
        "SELECT ar.id AS attendance_record_id, ar.attendance_session_id, "
        "ar.member_id AS attendance_member_id, ar.member_code_snapshot, "
        "ar.name_snapshot, ar.participant_type, ar.score_eligible, "
        "ar.attendance_status, ar.updated_at AS attendance_updated_at, "
        "s.session_code, s.session_name, s.session_order, "
        "sr.id AS score_record_id, sr.member_id AS score_member_id, "
        "sr.rule_id AS score_rule_id, sr.rule_version AS score_rule_version, "
        "sr.base_points, sr.late_deduction, sr.early_leave_deduction, "
        "sr.other_adjustment, sr.final_points, sr.is_late, "
        "sr.is_early_leave, sr.calculation_detail_json, "
        "sr.source_updated_at AS score_source_updated_at, "
        "sr.calculated_at AS score_calculated_at, "
        "sr.updated_at AS score_updated_at, m.name AS member_name, "
        "(SELECT MAX(COALESCE(aa.superseded_at, aa.created_at)) "
        " FROM attendance_adjudications aa "
        " WHERE aa.attendance_record_id=ar.id) AS latest_adjudication_at "
        "FROM attendance_records ar "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "LEFT JOIN attendance_score_records sr ON sr.attendance_record_id=ar.id "
        "LEFT JOIN members m ON m.id=ar.member_id "
        "WHERE s.event_group_id=? "
        "ORDER BY s.session_order, s.id, ar.member_id, ar.id",
        (event_group_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _adjudications_by_record(connection, record_ids: list[int]) -> dict[int, list[str]]:
    if not record_ids:
        return {}
    placeholders = ",".join("?" for _ in record_ids)
    rows = execute(
        connection,
        "SELECT attendance_record_id, adjudication_type "
        "FROM attendance_adjudications WHERE attendance_record_id IN ("
        + placeholders
        + ") AND superseded_at IS NULL ORDER BY id",
        tuple(record_ids),
    ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        record_id = int(row["attendance_record_id"])
        result.setdefault(record_id, []).append(str(row["adjudication_type"]))
    return result


def _member_has_class_relation(
    connection, member_id: int, class_org_unit_id: str, event_date: str
) -> bool:
    row = execute(
        connection,
        "SELECT 1 FROM member_org_relations "
        "WHERE member_id=? AND org_unit_id=? AND relation_type='STUDY_CLASS' "
        "AND (valid_from IS NULL OR valid_from<=?) "
        "AND (valid_until IS NULL OR valid_until>=?) LIMIT 1",
        (member_id, class_org_unit_id, event_date, event_date),
    ).fetchone()
    return bool(row)


def _common_meeting_reasons(meeting: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if normalize_activity_type(meeting.get("activity_type")) != "CLASS_MEETING":
        reasons.append("该活动不是班级学习会")
    if not meeting.get("study_org_unit_id"):
        reasons.append("班会缺少班级组织范围")
    if str(meeting.get("status") or "").upper() in _INVALID_STATUSES:
        reasons.append("班会活动已取消或作废")
    if not meeting.get("sessions"):
        reasons.append("班会缺少签到场次")
    session_codes = [
        str(item.get("session_code") or "").upper()
        for item in meeting.get("sessions", [])
    ]
    if len(session_codes) != len(set(session_codes)):
        reasons.append("班会存在重复场次编码，无法确定上午/下午/空巴对应关系")
    if any(code not in _SESSION_CODES for code in session_codes):
        reasons.append("班会存在不支持的签到场次编码")
    if any(
        str(item.get("status") or "").upper() in _INVALID_STATUSES
        for item in meeting.get("sessions", [])
    ):
        reasons.append("班会存在已取消或作废的签到场次")
    if not meeting.get("binding_id"):
        reasons.append("班级尚未绑定学习计划")
    elif str(meeting.get("plan_status") or "") != "PUBLISHED":
        reasons.append("班级学习计划版本未发布")
    rule = meeting.get("credit_rule") or {}
    if not rule:
        reasons.append("班级学习会学分规则未绑定")
    else:
        if rule.get("rule_version_status") != "PUBLISHED":
            reasons.append("班级学习会学分规则版本未发布")
        if rule.get("rule_status") != "ACTIVE":
            reasons.append("班级学习会学分规则未启用")
        if rule.get("credit_category") != STANDARD_LEARNING:
            reasons.append("班级学习会规则学分类别不一致")
        if rule.get("credit_type") != CLASS_MEETING_SCORE:
            reasons.append("班级学习会规则学分类型不一致")
        if rule.get("settlement_model") != "EVENT_ONCE":
            reasons.append("班级学习会规则结算模型不一致")
        if rule.get("rule_set_key") != meeting.get("plan_key"):
            reasons.append("班级学习会规则集与绑定学习计划不一致")
        if rule.get("rule_version") != meeting.get("version_label"):
            reasons.append("班级学习会规则版本与绑定学习计划不一致")
    return _unique(reasons)


def _score_detail(row: dict[str, Any], adjudication_types: list[str]) -> dict[str, Any]:
    detail = _decode_json(row.get("calculation_detail_json"))
    return {
        "attendance_record_id": int(row["attendance_record_id"]),
        "score_record_id": int(row["score_record_id"]),
        "session_id": int(row["attendance_session_id"]),
        "session_code": str(row["session_code"]).upper(),
        "session_name": row.get("session_name"),
        "attendance_status": row.get("attendance_status"),
        "score_eligible": bool(row.get("score_eligible")),
        "base_points": _as_float(row.get("base_points")),
        "late_deduction": _as_float(row.get("late_deduction")),
        "early_leave_deduction": _as_float(row.get("early_leave_deduction")),
        "other_adjustment": _as_float(row.get("other_adjustment")),
        "final_points": _as_float(row.get("final_points")),
        "is_late": bool(row.get("is_late")),
        "is_early_leave": bool(row.get("is_early_leave")),
        "attendance_rule_id": _as_int(row.get("score_rule_id")),
        "attendance_rule_version": _as_int(row.get("score_rule_version")),
        "calculation_detail": detail,
        "adjudications": list(adjudication_types),
    }


def _member_proposal(
    connection,
    meeting: dict[str, Any],
    rows: list[dict[str, Any]],
    adjudications: dict[int, list[str]],
    common_reasons: list[str],
) -> dict[str, Any]:
    first = rows[0]
    member_id = int(first["attendance_member_id"])
    member_name = first.get("member_name") or first.get("name_snapshot") or ""
    reasons = list(common_reasons)
    by_session: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_session.setdefault(int(row["attendance_session_id"]), []).append(row)

    expected_sessions = meeting.get("sessions", [])
    score_details: list[dict[str, Any]] = []
    score_by_code: dict[str, float] = {}
    for session in expected_sessions:
        session_id = int(session["id"])
        session_rows = by_session.get(session_id, [])
        if not session_rows:
            reasons.append(
                f"缺少{session.get('session_name') or session.get('session_code')}签到/最终评分事实，不能将缺失当作0分"
            )
            continue
        if len(session_rows) != 1:
            reasons.append(
                f"同一学员在{session.get('session_name') or session.get('session_code')}存在多个签到事实，无法确定最终评分"
            )
            continue
        row = session_rows[0]
        if row.get("score_record_id") is None:
            reasons.append(
                f"{session.get('session_name') or session.get('session_code')}缺少最终评分记录"
            )
            continue
        if _as_int(row.get("score_member_id")) != member_id:
            reasons.append("签到事实与最终评分记录的学员身份不一致")
        if row.get("score_rule_id") is None:
            reasons.append("最终评分记录缺少签到评分规则快照")
        attendance_status = str(row.get("attendance_status") or "").upper()
        if attendance_status not in _VALID_ATTENDANCE_STATUSES:
            reasons.append(f"签到事实状态无法结算：{attendance_status or 'EMPTY'}")
        if _as_float(row.get("final_points"), -1) < 0:
            reasons.append("最终评分不能为负数")
        if _timestamp_is_after(row.get("attendance_updated_at"), row.get("score_calculated_at")):
            reasons.append("签到事实更新晚于最终评分，请先重新计算")
        if _timestamp_is_after(
            row.get("latest_adjudication_at"), row.get("score_calculated_at")
        ):
            reasons.append("裁定更新晚于最终评分，请先重新计算")
        detail = _score_detail(row, adjudications.get(int(row["attendance_record_id"]), []))
        score_details.append(detail)
        code = detail["session_code"]
        score_by_code[code] = score_by_code.get(code, 0.0) + detail["final_points"]

    expected_session_ids = {int(session["id"]) for session in expected_sessions}
    if set(by_session).difference(expected_session_ids):
        reasons.append("班会存在不属于当前场次清单的签到事实")

    class_org_unit_id = meeting.get("study_org_unit_id")
    if class_org_unit_id and not _member_has_class_relation(
        connection, member_id, str(class_org_unit_id), str(meeting["event_date"])
    ):
        reasons.append("学员在班会日期没有有效的班级组织关系")

    if len(score_details) != len(expected_sessions):
        # The per-session reasons above explain the concrete missing facts;
        # this summary makes the failure state explicit to callers.
        reasons.append("班会最终评分事实不完整")

    final_points = sum(item["final_points"] for item in score_details)
    total_base = sum(item["base_points"] for item in score_details)
    total_late = sum(item["late_deduction"] for item in score_details)
    total_early = sum(item["early_leave_deduction"] for item in score_details)
    total_other = sum(item["other_adjustment"] for item in score_details)
    total_deductions = total_late + total_early
    existing_key = f"CLASS_MEETING:{member_id}:{int(meeting['id'])}"
    existing = _existing_entry(connection, existing_key)
    if existing:
        reasons.append("该学员在此班会已有账本记录")

    reasons = _unique(reasons)
    structurally_ready = not [
        reason for reason in reasons
        if reason != "该学员在此班会已有账本记录"
    ]
    no_credit = structurally_ready and not existing and final_points == 0
    if existing:
        status = "SKIPPED_DUPLICATE"
    elif no_credit:
        status = "NO_CREDIT"
    elif structurally_ready:
        status = "READY"
    else:
        status = "BLOCKED"

    rule = meeting.get("credit_rule") or {}
    rule_snapshot = {
        "rule_key": CLASS_MEETING_SCORE,
        "credit_category": STANDARD_LEARNING,
        "settlement_model": "EVENT_ONCE",
        "points": "FROM_ATTENDANCE_SCORE",
        "source": "attendance_score_records",
        "rule_id": _as_int(rule.get("rule_id")),
        "rule_set_key": meeting.get("plan_key"),
        "rule_version": rule.get("rule_version") or meeting.get("version_label"),
        "rule_version_status": rule.get("rule_version_status"),
        "rule_status": rule.get("rule_status"),
        "rule_definition": _decode_json(rule.get("rule_snapshot_json")),
        "rule_settlement_model": rule.get("settlement_model"),
        "binding_id": _as_int(meeting.get("binding_id")),
        "learning_cycle_id": _as_int(meeting.get("learning_cycle_id")),
        "score_records": score_details,
    }
    entry = {
        "entry_kind": CLASS_MEETING_SCORE,
        "member_id": member_id,
        "member_name": member_name,
        "meeting_id": int(meeting["id"]),
        "meeting_title": meeting.get("title"),
        "meeting_date": meeting.get("event_date"),
        "class_org_unit_id": class_org_unit_id,
        "class_name": meeting.get("class_name"),
        "org_unit_id": meeting.get("org_unit_id"),
        "morning_points": score_by_code.get("MORNING", 0.0),
        "afternoon_points": score_by_code.get("AFTERNOON", 0.0),
        "konpa_points": score_by_code.get("KONPA", 0.0),
        "other_session_points": sum(
            points for code, points in score_by_code.items() if code not in _SESSION_CODES
        ),
        "base_points": total_base,
        "late_deduction": total_late,
        "early_leave_deduction": total_early,
        "other_adjustment": total_other,
        "total_deductions": total_deductions,
        "adjudications": _unique(
            adjudication
            for detail in score_details
            for adjudication in detail["adjudications"]
        ),
        "score_details": score_details,
        "final_points": final_points,
        "points": final_points,
        "proposed_points": final_points,
        "credit_category": STANDARD_LEARNING,
        "credit_type": CLASS_MEETING_SCORE,
        "source_type": CLASS_MEETING_SOURCE_TYPE,
        "source_id": str(meeting["id"]),
        "learning_cycle_id": _as_int(meeting.get("learning_cycle_id")),
        "rule_key": CLASS_MEETING_SCORE,
        "rule_version": str(
            rule.get("rule_version") or meeting.get("version_label") or ""
        ),
        "rule_version_id": _as_int(rule.get("rule_version_id")),
        "rule_snapshot": rule_snapshot,
        "occurred_at": meeting.get("event_date"),
        "idempotency_key": existing_key,
        "existing_entry": _entry_payload(existing) if existing else None,
        "eligible": status in {"READY", "SKIPPED_DUPLICATE", "NO_CREDIT"},
        "postable": status == "READY",
        "status": status,
        "reasons": reasons,
        "blocking_reason": "；".join(
            reason for reason in reasons
            if reason != "该学员在此班会已有账本记录"
        )
        or None,
        "no_credit_reason": "最终班会得分为0，不创建0分账本记录" if no_credit else None,
        "source_record_count": len(rows),
        "score_record_count": len(score_details),
        "expected_session_count": len(expected_sessions),
        "recorded_session_codes": [detail["session_code"] for detail in score_details],
    }
    return entry


def _ledger_count(connection) -> int:
    row = execute(connection, "SELECT COUNT(*) AS count FROM learning_credit_entries").fetchone()
    return int(row["count"] or 0)


def _build_preview(connection, event_group_id: int) -> dict[str, Any]:
    meeting = _meeting_context(connection, event_group_id)
    if not meeting:
        raise LearningCreditError("班会活动不存在")
    common_reasons = _common_meeting_reasons(meeting)
    rows = _attendance_rows(connection, event_group_id)
    record_ids = [
        int(row["attendance_record_id"])
        for row in rows
        if row.get("attendance_record_id") is not None
    ]
    adjudications = _adjudications_by_record(connection, record_ids)
    by_member: dict[int, list[dict[str, Any]]] = {}
    excluded_record_count = 0
    for row in rows:
        member_id = _as_int(row.get("attendance_member_id"))
        participant_type = str(row.get("participant_type") or "").upper()
        if member_id is None or participant_type != "MEMBER" or not bool(row.get("score_eligible")):
            excluded_record_count += 1
            continue
        by_member.setdefault(member_id, []).append(row)

    entries = [
        _member_proposal(connection, meeting, member_rows, adjudications, common_reasons)
        for member_rows in by_member.values()
    ]
    entries.sort(key=lambda item: (item["member_name"], item["member_id"]))
    proposed = [item for item in entries if item["postable"]]
    blocking_reasons = _unique(
        reason
        for item in entries
        if item["status"] == "BLOCKED"
        for reason in item.get("reasons", [])
    )
    if not entries:
        blocking_reasons.append("没有匹配且可计分的学员评分事实")
    blocking_reasons = _unique(blocking_reasons + common_reasons)
    meeting_payload = {
        key: meeting.get(key)
        for key in (
            "id",
            "source_key",
            "external_group_id",
            "org_unit_id",
            "org_name",
            "study_org_unit_id",
            "class_name",
            "title",
            "activity_type",
            "event_date",
            "status",
            "binding_id",
            "binding_status",
            "plan_key",
            "version_label",
            "plan_status",
            "credit_rule_version_id",
            "learning_cycle_id",
            "cycle_index",
        )
    }
    meeting_payload["sessions"] = [
        {
            "id": int(session["id"]),
            "session_code": str(session.get("session_code") or "").upper(),
            "session_name": session.get("session_name"),
            "session_order": session.get("session_order"),
            "status": session.get("status"),
            "scheduled_start_at": session.get("scheduled_start_at"),
            "finalized_at": session.get("finalized_at"),
        }
        for session in meeting.get("sessions", [])
    ]
    return {
        "mode": "DRY_RUN",
        "persisted": False,
        "settlement_enabled": get_settings().learning_credit_settlement_enabled,
        # C6 deliberately exposes projection only.  A future class-meeting
        # POST flow must be added and reviewed separately before this changes.
        "formal_settlement_allowed": False,
        "meeting": meeting_payload,
        "entries": entries,
        "excluded_record_count": excluded_record_count,
        "totals": {
            "member_count": len(entries),
            "proposed_points": sum(item["proposed_points"] for item in proposed),
            "proposed_entry_count": len(proposed),
            "blocked_entry_count": sum(item["status"] == "BLOCKED" for item in entries),
            "duplicate_entry_count": sum(
                item["status"] == "SKIPPED_DUPLICATE" for item in entries
            ),
            "no_credit_entry_count": sum(
                item["status"] == "NO_CREDIT" for item in entries
            ),
        },
        "blocking_reasons": blocking_reasons,
    }


def _write_proof(connection, before: int) -> dict[str, Any]:
    after = _ledger_count(connection)
    return {
        "writes_performed": False,
        "ledger_entries_before": before,
        "ledger_entries_after": after,
        "ledger_entries_delta": after - before,
    }


def dry_run_class_meeting_settlement(
    *, actor_user_id: int, event_group_id: int
) -> dict[str, Any]:
    """Build one class-meeting proposal without changing any database row."""

    _require_preview_permission(actor_user_id)
    connection = connect()
    try:
        meeting = _meeting_context(connection, event_group_id)
        if not meeting:
            raise LearningCreditError("班会活动不存在")
        if not _scope_allows_group(actor_user_id, meeting):
            raise PermissionError("班会活动不在当前组织授权范围内")
        before = _ledger_count(connection)
        preview = _build_preview(connection, event_group_id)
        preview["write_proof"] = _write_proof(connection, before)
        return preview
    finally:
        connection.close()


def _validate_date(value: str | None, label: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError as exc:
        raise LearningCreditError(f"{label}必须是YYYY-MM-DD") from exc
    return parsed.isoformat()


def dry_run_class_meetings(
    *,
    actor_user_id: int,
    class_org_unit_id: str | None = None,
    event_date_from: str | None = None,
    event_date_to: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Build a bounded class/date-range reconciliation batch without writes."""

    _require_preview_permission(actor_user_id)
    if limit < 1 or limit > 500:
        raise LearningCreditError("DRY-RUN数量必须在1到500之间")
    event_date_from = _validate_date(event_date_from, "开始日期")
    event_date_to = _validate_date(event_date_to, "结束日期")
    if event_date_from and event_date_to and event_date_from > event_date_to:
        raise LearningCreditError("开始日期不能晚于结束日期")

    connection = connect()
    try:
        before = _ledger_count(connection)
        conditions = ["UPPER(COALESCE(eg.activity_type, ''))='CLASS_MEETING'"]
        params: list[Any] = []
        if class_org_unit_id:
            conditions.append("eg.study_org_unit_id=?")
            params.append(class_org_unit_id)
        if event_date_from:
            conditions.append("eg.event_date>=?")
            params.append(event_date_from)
        if event_date_to:
            conditions.append("eg.event_date<=?")
            params.append(event_date_to)
        rows = execute(
            connection,
            "SELECT eg.id, eg.org_unit_id, eg.study_org_unit_id "
            "FROM attendance_event_groups eg WHERE "
            + " AND ".join(conditions)
            + " ORDER BY eg.event_date, eg.id LIMIT ?",
            (*params, limit),
        ).fetchall()
        meetings: list[dict[str, Any]] = []
        for row in rows:
            meeting = _meeting_context(connection, int(row["id"]))
            if meeting is None or not _scope_allows_group(actor_user_id, meeting):
                continue
            meetings.append(_build_preview(connection, int(row["id"])))
        totals = {
            "meeting_count": len(meetings),
            "proposed_points": sum(
                item["totals"]["proposed_points"] for item in meetings
            ),
            "proposed_entry_count": sum(
                item["totals"]["proposed_entry_count"] for item in meetings
            ),
            "blocked_entry_count": sum(
                item["totals"]["blocked_entry_count"] for item in meetings
            ),
            "duplicate_entry_count": sum(
                item["totals"]["duplicate_entry_count"] for item in meetings
            ),
            "no_credit_entry_count": sum(
                item["totals"]["no_credit_entry_count"] for item in meetings
            ),
        }
        blocking_reasons = _unique(
            reason
            for item in meetings
            for reason in item.get("blocking_reasons", [])
        )
        return {
            "mode": "DRY_RUN",
            "persisted": False,
            "settlement_enabled": get_settings().learning_credit_settlement_enabled,
            "formal_settlement_allowed": False,
            "class_org_unit_id": class_org_unit_id,
            "event_date_from": event_date_from,
            "event_date_to": event_date_to,
            "limit": limit,
            "meetings": meetings,
            "totals": totals,
            "blocking_reasons": blocking_reasons,
            "write_proof": _write_proof(connection, before),
        }
    finally:
        connection.close()
