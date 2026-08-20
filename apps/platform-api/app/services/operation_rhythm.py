from __future__ import annotations

import calendar
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context
from app.services.organization_policy import is_suzhou_direct_class


RHYTHM_STATUSES = {
    "PENDING",
    "PLANNED",
    "IN_PROGRESS",
    "WAITING_EXTERNAL",
    "COMPLETED",
    "ATTENTION",
    "CANCELLED",
}
STATUS_LABELS = {
    "PENDING": "待确认",
    "PLANNED": "已计划",
    "IN_PROGRESS": "推进中",
    "WAITING_EXTERNAL": "等待外部反馈",
    "COMPLETED": "已圆满",
    "ATTENTION": "需关注",
    "CANCELLED": "已取消",
}
DEFAULT_TEMPLATE_CODE = "CLASS_MONTHLY_V1"
_UNSET = object()

DEFAULT_TEMPLATE_NODES = (
    {
        "node_code": "CLASS_OPERATION_MEETING",
        "title": "班级运营工作会议",
        "category": "班级治理",
        "rule_type": "FIXED_DAY",
        "rule_config": {"day": 5, "hour": 19, "minute": 30},
        "responsibility_role": "班主任",
        "external_responsibility_role": "班主任/副班主任",
        "sort_order": 10,
    },
    {
        "node_code": "CLASS_COMMITTEE_MEETING",
        "title": "班委工作会议",
        "category": "班级治理",
        "rule_type": "FIXED_DAY",
        "rule_config": {"day": 5, "hour": 20, "minute": 30},
        "responsibility_role": "班委",
        "external_responsibility_role": "班委",
        "sort_order": 20,
    },
    {
        "node_code": "TEACHING_REVIEW",
        "title": "教学研讨会",
        "category": "学习准备",
        "rule_type": "RELATIVE_TO_ANCHOR",
        "rule_config": {"anchor_code": "CLASS_MEETING"},
        "start_offset_days": -10,
        "due_offset_days": -10,
        "responsibility_role": "学习践行",
        "external_responsibility_role": "班主任/学习辅导员",
        "sort_order": 30,
    },
    {
        "node_code": "PRESENTATION_REVIEW",
        "title": "发表稿评审会",
        "category": "学习准备",
        "rule_type": "RELATIVE_TO_ANCHOR",
        "rule_config": {"anchor_code": "CLASS_MEETING"},
        "start_offset_days": -5,
        "due_offset_days": -5,
        "responsibility_role": "学习践行",
        "external_responsibility_role": "班主任/发表稿评审组",
        "sort_order": 40,
    },
    {
        "node_code": "CLASS_MEETING_PREPARATION",
        "title": "班级学习会筹备完成",
        "category": "学习准备",
        "rule_type": "RELATIVE_TO_ANCHOR",
        "rule_config": {"anchor_code": "CLASS_MEETING"},
        "start_offset_days": -7,
        "due_offset_days": -1,
        "responsibility_role": "运营人员",
        "external_responsibility_role": "班主任/筹备组",
        "sort_order": 50,
    },
    {
        "node_code": "CLASS_MEETING",
        "title": "班级学习会",
        "category": "学习活动",
        "rule_type": "FIXED_DAY",
        "rule_config": {"day": 28, "hour": 14, "minute": 0},
        "responsibility_role": "班主任",
        "external_responsibility_role": "班主任/班委",
        "sort_order": 25,
    },
    {
        "node_code": "CLASS_MEETING_REVIEW",
        "title": "学习会复盘及下次筹备启动",
        "category": "学习复盘",
        "rule_type": "RELATIVE_TO_ANCHOR",
        "rule_config": {"anchor_code": "CLASS_MEETING"},
        "start_offset_days": 0,
        "due_offset_days": 0,
        "responsibility_role": "运营人员",
        "external_responsibility_role": "班主任/班委",
        "sort_order": 70,
    },
    {
        "node_code": "CREDIT_SUBMISSION",
        "title": "本月学分记录表提交",
        "category": "数据与记录",
        "rule_type": "CROSS_MONTH_DAY",
        "rule_config": {"day": 2},
        "responsibility_role": "运营人员",
        "external_responsibility_role": "班主任",
        "sort_order": 80,
    },
    {
        "node_code": "GROUP_MEETING",
        "title": "各组小组会时间确认",
        "category": "学习活动",
        "rule_type": "MANUAL",
        "responsibility_role": "运营人员",
        "external_responsibility_role": "各组辅导员",
        "sort_order": 90,
    },
    {
        "node_code": "BIRTHDAY_CARE",
        "title": "生日学长关怀",
        "category": "学长关怀",
        "rule_type": "BIRTHDAY_MONTH",
        "responsibility_role": "运营人员",
        "external_responsibility_role": "班主任/班委",
        "business_type": "BIRTHDAY_CARE",
        "sort_order": 100,
    },
)


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _clamped_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(max(day, 1), calendar.monthrange(year, month)[1]))


