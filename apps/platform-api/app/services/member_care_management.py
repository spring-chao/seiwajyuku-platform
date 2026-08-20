from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Callable

from app.db import fetch_all
from app.services.followups import OPEN_STATES, list_tasks
from app.services.iam import accessible_org_ids, user_context
from app.services.member_care_actions import _member_contexts, build_member_care_actions
from app.services.renewals import (
    CLOSED_RENEWAL_STATUSES,
    MEMBER_RENEWAL_ORG_SQL,
    TODAY_ACTION_STAGE_CODES,
    determine_renewal_stage,
    list_today_actions,
)


SOURCE_KEYS = ("renewal", "followup", "birthday")
RENEWAL_EXCEPTION_STAGES = frozenset(TODAY_ACTION_STAGE_CODES)
EXCEPTION_RANK = {
    "CARE_OVERDUE": 0,
    "RENEWAL_SUPPORT_NEEDED": 1,
    "RENEWAL_RECOVERY_OPEN": 2,
    "RENEWAL_UNASSIGNED": 3,
    "RENEWAL_STAGE_UNTOUCHED": 4,
    "FOLLOWUP_NO_SCHEDULE": 5,
    "BIRTHDAY_CARE_MISSED": 6,
}


def _calendar_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    parsed = _calendar_date(value)
    return parsed.isoformat() if parsed else None


def _source_coverage(user_id: int) -> dict[str, dict[str, bool]]:
    context = user_context(user_id)
    permissions = set((context or {}).get("permissions", []))
    source_permission = {
        "renewal": "renewals:read",
        "followup": "followups:manage",
        "birthday": "plans:read",
    }
    coverage = {
        source: {"accessible": permission in permissions}
        for source, permission in source_permission.items()
    }
    return coverage


def _validate_org_filter(
    user_id: int, org_unit_id: str | None
) -> set[str] | None:
    allowed = accessible_org_ids(user_id)
    if org_unit_id and allowed is not None and org_unit_id not in allowed:
        raise PermissionError("组织不在当前用户授权范围内")
    return allowed


def _exception(
    *,
    exception_type: str,
    org_unit_id: str,
    org_name: str,
    member_id: int | None,
    member_name: str | None,
    source: str,
    source_id: int,
    reason: str,
    days_overdue: int | None = None,
    assigned_user_name: str | None = None,
    navigation_type: str,
    navigation_id: int,
    due_date: str | None = None,
) -> dict[str, Any]:
    return {
        "exception_type": exception_type,
        "org_unit_id": org_unit_id,
        "org_name": org_name,
        "member_id": member_id,
        "member_name": member_name,
        "source": source,
        "source_id": source_id,
        "reason": reason,
        "days_overdue": days_overdue,
        "assigned_user_name": assigned_user_name,
        "navigation_type": navigation_type,
        "navigation_id": navigation_id,
        "due_date": due_date,
    }


def _renewal_rows(
    user_id: int,
    as_of: date,
    *,
    allowed_org_ids: set[str] | None,
    org_unit_id: str | None,
) -> list[dict[str, Any]]:
    conditions = ["c.renewal_year=?"]
    params: list[Any] = [as_of.year]
    if org_unit_id:
        conditions.append(f"{MEMBER_RENEWAL_ORG_SQL}=?")
        params.append(org_unit_id)
    rows = fetch_all(
        "SELECT c.id, c.member_id, c.renewal_year, c.due_month, c.status, "
        "c.assigned_user_id, u.display_name AS assigned_user_name, "
        "m.name AS member_name, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, o.name AS org_name "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id "
        f"JOIN org_units o ON o.id={MEMBER_RENEWAL_ORG_SQL} "
        "LEFT JOIN app_users u ON u.id=c.assigned_user_id WHERE "
        + " AND ".join(conditions),
        tuple(params),
    )
    if allowed_org_ids is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed_org_ids]
    for row in rows:
        row["stage"] = determine_renewal_stage(
            row["renewal_year"], row["due_month"], row["status"], as_of=as_of
        )
    return rows


def _add_once(
    exceptions: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
    item: dict[str, Any],
) -> None:
    key = (
        item["exception_type"],
        item["source"],
        int(item["source_id"]),
    )
    if key in seen:
        return
    seen.add(key)
    exceptions.append(item)


