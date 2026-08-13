from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context


def list_plans() -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id, year, version, policy_text, status, write_enabled, business_approval_reference, "
        "created_at, updated_at FROM annual_plans ORDER BY year DESC, version DESC"
    )


def enable_plan_write(plan_id: int, actor_user_id: int, approval_reference: str) -> dict:
    reference = approval_reference.strip()
    if len(reference) < 6:
        raise ValueError("必须填写可追溯的业务批准依据")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        plan = execute(connection, "SELECT * FROM annual_plans WHERE id=?", (plan_id,)).fetchone()
        if not plan:
            raise ValueError("年度方案不存在")
        before = dict(plan)
        execute(
            connection,
            "UPDATE annual_plans SET status='EXECUTING', write_enabled=1, "
            "business_approval_reference=?, updated_at=? WHERE id=?",
            (reference, now, plan_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="plans.enable_write",
            resource_type="annual_plan",
            resource_id=str(plan_id),
            purpose=reference,
            before=before,
            after={"status": "EXECUTING", "write_enabled": True},
        )
    return fetch_one("SELECT * FROM annual_plans WHERE id=?", (plan_id,))


def period_values(
    *, plan_id: int, user_id: int, month: int, org_unit_id: str | None = None
) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(user_id)
    params: list[Any] = [plan_id, month]
    sql = (
        "SELECT v.id, v.org_unit_id, o.name AS org_name, d.metric_key, d.name AS metric_name, "
        "mv.unit, v.value_kind, v.numeric_value, v.value_state, v.source_type, "
        "v.is_manual_override, v.updated_at "
        "FROM metric_period_values v "
        "JOIN org_units o ON o.id=v.org_unit_id "
        "JOIN metric_versions mv ON mv.id=v.metric_version_id "
        "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
        "WHERE v.annual_plan_id=? AND v.period_type='MONTH' AND v.period_no=?"
    )
    if org_unit_id:
        sql += " AND v.org_unit_id=?"
        params.append(org_unit_id)
    sql += " ORDER BY o.name, d.id, CASE v.value_kind WHEN 'MP' THEN 1 WHEN 'FORECAST' THEN 2 ELSE 3 END"
    rows = fetch_all(sql, tuple(params))
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return rows


def update_period_values(
    *, plan_id: int, user_id: int, updates: list[dict[str, Any]]
) -> int:
    plan = fetch_one("SELECT status, write_enabled FROM annual_plans WHERE id=?", (plan_id,))
    if not plan:
        raise ValueError("年度方案不存在")
    if not plan["write_enabled"] or plan["status"] != "EXECUTING":
        raise PermissionError("年度方案尚未获得业务批准，当前只读")
    allowed = accessible_org_ids(user_id)
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        changed = 0
        for item in updates:
            value_id = int(item["id"])
            current = execute(
                connection,
                "SELECT id, org_unit_id, value_kind, numeric_value, value_state FROM metric_period_values "
                "WHERE id=? AND annual_plan_id=?",
                (value_id, plan_id),
            ).fetchone()
            if not current:
                raise ValueError(f"期间值不存在: {value_id}")
            current = dict(current)
            if allowed is not None and current["org_unit_id"] not in allowed:
                raise PermissionError("不能修改授权组织范围之外的数据")
            if current["value_kind"] == "MP":
                raise PermissionError("月MP基准不可在执行期直接修改")
            numeric_value = item.get("numeric_value")
            state = item.get("value_state") or ("ZERO_IS_VALID" if numeric_value == 0 else "VALUE")
            if state in {"VALUE", "ZERO_IS_VALID"} and numeric_value is None:
                raise ValueError("有效数值状态必须填写数值")
            if state not in {"VALUE", "ZERO_IS_VALID", "NO_DATA", "NOT_APPLICABLE", "NOT_DUE"}:
                raise ValueError("未知空值状态")
            execute(
                connection,
                "UPDATE metric_period_values SET numeric_value=?, value_state=?, source_type='MANUAL', "
                "is_manual_override=1, updated_by=?, updated_at=? WHERE id=?",
                (numeric_value, state, user_id, now, value_id),
            )
            changed += 1
        write_audit(
            connection,
            actor_user_id=user_id,
            action="metrics.period_values.update",
            resource_type="annual_plan",
            resource_id=str(plan_id),
            after={"updated_count": changed, "value_ids": [int(item["id"]) for item in updates]},
        )
    return changed


def mp_dashboard(*, plan_id: int, user_id: int, month: int) -> dict[str, Any]:
    allowed = accessible_org_ids(user_id)
    centers = fetch_all(
        "SELECT DISTINCT o.id, o.name FROM org_units o "
        "JOIN metric_period_values v ON v.org_unit_id=o.id "
        "WHERE o.unit_type='REGIONAL_CENTER' AND o.is_active=1 AND v.annual_plan_id=? "
        "ORDER BY o.name",
        (plan_id,),
    )
    if allowed is not None:
        centers = [center for center in centers if center["id"] in allowed]
    rows = period_values(plan_id=plan_id, user_id=user_id, month=month)
    annual_targets = fetch_all(
        "SELECT t.org_unit_id, d.metric_key, t.annual_target, t.value_state "
        "FROM org_metric_targets t "
        "JOIN metric_versions mv ON mv.id=t.metric_version_id "
        "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
        "WHERE t.annual_plan_id=?",
        (plan_id,),
    )
    annual_index = {
        (row["org_unit_id"], row["metric_key"]): row for row in annual_targets
    }
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["org_unit_id"], row["metric_key"])
        item = grouped.setdefault(
            key,
            {
                "org_unit_id": row["org_unit_id"],
                "org_name": row["org_name"],
                "metric_key": row["metric_key"],
                "metric_name": row["metric_name"],
                "unit": row["unit"],
                "mp": None,
                "forecast": None,
                "actual": None,
            },
        )
        field = row["value_kind"].lower()
        item[field] = {
            "value": row["numeric_value"],
            "state": row["value_state"],
        }
        target = annual_index.get(key)
        item["annual_target"] = target["annual_target"] if target else None
    items = list(grouped.values())
    for item in items:
        forecast = item.get("forecast") or {}
        actual = item.get("actual") or {}
        denominator = forecast.get("value")
        numerator = actual.get("value")
        item["forecast_achievement"] = (
            numerator / denominator
            if numerator is not None and denominator not in (None, 0)
            else None
        )
    return {"month": month, "centers": centers, "items": items}


