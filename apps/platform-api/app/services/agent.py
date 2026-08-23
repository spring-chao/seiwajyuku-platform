from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Callable
from uuid import uuid4

from app.core.privacy import normalize_phone, phone_hash
from app.db import fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.followups import list_tasks
from app.services.iam import accessible_org_ids, user_context
from app.services.members import (
    CURRENT_STUDY_CLASS_NAME_SQL,
    CURRENT_STUDY_GROUP_NAME_SQL,
    can_access_member,
    get_member_detail,
    get_member_timeline,
    resolve_member_scope,
)
from app.services.member_care_actions import build_member_care_actions
from app.services.renewals import determine_renewal_stage, list_cycles


class AgentDisabledError(PermissionError):
    """Raised when the optional agent surface is intentionally not enabled."""


@dataclass(frozen=True)
class AgentContext:
    actor_user_id: int
    agent_client_id: str
    channel: str
    session_id: str | None
    request_id: str


_PHONE_IN_TEXT_RE = re.compile(r"(?<!\d)1[3-9](?:[\s-]?\d){9}(?!\d)")
_PRECISE_AMOUNT_RE = re.compile(
    r"(?<!\d)(?:人民币\s*|RMB\s*|[￥¥]\s*)?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|"
    r"[零〇一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟萬]+)"
    r"\s*(?:亿元|万元|元|亿|万)(?![\d元])",
    re.IGNORECASE,
)


def _redact_agent_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = _PHONE_IN_TEXT_RE.sub("[手机号已脱敏]", text)
    return _PRECISE_AMOUNT_RE.sub("[明确金额已脱敏]", text)


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式") from exc


