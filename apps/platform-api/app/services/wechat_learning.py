"""Privacy-safe learning facts for the bound member's mini-program view.

This module deliberately aggregates learning facts only.  It does not read
``attendance_score_records.final_points`` as credits, advance learning cycles,
or write a credit ledger.  The formal credit model is a separate V1.1B phase.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from app.db import connect, execute
from app.services.learning_cycles import _active_binding, _cycle_at


RECENT_LEARNING_LIMIT = 20

_PUBLIC_LEARNING_RECORD_FIELDS = (
    "occurred_at",
    "learning_type",
    "title",
    "class_name",
    "group_name",
    "source_type",
    "status_name",
)

_LEARNING_TYPE_NAMES = {
    "CLASS_MEETING": "班级学习会",
    "CLASS_SESSION": "班级学习会",
    "GROUP_MEETING": "小组学习会",
    "GROUP_SESSION": "小组学习会",
    "COURSE": "课程学习",
    "STUDY_COURSE": "课程学习",
    "SEMINAR": "专题学习",
    "NATIONAL_REPORT": "全国报告会",
    "CENTER_QUARTERLY_REPORT": "分中心报告会",
    "REPORT_MEETING": "报告会",
    "REPORT_MEETINGS": "报告会",
    "STUDY_TOUR": "游学",
    "READING_CHECKIN": "读书打卡",
    "READING_CHECKINS": "读书打卡",
    "READING_SHARE": "读书分享",
    "READING_SHARES": "读书分享",
    "STAFF_TRAINING": "培训学习",
    "BOARD_MEETING": "理事会学习",
}

_CYCLE_STATUS_NAMES = {
    "UPCOMING": "待开始",
    "OPEN": "进行中",
    "CLOSED": "已结束",
}

_PARTICIPATION_STATUS_NAMES = {
    "PRESENT": "已参加",
    "COMPLETED": "已完成",
    "RECORDED": "已记录",
}


def _now_for_database(connection) -> str:
    now = datetime.now(UTC)
    if isinstance(connection, sqlite3.Connection):
        return now.isoformat()
    return now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _sort_datetime(value: Any) -> datetime:
    text = _as_text(value)
    if not text:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _learning_type_name(activity_type: Any) -> str:
    key = str(activity_type or "").strip().upper()
    return _LEARNING_TYPE_NAMES.get(key, "学习活动")


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _optional_rows(connection, statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Read an optional fact table without hiding real SQL/data errors.

    V1.1A can be deployed alongside an older read-only database snapshot where
    one of the already-planned fact tables has not been created yet.  Missing
    tables mean "no facts from this source"; all other failures must surface.
    """

    try:
        return [dict(row) for row in execute(connection, statement, params).fetchall()]
    except Exception as exc:  # pragma: no cover - MySQL and SQLite messages differ
        message = str(exc).lower()
        missing_table = (
            "no such table" in message
            or "doesn't exist" in message
            or "does not exist" in message
        )
        if missing_table:
            return []
        raise


def _current_relations(connection, member_id: int) -> list[dict[str, Any]]:
    now = _now_for_database(connection)
    rows = execute(
        connection,
        "SELECT r.id, r.relation_type, r.org_unit_id, r.is_primary, "
        "ou.name, ou.unit_type, ou.parent_id "
        "FROM member_org_relations r JOIN org_units ou ON ou.id=r.org_unit_id "
        "WHERE r.member_id=? AND r.relation_type IN ('STUDY_CLASS','STUDY_GROUP') "
        "AND ou.is_active=1 "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "ORDER BY r.relation_type, r.is_primary DESC, r.id DESC",
        (member_id, now, now),
    ).fetchall()
    return [dict(row) for row in rows]