def _nth_weekday(year: int, month: int, weekday: int, ordinal: int) -> date:
    first = date(year, month, 1)
    candidate = first + timedelta(days=(weekday - first.weekday()) % 7)
    candidate += timedelta(days=max(ordinal - 1, 0) * 7)
    if candidate.month != month:
        raise ValueError("第N个星期X超出本月范围")
    return candidate


def _node_rule_date(node: dict[str, Any], year: int, month: int, anchors: dict[str, date]) -> date | None:
    rule_type = str(node["rule_type"]).upper()
    try:
        config = json.loads(node.get("rule_config_json") or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"模板节点 {node['node_code']} 的时间规则无效") from exc
    if rule_type == "FIXED_DAY":
        return _clamped_date(year, month, int(config.get("day", 1)))
    if rule_type == "NTH_WEEKDAY":
        return _nth_weekday(
            year,
            month,
            int(config.get("weekday", 0)),
            int(config.get("ordinal", 1)),
        )
    if rule_type == "CROSS_MONTH_DAY":
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        return _clamped_date(next_year, next_month, int(config.get("day", 1)))
    if rule_type == "RELATIVE_TO_ANCHOR":
        anchor_code = str(config.get("anchor_code") or "")
        if anchor_code not in anchors:
            return None
        return anchors[anchor_code]
    if rule_type in {"MANUAL", "BIRTHDAY_MONTH"}:
        return None
    raise ValueError(f"不支持的时间规则: {rule_type}")


def _require_permission(user_id: int, permission: str) -> dict[str, Any]:
    user = user_context(user_id)
    if not user or permission not in user["permissions"]:
        raise PermissionError("当前角色不能维护运营节奏")
    return user


def _assert_scope(user_id: int, org_unit_id: str) -> None:
    allowed = accessible_org_ids(user_id)
    if allowed is not None and org_unit_id not in allowed:
        raise PermissionError("运营事项不在组织授权范围内")