def _member_filter(alias: str, allowed: set[str] | None) -> tuple[str, list[Any]]:
    if allowed is None:
        return "", []
    if not allowed:
        return " AND 1=0", []
    values = sorted(allowed)
    placeholders = ",".join("?" for _ in values)
    now = datetime.now(UTC).isoformat()
    return (
        f" AND ({alias}.org_unit_id IN ({placeholders}) OR EXISTS ("
        "SELECT 1 FROM member_org_relations scope_rel "
        f"WHERE scope_rel.member_id={alias}.id AND scope_rel.org_unit_id IN ({placeholders}) "
        "AND (scope_rel.valid_from IS NULL OR scope_rel.valid_from<=?) "
        "AND (scope_rel.valid_until IS NULL OR scope_rel.valid_until>=?)))",
        [*values, *values, now, now],
    )


def _event_filter(allowed: set[str] | None) -> tuple[str, list[str]]:
    if allowed is None:
        return "", []
    if not allowed:
        return " AND 1=0", []
    values = sorted(allowed)
    placeholders = ",".join("?" for _ in values)
    return (
        f" AND (eg.org_unit_id IN ({placeholders}) "
        f"OR eg.study_org_unit_id IN ({placeholders}))",
        [*values, *values],
    )


def _month_bounds(year: int, month: int) -> tuple[str, str, str]:
    start = date(year, month, 1)
    following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start.isoformat(), following.isoformat(), start.strftime("%Y-%m")