def _current_learning(connection, member_id: int) -> list[dict[str, Any]]:
    relations = _current_relations(connection, member_id)
    classes: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    for relation in relations:
        if relation["relation_type"] == "STUDY_CLASS":
            classes.setdefault(str(relation["org_unit_id"]), relation)
        else:
            groups.append(relation)

    # A group relation is authoritative for the current group.  Its parent
    # class is used only to pair that group with the current class label; no
    # member or organization ID is returned to the client.
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    used_classes: set[str] = set()
    for group in groups:
        class_relation = classes.get(str(group.get("parent_id")))
        if class_relation:
            used_classes.add(str(class_relation["org_unit_id"]))
        elif group.get("parent_id"):
            parent = execute(
                connection,
                "SELECT id, name, unit_type, parent_id FROM org_units "
                "WHERE id=? AND unit_type='CLASS' AND is_active=1 LIMIT 1",
                (group["parent_id"],),
            ).fetchone()
            class_relation = (
                {
                    "org_unit_id": parent["id"],
                    "name": parent["name"],
                    "parent_id": parent["parent_id"],
                }
                if parent
                else None
            )
        pairs.append((class_relation, group))

    for class_id, class_relation in classes.items():
        if class_id not in used_classes:
            pairs.append((class_relation, None))

    result: list[dict[str, Any]] = []
    for class_relation, group_relation in pairs:
        if not class_relation and not group_relation:
            continue
        class_id = class_relation.get("org_unit_id") if class_relation else None
        class_name = class_relation.get("name") if class_relation else None
        group_name = group_relation.get("name") if group_relation else None
        binding = _active_binding(connection, str(class_id)) if class_id else None
        cycle = _cycle_at(connection, int(binding["id"]), _now_for_database(connection)) if binding else None

        plan_name = _as_text(binding.get("plan_name")) if binding else None
        cycle_label = None
        year_index = None
        cycle_index = None
        status_name = "学习周期待配置"
        if cycle:
            cycle_index = int(cycle["learning_cycle_index"])
            status_name = _CYCLE_STATUS_NAMES.get(
                str(cycle.get("cycle_status") or "").upper(), "学习周期待配置"
            )
            plan_cycle = execute(
                connection,
                "SELECT cycle_label, year_index FROM learning_plan_cycles "
                "WHERE id=? LIMIT 1",
                (cycle["plan_cycle_id"],),
            ).fetchone()
            if plan_cycle:
                cycle_label = _as_text(plan_cycle["cycle_label"])
                year_index = int(plan_cycle["year_index"])

        result.append(
            {
                "class_name": _as_text(class_name) or "",
                "group_name": _as_text(group_name) or "",
                "plan_name": plan_name or "",
                "cycle_index": cycle_index,
                "cycle_label": cycle_label or "",
                "year_index": year_index,
                "status_name": status_name,
            }
        )
    return result