def _care_overdue_exceptions(
    care_data: dict[str, Any],
    as_of: date,
    exceptions: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
) -> None:
    for person in care_data.get("people", []):
        for action in person.get("actions", []):
            if action.get("urgency") != "OVERDUE":
                continue
            if action.get("source") == "BIRTHDAY" or action.get("navigation_type") == "BIRTHDAY":
                continue
            due_date = _calendar_date(action.get("due_date"))
            days_overdue = (as_of - due_date).days if due_date else None
            source = (
                "ENTERPRISE_VISIT"
                if action.get("navigation_type") == "ENTERPRISE_VISIT"
                else str(action.get("source") or "")
            )
            _add_once(
                exceptions,
                seen,
                _exception(
                    exception_type="CARE_OVERDUE",
                    org_unit_id=person["org_unit_id"],
                    org_name=person["org_name"],
                    member_id=int(person["member_id"]),
                    member_name=person.get("member_name"),
                    source=source,
                    source_id=int(action["source_id"]),
                    reason=str(action.get("reason") or "已有明确日期的关爱事项逾期"),
                    days_overdue=days_overdue,
                    assigned_user_name=action.get("assigned_user_name"),
                    navigation_type=action["navigation_type"],
                    navigation_id=int(action["navigation_id"]),
                ),
            )


def _birthday_missed_exceptions(
    as_of: date,
    *,
    allowed_org_ids: set[str] | None,
    org_unit_id: str | None,
    exceptions: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
) -> None:
    conditions = [
        "i.business_type='BIRTHDAY_CARE'",
        "i.period LIKE ?",
        "i.due_date IS NOT NULL",
        "i.due_date < ?",
    ]
    params: list[Any] = [f"{as_of.year}-%", as_of.isoformat()]
    rows = fetch_all(
        "SELECT i.id, i.business_id, i.due_date, i.status, i.actual_at "
        "FROM operation_items i WHERE " + " AND ".join(conditions) + " ORDER BY i.due_date, i.id",
        tuple(params),
    )
    member_ids: set[int] = set()
    normalized_rows: list[tuple[dict[str, Any], int, date]] = []
    for row in rows:
        try:
            member_id = int(row.get("business_id"))
        except (TypeError, ValueError):
            continue
        due_date = _calendar_date(row.get("due_date"))
        if not due_date or due_date >= as_of:
            continue
        member_ids.add(member_id)
        normalized_rows.append((row, member_id, due_date))

    contexts = _member_contexts(member_ids, allowed_org_ids)
    for row, member_id, due_date in normalized_rows:
        context = contexts.get(member_id)
        if not context or (org_unit_id and context["org_unit_id"] != org_unit_id):
            continue
        status = str(row.get("status") or "").upper()
        actual_at = _calendar_date(row.get("actual_at"))
        if status == "COMPLETED" and (not actual_at or actual_at <= due_date):
            continue
        if status == "COMPLETED" and actual_at:
            reason = (
                f"生日为{due_date.month}月{due_date.day}日，本年度生日关怀在"
                f"{actual_at.month}月{actual_at.day}日后才完成，仅用于内部运营复盘，不建议补发生日祝福"
            )
        else:
            reason = (
                f"生日为{due_date.month}月{due_date.day}日，本年度生日关怀未在生日当天前留下完成记录，"
                "仅用于内部运营复盘，不建议补发生日祝福"
            )
        _add_once(
            exceptions,
            seen,
            _exception(
                exception_type="BIRTHDAY_CARE_MISSED",
                org_unit_id=context["org_unit_id"],
                org_name=context["org_name"],
                member_id=member_id,
                member_name=context.get("member_name"),
                source="BIRTHDAY",
                source_id=int(row["id"]),
                reason=reason,
                navigation_type="BIRTHDAY",
                navigation_id=member_id,
                due_date=due_date.isoformat(),
            ),
        )


