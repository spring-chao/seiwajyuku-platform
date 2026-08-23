from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from starlette.requests import Request

from app.api.agent import AgentPrincipal
from app.services.agent import (
    AGENT_TOOL_POLICIES,
    audit_agent_tool_event,
    invoke_agent_tool,
    tool_manifest,
)


MCP_MOUNT_PATH = "/mcp"
MCP_PATH = f"{MCP_MOUNT_PATH}/seiwajuku"


# The official SDK owns the HTTP transport, JSON-RPC framing, protocol-version
# negotiation, session headers, POST handling, and GET stream handling. The
# application keeps the authorization boundary outside the SDK so every HTTP
# request still carries a validated operator JWT and Agent client credential.
mcp_server = FastMCP(
    name="seiwajyuku-agent-readonly",
    instructions="盛和塾运营平台只读 Agent。所有工具均按当前运营账号组织范围执行。",
    streamable_http_path="/seiwajuku",
    # JSON responses are an allowed Streamable HTTP response mode and make
    # connector interoperability deterministic while GET remains available
    # for the session stream.
    json_response=True,
    stateless_http=False,
    host="0.0.0.0",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


def _current_principal() -> AgentPrincipal:
    request_context = mcp_server._mcp_server.request_context  # noqa: SLF001
    request = request_context.request
    if not isinstance(request, Request):
        raise PermissionError("MCP 请求缺少已验证的 Agent 身份")
    principal = getattr(request.state, "agent_principal", None)
    if not isinstance(principal, AgentPrincipal):
        raise PermissionError("MCP 请求缺少已验证的运营账号")
    return principal


def _tool_definitions(principal: AgentPrincipal) -> list[Tool]:
    definitions: list[Tool] = []
    for item in tool_manifest(principal.context.allowed_tools):
        policy = AGENT_TOOL_POLICIES[item["name"]]
        definitions.append(
            Tool(
                name=item["name"],
                description=item["description"],
                inputSchema=item["inputSchema"],
                annotations=ToolAnnotations(
                    readOnlyHint=bool(policy["read_only"]),
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        )
    return definitions


def _error_result(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,
    )


@mcp_server._mcp_server.list_tools()  # noqa: SLF001
async def list_tools() -> list[Tool]:
    """Return only the tools allowed for the authenticated operator/client."""
    return _tool_definitions(_current_principal())


@mcp_server._mcp_server.call_tool(validate_input=False)  # noqa: SLF001
async def call_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Invoke a read-only tool through the existing strict service boundary."""
    principal = _current_principal()
    if tool_name not in AGENT_TOOL_POLICIES:
        audit_agent_tool_event(
            principal.context,
            tool_name or "mcp:tools_call",
            result="UNKNOWN_TOOL",
        )
        return _error_result("未知 Agent 工具")
    if tool_name not in principal.context.allowed_tools:
        audit_agent_tool_event(principal.context, tool_name, result="DENIED")
        return _error_result("当前 Agent 客户端未获准调用该工具")
    try:
        data = invoke_agent_tool(principal.context, tool_name, arguments)
    except PermissionError:
        return _error_result("当前操作者没有调用该工具的权限")
    except ValueError as exc:
        return _error_result(str(exc))
    except Exception:
        return _error_result("Agent 工具执行失败")
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(data, ensure_ascii=False, default=str),
            )
        ],
        structuredContent={"success": True, "data": data},
    )


mcp_http_app = mcp_server.streamable_http_app()