def operations_snapshot(*, user_id: int, year: int, month: int) -> dict[str, Any]:
    """Return privacy-safe monthly operating facts within the caller's org scope.

    Member master data is authoritative for member counts, birthday and join-date
    facts. Renewal completion comes from renewal status history, and each
    attendance event group counts as one scheduled event (never one per check-in
    session). Legacy participant facts are deliberately excluded from event
    counts because they cannot prove the number of distinct scheduled events.
    """
    allowed = accessible_org_ids(user_id)
    permissions = (user_context(user_id) or {}).get("permissions", [])
    start, end, period = _month_bounds(year, month)
    member_scope, member_scope_params = _member_filter("m", allowed)
    renewal_scope, renewal_scope_params = _member_filter("m", allowed)
    event_scope, event_scope_params = _event_filter(allowed)

    active_members = fetch_one(
        "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE'"
        + member_scope,
        tuple(member_scope_params),
    )["count"]
    new_members = fetch_one(
        "SELECT COUNT(*) AS count FROM members m WHERE "
        "m.join_date>=? AND m.join_date<?" + member_scope,
        (start, end, *member_scope_params),
    )["count"]
    missing_join_date = fetch_one(
        "SELECT COUNT(*) AS count FROM members m WHERE m.status='ACTIVE' "
        "AND m.join_date IS NULL" + member_scope,
        tuple(member_scope_params),
    )["count"]
    relation_as_of = datetime.now(UTC).isoformat()
    birthday_relation_params = (relation_as_of,) * 8
    birthdays = fetch_all(
        "SELECT m.id, m.name, m.birthday, "
        "COALESCE((SELECT rr.org_unit_id FROM member_org_relations rr "
        "WHERE rr.member_id=m.id AND rr.relation_type='PRIMARY_REGION' "
        "AND (rr.valid_from IS NULL OR rr.valid_from<=?) "
        "AND (rr.valid_until IS NULL OR rr.valid_until>=?) "
        "ORDER BY rr.is_primary DESC, rr.id LIMIT 1), m.org_unit_id) AS org_unit_id, "
        "COALESCE((SELECT ro.name FROM member_org_relations rr "
        "JOIN org_units ro ON ro.id=rr.org_unit_id "
        "WHERE rr.member_id=m.id AND rr.relation_type='PRIMARY_REGION' "
        "AND (rr.valid_from IS NULL OR rr.valid_from<=?) "
        "AND (rr.valid_until IS NULL OR rr.valid_until>=?) "
        "ORDER BY rr.is_primary DESC, rr.id LIMIT 1), o.name) AS org_name, "
        "(SELECT cr.org_unit_id FROM member_org_relations cr "
        "WHERE cr.member_id=m.id AND cr.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
        "AND (cr.valid_from IS NULL OR cr.valid_from<=?) "
        "AND (cr.valid_until IS NULL OR cr.valid_until>=?) "
        "ORDER BY cr.is_primary DESC, cr.id LIMIT 1) AS class_org_unit_id, "
        "(SELECT co.name FROM member_org_relations cr "
        "JOIN org_units co ON co.id=cr.org_unit_id "
        "WHERE cr.member_id=m.id AND cr.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
        "AND (cr.valid_from IS NULL OR cr.valid_from<=?) "
        "AND (cr.valid_until IS NULL OR cr.valid_until>=?) "
        "ORDER BY cr.is_primary DESC, cr.id LIMIT 1) AS class_name "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id "
        "WHERE m.status='ACTIVE' AND substr(m.birthday, 6, 2)=?"
        + member_scope
        + " ORDER BY substr(m.birthday, 6, 5), org_name, class_name, m.name, m.id",
        (
            *birthday_relation_params,
            f"{month:02d}",
            *member_scope_params,
        ),
    )
    birthday_members = [
        {
            "member_id": row["id"],
            "name": row["name"],
            "org_unit_id": row["org_unit_id"],
            "org_name": row["org_name"],
            "class_org_unit_id": row.get("class_org_unit_id"),
            "class_name": row.get("class_name"),
            "birthday": str(row["birthday"])[5:10],
        }
        for row in birthdays
    ]

    renewed_members = None
    if "renewals:read" in permissions:
        renewed_members = fetch_one(
            "SELECT COUNT(DISTINCT c.member_id) AS count FROM renewal_status_history h "
            "JOIN renewal_cycles c ON c.id=h.renewal_cycle_id "
            "JOIN members m ON m.id=c.member_id "
            "WHERE h.to_status='RENEWED' AND h.from_status IS NOT NULL "
            "AND h.created_at>=? AND h.created_at<?"
            + renewal_scope,
            (start, end, *renewal_scope_params),
        )["count"]

    centers = fetch_all(
        "SELECT o.id, o.name, COUNT(DISTINCT CASE WHEN m.status='ACTIVE' THEN m.id END) "
        "AS active_member_count "
        "FROM org_units o LEFT JOIN members m ON m.org_unit_id=o.id "
        "WHERE o.unit_type='REGIONAL_CENTER' AND o.is_active=1 "
        "GROUP BY o.id, o.name ORDER BY o.name, o.id"
    )
    if allowed is not None:
        centers = [row for row in centers if row["id"] in allowed]

    classes = fetch_all(
        "SELECT c.id, c.name AS class_name, c.unit_type AS class_org_unit_type, "
        "p.id AS class_owner_org_unit_id, p.name AS class_owner_org_name "
        "FROM org_units c LEFT JOIN org_units p ON p.id=c.parent_id "
        "WHERE c.unit_type IN ('CLASS','SPECIAL_COHORT') AND c.is_active=1 "
        "ORDER BY c.name, c.id"
    )
    if allowed is not None:
        classes = [row for row in classes if row["id"] in allowed]

    # 班级名称已被业务确认在全平台唯一。历史重复组织尚未完成归并前，
    # 驾驶舱按最早建立的节点汇总展示，避免同一班级被重复计数或重复提示待排期。
    canonical_class_id: dict[str, str] = {}
    display_classes: list[dict[str, Any]] = []
    duplicate_class_node_count = 0
    for class_row in classes:
        class_name = str(class_row["class_name"] or "").strip()
        existing = canonical_class_id.get(class_name)
        if existing:
            canonical_class_id[class_row["id"]] = existing
            duplicate_class_node_count += 1
            continue
        canonical_class_id[class_row["id"]] = class_row["id"]
        display_classes.append(class_row)

    annual_class_rows = fetch_all(
        "SELECT eg.id, eg.org_unit_id, eg.study_org_unit_id "
        "FROM attendance_event_groups eg WHERE eg.event_date>=? AND eg.event_date<? "
        "AND eg.activity_type='CLASS_MEETING' "
        "AND eg.status NOT IN ('CANCELLED','INACTIVE')"
        + event_scope
        + " ORDER BY eg.event_date, eg.id",
        (f"{year:04d}-01-01", end, *event_scope_params),
    )
    annual_sequence: dict[int, int] = {}
    annual_class_ordinal: dict[str, int] = {}
    for row in annual_class_rows:
        class_key = canonical_class_id.get(row.get("study_org_unit_id"), row.get("study_org_unit_id"))
        if not class_key:
            continue
        annual_class_ordinal[class_key] = annual_class_ordinal.get(class_key, 0) + 1
        annual_sequence[row["id"]] = annual_class_ordinal[class_key]

    event_rows = fetch_all(
        "SELECT eg.id, eg.title, eg.event_date, eg.activity_type, eg.org_unit_id, "
        "o.name AS org_name, eg.study_org_unit_id, co.name AS class_name "
        "FROM attendance_event_groups eg JOIN org_units o ON o.id=eg.org_unit_id "
        "LEFT JOIN org_units co ON co.id=eg.study_org_unit_id "
        "WHERE substr(eg.event_date, 1, 7)=? "
        "AND eg.status NOT IN ('CANCELLED','INACTIVE')"
        + event_scope
        + " ORDER BY eg.event_date, co.name, eg.title, eg.id",
        (period, *event_scope_params),
    )
    class_meetings: list[dict[str, Any]] = []
    courses: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    for row in event_rows:
        activity_type = str(row["activity_type"] or "").upper()
        item = {
            "id": row["id"],
            "title": row["title"] or "未命名活动",
            "event_date": str(row["event_date"])[:10],
            "activity_type": activity_type,
            "org_name": row["org_name"],
            "class_name": row.get("class_name"),
            "class_org_unit_id": canonical_class_id.get(
                row.get("study_org_unit_id"), row.get("study_org_unit_id")
            ),
        }
        if activity_type == "CLASS_MEETING":
            item["year_sequence"] = annual_sequence.get(row["id"])
            class_meetings.append(item)
        elif activity_type in {"COURSE", "STUDY_COURSE", "SEMINAR"}:
            courses.append(item)
        else:
            activities.append(item)

    meetings_by_class: dict[str, list[dict[str, Any]]] = {}
    for item in class_meetings:
        if item.get("class_org_unit_id"):
            meetings_by_class.setdefault(item["class_org_unit_id"], []).append(item)
    class_meeting_schedule: list[dict[str, Any]] = []
    for class_row in display_classes:
        scheduled = meetings_by_class.get(class_row["id"], [])
        if scheduled:
            for item in scheduled:
                class_meeting_schedule.append({
                    **item,
                    "org_name": (
                        "苏州塾直属"
                        if class_row["class_owner_org_unit_id"] == "org-suzhou"
                        else class_row["class_owner_org_name"] or "归属待核"
                    ),
                    "class_owner_org_unit_id": class_row["class_owner_org_unit_id"],
                    "class_owner_org_name": class_row["class_owner_org_name"],
                    "class_owner_scope": (
                        "DIRECT"
                        if class_row["class_owner_org_unit_id"] == "org-suzhou"
                        else "CENTER"
                    ),
                    "class_org_unit_id": class_row["id"],
                    "class_name": class_row["class_name"],
                    "status": "SCHEDULED",
                })
        else:
            class_meeting_schedule.append({
                "id": None,
                "title": "本月未排期",
                "event_date": None,
                "activity_type": "CLASS_MEETING",
                "org_name": (
                    "苏州塾直属"
                    if class_row["class_owner_org_unit_id"] == "org-suzhou"
                    else class_row["class_owner_org_name"] or "归属待核"
                ),
                "class_owner_org_unit_id": class_row["class_owner_org_unit_id"],
                "class_owner_org_name": class_row["class_owner_org_name"],
                "class_owner_scope": (
                    "DIRECT"
                    if class_row["class_owner_org_unit_id"] == "org-suzhou"
                    else "CENTER"
                ),
                "class_org_unit_id": class_row["id"],
                "class_name": class_row["class_name"],
                "year_sequence": None,
                "status": "UNSCHEDULED",
            })
    class_meeting_schedule.extend(
        {**item, "status": "SCHEDULED"}
        for item in class_meetings
        if not item.get("class_org_unit_id")
    )
    class_operations_rows = []
    for class_row in display_classes:
        scheduled = meetings_by_class.get(class_row["id"], [])
        class_operations_rows.append({
            "class_org_unit_id": class_row["id"],
            "class_name": class_row["class_name"],
            "org_name": (
                "苏州塾直属"
                if class_row["class_owner_org_unit_id"] == "org-suzhou"
                else class_row["class_owner_org_name"] or "归属待核"
            ),
            "class_owner_org_unit_id": class_row["class_owner_org_unit_id"],
            "class_owner_org_name": class_row["class_owner_org_name"],
            "class_owner_scope": (
                "DIRECT"
                if class_row["class_owner_org_unit_id"] == "org-suzhou"
                else "CENTER"
            ),
            "class_meeting_count": len(scheduled),
            "class_meeting_at": scheduled[0]["event_date"] if scheduled else None,
            "year_sequence": scheduled[-1].get("year_sequence") if scheduled else None,
            "status": "SCHEDULED" if scheduled else "UNSCHEDULED",
        })

    event_schedule_source_ready = bool(
        fetch_one(
            "SELECT COUNT(*) AS count FROM attendance_event_groups eg WHERE 1=1"
            + event_scope,
            tuple(event_scope_params),
        )["count"]
    )
    course_source_ready = bool(
        fetch_one(
            "SELECT COUNT(*) AS count FROM attendance_event_groups eg "
            "WHERE activity_type IN ('COURSE','STUDY_COURSE','SEMINAR')"
            + event_scope,
            tuple(event_scope_params),
        )["count"]
    )
    return {
        "period": period,
        "scope_label": "苏州塾" if allowed is None else "授权范围",
        "summary": {
            "renewed_member_count": renewed_members,
            "new_member_count": new_members,
            "active_member_count": active_members,
            "birthday_member_count": len(birthday_members),
            "class_count": len(display_classes),
            "class_meeting_count": len(class_meetings),
            "course_count": len(courses),
            "activity_count": len(activities),
        },
        "centers": centers,
        "birthday_members": birthday_members,
        "classes": class_operations_rows,
        "class_meeting_schedule": class_meeting_schedule,
        "class_meetings": class_meetings,
        "courses": courses,
        "activities": activities,
        "data_quality": {
            "missing_join_date_count": missing_join_date,
            "attendance_schedule_source_ready": event_schedule_source_ready,
            "course_schedule_source_ready": course_source_ready,
            "unscheduled_class_count": sum(
                1 for row in class_meeting_schedule if row["status"] == "UNSCHEDULED"
            ),
            "unlinked_class_meeting_count": sum(
                1 for row in class_meetings if not row.get("class_org_unit_id")
            ),
            "duplicate_class_node_count": duplicate_class_node_count,
            "renewal_source_authorized": "renewals:read"
            in permissions,
            "active_member_count_as_of": "CURRENT",
            "notes": [
                "新增学员按学员主档入塾日期统计；缺少入塾日期的在册学员不计入新增。",
                "续费人数按续费状态首次变为已续费的时间统计。",
                "班会、课程和活动按活动组计次，上午、下午、恳亲会不重复计数。",
                "班级运营归属按班级组织的直属父组织统计，不按班内学长的发展分中心反推。",
                *(
                    ["发现历史重复班级组织，驾驶舱已按班级名称合并展示；请在系统设置完成组织归并。"]
                    if duplicate_class_node_count
                    else []
                ),
            ],
        },
    }


