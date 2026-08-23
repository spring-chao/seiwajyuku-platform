from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from app.core.security import decode_token
from app.services.iam import user_context
from app.services.agent import (
    AgentContext,
    AgentDisabledError,
    AGENT_TOOL_POLICIES,
    audit_agent_auth_event,
    audit_agent_tool_event,
    authorize_agent_client,
    invoke_agent_tool,
    tool_manifest,
)


router = APIRouter(tags=["agent-readonly"])
agent_bearer = HTTPBearer(auto_error=False)


class AgentPrincipal(BaseModel):
    user_id: int
    context: AgentContext


def agent_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(agent_bearer),
    client_id: str | None = Header(default=None, alias="X-Agent-Client-ID"),
    client_secret: str | None = Header(default=None, alias="X-Agent-Client-Secret"),
    channel: str = Header(default="api", alias="X-Agent-Channel"),
    session_id: str | None = Header(default=None, alias="X-Agent-Session-ID"),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> AgentPrincipal:
    if not credentials:
        audit_agent_auth_event(
            actor_user_id=None,
            client_id=client_id,
            channel=channel,
            session_id=session_id,
            request_id=request_id,
            result="AUTH_FAILED",
        )
        raise HTTPException(401, "需要登录")
    try:
        payload = decode_token(credentials.credentials, "access")
        actor_user_id = int(payload["sub"])
        user = user_context(actor_user_id)
    except (KeyError, TypeError, ValueError):
        audit_agent_auth_event(
            actor_user_id=None,
            client_id=client_id,
            channel=channel,
            session_id=session_id,
            request_id=request_id,
            result="AUTH_FAILED",
        )
        raise HTTPException(401, "登录已失效")
    if not user or int(payload.get("ver", -1)) != int(user["token_version"]):
        audit_agent_auth_event(
            actor_user_id=actor_user_id,
            client_id=client_id,
            channel=channel,
            session_id=session_id,
            request_id=request_id,
            result="AUTH_FAILED",
        )
        raise HTTPException(401, "账户或令牌已失效")
    try:
        context = authorize_agent_client(
            user=user,
            client_id=client_id or "",
            client_secret=client_secret or "",
            channel=channel,
            session_id=session_id,
            request_id=request_id,
        )
    except AgentDisabledError as exc:
        audit_agent_auth_event(
            actor_user_id=actor_user_id,
            client_id=client_id,
            channel=channel,
            session_id=session_id,
            request_id=request_id,
            result="DISABLED",
        )
        raise HTTPException(404, "Agent 接入层不存在或尚未启用") from exc
    except PermissionError as exc:
        # Do not disclose whether the client id, secret or channel was the
        # failing credential. The operator JWT has already been validated.
        audit_agent_auth_event(
            actor_user_id=actor_user_id,
            client_id=client_id,
            channel=channel,
            session_id=session_id,
            request_id=request_id,
            result="AUTH_FAILED",
        )
        raise HTTPException(401, "Agent 客户端认证失败") from exc
    except ValueError as exc:
        audit_agent_auth_event(
            actor_user_id=actor_user_id,
            client_id=client_id,
            channel=channel,
            session_id=session_id,
            request_id=request_id,
            result="INVALID_ARGUMENT",
        )
        raise HTTPException(400, str(exc)) from exc
    return AgentPrincipal(user_id=context.actor_user_id, context=context)


def _data_response(principal: AgentPrincipal, data: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data": data,
        "meta": {
            "request_id": principal.context.request_id,
            "read_only": True,
        },
    }


def _invoke(
    principal: AgentPrincipal, tool_name: str, arguments: dict[str, Any]
) -> Any:
    try:
        return invoke_agent_tool(principal.context, tool_name, arguments)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/agent/v1/manifest")
def manifest(principal: AgentPrincipal = Depends(agent_principal)) -> dict[str, Any]:
    return _data_response(
        principal,
        {
            "name": "seiwajyuku-agent-readonly",
            "version": "AI-1.3",
            "transport": {"rest": "/api/agent/v1", "mcp": "/mcp/seiwajuku"},
            "write_enabled": False,
            "tools": tool_manifest(principal.context.allowed_tools),
            "excluded_tools": {
                "reason": "AI-1.3 只读阶段，所有新增、修改、删除、联系方式展开和批量导出均未开放",
                "examples": [
                    "create_followup_record",
                    "schedule_next_followup",
                    "create_member",
                    "update_member",
                    "reveal_contact",
                ],
            },
        },
    )