def _renewal_exceptions(
    user_id: int,
    as_of: date,
    *,
    allowed_org_ids: set[str] | None,
    org_unit_id: str | None,
    exceptions: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
) -> list[dict[str, Any]]:
    renewal_rows = _renewal_rows(
        user_id,
        as_of,
        allowed_org_ids=allowed_org_ids,
        org_unit_id=org_unit_id,
    )
    by_cycle = {int(row["id"]): row for row in renewal_rows}
    try:
        today_actions = list_today_actions(
            user_id,
            as_of.year,
            org_unit_id=org_unit_id,
            as_of=as_of,
        )
    except ValueError:
        today_actions = {"items": []}
    for item in today_actions.get("items", []):
        cycle = by_cycle.get(int(item["cycle_id"]))
        if not cycle:
            continue
        for reason in item.get("reasons", []):
            reason_code = str(reason.get("code") or "")
            if reason_code not in {"SUPPORT_NEEDED", "STAGE_UNTOUCHED"}:
                continue
            exception_type = (
                "RENEWAL_SUPPORT_NEEDED"
                if reason_code == "SUPPORT_NEEDED"
                else "RENEWAL_STAGE_UNTOUCHED"
            )
            reason_text = (
                "最近一次续费沟通标记需要内部协助"
                if reason_code == "SUPPORT_NEEDED"
                else "当前阶段尚未留下关爱记录"
            )
            _add_once(
                exceptions,
                seen,
                _exception(
                    exception_type=exception_type,
                    org_unit_id=cycle["org_unit_id"],
                    org_name=cycle["org_name"],
                    member_id=int(cycle["member_id"]),
                    member_name=cycle.get("member_name"),
                    source="RENEWAL",
                    source_id=int(cycle["id"]),
                    reason=reason_text,
                    assigned_user_name=cycle.get("assigned_user_name"),
                    navigation_type="RENEWAL",
                    navigation_id=int(cycle["id"]),
                ),
            )
    for cycle in renewal_rows:
        stage_code = cycle["stage"]["code"]
        if stage_code == "RECOVERY":
            _add_once(
                exceptions,
                seen,
                _exception(
                    exception_type="RENEWAL_RECOVERY_OPEN",
                    org_unit_id=cycle["org_unit_id"],
                    org_name=cycle["org_name"],
                    member_id=int(cycle["member_id"]),
                    member_name=cycle.get("member_name"),
                    source="RENEWAL",
                    source_id=int(cycle["id"]),
                    reason="续费月份已过，续费结果尚未闭环",
                    assigned_user_name=cycle.get("assigned_user_name"),
                    navigation_type="RENEWAL",
                    navigation_id=int(cycle["id"]),
                ),
            )
        if (
            stage_code in RENEWAL_EXCEPTION_STAGES
            and not cycle.get("assigned_user_id")
            and str(cycle.get("status") or "").upper()
            not in CLOSED_RENEWAL_STATUSES
        ):
            _add_once(
                exceptions,
                seen,
                _exception(
                    exception_type="RENEWAL_UNASSIGNED",
                    org_unit_id=cycle["org_unit_id"],
                    org_name=cycle["org_name"],
                    member_id=int(cycle["member_id"]),
                    member_name=cycle.get("member_name"),
                    source="RENEWAL",
                    source_id=int(cycle["id"]),
                    reason=f"当前{cycle['stage']['label']}阶段尚未分配责任人",
                    assigned_user_name=None,
                    navigation_type="RENEWAL",
                    navigation_id=int(cycle["id"]),
                ),
            )
    return renewal_rows


def _followup_exceptions(
    user_id: int,
    *,
    org_unit_id: str | None,
    exceptions: list[dict[str, Any]],
    seen: set[tuple[str, str, int]],
) -> None:
    for task in list_tasks(user_id):
        if str(task.get("status") or "").upper() not in OPEN_STATES:
            continue
        if org_unit_id and task.get("org_unit_id") != org_unit_id:
            continue
        if task.get("next_followup_at") or task.get("due_at"):
            continue
        is_visit = str(task.get("task_type") or "").upper() == "VISIT"
        source = "ENTERPRISE_VISIT" if is_visit else "FOLLOWUP"
        navigation_type = "ENTERPRISE_VISIT" if is_visit else "FOLLOWUP"
        reason = (
            "企业走访尚未设置下一时间"
            if is_visit
            else "已有关怀服务事项，但尚未设置下一时间"
        )
        _add_once(
            exceptions,
            seen,
            _exception(
                exception_type="FOLLOWUP_NO_SCHEDULE",
                org_unit_id=task["org_unit_id"],
                org_name=task["org_name"],
                member_id=int(task["member_id"]),
                member_name=task.get("member_name"),
                source=source,
                source_id=int(task["id"]),
                reason=reason,
                assigned_user_name=task.get("assignee_name"),
                navigation_type=navigation_type,
                navigation_id=int(task["id"]),
            ),
        )


