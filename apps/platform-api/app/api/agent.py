from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.auth import current_user
from app.services.agent import (
    AgentContext,
    AgentDisabledError,
    AGENT_TOOL_POLICIES,
    authorize_agent_client,
    invoke_agent_tool,
    tool_manifest,
)


router = APIRouter(tags=["agent-readonly"])


class AgentPrincipal(BaseModel):
    user_id: int
    context: AgentContext


def agent_principal(
    user: dict = Depends(current_user),
    client_id: str | None = Header(default=None, alias="X-Agent-Client-ID"),
    client_secret: str | None = Header(default=None, alias="X-Agent-Client-Secret"),
    channel: str = Header(default="api", alias="X-Agent-Channel"),
    session_id: str | None = Header(default=None, alias="X-Agent-Session-ID"),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> AgentPrincipal:
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
        raise HTTPException(404, "Agent 接入层不存在或尚未启用") from exc
    except PermissionError as exc:
        # Do not disclose whether the client id, secret or channel was the
        # failing credential. The operator JWT has already been validated.
        raise HTTPException(401, "Agent 客户端认证失败") from exc
    except ValueError as exc:
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
            "tools": tool_manifest(),
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
    name: str = Query(min_length=1, max_length=100),
    phone: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=20, ge=1, le=50),
    principal: AgentPrincipal = Depends(agent_principal),
) -> dict[str, Any]:
    return _data_response(
        principal,
        _invoke(
            principal,
            "find_member",
            {"name": name, "phone": phone, "limit": limit},
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
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] | None = Field(default=None)


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
        raise HTTPException(400, "仅支持 JSON-RPC 2.0")
    params = payload.params or {}
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
            "result": {"tools": tool_manifest()},
        }
    if payload.method == "tools/call":
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(payload.id, -32602, "arguments 必须是对象")
        if tool_name not in AGENT_TOOL_POLICIES:
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
    return _rpc_error(payload.id, -32601, "未知 MCP 方法")