@router.get("/api/agent/v1/today-actions")
def today_actions(
    as_of: str | None = Query(default=None),
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    arguments = {"as_of": as_of} if as_of else {}
    return _data_response(
        principal, _invoke(principal, "get_my_today_actions", arguments)
    )


@router.get("/api/agent/v1/members/search")
def member_search(
    request: Request,
    name: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    if "phone" in request.query_params:
        audit_agent_tool_event(
            principal.context,
            "find_member",
            result="INVALID_ARGUMENT",
        )
        raise HTTPException(400, "手机号匹配必须使用 POST Body")
    return _data_response(
        principal,
        _invoke(
            principal,
            "find_member",
            {"name": name, "limit": limit},
        ),
    )


class MemberMatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)
    limit: int = Field(default=20, ge=1, le=50)


@router.post("/api/agent/v1/members/match")
def member_match(
    payload: MemberMatchPayload,
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    return _data_response(
        principal,
        _invoke(
            principal,
            "find_member",
            payload.model_dump(exclude_none=True),
        ),
    )


@router.get("/api/agent/v1/members/{member_id}/summary")
def member_summary(
    member_id: int,
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    return _data_response(
        principal,
        _invoke(principal, "get_member_summary", {"member_id": member_id}),
    )


@router.get("/api/agent/v1/members/{member_id}/timeline")
def member_timeline(
    member_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    return _data_response(
        principal,
        _invoke(
            principal,
            "get_member_timeline",
            {"member_id": member_id, "limit": limit},
        ),
    )


@router.get("/api/agent/v1/members/{member_id}/renewal-context")
def renewal_context(
    member_id: int,
    year: int | None = Query(default=None, ge=2020, le=2100),
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"member_id": member_id}
    if year is not None:
        arguments["year"] = year
    return _data_response(
        principal, _invoke(principal, "get_renewal_context", arguments)
    )


@router.get("/api/agent/v1/members/{member_id}/followup-context")
def followup_context(
    member_id: int,
    limit: int = Query(default=20, ge=1, le=50),
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    return _data_response(
        principal,
        _invoke(
            principal,
            "get_followup_context",
            {"member_id": member_id, "limit": limit},
        ),
    )


class JsonRpcPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: Any = Field(default=None)


def _rpc_error(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


@router.post("/mcp/seiwajuku", response_model=None)
def mcp_endpoint(
    payload: JsonRpcPayload,
    principal: AgentPrincipal = Depends(agent_principal),
) -> Any:
    if payload.jsonrpc != "2.0":
        audit_agent_tool_event(principal.context, "mcp:invalid_request", result="INVALID_ARGUMENT")
        raise HTTPException(400, "仅支持 JSON-RPC 2.0")
    if payload.params is None:
        params: dict[str, Any] = {}
    elif isinstance(payload.params, dict):
        params = payload.params
    else:
        audit_agent_tool_event(principal.context, "mcp:invalid_params", result="INVALID_ARGUMENT")
        return _rpc_error(payload.id, -32602, "params 必须是对象")
    if payload.method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "seiwajyuku-agent-readonly",
                    "version": "AI-1.3",
                },
            },
        }
    if payload.method == "notifications/initialized":
        return Response(status_code=204)
    if payload.method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {"tools": tool_manifest(principal.context.allowed_tools)},
        }
    if payload.method == "tools/call":
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            audit_agent_tool_event(principal.context, tool_name or "mcp:tools_call", result="INVALID_ARGUMENT")
            return _rpc_error(payload.id, -32602, "arguments 必须是对象")
        if tool_name not in AGENT_TOOL_POLICIES:
            audit_agent_tool_event(principal.context, tool_name or "mcp:tools_call", result="UNKNOWN_TOOL")
            return _rpc_error(payload.id, -32601, "未知 Agent 工具")
        try:
            data = invoke_agent_tool(principal.context, tool_name, arguments)
        except PermissionError:
            return _rpc_error(payload.id, -32003, "当前操作者没有调用该工具的权限")
        except ValueError as exc:
            return _rpc_error(payload.id, -32602, str(exc))
        text = json.dumps(data, ensure_ascii=False, default=str)
        return {
            "jsonrpc": "2.0",
            "id": payload.id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": {"success": True, "data": data},
            },
        }
    audit_agent_tool_event(
        principal.context,
        f"mcp:{payload.method}",
        result="UNKNOWN_METHOD",
    )
    return _rpc_error(payload.id, -32601, "未知 MCP 方法")