def target_variances(plan_id: int, user_id: int) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(user_id)
    if allowed is not None and "org-suzhou" not in allowed:
        return []
    targets = fetch_all(
        "SELECT t.org_unit_id, o.unit_type, d.metric_key, mv.aggregation_type, t.annual_target "
        "FROM org_metric_targets t JOIN org_units o ON o.id=t.org_unit_id "
        "JOIN metric_versions mv ON mv.id=t.metric_version_id "
        "JOIN metric_definitions d ON d.id=mv.metric_definition_id "
        "WHERE t.annual_plan_id=? AND t.annual_target IS NOT NULL",
        (plan_id,),
    )
    results = []
    metric_keys = sorted({row["metric_key"] for row in targets})
    for metric_key in metric_keys:
        root = next(
            (row["annual_target"] for row in targets if row["metric_key"] == metric_key and row["org_unit_id"] == "org-suzhou"),
            None,
        )
        children = [
            row["annual_target"] for row in targets
            if row["metric_key"] == metric_key and row["unit_type"] == "REGIONAL_CENTER"
        ]
        if root is None or not children:
            continue
        method = "SUM" if metric_key in {"active_member_count", "new_member_count"} else "AVG"
        aggregate = sum(children) if method == "SUM" else sum(children) / len(children)
        results.append({
            "metric_key": metric_key,
            "root_target": root,
            "child_aggregate": aggregate,
            "aggregation": method,
            "difference": root - aggregate,
        })
    return results
