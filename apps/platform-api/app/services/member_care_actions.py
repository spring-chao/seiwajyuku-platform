from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.db import fetch_all
from app.services.followups import OPEN_STATES, list_tasks
from app.services.iam import accessible_org_ids, user_context
from app.services.members import resolve_member_scope
from app.services.renewals import (
    MEMBER_CLASS_NAME_SQL,
    MEMBER_GROUP_NAME_SQL,
    MEMBER_RENEWAL_ORG_SQL,
    list_today_actions,
)


URGENCY_RANK = {"OVERDUE": 0, "TODAY": 1, "ATTENTION": 2, "WINDOW": 3}
SOURCE_RANK = {"RENEWAL": 0, "FOLLOWUP": 1, "BIRTHDAY": 2}
RENEWAL_REASON_URGENCY = {
    "FOLLOWUP_OVERDUE": "OVERDUE",
    "FOLLOWUP_TODAY": "TODAY",
    "SUPPORT_NEEDED": "ATTENTION",
    "NEXT_STEP_MISSING": "ATTENTION",
    "STAGE_UNTOUCHED": "WINDOW",
}
FOLLOWUP_TYPE_LABELS = {
    "CARE": ("CARE", "日常关怀"),
    "PHONE": ("PHONE", "电话关怀"),
    "WECHAT": ("WECHAT", "微信关怀"),
    "MEETING": ("MEETING", "面谈关怀"),
    "VISIT": ("ENTERPRISE_VISIT", "企业走访"),
    "COURSE": ("COURSE", "学习关怀"),
    "OTHER": ("OTHER", "日常关怀"),
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


def _member_contexts(
    member_ids: set[int], allowed_org_ids: set[str] | None
) -> dict[int, dict[str, Any]]:
    if not member_ids:
        return {}
    placeholders = ",".join("?" for _ in member_ids)
    rows = fetch_all(
        "SELECT m.id, m.name, m.org_unit_id, m.development_org_unit_id, "
        "o.name AS primary_org_name, "
        f"{MEMBER_CLASS_NAME_SQL} AS class_name, "
        f"{MEMBER_GROUP_NAME_SQL} AS group_name "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id "
        f"WHERE m.id IN ({placeholders})",
        tuple(sorted(member_ids)),
    )
    scoped_rows: list[tuple[int, Any, str]] = []
    for row in rows:
        try:
            scoped_org_id = resolve_member_scope(
                int(row["id"]), row["org_unit_id"], allowed_org_ids
            )
        except PermissionError:
            # A source may be visible through its own scope while the member
            # master is not visible there; fail closed instead of leaking
            # cross-organization member metadata.
            continue
        scoped_rows.append((int(row["id"]), row, scoped_org_id))
    if not scoped_rows:
        return {}
    scoped_org_ids = sorted({org_id for _, _, org_id in scoped_rows})
    org_rows = fetch_all(
        "SELECT id, name FROM org_units WHERE id IN ("
        + ",".join("?" for _ in scoped_org_ids)
        + ")",
        tuple(scoped_org_ids),
    )
    org_names = {str(row["id"]): row["name"] for row in org_rows}
    contexts: dict[int, dict[str, Any]] = {}
    for member_id, row, scoped_org_id in scoped_rows:
        contexts[int(row["id"])] = {
            "member_id": member_id,
            "member_name": row["name"],
            "org_unit_id": scoped_org_id,
            "org_name": org_names.get(scoped_org_id) or row["primary_org_name"],
            "class_name": row["class_name"],
            "group_name": row["group_name"],
        }
    return contexts


def _birthday_items(user_id: int, today: date) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(user_id)
    conditions = [
        "i.business_type='BIRTHDAY_CARE'",
        "i.status NOT IN ('COMPLETED', 'CANCELLED')",
        "i.start_date IS NOT NULL",
        "i.start_date<=?",
    ]
    params: list[Any] = [today.isoformat()]
    if allowed is not None:
        if not allowed:
            return []
        values = sorted(allowed)
        conditions.append(
            f"i.org_unit_id IN ({','.join('?' for _ in values)})"
        )
        params.extend(values)
    rows = fetch_all(
        "SELECT i.id, i.business_id, i.org_unit_id, i.status, i.start_date, "
        "i.due_date, i.title FROM operation_items i WHERE "
        + " AND ".join(conditions)
        + " ORDER BY i.due_date, i.id",
        tuple(params),
    )
    result = []
    for row in rows:
        due_date = _calendar_date(row.get("due_date"))
        start_date = _calendar_date(row.get("start_date"))
        try:
            member_id = int(row.get("business_id"))
        except (TypeError, ValueError):
            continue
        if not start_date or not due_date or today < start_date:
            continue
        if today < due_date:
            days_until = (due_date - today).days
            urgency = "WINDOW"
            reason = f"{days_until}天后生日｜已进入关怀窗口"
        elif today == due_date:
            urgency = "TODAY"
            reason = "今天生日"
        else:
            days_overdue = (today - due_date).days
            urgency = "OVERDUE"
            reason = f"生日关怀已逾期{days_overdue}天"
        result.append(
            {
                "member_id": member_id,
                "source_id": int(row["id"]),
                "action_type": "BIRTHDAY",
                "label": f"生日关怀｜{reason}",
                "reason": reason,
                "urgency": urgency,
                "due_date": due_date.isoformat(),
                "assigned_user_id": None,
                "assigned_user_name": None,
                "navigation_type": "BIRTHDAY",
                "navigation_id": member_id,
                "source_org_unit_id": row["org_unit_id"],
                "_source_order": len(result),
            }
        )
    return result


def _followup_actions(user_id: int, today: date) -> list[dict[str, Any]]:
    rows = list_tasks(user_id)
    result = []
    for row in rows:
        if str(row.get("status") or "").upper() not in OPEN_STATES:
            continue
        raw_due = row.get("next_followup_at") or row.get("due_at")
        due_date = _calendar_date(raw_due)
        if not due_date or due_date > today:
            continue
        task_type = str(row.get("task_type") or "OTHER").upper()
        action_type, type_label = FOLLOWUP_TYPE_LABELS.get(
            task_type, ("OTHER", "日常关怀")
        )
        if due_date < today:
            days_overdue = (today - due_date).days
            reason = f"{type_label}已逾期{days_overdue}天"
            urgency = "OVERDUE"
        else:
            reason = f"约定今天{type_label}"
            urgency = "TODAY"
        result.append(
            {
                "member_id": int(row["member_id"]),
                "source_id": int(row["id"]),
                "action_type": action_type,
                "label": f"{type_label}｜{reason}",
                "reason": reason,
                "urgency": urgency,
                "due_date": due_date.isoformat(),
                "assigned_user_id": row.get("assigned_user_id"),
                "assigned_user_name": row.get("assignee_name"),
                "navigation_type": (
                    "ENTERPRISE_VISIT" if action_type == "ENTERPRISE_VISIT" else "FOLLOWUP"
                ),
                "navigation_id": int(row["id"]),
                "source_org_unit_id": row["org_unit_id"],
                "_source_order": len(result),
            }
        )
    return result


def _renewal_actions(user_id: int, year: int, today: date) -> list[dict[str, Any]]:
    data = list_today_actions(user_id, year, as_of=today)
    result = []
    for item in data["items"]:
        primary_reason = item["primary_reason"]
        urgency = RENEWAL_REASON_URGENCY[primary_reason]
        reason_labels = [
            str(reason.get("label") or "")
            for reason in item.get("reasons", [])
            if reason.get("label")
        ]
        reason = " · ".join(reason_labels) or "续费阶段需要关注"
        result.append(
            {
                "member_id": int(item["member_id"]),
                "source_id": int(item["cycle_id"]),
                "action_type": "RENEWAL",
                "label": f"续费关爱｜{item['stage_label']}｜{reason}",
                "reason": reason,
                "urgency": urgency,
                "due_date": _date_text(item.get("next_followup_at")),
                "assigned_user_id": item.get("assigned_user_id"),
                "assigned_user_name": item.get("assigned_user_name"),
                "navigation_type": "RENEWAL",
                "navigation_id": int(item["cycle_id"]),
                "source_org_unit_id": item["org_unit_id"],
                "_source_order": len(result),
            }
        )
    return result


def _action_sort_key(action: dict[str, Any]) -> tuple[Any, ...]:
    due_date = _calendar_date(action.get("due_date")) or date.max
    return (
        URGENCY_RANK[action["urgency"]],
        SOURCE_RANK[action["source"]],
        due_date,
        int(action["source_id"]),
        int(action.get("_source_order", 0)),
    )


def build_member_care_actions(
    user_id: int,
    *,
    as_of: date | datetime | None = None,
) -> dict[str, Any]:
    """Aggregate member-centered care actions without creating a new task source."""
    today = (as_of or datetime.now(UTC))
    today = today.date() if isinstance(today, datetime) else today
    context = user_context(user_id)
    permissions = set((context or {}).get("permissions", []))
    source_permissions = {"renewals:read", "followups:manage", "plans:read"}
    if not permissions.intersection(source_permissions):
        raise PermissionError("当前账号没有学长关爱数据查看权限")

    actions: list[dict[str, Any]] = []
    if "renewals:read" in permissions:
        actions.extend(_renewal_actions(user_id, today.year, today))
    if "followups:manage" in permissions:
        actions.extend(_followup_actions(user_id, today))
    if "plans:read" in permissions:
        actions.extend(_birthday_items(user_id, today))

    member_ids = {int(action["member_id"]) for action in actions}
    contexts = _member_contexts(member_ids, accessible_org_ids(user_id))
    people: dict[int, dict[str, Any]] = {}
    for action in actions:
        member_id = int(action["member_id"])
        member = contexts.get(member_id)
        if not member:
            continue
        source = (
            "RENEWAL"
            if action["action_type"] == "RENEWAL"
            else "BIRTHDAY"
            if action["action_type"] == "BIRTHDAY"
            else "FOLLOWUP"
        )
        clean_action = {
            key: value
            for key, value in action.items()
            if not key.startswith("_") and key != "member_id" and key != "source_org_unit_id"
        }
        clean_action["source"] = source
        people.setdefault(
            member_id,
            {
                **member,
                "actions": [],
            },
        )["actions"].append(clean_action)

    output_people = []
    for person in people.values():
        person["actions"].sort(key=_action_sort_key)
        primary = person["actions"][0]
        person["primary_action"] = primary
        person["action_count"] = len(person["actions"])
        person["has_overdue"] = any(
            action["urgency"] == "OVERDUE" for action in person["actions"]
        )
        output_people.append(person)
    output_people.sort(
        key=lambda person: (
            URGENCY_RANK[person["primary_action"]["urgency"]],
            SOURCE_RANK[person["primary_action"]["source"]],
            _calendar_date(person["primary_action"].get("due_date")) or date.max,
            str(person["member_name"] or ""),
            int(person["member_id"]),
        )
    )

    def count_people(predicate) -> int:
        return sum(1 for person in output_people if predicate(person["actions"]))

    summary = {
        "people_total": len(output_people),
        "action_total": sum(person["action_count"] for person in output_people),
        "overdue_people_count": count_people(
            lambda items: any(item["urgency"] == "OVERDUE" for item in items)
        ),
        "today_people_count": count_people(
            lambda items: any(item["urgency"] == "TODAY" for item in items)
        ),
        "attention_people_count": count_people(
            lambda items: any(item["urgency"] == "ATTENTION" for item in items)
        ),
        "renewal_people_count": count_people(
            lambda items: any(item["source"] == "RENEWAL" for item in items)
        ),
        "birthday_people_count": count_people(
            lambda items: any(item["source"] == "BIRTHDAY" for item in items)
        ),
        "followup_people_count": count_people(
            lambda items: any(item["source"] == "FOLLOWUP" for item in items)
        ),
        "enterprise_visit_people_count": count_people(
            lambda items: any(item["action_type"] == "ENTERPRISE_VISIT" for item in items)
        ),
    }
    return {
        "as_of": today.isoformat(),
        "summary": summary,
        "people": output_people,
    }