def _sort_exception(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        EXCEPTION_RANK.get(item["exception_type"], 99),
        -(int(item.get("days_overdue") or 0)),
        str(item.get("org_name") or ""),
        str(item.get("member_name") or ""),
        int(item.get("source_id") or 0),
    )


def _empty_org(org_unit_id: str, org_name: str, coverage: dict[str, dict[str, bool]]) -> dict[str, Any]:
    return {
        "org_unit_id": org_unit_id,
        "org_name": org_name,
        "today_care_people_count": 0,
        "overdue_people_count": 0,
        "oldest_overdue_days": 0,
        "renewal_support_needed_count": 0 if coverage["renewal"]["accessible"] else None,
        "renewal_stage_untouched_count": 0 if coverage["renewal"]["accessible"] else None,
        "renewal_recovery_open_count": 0 if coverage["renewal"]["accessible"] else None,
        "renewal_unassigned_count": 0 if coverage["renewal"]["accessible"] else None,
        "followup_no_schedule_count": 0 if coverage["followup"]["accessible"] else None,
        "birthday_overdue_count": 0 if coverage["birthday"]["accessible"] else None,
        "birthday_care_missed_count": 0 if coverage["birthday"]["accessible"] else None,
        "followup_overdue_count": 0 if coverage["followup"]["accessible"] else None,
        "enterprise_visit_overdue_count": 0 if coverage["followup"]["accessible"] else None,
        "renewal_overdue_count": 0 if coverage["renewal"]["accessible"] else None,
    }