def _default_template(connection: Any, actor_user_id: int) -> dict[str, Any]:
    existing = execute(
        connection,
        "SELECT * FROM operation_templates WHERE template_code=?",
        (DEFAULT_TEMPLATE_CODE,),
    ).fetchone()
    if existing:
        return dict(existing)
    now = datetime.now(UTC).isoformat()
    cursor = execute(
        connection,
        "INSERT INTO operation_templates(template_code, name, scope_type, description, created_by, created_at, updated_at) "
        "VALUES (?, ?, 'CLASS', ?, ?, ?, ?)",
        (
            DEFAULT_TEMPLATE_CODE,
            "班级月度运营节奏",
            "统一机制、班级参数可调整；一期由核心运营人员维护。",
            actor_user_id,
            now,
            now,
        ),
    )
    template_id = cursor.lastrowid
    for node in DEFAULT_TEMPLATE_NODES:
        execute(
            connection,
            "INSERT INTO operation_template_nodes("
            "template_id, node_code, title, category, rule_type, rule_config_json, "
            "start_offset_days, due_offset_days, responsibility_role, external_responsibility_role, "
            "business_type, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template_id,
                node["node_code"],
                node["title"],
                node["category"],
                node["rule_type"],
                json.dumps(node.get("rule_config", {}), ensure_ascii=False),
                node.get("start_offset_days", 0),
                node.get("due_offset_days", 0),
                node.get("responsibility_role"),
                node.get("external_responsibility_role"),
                node.get("business_type"),
                node["sort_order"],
                now,
                now,
            ),
        )
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="operations.rhythm.template.create_default",
        resource_type="operation_template",
        resource_id=str(template_id),
        after={"template_code": DEFAULT_TEMPLATE_CODE, "node_count": len(DEFAULT_TEMPLATE_NODES)},
    )
    return dict(
        execute(connection, "SELECT * FROM operation_templates WHERE id=?", (template_id,)).fetchone()
    )


def list_rhythm_templates(user_id: int) -> list[dict[str, Any]]:
    _require_permission(user_id, "plans:read")
    templates = fetch_all(
        "SELECT id, template_code, name, scope_type, description, is_active, updated_at "
        "FROM operation_templates WHERE is_active=1 ORDER BY id"
    )
    for template in templates:
        template["nodes"] = fetch_all(
            "SELECT id, node_code, title, category, rule_type, rule_config_json, "
            "start_offset_days, due_offset_days, responsibility_role, external_responsibility_role, "
            "business_type, sort_order FROM operation_template_nodes "
            "WHERE template_id=? AND is_active=1 ORDER BY sort_order, id",
            (template["id"],),
        )
    return templates


def _visible_class_ids(user_id: int) -> list[str]:
    allowed = accessible_org_ids(user_id)
    rows = fetch_all(
        "SELECT id, name, parent_id FROM org_units WHERE is_active=1 AND unit_type IN ('CLASS','SPECIAL_COHORT') "
        "ORDER BY name, id"
    )
    visible: list[str] = []
    for row in rows:
        if row["parent_id"] == "org-suzhou" and not is_suzhou_direct_class(
            class_name=row["name"], parent_id=row["parent_id"]
        ):
            continue
        if allowed is None or row["id"] in allowed:
            visible.append(row["id"])
    return visible


def _birthday_members(class_org_unit_id: str, year: int, month: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT DISTINCT m.id, m.name, m.birthday FROM members m "
        "JOIN member_org_relations r ON r.member_id=m.id "
        "WHERE m.status='ACTIVE' AND r.org_unit_id=? "
        "AND r.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "AND substr(m.birthday, 6, 2)=? ORDER BY m.birthday, m.id",
        (
            class_org_unit_id,
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
            f"{month:02d}",
        ),
    )


def _item_exists(connection: Any, cycle_id: int, item_key: str) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT id, status, manual_override FROM operation_items WHERE cycle_id=? AND item_key=?",
        (cycle_id, item_key),
    ).fetchone()
    return dict(row) if row else None