AGENT_TOOL_POLICIES: dict[str, dict[str, Any]] = {
    "get_my_today_actions": {
        "description": "按当前运营账号的组织范围返回今日学长关爱行动。",
        "risk_level": "L0",
        "read_only": True,
        "required_permissions": ["org:read"],
        "input_schema": {
            "type": "object",
            "properties": {
                "as_of": {
                    "type": "string",
                    "description": "可选，YYYY-MM-DD；仅用于回放和测试。",
                }
            },
            "additionalProperties": False,
        },
    },
    "find_member": {
        "description": "在当前组织授权范围内按姓名和可选手机号匹配学长，返回脱敏摘要。",
        "risk_level": "L0",
        "read_only": True,
        "required_permissions": ["members:read"],
        "input_schema": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 100},
                "phone": {
                    "type": "string",
                    "description": "可选完整手机号，仅用于服务端匹配，不会回显或写入审计。",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
    "get_member_summary": {
        "description": "返回一个学长的基础资料，手机号保持脱敏。",
        "risk_level": "L0",
        "read_only": True,
        "required_permissions": ["members:detail_view"],
        "input_schema": {
            "type": "object",
            "required": ["member_id"],
            "properties": {"member_id": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        },
    },
    "get_member_timeline": {
        "description": "返回一个学长的隐私安全服务时间线和活动元数据。",
        "risk_level": "L0",
        "read_only": True,
        "required_permissions": ["members:detail_view"],
        "input_schema": {
            "type": "object",
            "required": ["member_id"],
            "properties": {
                "member_id": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    "get_renewal_context": {
        "description": "返回一个学长指定年度的续费周期、阶段和最近一次跟进上下文。",
        "risk_level": "L0",
        "read_only": True,
        "required_permissions": ["renewals:read"],
        "input_schema": {
            "type": "object",
            "required": ["member_id"],
            "properties": {
                "member_id": {"type": "integer", "minimum": 1},
                "year": {"type": "integer", "minimum": 2020, "maximum": 2100},
            },
            "additionalProperties": False,
        },
    },
    "get_followup_context": {
        "description": "返回一个学长可见的关怀任务及联系记录，文本会做手机号和明确金额脱敏。",
        "risk_level": "L0",
        "read_only": True,
        "required_permissions": ["followups:manage"],
        "input_schema": {
            "type": "object",
            "required": ["member_id"],
            "properties": {
                "member_id": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
    },
}


def tool_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": policy["description"],
            "inputSchema": policy["input_schema"],
            "annotations": {
                "readOnlyHint": policy["read_only"],
                "riskLevel": policy["risk_level"],
            },
        }
        for name, policy in AGENT_TOOL_POLICIES.items()
    ]


def authorize_agent_client(
    *,
    user: dict[str, Any],
    client_id: str,
    client_secret: str,
    channel: str,
    session_id: str | None,
    request_id: str | None,
) -> AgentContext:
    from app.core.settings import get_settings

    settings = get_settings()
    if not settings.agent_api_enabled:
        raise AgentDisabledError("Agent 接入层尚未启用")
    if not client_id or not settings.agent_client_id:
        raise PermissionError("缺少 Agent 客户端身份")
    if len(client_id) > 128:
        raise ValueError("Agent 客户端标识过长")
    if not hmac.compare_digest(client_id, settings.agent_client_id):
        raise PermissionError("Agent 客户端身份无效")
    if not settings.agent_client_secret or not hmac.compare_digest(
        client_secret or "", settings.agent_client_secret
    ):
        raise PermissionError("Agent 客户端凭证无效")
    channel = (channel or "api").strip().lower()
    if channel not in settings.agent_allowed_channels:
        raise PermissionError("Agent 渠道未获允许")
    if session_id is not None and len(session_id) > 128:
        raise ValueError("Agent 会话标识过长")
    if request_id is not None and len(request_id) > 128:
        raise ValueError("请求标识过长")
    if not user or not user.get("id"):
        raise PermissionError("操作者身份无效")
    return AgentContext(
        actor_user_id=int(user["id"]),
        agent_client_id=client_id,
        channel=channel,
        session_id=session_id,
        request_id=(request_id or f"agent-{uuid4().hex}").strip()[:128],
    )


def _require_tool_access(context: AgentContext, tool_name: str) -> None:
    policy = AGENT_TOOL_POLICIES.get(tool_name)
    if not policy:
        raise ValueError("未知 Agent 工具")
    if not policy["read_only"]:
        raise PermissionError("当前 Agent 接入层禁止写工具")
    user = user_context(context.actor_user_id)
    if not user:
        raise PermissionError("操作者账号已失效")
    missing = set(policy["required_permissions"]) - set(user["permissions"])
    if missing:
        raise PermissionError("当前操作者没有调用该工具的权限")


def _audit_agent_tool(
    context: AgentContext,
    tool_name: str,
    *,
    result: str,
    resource_type: str = "agent_tool",
    resource_id: str | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "agent_client_id": context.agent_client_id,
        "channel": context.channel,
        "session_id": context.session_id,
        "tool_name": tool_name,
        "read_only": True,
    }
    if after:
        metadata.update(after)
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=context.actor_user_id,
            action="agent.tool.invoke",
            resource_type=resource_type,
            resource_id=resource_id,
            purpose="Agent 只读运营查询",
            result=result,
            after=metadata,
            request_id=context.request_id,
        )


def _member_scope(member_id: int, actor_user_id: int) -> tuple[dict[str, Any], str]:
    member = fetch_one(
        "SELECT m.id, m.name, m.org_unit_id, o.name AS org_name "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id WHERE m.id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学长不存在")
    allowed = accessible_org_ids(actor_user_id)
    try:
        scoped_org_id = resolve_member_scope(member_id, member["org_unit_id"], allowed)
    except PermissionError as exc:
        raise PermissionError("学长不在组织授权范围内") from exc
    return member, scoped_org_id


def find_members(
    actor_user_id: int,
    *,
    name: str,
    phone: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("姓名不能为空且不能超过100个字符")
    if not 1 <= limit <= 50:
        raise ValueError("匹配条数必须在1至50之间")
    phone_digest = None
    if phone is not None and phone.strip():
        phone_digest = phone_hash(normalize_phone(phone))
    conditions = ["m.name LIKE ?"]
    params: list[Any] = [f"%{name}%"]
    if phone_digest:
        conditions.append("m.phone_hash=?")
        params.append(phone_digest)
    rows = fetch_all(
        "SELECT m.id, m.name, m.org_unit_id, o.name AS org_name, m.status, "
        "m.phone_masked, "
        f"{CURRENT_STUDY_CLASS_NAME_SQL} AS class_name, "
        f"{CURRENT_STUDY_GROUP_NAME_SQL} AS group_name "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY CASE WHEN m.name=? THEN 0 ELSE 1 END, m.name, m.id LIMIT ?",
        (*params, name, limit),
    )
    allowed = accessible_org_ids(actor_user_id)
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            scoped_org_id = resolve_member_scope(int(row["id"]), row["org_unit_id"], allowed)
        except PermissionError:
            continue
        scoped_org = fetch_one("SELECT name FROM org_units WHERE id=?", (scoped_org_id,))
        result.append(
            {
                "member_id": int(row["id"]),
                "name": row["name"],
                "org_unit_id": scoped_org_id,
                "org_name": scoped_org["name"] if scoped_org else row["org_name"],
                "class_name": row["class_name"],
                "group_name": row["group_name"],
                "status": row["status"],
                "phone_masked": row["phone_masked"],
            }
        )
    return result


def get_agent_renewal_context(
    actor_user_id: int, *, member_id: int, year: int | None = None
) -> dict[str, Any]:
    _member_scope(member_id, actor_user_id)
    current_year = year or datetime.now(UTC).year
    if not 2020 <= int(current_year) <= 2100:
        raise ValueError("续费年度无效")
    cycles = [
        row
        for row in list_cycles(
            actor_user_id,
            int(current_year),
            renewal_status="ALL",
            include_past=True,
        )
        if int(row["member_id"]) == int(member_id)
    ]
    if not cycles:
        return {"year": int(current_year), "member_id": member_id, "cycles": []}
    cycle_ids = [int(row["id"]) for row in cycles]
    placeholders = ",".join("?" for _ in cycle_ids)
    followups = fetch_all(
        "SELECT renewal_cycle_id, followed_at, channel, summary, intention, "
        "needs_support, next_action, next_followup_at "
        f"FROM renewal_followups WHERE renewal_cycle_id IN ({placeholders}) "
        "ORDER BY followed_at DESC, id DESC",
        tuple(cycle_ids),
    )
    latest_by_cycle: dict[int, dict[str, Any]] = {}
    for followup in followups:
        cycle_key = int(followup["renewal_cycle_id"])
        latest_by_cycle.setdefault(
            cycle_key,
            {
                "followed_at": followup["followed_at"],
                "channel": followup["channel"],
                "summary": _redact_agent_text(followup.get("summary")),
                "intention": followup.get("intention"),
                "needs_support": bool(followup.get("needs_support")),
                "next_action": _redact_agent_text(followup.get("next_action")),
                "next_followup_at": followup.get("next_followup_at"),
            },
        )
    output: list[dict[str, Any]] = []
    for cycle in cycles:
        output.append(
            {
                "cycle": {
                    "id": cycle["id"],
                    "renewal_year": cycle["renewal_year"],
                    "due_month": cycle["due_month"],
                    "status": cycle["status"],
                    "phase": cycle["phase"],
                    "result": cycle["result"],
                    "assigned_user_id": cycle["assigned_user_id"],
                    "assigned_user_name": cycle["assigned_user_name"],
                },
                "member": {
                    "id": cycle["member_id"],
                    "name": cycle["member_name"],
                    "org_unit_id": cycle["org_unit_id"],
                    "org_name": cycle["org_name"],
                    "class_name": cycle["member_class_name"],
                    "group_name": cycle["member_group_name"],
                },
                "stage": determine_renewal_stage(
                    cycle["renewal_year"], cycle["due_month"], cycle["status"]
                ),
                "latest_followup": latest_by_cycle.get(int(cycle["id"])),
            }
        )
    return {"year": int(current_year), "member_id": member_id, "cycles": output}


def get_agent_followup_context(
    actor_user_id: int, *, member_id: int, limit: int = 20
) -> dict[str, Any]:
    if not 1 <= limit <= 50:
        raise ValueError("关怀记录条数必须在1至50之间")
    member, scoped_org_id = _member_scope(member_id, actor_user_id)
    visible_tasks = [
        row for row in list_tasks(actor_user_id) if int(row["member_id"]) == int(member_id)
    ]
    visible_tasks = visible_tasks[:limit]
    task_ids = [int(row["id"]) for row in visible_tasks]
    tasks = [
        {
            "id": row["id"],
            "member_id": row["member_id"],
            "org_unit_id": row["org_unit_id"],
            "org_name": row["org_name"],
            "task_type": row["task_type"],
            "status": row["status"],
            "due_at": row["due_at"],
            "next_followup_at": row["next_followup_at"],
            "assigned_user_id": row["assigned_user_id"],
            "assignee_name": row["assignee_name"],
        }
        for row in visible_tasks
    ]
    records: list[dict[str, Any]] = []
    visits: list[dict[str, Any]] = []
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        record_rows = fetch_all(
            "SELECT id, task_id, channel, contacted_at, outcome_code, subject_statement, "
            "objective_facts, staff_judgment, next_action, next_followup_at "
            f"FROM followup_records WHERE member_id=? AND task_id IN ({placeholders}) "
            "ORDER BY contacted_at DESC, id DESC LIMIT ?",
            (member_id, *task_ids, limit),
        )
        records = [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "channel": row["channel"],
                "contacted_at": row["contacted_at"],
                "outcome_code": row["outcome_code"],
                "subject_statement": _redact_agent_text(row.get("subject_statement")),
                "objective_facts": _redact_agent_text(row.get("objective_facts")),
                "staff_judgment": _redact_agent_text(row.get("staff_judgment")),
                "next_action": _redact_agent_text(row.get("next_action")),
                "next_followup_at": row["next_followup_at"],
            }
            for row in record_rows
        ]
        visit_rows = fetch_all(
            "SELECT id, task_id, visited_at, location_type, objective_facts, "
            "expressed_needs, support_provided, staff_judgment, next_action, next_followup_at "
            f"FROM enterprise_visit_records WHERE member_id=? AND task_id IN ({placeholders}) "
            "ORDER BY visited_at DESC, id DESC LIMIT ?",
            (member_id, *task_ids, limit),
        )
        visits = [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "visited_at": row["visited_at"],
                "location_type": row["location_type"],
                "objective_facts": _redact_agent_text(row.get("objective_facts")),
                "expressed_needs": _redact_agent_text(row.get("expressed_needs")),
                "support_provided": _redact_agent_text(row.get("support_provided")),
                "staff_judgment": _redact_agent_text(row.get("staff_judgment")),
                "next_action": _redact_agent_text(row.get("next_action")),
                "next_followup_at": row["next_followup_at"],
            }
            for row in visit_rows
        ]
    return {
        "member": {
            "id": member["id"],
            "name": member["name"],
            "org_unit_id": scoped_org_id,
            "org_name": (
                fetch_one("SELECT name FROM org_units WHERE id=?", (scoped_org_id,))
                or {"name": member["org_name"]}
            )["name"],
        },
        "tasks": tasks,
        "records": records,
        "visits": visits,
    }


def _invoke_get_my_today_actions(context: AgentContext, arguments: dict[str, Any]) -> Any:
    return build_member_care_actions(
        context.actor_user_id, as_of=_date_value(arguments.get("as_of"))
    )


def _invoke_find_member(context: AgentContext, arguments: dict[str, Any]) -> Any:
    return find_members(
        context.actor_user_id,
        name=str(arguments.get("name") or ""),
        phone=arguments.get("phone"),
        limit=int(arguments.get("limit") or 20),
    )


def _invoke_member_summary(context: AgentContext, arguments: dict[str, Any]) -> Any:
    return get_member_detail(int(arguments["member_id"]), context.actor_user_id)


def _invoke_member_timeline(context: AgentContext, arguments: dict[str, Any]) -> Any:
    return get_member_timeline(
        int(arguments["member_id"]),
        context.actor_user_id,
        limit=int(arguments.get("limit") or 100),
    )


def _invoke_renewal_context(context: AgentContext, arguments: dict[str, Any]) -> Any:
    return get_agent_renewal_context(
        context.actor_user_id,
        member_id=int(arguments["member_id"]),
        year=int(arguments["year"]) if arguments.get("year") is not None else None,
    )


def _invoke_followup_context(context: AgentContext, arguments: dict[str, Any]) -> Any:
    return get_agent_followup_context(
        context.actor_user_id,
        member_id=int(arguments["member_id"]),
        limit=int(arguments.get("limit") or 20),
    )


_TOOL_HANDLERS: dict[str, Callable[[AgentContext, dict[str, Any]], Any]] = {
    "get_my_today_actions": _invoke_get_my_today_actions,
    "find_member": _invoke_find_member,
    "get_member_summary": _invoke_member_summary,
    "get_member_timeline": _invoke_member_timeline,
    "get_renewal_context": _invoke_renewal_context,
    "get_followup_context": _invoke_followup_context,
}


def invoke_agent_tool(
    context: AgentContext, tool_name: str, arguments: dict[str, Any] | None = None
) -> Any:
    arguments = arguments or {}
    _require_tool_access(context, tool_name)
    handler = _TOOL_HANDLERS.get(tool_name)
    if not handler:
        raise ValueError("当前 Agent 工具尚未实现")
    try:
        result = handler(context, arguments)
    except PermissionError:
        _audit_agent_tool(context, tool_name, result="DENIED")
        raise
    except ValueError:
        _audit_agent_tool(context, tool_name, result="INVALID_ARGUMENT")
        raise
    _audit_agent_tool(
        context,
        tool_name,
        result="SUCCESS",
        after={
            "result_type": type(result).__name__,
            "result_count": len(result) if isinstance(result, (list, dict)) else None,
        },
    )
    return result
