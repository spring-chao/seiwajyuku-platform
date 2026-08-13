from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context


PRESENT_STATUSES = ("PRESENT", "MANUAL_PRESENT")
ENTREPRENEUR_POSITION_PATTERN = re.compile(
    r"董事长|总经理|总裁|CEO|创始人|经营者|负责人|老板|法人",
    re.IGNORECASE,
)
EXECUTIVE_POSITION_PATTERN = re.compile(
    r"副总经理|副总裁|COO|CFO|CTO|总监|高管",
    re.IGNORECASE,
)


def _visible_class(class_org_unit_id: str, user_id: int) -> dict[str, Any]:
    unit = fetch_one(
        "SELECT c.id, c.name, c.unit_type, c.parent_id, p.name AS org_name "
        "FROM org_units c LEFT JOIN org_units p ON p.id=c.parent_id "
        "WHERE c.id=? AND c.unit_type IN ('CLASS','SPECIAL_COHORT') AND c.is_active=1",
        (class_org_unit_id,),
    )
    if not unit:
        raise ValueError("班级不存在或已停用")
    allowed = accessible_org_ids(user_id)
    if allowed is not None and class_org_unit_id not in allowed:
        raise PermissionError("班级不在组织授权范围内")
    return unit


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _member_rows(class_org_unit_id: str) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT DISTINCT m.id, m.position, m.enterprise_financial_ciphertext "
        "FROM members m JOIN member_org_relations r ON r.member_id=m.id "
        "WHERE m.status='ACTIVE' AND r.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
        "AND r.org_unit_id=? AND (r.valid_from IS NULL OR r.valid_from<=CURRENT_TIMESTAMP) "
        "AND (r.valid_until IS NULL OR r.valid_until>=CURRENT_TIMESTAMP) ORDER BY m.id",
        (class_org_unit_id,),
    )


def _attendance_rate(
    event_group_ids: list[int], org_unit_id: str, relation_type: str
) -> dict[str, Any]:
    if not event_group_ids:
        return {"event_count": 0, "eligible_count": 0, "present_count": 0, "rate": None}
    placeholders = ",".join("?" for _ in event_group_ids)
    eligible = fetch_one(
        "SELECT COUNT(DISTINCT mor.member_id) AS count FROM member_org_relations mor "
        "JOIN members m ON m.id=mor.member_id AND m.status='ACTIVE' "
        "WHERE mor.relation_type=? AND mor.org_unit_id=?",
        (relation_type, org_unit_id),
    )["count"]
    present = fetch_one(
        "SELECT COUNT(*) AS count FROM (SELECT DISTINCT ar.member_id, s.event_group_id "
        "FROM attendance_records ar JOIN attendance_sessions s ON s.id=ar.attendance_session_id "
        "JOIN member_org_relations mor ON mor.member_id=ar.member_id "
        f"WHERE s.event_group_id IN ({placeholders}) AND ar.member_id IS NOT NULL "
        "AND mor.relation_type=? AND mor.org_unit_id=? "
        "AND ar.participant_type='MEMBER' "
        "AND ar.attendance_status IN ('PRESENT','MANUAL_PRESENT')) attended",
        (*event_group_ids, relation_type, org_unit_id),
    )["count"]
    denominator = int(eligible) * len(event_group_ids)
    return {
        "event_count": len(event_group_ids),
        "eligible_count": denominator,
        "present_count": int(present),
        "rate": _ratio(int(present), denominator),
    }