def _count_visible(items: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> int:
    return sum(1 for item in items if predicate(item))


def build_member_care_management_overview(
    user_id: int,
    *,
    as_of: date | datetime | None = None,
    org_unit_id: str | None = None,
) -> dict[str, Any]:
    current = as_of or datetime.now(UTC)
    today = current.date() if isinstance(current, datetime) else current
    coverage = _source_coverage(user_id)
    if not any(item["accessible"] for item in coverage.values()):
        raise PermissionError("当前账号没有学长关爱管理数据查看权限")
    allowed_org_ids = _validate_org_filter(user_id, org_unit_id)

    care_data = build_member_care_actions(user_id, as_of=today)
    people = [
        person
        for person in care_data.get("people", [])
        if not org_unit_id or person.get("org_unit_id") == org_unit_id
    ]
    exceptions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    _care_overdue_exceptions(care_data={"people": people}, as_of=today, exceptions=exceptions, seen=seen)
    if coverage["birthday"]["accessible"]:
        _birthday_missed_exceptions(
            today,
            allowed_org_ids=allowed_org_ids,
            org_unit_id=org_unit_id,
            exceptions=exceptions,
            seen=seen,
        )

    renewal_rows: list[dict[str, Any]] = []
    if coverage["renewal"]["accessible"]:
        renewal_rows = _renewal_exceptions(
            user_id,
            today,
            allowed_org_ids=allowed_org_ids,
            org_unit_id=org_unit_id,
            exceptions=exceptions,
            seen=seen,
        )
    if coverage["followup"]["accessible"]:
        _followup_exceptions(
            user_id,
            org_unit_id=org_unit_id,
            exceptions=exceptions,
            seen=seen,
        )
    exceptions.sort(key=_sort_exception)

    organizations: dict[str, dict[str, Any]] = {}
    for person in people:
        org_id = str(person["org_unit_id"])
        organizations.setdefault(
            org_id, _empty_org(org_id, str(person.get("org_name") or org_id), coverage)
        )["today_care_people_count"] += 1

    overdue_people: set[tuple[str, int]] = set()
    for item in exceptions:
        org_id = str(item["org_unit_id"])
        org = organizations.setdefault(
            org_id, _empty_org(org_id, str(item.get("org_name") or org_id), coverage)
        )
        if item["exception_type"] == "CARE_OVERDUE":
            if item.get("member_id") is not None:
                overdue_people.add((org_id, int(item["member_id"])))
            days = int(item.get("days_overdue") or 0)
            org["oldest_overdue_days"] = max(org["oldest_overdue_days"], days)
            if item["source"] == "RENEWAL":
                org["renewal_overdue_count"] += 1
            elif item["source"] == "BIRTHDAY":
                org["birthday_overdue_count"] += 1
            elif item["source"] == "ENTERPRISE_VISIT":
                org["enterprise_visit_overdue_count"] += 1
            elif item["source"] == "FOLLOWUP":
                org["followup_overdue_count"] += 1
        elif item["exception_type"] == "RENEWAL_SUPPORT_NEEDED":
            org["renewal_support_needed_count"] += 1
        elif item["exception_type"] == "RENEWAL_STAGE_UNTOUCHED":
            org["renewal_stage_untouched_count"] += 1
        elif item["exception_type"] == "RENEWAL_RECOVERY_OPEN":
            org["renewal_recovery_open_count"] += 1
        elif item["exception_type"] == "RENEWAL_UNASSIGNED":
            org["renewal_unassigned_count"] += 1
        elif item["exception_type"] == "FOLLOWUP_NO_SCHEDULE":
            org["followup_no_schedule_count"] += 1
        elif item["exception_type"] == "BIRTHDAY_CARE_MISSED":
            org["birthday_care_missed_count"] += 1

    for org_id, org in organizations.items():
        org["overdue_people_count"] = sum(1 for item in overdue_people if item[0] == org_id)

    def exception_count(exception_type: str) -> int | None:
        if exception_type.startswith("RENEWAL_") and not coverage["renewal"]["accessible"]:
            return None
        if exception_type == "FOLLOWUP_NO_SCHEDULE" and not coverage["followup"]["accessible"]:
            return None
        return _count_visible(exceptions, lambda item: item["exception_type"] == exception_type)

    summary = {
        "today_care_people_count": len(people),
        "today_care_action_count": sum(
            int(person.get("action_count") or len(person.get("actions", [])))
            for person in people
        ),
        "overdue_people_count": len(overdue_people),
        "oldest_overdue_days": max(
            (int(item.get("days_overdue") or 0) for item in exceptions if item["exception_type"] == "CARE_OVERDUE"),
            default=0,
        ),
        "renewal_support_needed_count": exception_count("RENEWAL_SUPPORT_NEEDED"),
        "renewal_recovery_open_count": exception_count("RENEWAL_RECOVERY_OPEN"),
        "renewal_unassigned_count": exception_count("RENEWAL_UNASSIGNED"),
        "followup_no_schedule_count": exception_count("FOLLOWUP_NO_SCHEDULE"),
        "birthday_care_missed_count": exception_count("BIRTHDAY_CARE_MISSED"),
        "renewal_overdue_count": (
            _count_visible(exceptions, lambda item: item["exception_type"] == "CARE_OVERDUE" and item["source"] == "RENEWAL")
            if coverage["renewal"]["accessible"]
            else None
        ),
        "followup_overdue_count": (
            _count_visible(exceptions, lambda item: item["exception_type"] == "CARE_OVERDUE" and item["source"] == "FOLLOWUP")
            if coverage["followup"]["accessible"]
            else None
        ),
        "enterprise_visit_overdue_count": (
            _count_visible(exceptions, lambda item: item["exception_type"] == "CARE_OVERDUE" and item["source"] == "ENTERPRISE_VISIT")
            if coverage["followup"]["accessible"]
            else None
        ),
        "birthday_overdue_count": (
            _count_visible(exceptions, lambda item: item["exception_type"] == "CARE_OVERDUE" and item["source"] == "BIRTHDAY")
            if coverage["birthday"]["accessible"]
            else None
        ),
    }
    return {
        "as_of": today.isoformat(),
        "summary": summary,
        "source_coverage": coverage,
        "organizations": sorted(
            organizations.values(),
            key=lambda item: (
                -(1 if item["overdue_people_count"] else 0),
                -int(item["oldest_overdue_days"] or 0),
                -int(item["renewal_support_needed_count"] or 0),
                -int(item["renewal_recovery_open_count"] or 0),
                str(item["org_name"] or ""),
            ),
        ),
        "exceptions": exceptions,
    }