def _insert_item(
    connection: Any,
    *,
    cycle_id: int,
    node: dict[str, Any],
    org_unit_id: str,
    period: str,
    item_key: str,
    title: str,
    start_date: date | None,
    due_date: date | None,
    business_type: str | None = None,
    business_id: str | None = None,
) -> None:
    existing = _item_exists(connection, cycle_id, item_key)
    if existing:
        if not existing["manual_override"]:
            now = datetime.now(UTC).isoformat()
            execute(
                connection,
                "UPDATE operation_items SET title=?, status=?, start_date=?, due_date=?, updated_at=? "
                "WHERE id=? AND manual_override=0",
                (
                    title,
                    "PLANNED" if due_date else "PENDING",
                    _date_text(start_date),
                    _date_text(due_date),
                    now,
                    existing["id"],
                ),
            )
        elif item_key == "CLASS_MEETING":
            # 班会日期永远来自班级运营日历；状态或备注人工维护不能切断日期联动。
            execute(
                connection,
                "UPDATE operation_items SET start_date=?, due_date=?, updated_at=? "
                "WHERE id=?",
                (
                    _date_text(start_date),
                    _date_text(due_date),
                    datetime.now(UTC).isoformat(),
                    existing["id"],
                ),
            )
        return
    now = datetime.now(UTC).isoformat()
    status = "PLANNED" if due_date else "PENDING"
    execute(
        connection,
        "INSERT INTO operation_items(cycle_id, node_id, org_unit_id, period, item_key, title, category, status, "
        "responsibility_role, external_responsibility_role, start_date, due_date, business_type, business_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cycle_id,
            node["id"],
            org_unit_id,
            period,
            item_key,
            title,
            node["category"],
            status,
            node.get("responsibility_role"),
            node.get("external_responsibility_role"),
            _date_text(start_date),
            _date_text(due_date),
            business_type or node.get("business_type"),
            business_id,
            now,
            now,
        ),
    )


def _class_meeting_anchor(
    connection: Any, class_org_unit_id: str, period: str
) -> date | None:
    row = execute(
        connection,
        "SELECT planned_class_meeting_at FROM class_operation_monthly "
        "WHERE class_org_unit_id=? AND period=?",
        (class_org_unit_id, period),
    ).fetchone()
    return _parse_date(row["planned_class_meeting_at"]) if row else None


def _scheduled_node_dates(
    connection: Any,
    nodes: list[dict[str, Any]],
    class_org_unit_id: str,
    year: int,
    month: int,
) -> dict[str, tuple[date | None, date | None]]:
    period = _period(year, month)
    class_meeting_date = _class_meeting_anchor(connection, class_org_unit_id, period)
    anchors: dict[str, date] = {}
    scheduled: dict[str, tuple[date | None, date | None]] = {}
    for node in nodes:
        if node["rule_type"] == "BIRTHDAY_MONTH":
            continue
        # 班级运营日历是班会的唯一日期来源；模板固定日不能作为回退，
        # 否则班会及其关联事项会与服务日历脱节。
        base = (
            class_meeting_date
            if node["node_code"] == "CLASS_MEETING"
            else _node_rule_date(node, year, month, anchors)
        )
        if base:
            anchors[node["node_code"]] = base
        scheduled[node["node_code"]] = (
            base + timedelta(days=int(node["start_offset_days"])) if base else None,
            base + timedelta(days=int(node["due_offset_days"])) if base else None,
        )
    return scheduled


def sync_operation_cycle_dates(
    connection: Any,
    *,
    class_org_unit_id: str,
    year: int,
    month: int,
    actor_user_id: int | None = None,
) -> int:
    """同步班级服务日历日期到尚未人工覆盖的运营事项。"""
    period = _period(year, month)
    cycle = execute(
        connection,
        "SELECT c.id, c.template_id FROM operation_cycles c "
        "WHERE c.period=? AND c.org_unit_id=?",
        (period, class_org_unit_id),
    ).fetchone()
    if not cycle:
        return 0
    nodes = [
        dict(row)
        for row in execute(
            connection,
            "SELECT * FROM operation_template_nodes WHERE template_id=? AND is_active=1 "
            "ORDER BY sort_order, id",
            (cycle["template_id"],),
        ).fetchall()
    ]
    scheduled = _scheduled_node_dates(
        connection, nodes, class_org_unit_id, year, month
    )
    changed = 0
    now = datetime.now(UTC).isoformat()
    for node in nodes:
        dates = scheduled.get(node["node_code"])
        if not dates:
            continue
        item = execute(
            connection,
            "SELECT id, manual_override FROM operation_items "
            "WHERE cycle_id=? AND item_key=?",
            (cycle["id"], node["node_code"]),
        ).fetchone()
        if not item or (item["manual_override"] and node["node_code"] != "CLASS_MEETING"):
            continue
        start_date, due_date = dates
        if item["manual_override"] and node["node_code"] == "CLASS_MEETING":
            cursor = execute(
                connection,
                "UPDATE operation_items SET start_date=?, due_date=?, updated_at=? WHERE id=?",
                (_date_text(start_date), _date_text(due_date), now, item["id"]),
            )
        else:
            cursor = execute(
                connection,
                "UPDATE operation_items SET status=?, start_date=?, due_date=?, updated_at=? "
                "WHERE id=? AND manual_override=0",
                (
                    "PLANNED" if due_date else "PENDING",
                    _date_text(start_date),
                    _date_text(due_date),
                    now,
                    item["id"],
                ),
            )
        changed += cursor.rowcount
    if changed and actor_user_id is not None:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="operations.rhythm.class_meeting_anchor.sync",
            resource_type="operation_cycle",
            resource_id=f"{class_org_unit_id}:{period}",
            org_unit_id=class_org_unit_id,
            after={"period": period, "updated_item_count": changed},
        )
    return changed


