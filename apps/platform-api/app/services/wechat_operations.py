"""Worker mini-program operations backed by live IAM2 authorization.

This is intentionally a thin adapter over existing business services.  It
does not copy roles, scopes, member facts, or follow-up facts into a mobile
permission/data model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.settings import get_settings
from app.services.followups import add_followup_record, list_tasks
from app.services.iam import mobile_iam_context, resolve_employee_mobile_principal
from app.services.members import search_members
from app.services.renewals import list_today_actions as list_renewal_today_actions
from app.services.study_meetings import list_study_meeting_records
from app.services.wechat_identity import WeChatIdentityError


MOBILE_ENTRIES = (
    {
        "key": "today_actions",
        "name": "今日行动",
        "description": "查看当前授权范围内的续费与关怀待办",
        "permissions": frozenset({"followups:manage", "renewals:read"}),
    },
    {
        "key": "member_search",
        "name": "学长查询",
        "description": "按当前组织授权范围快速查询学长",
        "permissions": frozenset({"members:read"}),
    },
    {
        "key": "followup_records",
        "name": "关怀跟进",
        "description": "查看任务并记录服务过程",
        "permissions": frozenset({"followups:manage"}),
    },
    {
        "key": "study_meetings",
        "name": "学习会记录",
        "description": "查看当前授权范围内的学习会事实记录",
        "permissions": frozenset({"plans:read"}),
    },
)


def _require_enabled() -> None:
    if not get_settings().wechat_staff_mobile_operations_enabled:
        raise WeChatIdentityError("工作人员移动运营功能尚未开启")


def _principal(session: dict[str, Any]) -> dict[str, Any]:
    _require_enabled()
    principal = resolve_employee_mobile_principal(
        str(session.get("person_id") or ""),
        verified_user_id=session.get("verified_user_id"),
    )
    if not principal:
        raise PermissionError("当前微信身份没有有效的工作人员移动运营权限")
    return principal


def _require_permission(principal: dict[str, Any], permission: str) -> None:
    if permission not in principal["permissions"]:
        raise PermissionError("当前工作人员账号没有此移动操作权限")


def _entry_payload(entry: dict[str, Any]) -> dict[str, str]:
    return {
        "key": entry["key"],
        "name": entry["name"],
        "description": entry["description"],
    }


def operation_workbench(session: dict[str, Any]) -> dict[str, Any]:
    principal = _principal(session)
    permissions = set(principal["permissions"])
    return {
        "worker_name": principal["display_name"],
        "entries": [
            _entry_payload(entry)
            for entry in MOBILE_ENTRIES
            if permissions.intersection(entry["permissions"])
        ],
    }


def today_actions(session: dict[str, Any]) -> dict[str, Any]:
    principal = _principal(session)
    permissions = set(principal["permissions"])
    if not permissions.intersection({"followups:manage", "renewals:read"}):
        raise PermissionError("当前工作人员账号没有今日行动查看权限")
    items: list[dict[str, Any]] = []
    if "followups:manage" in permissions:
        with mobile_iam_context(principal, "followups:manage"):
            today = datetime.now(UTC).date()
            for task in list_tasks(principal["user_id"]):
                raw_due = task.get("next_followup_at") or task.get("due_at")
                try:
                    due = datetime.fromisoformat(str(raw_due).replace("Z", "+00:00")).date()
                except (TypeError, ValueError):
                    continue
                if due <= today and str(task.get("status") or "").upper() in {"OPEN", "IN_PROGRESS"}:
                    items.append(
                        {
                            "source": "关怀任务",
                            "id": int(task["id"]),
                            "member_name": task["member_name"],
                            "label": task["service_purpose"],
                            "due_date": due.isoformat(),
                            "status": task["status"],
                            "can_record": bool(task.get("can_record")),
                        }
                    )
    if "renewals:read" in permissions:
        with mobile_iam_context(principal, "renewals:read"):
            renewal_data = list_renewal_today_actions(
                principal["user_id"], datetime.now(UTC).year
            )
            for item in renewal_data.get("items", []):
                items.append(
                    {
                        "source": "续费关爱",
                        "id": int(item["cycle_id"]),
                        "member_name": item["member_name"],
                        "label": item["stage_label"],
                        "due_date": item.get("next_followup_at"),
                        "status": item.get("status"),
                        "can_record": False,
                    }
                )
    items.sort(key=lambda item: (str(item.get("due_date") or "9999-12-31"), item["source"], item["id"]))
    return {"items": items, "total": len(items)}


def member_search(session: dict[str, Any], *, name: str, limit: int = 20) -> list[dict[str, Any]]:
    principal = _principal(session)
    _require_permission(principal, "members:read")
    with mobile_iam_context(principal, "members:read"):
        return search_members(principal["user_id"], name=name, limit=limit)


def followup_tasks(session: dict[str, Any], *, status: str | None = None) -> list[dict[str, Any]]:
    principal = _principal(session)
    _require_permission(principal, "followups:manage")
    with mobile_iam_context(principal, "followups:manage"):
        return list_tasks(principal["user_id"], status=status)


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
    with mobile_iam_context(principal, "followups:manage"):
        record_id = add_followup_record(
            task_id,
            principal["user_id"],
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
    with mobile_iam_context(principal, "plans:read"):
        return list_study_meeting_records(actor_user_id=principal["user_id"])