def class_operations_detail(
    *, user_id: int, class_org_unit_id: str, year: int, month: int
) -> dict[str, Any]:
    unit = _visible_class(class_org_unit_id, user_id)
    period = f"{year:04d}-{month:02d}"
    profile = fetch_one(
        "SELECT weekly_meeting_at, planned_class_meeting_at, learning_month, learning_progress, "
        "revenue_growing_member_count, revenue_comparable_member_count, updated_at "
        "FROM class_operation_monthly WHERE class_org_unit_id=? AND period=?",
        (class_org_unit_id, period),
    ) or {}
    events = fetch_all(
        "SELECT id, title, event_date, activity_type FROM attendance_event_groups "
        "WHERE study_org_unit_id=? AND substr(event_date,1,7)=? "
        "AND status NOT IN ('CANCELLED','INACTIVE') ORDER BY event_date, id",
        (class_org_unit_id, period),
    )
    class_meetings = [row for row in events if str(row["activity_type"]).upper() == "CLASS_MEETING"]
    group_rows = fetch_all(
        "SELECT g.id, g.name, gom.planned_meeting_at FROM org_units g "
        "LEFT JOIN group_operation_monthly gom ON gom.group_org_unit_id=g.id AND gom.period=? "
        "WHERE g.parent_id=? AND g.unit_type='GROUP' AND g.is_active=1 ORDER BY g.name, g.id",
        (period, class_org_unit_id),
    )
    group_events = fetch_all(
        "SELECT eg.id, eg.title, eg.event_date, eg.study_org_unit_id, g.name AS group_name "
        "FROM attendance_event_groups eg JOIN org_units g ON g.id=eg.study_org_unit_id "
        "WHERE g.parent_id=? AND g.unit_type='GROUP' AND substr(eg.event_date,1,7)=? "
        "AND eg.activity_type='GROUP_MEETING' AND eg.status NOT IN ('CANCELLED','INACTIVE') "
        "ORDER BY eg.event_date, eg.id",
        (class_org_unit_id, period),
    )
    members = _member_rows(class_org_unit_id)
    class_relation_type = (
        "SPECIAL_COHORT" if unit["unit_type"] == "SPECIAL_COHORT" else "STUDY_CLASS"
    )
    executive_count = sum(
        1 for member in members if EXECUTIVE_POSITION_PATTERN.search(str(member.get("position") or ""))
    )
    entrepreneur_count = sum(
        1 for member in members
        if ENTREPRENEUR_POSITION_PATTERN.search(str(member.get("position") or ""))
    )
    actor = user_context(user_id) or {"permissions": set()}
    can_view_financial = "members:enterprise_view" in actor["permissions"]
    revenue_growing = profile.get("revenue_growing_member_count")
    revenue_comparable = profile.get("revenue_comparable_member_count")
    group_result = []
    for group in group_rows:
        ids = [int(row["id"]) for row in group_events if row["study_org_unit_id"] == group["id"]]
        group_result.append({
            **group,
            "events": [row for row in group_events if row["study_org_unit_id"] == group["id"]],
            "attendance": _attendance_rate(ids, group["id"], "STUDY_GROUP"),
        })
    return {
        "class_org_unit_id": unit["id"],
        "class_name": unit["name"],
        "org_name": (
            "苏州塾直属"
            if unit.get("parent_id") == "org-suzhou"
            else unit.get("org_name") or "归属待核"
        ),
        "class_owner_org_unit_id": unit.get("parent_id"),
        "class_owner_org_name": unit.get("org_name"),
        "class_owner_scope": (
            "DIRECT" if unit.get("parent_id") == "org-suzhou" else "CENTER"
        ),
        "period": period,
        "active_member_count": len(members),
        "weekly_meeting_at": profile.get("weekly_meeting_at"),
        "planned_class_meeting_at": profile.get("planned_class_meeting_at"),
        "learning_month": profile.get("learning_month"),
        "learning_progress": profile.get("learning_progress"),
        "class_meetings": class_meetings,
        "class_attendance": _attendance_rate(
            [int(row["id"]) for row in class_meetings],
            class_org_unit_id,
            class_relation_type,
        ),
        "groups": group_result,
        "entrepreneur_count": entrepreneur_count,
        "entrepreneur_ratio": _ratio(entrepreneur_count, len(members)),
        "executive_count": executive_count,
        "executive_ratio": _ratio(executive_count, len(members)),
        "position_classification_note": "经营者和高管均按学员主档职务关键词分类，需运营人员复核；未维护职务者不纳入任一分类。",
        "revenue_growth_authorized": can_view_financial,
        "revenue_growing_member_count": revenue_growing if can_view_financial else None,
        "revenue_comparable_member_count": revenue_comparable if can_view_financial else None,
        "revenue_growth_ratio": (
            _ratio(int(revenue_growing), int(revenue_comparable))
            if can_view_financial and revenue_growing is not None and revenue_comparable is not None
            else None
        ),
        "updated_at": profile.get("updated_at"),
    }