def _generate_cycle(
    connection: Any,
    *,
    template: dict[str, Any],
    class_org_unit_id: str,
    year: int,
    month: int,
    actor_user_id: int,
) -> tuple[int, int]:
    period = _period(year, month)
    now = datetime.now(UTC).isoformat()
    cycle = execute(
        connection,
        "SELECT * FROM operation_cycles WHERE template_id=? AND period=? AND org_unit_id=?",
        (template["id"], period, class_org_unit_id),
    ).fetchone()
    if cycle:
        cycle_id = int(cycle["id"])
    else:
        cycle_id = int(
            execute(
                connection,
                "INSERT INTO operation_cycles(template_id, period, org_unit_id, generated_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (template["id"], period, class_org_unit_id, actor_user_id, now, now),
            ).lastrowid
        )
    nodes = [
        dict(row)
        for row in execute(
            connection,
            "SELECT * FROM operation_template_nodes WHERE template_id=? AND is_active=1 ORDER BY sort_order, id",
            (template["id"],),
        ).fetchall()
    ]
    scheduled = _scheduled_node_dates(connection, nodes, class_org_unit_id, year, month)
    created = 0
    for node in nodes:
        if node["rule_type"] == "BIRTHDAY_MONTH":
            for member in _birthday_members(class_org_unit_id, year, month):
                birthday = _parse_date(member.get("birthday"))
                if not birthday:
                    continue
                birthday_date = _clamped_date(year, month, birthday.day)
                before = _item_exists(connection, cycle_id, f"BIRTHDAY_CARE:{member['id']}")
                _insert_item(
                    connection,
                    cycle_id=cycle_id,
                    node=node,
                    org_unit_id=class_org_unit_id,
                    period=period,
                    item_key=f"BIRTHDAY_CARE:{member['id']}",
                    title=f"{member['name']}学长生日关怀",
                    start_date=birthday_date - timedelta(days=7),
                    due_date=birthday_date,
                    business_id=str(member["id"]),
                )
                created += int(before is None)
            continue
        start_date, due_date = scheduled[node["node_code"]]
        before = _item_exists(connection, cycle_id, node["node_code"])
        _insert_item(
            connection,
            cycle_id=cycle_id,
            node=node,
            org_unit_id=class_org_unit_id,
            period=period,
            item_key=node["node_code"],
            title=node["title"],
            start_date=start_date,
            due_date=due_date,
        )
        created += int(before is None)
    execute(
        connection,
        "UPDATE operation_cycles SET updated_at=?, generated_by=? WHERE id=?",
        (now, actor_user_id, cycle_id),
    )
    return cycle_id, created