def _attendance_records(connection, member_id: int) -> list[dict[str, Any]]:
    rows = execute(
        connection,
        "SELECT DISTINCT eg.source_key, eg.external_group_id, eg.title, "
        "eg.activity_type, eg.event_date, class_org.name AS class_name, "
        "group_org.name AS group_name "
        "FROM attendance_records ar "
        "JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "JOIN attendance_event_groups eg ON eg.id=s.event_group_id "
        "LEFT JOIN org_units class_org ON class_org.id=eg.org_unit_id "
        "LEFT JOIN org_units group_org ON group_org.id=eg.study_org_unit_id "
        "WHERE ar.member_id=? AND ar.participant_type='MEMBER' "
        "AND ar.attendance_status IN ('PRESENT','MANUAL_PRESENT') "
        "AND COALESCE(eg.status, 'ACTIVE') NOT IN ('CANCELLED','DELETED') "
        "AND COALESCE(s.status, 'ACTIVE') NOT IN ('CANCELLED','DELETED') "
        "ORDER BY eg.event_date DESC, eg.source_key, eg.external_group_id",
        (member_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        activity_name = _learning_type_name(row["activity_type"])
        title = _as_text(row["title"]) or activity_name
        source_key = _as_text(row["source_key"]) or "attendance"
        external_id = _as_text(row["external_group_id"]) or "unknown"
        result.append(
            {
                "occurred_at": _as_text(row["event_date"]) or "",
                "learning_type": activity_name,
                "title": title,
                "class_name": _as_text(row["class_name"]) or "",
                "group_name": _as_text(row["group_name"]) or "",
                "source_type": "签到活动",
                "source_id": f"attendance:{source_key}:{external_id}",
                "status_name": "已参加",
                "_priority": 0,
            }
        )
    return result


def _study_meeting_records(connection, member_id: int) -> list[dict[str, Any]]:
    rows = _optional_rows(
        connection,
        "SELECT DISTINCT s.id AS _session_id, s.session_code, s.meeting_date, "
        "s.course_name_snapshot, c.name AS class_name, g.name AS group_name "
        "FROM study_meeting_attendances a "
        "JOIN study_meeting_sessions s ON s.id=a.study_meeting_session_id "
        "LEFT JOIN org_units c ON c.id=s.class_org_unit_id "
        "LEFT JOIN org_units g ON g.id=s.study_group_org_unit_id "
        "WHERE a.member_id=? AND s.status='SUBMITTED' "
        "ORDER BY s.meeting_date DESC, s.id DESC",
        (member_id,),
    )
    if not rows:
        return []

    session_ids = [int(row["_session_id"]) for row in rows]
    placeholders = ",".join("?" for _ in session_ids)
    course_rows = _optional_rows(
        connection,
        "SELECT study_meeting_session_id, course_name_snapshot "
        f"FROM study_meeting_courses WHERE study_meeting_session_id IN ({placeholders}) "
        "ORDER BY id",
        tuple(session_ids),
    )
    course_names: dict[int, list[str]] = {}
    for course in course_rows:
        name = _as_text(course.get("course_name_snapshot"))
        if name:
            course_names.setdefault(int(course["study_meeting_session_id"]), []).append(name)

    result: list[dict[str, Any]] = []
    for row in rows:
        names = course_names.get(int(row["_session_id"]), [])
        legacy_course = _as_text(row.get("course_name_snapshot"))
        if not names and legacy_course:
            names = [legacy_course]
        title = "小组学习会"
        if names:
            title = f"{title} · {'、'.join(dict.fromkeys(names))}"
        session_code = _as_text(row.get("session_code")) or "unknown"
        result.append(
            {
                "occurred_at": _as_text(row.get("meeting_date")) or "",
                "learning_type": "小组学习会",
                "title": title,
                "class_name": _as_text(row.get("class_name")) or "",
                "group_name": _as_text(row.get("group_name")) or "",
                "source_type": "小组学习会记录",
                "source_id": f"study-meeting:{session_code}",
                "status_name": "已参加",
                "_priority": 1,
            }
        )
    return result


def _legacy_activity_records(connection, member_id: int) -> list[dict[str, Any]]:
    rows = _optional_rows(
        connection,
        "SELECT f.external_id, f.source_system, f.source_table, f.activity_type, "
        "f.occurred_on, f.participation_status, f.title, ou.name AS org_name, "
        "ou.unit_type, parent.name AS parent_name "
        "FROM member_activity_facts f "
        "LEFT JOIN org_units ou ON ou.id=f.org_unit_id "
        "LEFT JOIN org_units parent ON parent.id=ou.parent_id "
        "WHERE f.member_id=? AND f.participation_status IN "
        "('PRESENT','COMPLETED','RECORDED') "
        "ORDER BY f.occurred_on DESC, f.external_id",
        (member_id,),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        activity_name = _learning_type_name(row.get("activity_type"))
        unit_type = str(row.get("unit_type") or "").upper()
        class_name = _as_text(row.get("org_name")) if unit_type == "CLASS" else ""
        group_name = _as_text(row.get("org_name")) if unit_type == "GROUP" else ""
        if unit_type == "GROUP":
            class_name = _as_text(row.get("parent_name")) or ""
        title = _as_text(row.get("title")) or activity_name
        source_system = _as_text(row.get("source_system")) or "legacy"
        source_table = _as_text(row.get("source_table")) or "activity"
        external_id = _as_text(row.get("external_id")) or "unknown"
        status = str(row.get("participation_status") or "").upper()
        result.append(
            {
                "occurred_at": _as_text(row.get("occurred_on")) or "",
                "learning_type": activity_name,
                "title": title,
                "class_name": class_name,
                "group_name": group_name,
                "source_type": "历史学习事实",
                "source_id": f"history:{source_system}:{source_table}:{external_id}",
                "status_name": _PARTICIPATION_STATUS_NAMES.get(status, "已记录"),
                "_priority": 2,
            }
        )
    return result


def _dedupe_key(record: dict[str, Any]) -> tuple[str, ...] | None:
    occurred_at = _as_text(record.get("occurred_at"))
    learning_type = _normalize_text(record.get("learning_type"))
    title = _normalize_text(record.get("title"))
    if not occurred_at or not learning_type or not title:
        source_type = _normalize_text(record.get("source_type"))
        source_id = _normalize_text(record.get("source_id"))
        return ("source", source_type, source_id) if source_type and source_id else None
    return (
        occurred_at[:10],
        learning_type,
        title,
        _normalize_text(record.get("class_name")),
        _normalize_text(record.get("group_name")),
    )


def _coarse_dedupe_keys(record: dict[str, Any]) -> list[tuple[str, ...]]:
    """Build legacy-compatible keys when one or both org levels are known."""

    occurred_at = _as_text(record.get("occurred_at"))
    learning_type = _normalize_text(record.get("learning_type"))
    title = _normalize_text(record.get("title"))
    class_name = _normalize_text(record.get("class_name"))
    group_name = _normalize_text(record.get("group_name"))
    if not occurred_at or not learning_type or not title:
        return []
    keys: list[tuple[str, ...]] = []
    if class_name and not group_name:
        keys.append(("coarse-class", occurred_at[:10], learning_type, title, class_name))
    if group_name and not class_name:
        keys.append(("coarse-group", occurred_at[:10], learning_type, title, group_name))
    if class_name and group_name:
        keys.extend(
            [
                ("coarse-class", occurred_at[:10], learning_type, title, class_name),
                ("coarse-group", occurred_at[:10], learning_type, title, group_name),
            ]
        )
    return keys


def _deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        records,
        key=lambda item: (
            _sort_datetime(item.get("occurred_at")),
            -int(item.get("_priority", 99)),
            str(item.get("source_id") or ""),
        ),
        reverse=True,
    )
    # With the descending sort, -priority puts current structured facts (0/1)
    # before matching imported historical facts (2) on the same date.
    seen: set[tuple[str, ...]] = set()
    structured_coarse_seen: set[tuple[str, ...]] = set()
    result: list[dict[str, Any]] = []
    for record in ordered:
        key = _dedupe_key(record)
        if key is not None and key in seen:
            continue
        coarse_keys = _coarse_dedupe_keys(record)
        priority = int(record.get("_priority", 99))
        if priority >= 2 and any(key in structured_coarse_seen for key in coarse_keys):
            continue
        if key is not None:
            seen.add(key)
        if priority < 2:
            structured_coarse_seen.update(coarse_keys)
        result.append(
            {
                key: record[key]
                for key in _PUBLIC_LEARNING_RECORD_FIELDS
                if key in record
            }
        )
        if len(result) >= RECENT_LEARNING_LIMIT:
            break
    return result


def get_member_learning_summary(member_id: int) -> dict[str, Any]:
    """Return only the learning facts belonging to one resolved member."""

    connection = connect()
    try:
        member = execute(
            connection,
            "SELECT id FROM members WHERE id=? AND status='ACTIVE' LIMIT 1",
            (member_id,),
        ).fetchone()
        if not member:
            raise ValueError("当前学员身份不可用")
        current_learning = _current_learning(connection, member_id)
        records = [
            *_attendance_records(connection, member_id),
            *_study_meeting_records(connection, member_id),
            *_legacy_activity_records(connection, member_id),
        ]
        return {
            "current_learning": current_learning,
            "recent_learning": _deduplicate_records(records),
        }
    finally:
        connection.close()