def update_class_operations(
    *, actor_user_id: int, class_org_unit_id: str, year: int, month: int,
    updates: dict[str, Any]
) -> dict[str, Any]:
    unit = _visible_class(class_org_unit_id, actor_user_id)
    period = f"{year:04d}-{month:02d}"
    learning_month = updates.get("learning_month")
    if learning_month is not None and not 1 <= int(learning_month) <= 240:
        raise ValueError("班会学习月份必须在 1 到 240 之间")
    growing = updates.get("revenue_growing_member_count")
    comparable = updates.get("revenue_comparable_member_count")
    if growing is not None and comparable is not None and int(growing) > int(comparable):
        raise ValueError("业绩增长人数不能大于可比人数")
    groups = updates.pop("groups", [])
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        existing = execute(
            connection,
            "SELECT id FROM class_operation_monthly WHERE class_org_unit_id=? AND period=?",
            (class_org_unit_id, period),
        ).fetchone()
        values = (
            updates.get("weekly_meeting_at"), updates.get("planned_class_meeting_at"),
            learning_month, updates.get("learning_progress"), growing, comparable,
            actor_user_id, now,
        )
        if existing:
            execute(
                connection,
                "UPDATE class_operation_monthly SET weekly_meeting_at=?, planned_class_meeting_at=?, "
                "learning_month=?, learning_progress=?, revenue_growing_member_count=?, "
                "revenue_comparable_member_count=?, updated_by=?, updated_at=? WHERE id=?",
                (*values, existing["id"]),
            )
        else:
            execute(
                connection,
                "INSERT INTO class_operation_monthly(class_org_unit_id, period, weekly_meeting_at, "
                "planned_class_meeting_at, learning_month, learning_progress, "
                "revenue_growing_member_count, revenue_comparable_member_count, updated_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (class_org_unit_id, period, *values[:-1], now, now),
            )
        for group in groups:
            group_id = str(group.get("group_org_unit_id") or "")
            valid_group = execute(
                connection,
                "SELECT id FROM org_units WHERE id=? AND parent_id=? AND unit_type='GROUP' AND is_active=1",
                (group_id, class_org_unit_id),
            ).fetchone()
            if not valid_group:
                raise ValueError("小组不属于当前班级")
            row = execute(
                connection,
                "SELECT id FROM group_operation_monthly WHERE group_org_unit_id=? AND period=?",
                (group_id, period),
            ).fetchone()
            planned = group.get("planned_meeting_at")
            if row:
                execute(connection, "UPDATE group_operation_monthly SET planned_meeting_at=?, updated_by=?, updated_at=? WHERE id=?", (planned, actor_user_id, now, row["id"]))
            else:
                execute(connection, "INSERT INTO group_operation_monthly(group_org_unit_id, period, planned_meeting_at, updated_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (group_id, period, planned, actor_user_id, now, now))
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="class.operations.monthly.update", resource_type="org_unit",
            resource_id=class_org_unit_id, org_unit_id=class_org_unit_id,
            purpose=f"维护 {unit['name']} {period} 班级运营事项",
            after={"period": period, "fields": sorted(updates), "group_count": len(groups)},
        )
    return class_operations_detail(
        user_id=actor_user_id, class_org_unit_id=class_org_unit_id, year=year, month=month
    )