def generate_rhythm_cycles(user_id: int, year: int, month: int) -> dict[str, Any]:
    _require_permission(user_id, "plans:period_write")
    if not 1 <= month <= 12:
        raise ValueError("月份必须在 1 到 12 之间")
    class_ids = _visible_class_ids(user_id)
    if not class_ids:
        return {"period": _period(year, month), "cycle_count": 0, "created_item_count": 0}
    with transaction() as connection:
        template = _default_template(connection, user_id)
        cycles = 0
        created_items = 0
        for class_id in class_ids:
            _, created = _generate_cycle(
                connection,
                template=template,
                class_org_unit_id=class_id,
                year=year,
                month=month,
                actor_user_id=user_id,
            )
            cycles += 1
            created_items += created
        write_audit(
            connection,
            actor_user_id=user_id,
            action="operations.rhythm.cycles.generate",
            resource_type="operation_cycle",
            resource_id=_period(year, month),
            after={"period": _period(year, month), "cycle_count": cycles, "created_item_count": created_items},
        )
    return {"period": _period(year, month), "cycle_count": cycles, "created_item_count": created_items}


def _item_filter(
    user_id: int,
    period: str,
    *,
    organization_id: str | None = None,
    class_org_unit_id: str | None = None,
    status: str | None = None,
) -> tuple[str, list[Any]]:
    allowed = accessible_org_ids(user_id)
    conditions = ["i.period=?"]
    params: list[Any] = [period]
    if allowed is None:
        pass
    elif not allowed:
        conditions.append("1=0")
    else:
        values = sorted(allowed)
        placeholders = ",".join("?" for _ in values)
        conditions.append(f"i.org_unit_id IN ({placeholders})")
        params.extend(values)
    if organization_id:
        conditions.append("organization_ou.id=?")
        params.append(organization_id)
    if class_org_unit_id:
        conditions.append("i.org_unit_id=?")
        params.append(class_org_unit_id)
    if status:
        if status not in RHYTHM_STATUSES:
            raise ValueError("未知运营事项状态")
        conditions.append("i.status=?")
        params.append(status)
    return " " + " AND ".join(conditions), params


