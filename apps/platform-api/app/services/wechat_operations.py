"""Mobile operations BFF backed by live IAM2 and existing business facts.

The mini-program deliberately has no mobile-only task, role, scope, member,
care or renewal store. Every request resolves the person session to a live
employee principal, then invokes one existing domain service inside the
matching IAM2 permission scope.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Iterable

from app.core.settings import get_settings
from app.services.followups import add_followup_record, create_task, list_tasks
from app.services.iam import mobile_iam_context, resolve_employee_mobile_principal
from app.services.member_care_actions import (
    build_member_care_actions,
    complete_birthday_care,
)
from app.services.members import (
    get_member_access_context,
    get_member_detail,
    get_member_timeline,
    reveal_contact,
    search_members,
)
from app.services.renewals import add_followup as add_renewal_followup, list_cycles
from app.services.study_meetings import list_study_meeting_records
from app.services.volunteer_positions import get_member_volunteer_services
from app.services.wechat_identity import WeChatIdentityError


MOBILE_ENTRIES = (
    {
        "key": "today_actions",
        "name": "今日行动",
        "description": "查看待处理和今天已完成的运营动作",
        "permissions": frozenset(
            {"renewals:read", "followups:manage", "members:detail_view"}
        ),
        "match": "ANY",
    },
    {
        "key": "member_search",
        "name": "学长查询",
        "description": "按姓名或手机号后四位快速找到学长",
        "permissions": frozenset({"members:read", "members:detail_view"}),
        "match": "ALL",
    },
    {
        "key": "care_records",
        "name": "关爱跟进",
        "description": "在学长详情中记录一次关爱",
        "permissions": frozenset({"followups:manage"}),
        "match": "ALL",
    },
    {
        "key": "renewal_watch",
        "name": "续费关注",
        "description": "按观3、续2、追1查看当前应关注的学长",
        "permissions": frozenset({"renewals:read"}),
        "match": "ALL",
    },
)

_CARE_SOURCE_PERMISSIONS = (
    ("RENEWAL", "renewal", "renewals:read"),
    ("FOLLOWUP", "followup", "followups:manage"),
    ("BIRTHDAY", "birthday", "members:detail_view"),
)
_ACTION_TYPE_NAMES = {
    "RENEWAL": "续费关注",
    "FOLLOWUP": "关爱跟进",
    "ENTERPRISE_VISIT": "企业走访",
    "BIRTHDAY": "生日关爱",
    "OTHER": "日常关爱",
}
_ACTION_URGENCY_RANK = {
    "OVERDUE": 0,
    "TODAY": 1,
    "ATTENTION": 2,
    "WINDOW": 3,
}
_ACTION_SOURCE_RANK = {"RENEWAL": 0, "FOLLOWUP": 1, "BIRTHDAY": 2}
_RENEWAL_STATUS_NAMES = {
    "PENDING_FIRST_CONTACT": "待首次联系",
    "CONTACTED_WAITING_REPLY": "已联系，等待回复",
    "IN_COMMUNICATION": "沟通中",
    "RENEWED": "已续费",
    "NOT_RENEWING": "暂不续费",
    "DEFERRED": "延期关注",
    "EXITED": "已退出",
}
_RENEWAL_WATCH_STAGES = (
    ("OBSERVE_3", "观3"),
    ("RENEW_2", "续2"),
    ("FOLLOW_1", "追1"),
)
_RENEWAL_CONTEXT_STAGE_RANK = {
    "RECOVERY": 0,
    "DUE_NOW": 1,
    "FOLLOW_1": 2,
    "RENEW_2": 3,
    "OBSERVE_3": 4,
    "PREPARE": 5,
    "CLOSED": 6,
}


def _require_enabled() -> None:
    if not get_settings().wechat_staff_mobile_operations_enabled:
        raise WeChatIdentityError("工作人员移动运营功能尚未开启")


def _principal(session: dict[str, Any]) -> dict[str, Any]:
    """Resolve account, employment, grants and scopes for this request only."""

    _require_enabled()
    principal = resolve_employee_mobile_principal(
        str(session.get("person_id") or ""),
        verified_user_id=session.get("verified_user_id"),
    )
    if not principal or not (
        principal.get("authorization_sources") or {}
    ).get("explicit_employee_grants"):
        raise PermissionError("当前微信身份没有有效的工作人员移动运营权限")
    return principal


def _require_permission(principal: dict[str, Any], permission: str) -> None:
    if permission not in set(principal.get("permissions") or []):
        raise PermissionError("当前工作人员账号没有此移动操作权限")


def _source_principal(
    principal: dict[str, Any],
    permission: str,
    *,
    extra_permissions: Iterable[str] = (),
) -> dict[str, Any]:
    """Limit a delegated service call to one IAM2 permission's own grants.

    A staff member can hold different roles with different organization scopes.
    Passing the full union into a legacy service while asking for one permission
    would recreate the forbidden role-times-scope cross product. The adapter
    therefore retains only grants that actually provide the requested
    permission. Extra permissions satisfy legacy read guards only; they do
    not add grants or widen the active request scope.
    """

    _require_permission(principal, permission)
    sources = dict(principal.get("authorization_sources") or {})
    grants = [
        dict(grant)
        for grant in sources.get("explicit_employee_grants", [])
        if permission in set(grant.get("permissions") or [])
    ]
    sources["explicit_employee_grants"] = grants
    return {
        **principal,
        "permissions": sorted({permission, *extra_permissions}),
        "authorization_sources": sources,
    }


def _entry_available(entry: dict[str, Any], permissions: set[str]) -> bool:
    required = set(entry["permissions"])
    if entry.get("match") == "ALL":
        return required.issubset(permissions)
    return bool(required.intersection(permissions))


def _entry_payload(entry: dict[str, Any]) -> dict[str, str]:
    return {
        "key": entry["key"],
        "name": entry["name"],
        "description": entry["description"],
    }


def _optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _action_payload(
    member: dict[str, Any], action: dict[str, Any], *, completed: bool
) -> dict[str, Any]:
    source = str(action.get("source") or "OTHER")
    navigation_id = action.get("navigation_id")
    cycle_id = action.get("renewal_cycle_id")
    if source == "RENEWAL" and cycle_id is None:
        cycle_id = navigation_id
    key_time = action.get("completed_at") if completed else action.get("due_date")
    return {
        "id": f"{source}:{action.get('source_id')}:{member.get('member_id')}",
        "member_id": int(member["member_id"]),
        "member_name": member.get("member_name"),
        "org_name": member.get("org_name"),
        "class_name": member.get("class_name"),
        "group_name": member.get("group_name"),
        "source": source,
        "action_type": action.get("action_type") or source,
        "action_type_name": _ACTION_TYPE_NAMES.get(
            str(action.get("action_type") or source), "日常关爱"
        ),
        "label": action.get("label") or "运营行动",
        "reason": action.get("reason") or "",
        "key_time": key_time,
        "due_date": action.get("due_date"),
        "completed_at": action.get("completed_at"),
        "urgency": action.get("urgency"),
        "navigation_type": action.get("navigation_type"),
        "navigation_id": navigation_id,
        "renewal_cycle_id": cycle_id,
        "birthday_due_date": (
            action.get("due_date") if source == "BIRTHDAY" else None
        ),
        "operation_item_id": action.get("operation_item_id"),
    }


def _pending_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _ACTION_URGENCY_RANK.get(str(item.get("urgency") or ""), 99),
        _ACTION_SOURCE_RANK.get(str(item.get("source") or ""), 99),
        str(item.get("key_time") or "9999-12-31"),
        str(item.get("member_name") or ""),
        int(item.get("member_id") or 0),
    )


def _completed_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(item.get("key_time") or ""),
        -_ACTION_SOURCE_RANK.get(str(item.get("source") or ""), 99),
        str(item.get("member_name") or ""),
    )


def _today_actions_for_principal(principal: dict[str, Any]) -> dict[str, Any]:
    """Read the existing Today Action V1 projection source by source.

    ``build_member_care_actions`` remains the only action aggregate. Calling
    it once per permitted source is intentional: it retains the source
    permission's own IAM2 scope rather than merging unrelated scopes.
    """

    permissions = set(principal.get("permissions") or [])
    coverage: dict[str, dict[str, Any]] = {
        coverage_key: {"accessible": permission in permissions, "available": False}
        for _, coverage_key, permission in _CARE_SOURCE_PERMISSIONS
    }
    pending: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    pending_seen: set[tuple[str, str, int]] = set()
    completed_seen: set[tuple[str, str, int]] = set()
    as_of = datetime.now(UTC).date().isoformat()

    for source, coverage_key, permission in _CARE_SOURCE_PERMISSIONS:
        if permission not in permissions:
            continue
        scoped_principal = _source_principal(principal, permission)
        with mobile_iam_context(scoped_principal, permission):
            data = build_member_care_actions(int(principal["user_id"]))
        as_of = str(data.get("as_of") or as_of)
        coverage[coverage_key] = dict(
            (data.get("source_coverage") or {}).get(
                coverage_key, coverage[coverage_key]
            )
        )
        for person in data.get("people") or []:
            for action in person.get("actions") or []:
                if str(action.get("source") or "") != source:
                    continue
                item = _action_payload(person, action, completed=False)
                marker = (source, str(action.get("source_id")), int(person["member_id"]))
                if marker not in pending_seen:
                    pending_seen.add(marker)
                    pending.append(item)
        for action in data.get("completed_today") or []:
            if str(action.get("source") or "") != source:
                continue
            member = {
                "member_id": action.get("member_id"),
                "member_name": action.get("member_name"),
                "org_name": action.get("org_name"),
                "class_name": action.get("class_name"),
                "group_name": action.get("group_name"),
            }
            if member["member_id"] is None:
                continue
            item = _action_payload(member, action, completed=True)
            marker = (source, str(action.get("source_id")), int(member["member_id"]))
            if marker not in completed_seen:
                completed_seen.add(marker)
                completed.append(item)

    pending.sort(key=_pending_sort_key)
    completed.sort(key=_completed_sort_key, reverse=True)
    return {
        "as_of": as_of,
        "summary": {
            "pending_action_count": len(pending),
            "pending_people_count": len({item["member_id"] for item in pending}),
            "completed_action_count": len(completed),
            "completed_people_count": len({item["member_id"] for item in completed}),
        },
        "source_coverage": coverage,
        "pending": pending,
        "completed": completed,
    }


def operation_workbench(session: dict[str, Any]) -> dict[str, Any]:
    principal = _principal(session)
    permissions = set(principal.get("permissions") or [])
    today = _today_actions_for_principal(principal)
    return {
        "worker_name": principal["display_name"],
        "summary": today["summary"],
        "entries": [
            _entry_payload(entry)
            for entry in MOBILE_ENTRIES
            if _entry_available(entry, permissions)
        ],
    }


def today_actions(session: dict[str, Any]) -> dict[str, Any]:
    return _today_actions_for_principal(_principal(session))


def _renewal_rows_for_principal(
    principal: dict[str, Any], *, permission: str = "renewals:read"
) -> list[dict[str, Any]]:
    """Read the current and immediately upcoming renewal year in one scope.

    ``观3 / 续2 / 追1`` naturally crosses a calendar-year boundary in the
    fourth quarter.  The renewal fact source remains ``renewal_cycles``; this
    only combines its two adjacent yearly reads so the mobile watchlist does
    not disappear at year end.
    """

    _require_permission(principal, permission)
    scoped_principal = _source_principal(principal, permission)
    current_year = datetime.now(UTC).year
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    with mobile_iam_context(scoped_principal, permission):
        for renewal_year in (current_year, current_year + 1):
            for row in list_cycles(
                int(principal["user_id"]),
                renewal_year,
                renewal_status="ALL",
                include_past=True,
            ):
                cycle_id = int(row["id"])
                if cycle_id not in seen:
                    seen.add(cycle_id)
                    rows.append(row)
    return rows


def _renewal_payload(row: dict[str, Any]) -> dict[str, Any]:
    stage = dict(row.get("stage") or {})
    status = str(row.get("status") or "")
    return {
        "cycle_id": int(row["id"]),
        "member_id": int(row["member_id"]),
        "member_name": row.get("member_name"),
        "org_name": row.get("org_name"),
        "class_name": row.get("member_class_name"),
        "group_name": row.get("member_group_name"),
        "due_month": row.get("due_month"),
        "months_until_due": stage.get("months_until_due"),
        "stage": stage.get("code"),
        "stage_label": stage.get("label"),
        "status": status,
        "status_label": _RENEWAL_STATUS_NAMES.get(status, "续费状态待确认"),
    }


def _renewal_context_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    return (
        _RENEWAL_CONTEXT_STAGE_RANK.get(str(item.get("stage") or ""), 99),
        int(item.get("due_month") or 99),
        int(item.get("cycle_id") or 0),
    )


def renewal_watch(session: dict[str, Any]) -> dict[str, Any]:
    principal = _principal(session)
    rows = _renewal_rows_for_principal(principal)
    by_stage: dict[str, list[dict[str, Any]]] = {
        stage: [] for stage, _ in _RENEWAL_WATCH_STAGES
    }
    for row in rows:
        stage = str((row.get("stage") or {}).get("code") or "")
        if stage in by_stage:
            by_stage[stage].append(_renewal_payload(row))
    for values in by_stage.values():
        values.sort(
            key=lambda item: (
                int(item.get("due_month") or 99),
                str(item.get("member_name") or ""),
                int(item.get("member_id") or 0),
            )
        )
    return {
        "as_of": datetime.now(UTC).date().isoformat(),
        "groups": [
            {"key": stage, "name": name, "items": by_stage[stage]}
            for stage, name in _RENEWAL_WATCH_STAGES
        ],
    }


def member_search(
    session: dict[str, Any], *, keyword: str, limit: int = 20
) -> list[dict[str, Any]]:
    principal = _principal(session)
    _require_permission(principal, "members:read")
    query = keyword.strip()
    if not query:
        raise ValueError("请输入姓名或手机号后4位")
    scoped_principal = _source_principal(principal, "members:read")
    with mobile_iam_context(scoped_principal, "members:read"):
        if len(query) == 4 and query.isdecimal():
            rows = search_members(
                int(principal["user_id"]), name=None, phone_last4=query, limit=limit
            )
        else:
            rows = search_members(int(principal["user_id"]), name=query, limit=limit)

    renewal_by_member: dict[int, dict[str, Any]] = {}
    if "renewals:read" in set(principal.get("permissions") or []):
        for cycle in _renewal_rows_for_principal(principal):
            payload = _renewal_payload(cycle)
            member_id = int(cycle["member_id"])
            current = renewal_by_member.get(member_id)
            if current is None or _renewal_context_sort_key(payload) < _renewal_context_sort_key(current):
                renewal_by_member[member_id] = payload

    result: list[dict[str, Any]] = []
    has_renewal_permission = "renewals:read" in set(principal.get("permissions") or [])
    for row in rows:
        member_id = int(row["member_id"])
        renewal = renewal_by_member.get(member_id)
        renewal_available = has_renewal_permission and _member_in_permission_scope(
            principal, member_id, "renewals:read"
        )
        result.append(
            {
                "member_id": member_id,
                "name": row.get("name"),
                "org_name": row.get("org_name"),
                "class_name": row.get("class_name"),
                "group_name": row.get("group_name"),
                "join_date": str(row["join_date"])[:10] if row.get("join_date") else None,
                "renewal": renewal
                or {
                    "available": renewal_available,
                    "status": None,
                    "status_label": "暂无当前续费周期" if renewal_available else "当前无权查看",
                    "stage": None,
                    "stage_label": None,
                },
            }
        )
    return result


def _member_in_permission_scope(
    principal: dict[str, Any], member_id: int, permission: str
) -> bool:
    if permission not in set(principal.get("permissions") or []):
        return False
    scoped_principal = _source_principal(principal, permission)
    try:
        with mobile_iam_context(scoped_principal, permission):
            get_member_access_context(member_id, int(principal["user_id"]))
    except PermissionError:
        return False
    return True


def _timeline_for_source(
    principal: dict[str, Any], member_id: int, permission: str
) -> dict[str, Any] | None:
    if not _member_in_permission_scope(principal, member_id, permission):
        return None
    # Timeline keeps ``members:detail_view`` as a legacy guard. The grant
    # list remains restricted to ``permission`` and the active request scope
    # remains that permission, so this does not grant a role/scope union.
    scoped_principal = _source_principal(
        principal, permission, extra_permissions={"members:detail_view"}
    )
    try:
        with mobile_iam_context(scoped_principal, permission):
            return get_member_timeline(member_id, int(principal["user_id"]), limit=20)
    except PermissionError:
        return None


def _latest_event(
    timeline: dict[str, Any] | None, event_types: set[str]
) -> dict[str, Any] | None:
    if not timeline:
        return None
    for event in timeline.get("events") or []:
        if str(event.get("event_type") or "") in event_types:
            return {
                "occurred_at": event.get("occurred_at"),
                "title": event.get("title"),
                "status": event.get("status"),
                "channel": event.get("channel"),
            }
    return None


def _member_renewal_context(
    principal: dict[str, Any], member_id: int
) -> dict[str, Any]:
    if not _member_in_permission_scope(principal, member_id, "renewals:read"):
        return {"available": False, "reason": "当前无权查看"}
    matching = [
        _renewal_payload(row)
        for row in _renewal_rows_for_principal(principal)
        if int(row["member_id"]) == int(member_id)
    ]
    if not matching:
        return {
            "available": True,
            "cycle_id": None,
            "status": None,
            "status_label": "暂无当前续费周期",
            "stage": None,
            "stage_label": None,
            "due_month": None,
            "months_until_due": None,
        }
    matching.sort(key=_renewal_context_sort_key)
    return {"available": True, **matching[0]}


def member_detail(session: dict[str, Any], *, member_id: int) -> dict[str, Any]:
    principal = _principal(session)
    _require_permission(principal, "members:detail_view")
    scoped_principal = _source_principal(principal, "members:detail_view")
    with mobile_iam_context(scoped_principal, "members:detail_view"):
        basic = get_member_detail(member_id, int(principal["user_id"]))

    learning_timeline = _timeline_for_source(principal, member_id, "members:read")
    care_timeline = _timeline_for_source(principal, member_id, "followups:manage")
    renewal = _member_renewal_context(principal, member_id)
    volunteer = get_member_volunteer_services(member_id)
    can_record_care = _member_in_permission_scope(
        principal, member_id, "followups:manage"
    )
    can_reveal_contact = (
        can_record_care
        and _member_in_permission_scope(principal, member_id, "contact:reveal")
    )
    can_record_renewal = bool(
        renewal.get("cycle_id")
        and _member_in_permission_scope(principal, member_id, "renewals:manage")
    )
    return {
        "member": {
            "member_id": int(basic["id"]),
            "name": basic.get("name"),
            "org_name": basic.get("org_name"),
            "class_name": basic.get("class_name"),
            "group_name": basic.get("group_name"),
            "join_date": str(basic["join_date"])[:10] if basic.get("join_date") else None,
            "membership_years": basic.get("membership_years"),
            "status": basic.get("status"),
        },
        "learning": {
            "available": learning_timeline is not None,
            "latest": _latest_event(
                learning_timeline, {"ATTENDANCE", "LEARNING_ACTIVITY"}
            ),
        },
        "care": {
            "available": care_timeline is not None,
            "latest": _latest_event(
                care_timeline, {"FOLLOWUP_RECORD", "ENTERPRISE_VISIT"}
            ),
        },
        "renewal": renewal,
        "volunteer_roles": [
            {
                "position_name": role.get("position_name"),
                "scope_name": role.get("scope_name"),
                "service_unit_name": role.get("service_unit_name"),
            }
            for role in volunteer.get("roles") or []
        ],
        "actions": {
            "can_record_care": can_record_care,
            "can_view_renewal": bool(renewal.get("available")),
            "can_record_renewal": can_record_renewal,
            "can_reveal_contact": can_reveal_contact,
        },
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(text[:10]), datetime.min.time(), UTC)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _get_or_create_care_task(
    principal: dict[str, Any],
    *,
    member_id: int,
    service_purpose: str,
    due_at: str | None,
    require_unexpired: bool,
) -> tuple[int, bool]:
    _require_permission(principal, "followups:manage")
    scoped_principal = _source_principal(principal, "followups:manage")
    with mobile_iam_context(scoped_principal, "followups:manage"):
        get_member_access_context(member_id, int(principal["user_id"]))
        now = datetime.now(UTC)
        candidates = [
            task
            for task in list_tasks(int(principal["user_id"]))
            if int(task.get("member_id") or 0) == int(member_id)
            and bool(task.get("can_record"))
            and str(task.get("status") or "").upper() in {"OPEN", "IN_PROGRESS"}
        ]
        for task in candidates:
            if require_unexpired:
                deadline = _parse_timestamp(task.get("due_at"))
                if deadline is not None and deadline < now:
                    continue
            return int(task["id"]), False
        task_id = create_task(
            int(principal["user_id"]),
            member_id=member_id,
            task_type="OTHER",
            service_purpose=service_purpose,
            assigned_user_id=int(principal["user_id"]),
            due_at=due_at,
            confidentiality_level="ASSIGNEE",
        )
    return int(task_id), True


def record_care(
    session: dict[str, Any],
    *,
    member_id: int,
    channel: str,
    situation: str,
    next_action: str | None,
    next_followup_at: str | None,
) -> dict[str, Any]:
    principal = _principal(session)
    _require_permission(principal, "followups:manage")
    task_id, created_task = _get_or_create_care_task(
        principal,
        member_id=member_id,
        service_purpose="移动端日常关怀",
        due_at=datetime.now(UTC).isoformat(),
        require_unexpired=False,
    )
    scoped_principal = _source_principal(principal, "followups:manage")
    with mobile_iam_context(scoped_principal, "followups:manage"):
        record_id = add_followup_record(
            task_id,
            int(principal["user_id"]),
            channel=channel.strip().upper(),
            contacted_at=datetime.now(UTC).isoformat(),
            outcome_code="COMPLETED",
            subject_statement=None,
            objective_facts=situation.strip(),
            staff_judgment=None,
            next_action=_optional_text(next_action),
            next_followup_at=_optional_text(next_followup_at),
        )
    return {
        "record_id": int(record_id),
        "task_id": task_id,
        "created_task": created_task,
        "today_actions": _today_actions_for_principal(principal),
    }


def record_renewal_care(
    session: dict[str, Any],
    *,
    member_id: int,
    renewal_cycle_id: int,
    channel: str,
    situation: str,
    next_action: str | None,
    next_followup_at: str | None,
) -> dict[str, Any]:
    principal = _principal(session)
    _require_permission(principal, "renewals:manage")
    scoped_principal = _source_principal(principal, "renewals:manage")
    cycles = _renewal_rows_for_principal(principal, permission="renewals:manage")
    matched = next(
        (
            cycle
            for cycle in cycles
            if int(cycle["id"]) == int(renewal_cycle_id)
            and int(cycle["member_id"]) == int(member_id)
        ),
        None,
    )
    if not matched:
        raise ValueError("续费关注与当前学长不匹配或不在授权范围内")
    with mobile_iam_context(scoped_principal, "renewals:manage"):
        followup_id = add_renewal_followup(
            int(renewal_cycle_id),
            int(principal["user_id"]),
            channel=channel.strip().upper(),
            summary=situation.strip(),
            intention=None,
            needs_support=False,
            next_action=_optional_text(next_action),
            next_followup_at=_optional_text(next_followup_at),
            allow_brief_summary=True,
        )
    return {
        "record_id": int(followup_id),
        "renewal_cycle_id": int(renewal_cycle_id),
        "today_actions": _today_actions_for_principal(principal),
    }


def complete_mobile_birthday_care(
    session: dict[str, Any],
    *,
    member_id: int,
    due_date: str,
    channel: str,
    operation_item_id: int | None,
) -> dict[str, Any]:
    principal = _principal(session)
    _require_permission(principal, "members:detail_view")
    _require_permission(principal, "followups:manage")
    if not _member_in_permission_scope(principal, member_id, "followups:manage"):
        raise PermissionError("学长不在当前关爱授权范围内")
    try:
        birthday_year = date.fromisoformat(due_date[:10]).year
    except (TypeError, ValueError) as exc:
        raise ValueError("生日关怀日期无效") from exc
    scoped_principal = _source_principal(
        principal, "members:detail_view", extra_permissions={"followups:manage"}
    )
    with mobile_iam_context(scoped_principal, "members:detail_view"):
        result = complete_birthday_care(
            member_id,
            int(principal["user_id"]),
            birthday_year=birthday_year,
            due_date=due_date,
            channel=channel.strip().upper(),
            operation_item_id=operation_item_id,
        )
    return {**result, "today_actions": _today_actions_for_principal(principal)}


def reveal_member_contact(
    session: dict[str, Any], *, member_id: int, purpose: str
) -> dict[str, Any]:
    """Reveal a single full phone only through the existing audited domain flow."""

    principal = _principal(session)
    _require_permission(principal, "contact:reveal")
    _require_permission(principal, "followups:manage")
    if not _member_in_permission_scope(principal, member_id, "contact:reveal"):
        raise PermissionError("学长不在当前联系方式授权范围内")
    task_id, created_task = _get_or_create_care_task(
        principal,
        member_id=member_id,
        service_purpose=purpose.strip(),
        due_at=None,
        require_unexpired=True,
    )
    scoped_principal = _source_principal(
        principal, "followups:manage", extra_permissions={"contact:reveal"}
    )
    with mobile_iam_context(scoped_principal, "followups:manage"):
        data = reveal_contact(
            member_id=member_id,
            task_id=task_id,
            actor_user_id=int(principal["user_id"]),
            purpose=purpose.strip(),
            client_reference="wechat-mobile-operations",
        )
    # The caller must keep this value only in current page memory; it is never
    # placed in workbench/search/detail payloads or returned by list APIs.
    return {
        "name": data["name"],
        "phone": data["phone"],
        "expires_in": data["expires_in"],
        "task_id": task_id,
        "created_task": created_task,
    }


# The older task and study-meeting routes remain for existing deep links. They
# are no longer V1 workbench entries, but retain the same live IAM2 boundary.
def followup_tasks(session: dict[str, Any], *, status: str | None = None) -> list[dict[str, Any]]:
    principal = _principal(session)
    _require_permission(principal, "followups:manage")
    scoped_principal = _source_principal(principal, "followups:manage")
    with mobile_iam_context(scoped_principal, "followups:manage"):
        return list_tasks(int(principal["user_id"]), status=status)


def record_followup(
    session: dict[str, Any],
    *,
    task_id: int,
    channel: str,
    contacted_at: str,
    outcome_code: str,
    subject_statement: str | None,
    objective_facts: str | None,
    staff_judgment: str | None,
    next_action: str | None,
    next_followup_at: str | None,
) -> dict[str, int]:
    principal = _principal(session)
    _require_permission(principal, "followups:manage")
    scoped_principal = _source_principal(principal, "followups:manage")
    with mobile_iam_context(scoped_principal, "followups:manage"):
        record_id = add_followup_record(
            task_id,
            int(principal["user_id"]),
            channel=channel,
            contacted_at=contacted_at,
            outcome_code=outcome_code,
            subject_statement=subject_statement,
            objective_facts=objective_facts,
            staff_judgment=staff_judgment,
            next_action=next_action,
            next_followup_at=next_followup_at,
        )
    return {"id": int(record_id)}


def study_meeting_records(session: dict[str, Any]) -> list[dict[str, Any]]:
    principal = _principal(session)
    _require_permission(principal, "plans:read")
    scoped_principal = _source_principal(principal, "plans:read")
    with mobile_iam_context(scoped_principal, "plans:read"):
        return list_study_meeting_records(actor_user_id=int(principal["user_id"]))