def _item_rows(
    user_id: int,
    period: str,
    *,
    organization_id: str | None = None,
    class_org_unit_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    condition, params = _item_filter(
        user_id,
        period,
        organization_id=organization_id,
        class_org_unit_id=class_org_unit_id,
        status=status,
    )
    rows = fetch_all(
        "SELECT i.id, i.org_unit_id, class_ou.name AS org_name, "
        "i.org_unit_id AS class_org_unit_id, class_ou.name AS class_name, "
        "organization_ou.id AS organization_id, organization_ou.name AS organization_name, "
        "i.period, i.item_key, i.title, i.category, i.status, "
        "i.responsibility_role, i.external_responsibility_role, i.start_date, i.due_date, i.actual_at, "
        "i.completion_note, i.business_type, i.business_id, i.manual_override, i.updated_at "
        "FROM operation_items i "
        "JOIN org_units class_ou ON class_ou.id=i.org_unit_id "
        "LEFT JOIN org_units organization_ou ON organization_ou.id=class_ou.parent_id "
        "WHERE" + condition + " "
        "ORDER BY CASE WHEN i.due_date IS NULL THEN 1 ELSE 0 END, i.due_date, i.id",
        tuple(params),
    )
    for row in rows:
        # SQLite returns DATE columns as strings while PyMySQL returns
        # ``datetime.date`` objects. Normalize the operational dates before
        # comparing them or serializing the snapshot so both runtimes behave
        # identically.
        for field in ("start_date", "due_date"):
            row[field] = _date_text(_parse_date(row.get(field)))
        row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        business_id = row.get("business_id")
        row["business_id"] = int(business_id) if isinstance(business_id, str) and business_id.isdigit() else business_id
    return rows


def rhythm_snapshot(
    user_id: int,
    year: int,
    month: int,
    *,
    organization_id: str | None = None,
    class_org_unit_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    _require_permission(user_id, "plans:read")
    period = _period(year, month)
    items = _item_rows(
        user_id,
        period,
        organization_id=organization_id,
        class_org_unit_id=class_org_unit_id,
        status=status,
    )
    today = datetime.now(UTC).date()
    next_week = today + timedelta(days=7)
    today_rows = [item for item in items if item.get("due_date") == today.isoformat()]
    next_week_rows = [
        item
        for item in items
        if item.get("due_date") and today.isoformat() <= item["due_date"] <= next_week.isoformat()
    ]
    attention_rows = [
        item
        for item in items
        if item.get("business_type") != "BIRTHDAY_CARE"
        and (
            item["status"] == "ATTENTION"
            or (
                item.get("due_date")
                and item["due_date"] < today.isoformat()
                and item["status"] not in {"COMPLETED", "CANCELLED"}
            )
        )
    ]
    counts = {status: sum(1 for item in items if item["status"] == status) for status in RHYTHM_STATUSES}
    has_cycle = bool(items)
    missing_class_meeting_count = sum(
        1
        for item in items
        if item["item_key"] == "CLASS_MEETING" and not item.get("due_date")
    )
    data_quality_notes = [] if has_cycle else ["本月运营事项尚未生成，请由核心运营人员生成本月节奏。"]
    if missing_class_meeting_count:
        data_quality_notes.append(
            f"{missing_class_meeting_count} 个班级未在班级运营与本月服务日历维护班会日期，相关事项暂不推算。"
        )
    return {
        "period": period,
        "items": items,
        "views": {"today": today_rows, "next_7_days": next_week_rows, "month": items, "attention": attention_rows},
        "summary": {
            "total": len(items),
            "today_count": len(today_rows),
            "next_7_days_count": len(next_week_rows),
            "attention_count": len(attention_rows),
            "status_counts": counts,
        },
        "data_quality": {
            "generated": has_cycle,
            "notes": data_quality_notes,
        },
        "policy": "一期由核心运营人员维护；微信群、电话和线下沟通继续保留，系统只记录计划、推进、反馈、结果与异常。",
    }


def update_rhythm_item(
    user_id: int,
    item_id: int,
    *,
    status: str | None = None,
    title: str | None = None,
    note: str | None | object = _UNSET,
    start_date: str | None | object = _UNSET,
    due_date: str | None | object = _UNSET,
) -> dict[str, Any]:
    _require_permission(user_id, "plans:period_write")
    if status is not None and status not in RHYTHM_STATUSES:
        raise ValueError("未知运营事项状态")
    current = fetch_one("SELECT * FROM operation_items WHERE id=?", (item_id,))
    if not current:
        raise ValueError("运营事项不存在")
    _assert_scope(user_id, current["org_unit_id"])
    if current["item_key"] == "CLASS_MEETING" and (
        start_date is not _UNSET or due_date is not _UNSET
    ):
        raise ValueError("班会日期请在班级运营与本月服务日历中维护")
    if status is None and title is None and note is _UNSET and start_date is _UNSET and due_date is _UNSET:
        raise ValueError("没有可更新的运营事项字段")
    next_status = status or current["status"]
    next_title = title if title is not None else current["title"]
    next_note = current.get("completion_note") if note is _UNSET else note
    raw_start = current.get("start_date") if start_date is _UNSET else start_date
    raw_due = current.get("due_date") if due_date is _UNSET else due_date
    parsed_start = _parse_date(raw_start) if raw_start else None
    parsed_due = _parse_date(raw_due) if raw_due else None
    if raw_start and parsed_start is None:
        raise ValueError("开始日期格式无效，应为 YYYY-MM-DD")
    if raw_due and parsed_due is None:
        raise ValueError("截止日期格式无效，应为 YYYY-MM-DD")
    if parsed_start and parsed_due and parsed_start > parsed_due:
        raise ValueError("开始日期不能晚于截止日期")
    now = datetime.now(UTC).isoformat()
    actual_at = (
        current.get("actual_at") or now
        if next_status == "COMPLETED"
        else None
    )
    before = {
        "status": current["status"],
        "title": current["title"],
        "start_date": _date_text(_parse_date(current.get("start_date"))),
        "due_date": _date_text(_parse_date(current.get("due_date"))),
        "has_note": bool(current.get("completion_note")),
    }
    after = {
        "status": next_status,
        "title": next_title,
        "start_date": _date_text(parsed_start),
        "due_date": _date_text(parsed_due),
        "has_note": bool(next_note),
    }
    with transaction() as connection:
        execute(
            connection,
            "UPDATE operation_items SET status=?, title=?, start_date=?, due_date=?, actual_at=?, completion_note=?, "
            "manual_override=1, updated_at=? WHERE id=?",
            (
                next_status,
                next_title,
                _date_text(parsed_start),
                _date_text(parsed_due),
                actual_at,
                next_note,
                now,
                item_id,
            ),
        )
        if status is not None:
            execute(
                connection,
                "INSERT INTO operation_progress_records(item_id, status, note, occurred_at, actor_user_id, source_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'MANUAL', ?)",
                (item_id, next_status, next_note, now, user_id, now),
            )
        write_audit(
            connection,
            actor_user_id=user_id,
            action="operations.rhythm.item.update",
            resource_type="operation_item",
            resource_id=str(item_id),
            org_unit_id=current["org_unit_id"],
            before=before,
            after=after,
        )
    return fetch_one(
        "SELECT i.id, i.org_unit_id, class_ou.name AS org_name, "
        "i.org_unit_id AS class_org_unit_id, class_ou.name AS class_name, "
        "organization_ou.id AS organization_id, organization_ou.name AS organization_name, "
        "i.period, i.item_key, i.title, i.category, i.status, i.responsibility_role, "
        "i.external_responsibility_role, i.start_date, i.due_date, i.actual_at, i.completion_note, "
        "i.business_type, i.business_id, i.updated_at "
        "FROM operation_items i JOIN org_units class_ou ON class_ou.id=i.org_unit_id "
        "LEFT JOIN org_units organization_ou ON organization_ou.id=class_ou.parent_id WHERE i.id=?",
        (item_id,),
    ) or {}


def update_rhythm_template_node(
    user_id: int, template_id: int, node_id: int, updates: dict[str, Any]
) -> dict[str, Any]:
    _require_permission(user_id, "plans:period_write")
    node = fetch_one(
        "SELECT n.*, t.template_code FROM operation_template_nodes n "
        "JOIN operation_templates t ON t.id=n.template_id WHERE n.id=? AND n.template_id=?",
        (node_id, template_id),
    )
    if not node:
        raise ValueError("运营模板节点不存在")
    allowed_fields = {
        "title",
        "category",
        "rule_type",
        "rule_config_json",
        "start_offset_days",
        "due_offset_days",
        "responsibility_role",
        "external_responsibility_role",
    }
    changed = {key: value for key, value in updates.items() if key in allowed_fields and value is not None}
    if "rule_config" in updates:
        changed["rule_config_json"] = json.dumps(updates["rule_config"], ensure_ascii=False)
    if not changed:
        raise ValueError("没有可更新的模板字段")
    now = datetime.now(UTC).isoformat()
    assignments = ", ".join(f"{key}=?" for key in changed)
    with transaction() as connection:
        execute(
            connection,
            f"UPDATE operation_template_nodes SET {assignments}, updated_at=? WHERE id=? AND template_id=?",
            (*changed.values(), now, node_id, template_id),
        )
        write_audit(
            connection,
            actor_user_id=user_id,
            action="operations.rhythm.template_node.update",
            resource_type="operation_template_node",
            resource_id=str(node_id),
            after={"template_id": template_id, "changed_fields": sorted(changed)},
        )
    return fetch_one("SELECT * FROM operation_template_nodes WHERE id=?", (node_id,)) or {}
