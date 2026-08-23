from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx


PROTOCOL_VERSION = "2025-06-18"
EXPECTED_READ_TOOLS = frozenset(
    {
        "get_my_today_actions",
        "find_member",
        "get_member_summary",
        "get_member_timeline",
        "get_renewal_context",
        "get_followup_context",
    }
)
FORBIDDEN_WRITE_TOOLS = frozenset(
    {
        "create_followup_record",
        "schedule_next_followup",
        "close_followup_task",
        "create_member",
        "update_member",
        "reveal_contact",
    }
)
SAFE_REMOTE_HOST_MARKERS = ("staging", "preview")
SAFE_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ProbeError(RuntimeError):
    """A redacted verification failure safe to print in CI or a report."""


@dataclass(frozen=True)
class ProbeConfig:
    endpoint: str
    expected_host: str
    operator_token: str
    client_id: str
    client_secret: str
    target_environment: str = "staging"
    channel: str = "api"
    external_session_id: str = ""
    member_name: str | None = None
    timeout_seconds: float = 15.0

    @classmethod
    def from_environment(cls) -> "ProbeConfig":
        return cls(
            endpoint=os.getenv("AGENT_PROBE_URL", "").strip(),
            expected_host=os.getenv("AGENT_PROBE_EXPECTED_HOST", "")
            .strip()
            .lower(),
            operator_token=os.getenv("AGENT_OPERATOR_TOKEN", "").strip(),
            client_id=os.getenv("AGENT_CLIENT_ID", "").strip(),
            client_secret=os.getenv("AGENT_CLIENT_SECRET", "").strip(),
            target_environment=os.getenv(
                "AGENT_PROBE_ENVIRONMENT", os.getenv("APP_ENV", "")
            )
            .strip()
            .lower(),
            channel=os.getenv("AGENT_PROBE_CHANNEL", "api").strip().lower(),
            external_session_id=os.getenv(
                "AGENT_PROBE_SESSION_ID", f"agent-v2-{uuid4().hex}"
            ).strip(),
            member_name=os.getenv("AGENT_PROBE_MEMBER_NAME", "").strip() or None,
            timeout_seconds=float(os.getenv("AGENT_PROBE_TIMEOUT_SECONDS", "15")),
        )


def _assert_safe_config(config: ProbeConfig) -> None:
    if config.target_environment != "staging":
        raise ProbeError("验收工具仅允许 target_environment=staging")
    if not config.endpoint:
        raise ProbeError("缺少 AGENT_PROBE_URL")
    parsed = urlparse(config.endpoint)
    hostname = (parsed.hostname or "").lower()
    if parsed.path.rstrip("/") != "/mcp/seiwajuku":
        raise ProbeError("AGENT_PROBE_URL 必须指向 /mcp/seiwajuku")
    if hostname in SAFE_LOCAL_HOSTS:
        if parsed.scheme not in {"http", "https"}:
            raise ProbeError("本地验收地址必须使用 http 或 https")
    else:
        if parsed.scheme != "https":
            raise ProbeError("远程 staging 验收地址必须使用 https")
        if not any(marker in hostname for marker in SAFE_REMOTE_HOST_MARKERS):
            raise ProbeError("远程主机名必须明确包含 staging 或 preview")
        if not config.expected_host or hostname != config.expected_host:
            raise ProbeError("远程主机必须与 AGENT_PROBE_EXPECTED_HOST 完全一致")
    if not config.operator_token:
        raise ProbeError("缺少 AGENT_OPERATOR_TOKEN")
    if not config.client_id:
        raise ProbeError("缺少 AGENT_CLIENT_ID")
    if len(config.client_secret) < 32:
        raise ProbeError("AGENT_CLIENT_SECRET 至少需要 32 位")
    if not config.channel:
        raise ProbeError("缺少 AGENT_PROBE_CHANNEL")
    if not config.external_session_id or len(config.external_session_id) > 128:
        raise ProbeError("AGENT_PROBE_SESSION_ID 必须为 1 至 128 个字符")
    if config.member_name is not None and len(config.member_name) > 100:
        raise ProbeError("AGENT_PROBE_MEMBER_NAME 最多 100 个字符")
    if config.timeout_seconds <= 0 or config.timeout_seconds > 60:
        raise ProbeError("AGENT_PROBE_TIMEOUT_SECONDS 必须大于 0 且不超过 60")


def _base_headers(config: ProbeConfig, request_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.operator_token}",
        "X-Agent-Client-ID": config.client_id,
        "X-Agent-Client-Secret": config.client_secret,
        "X-Agent-Channel": config.channel,
        "X-Agent-Session-ID": config.external_session_id,
        "X-Request-ID": request_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _expect_status(
    response: httpx.Response, allowed: set[int], stage: str
) -> None:
    if response.status_code not in allowed:
        raise ProbeError(f"{stage} HTTP 状态异常：{response.status_code}")


def _response_json(response: httpx.Response, stage: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProbeError(f"{stage} 未返回 JSON") from exc
    if not isinstance(payload, dict):
        raise ProbeError(f"{stage} JSON 根节点必须是对象")
    if isinstance(payload.get("error"), dict):
        code = payload["error"].get("code", "unknown")
        raise ProbeError(f"{stage} 返回 JSON-RPC 错误：{code}")
    return payload


def _post_rpc(
    client: httpx.Client,
    config: ProbeConfig,
    headers: dict[str, str],
    payload: dict[str, Any],
    stage: str,
) -> tuple[dict[str, Any], httpx.Response]:
    try:
        response = client.post(config.endpoint, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise ProbeError(f"{stage} 请求失败：{type(exc).__name__}") from exc
    _expect_status(response, {200}, stage)
    return _response_json(response, stage), response


def _result_object(payload: dict[str, Any], stage: str) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ProbeError(f"{stage} 缺少 result 对象")
    return result


def _summarize_tool_data(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return {"shape": "list", "item_count": len(data)}
    if isinstance(data, dict):
        for key in ("items", "actions", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return {"shape": "object", "item_count": len(value)}
        return {"shape": "object"}
    return {"shape": type(data).__name__}


def _call_tool(
    client: httpx.Client,
    config: ProbeConfig,
    headers: dict[str, str],
    *,
    rpc_id: int,
    name: str,
    arguments: dict[str, Any],
) -> Any:
    payload, _ = _post_rpc(
        client,
        config,
        headers,
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        f"tools/call:{name}",
    )
    result = _result_object(payload, f"tools/call:{name}")
    if result.get("isError") is True:
        raise ProbeError(f"tools/call:{name} 返回工具错误")
    structured = result.get("structuredContent")
    if not isinstance(structured, dict) or structured.get("success") is not True:
        raise ProbeError(f"tools/call:{name} 缺少成功的 structuredContent")
    return structured.get("data")


def run_probe(
    config: ProbeConfig,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Verify a non-production MCP endpoint without returning business data."""
    _assert_safe_config(config)
    request_id = f"agent-v2-{uuid4().hex}"
    headers = _base_headers(config, request_id)
    timeout = httpx.Timeout(config.timeout_seconds)

    with httpx.Client(
        timeout=timeout,
        transport=transport,
        follow_redirects=False,
    ) as client:
        initialized, initialize_response = _post_rpc(
            client,
            config,
            headers,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "seiwajyuku-agent-v2-probe",
                        "version": "1.0",
                    },
                },
            },
            "initialize",
        )
        initialize_result = _result_object(initialized, "initialize")
        protocol_version = initialize_result.get("protocolVersion")
        if protocol_version != PROTOCOL_VERSION:
            raise ProbeError("initialize 协议版本协商结果不符合预期")
        server_info = initialize_result.get("serverInfo")
        if not isinstance(server_info, dict) or server_info.get("name") != (
            "seiwajyuku-agent-readonly"
        ):
            raise ProbeError("initialize 服务端身份不符合预期")
        session_id = initialize_response.headers.get("mcp-session-id", "").strip()
        if not session_id:
            raise ProbeError("initialize 缺少 Mcp-Session-Id")
        session_headers = {
            **headers,
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }

        try:
            initialized_notification = client.post(
                config.endpoint,
                headers=session_headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            _expect_status(
                initialized_notification,
                {200, 202, 204},
                "notifications/initialized",
            )

            event_stream_headers = {
                **session_headers,
                "Accept": "text/event-stream",
            }
            with client.stream(
                "GET", config.endpoint, headers=event_stream_headers
            ) as event_stream:
                _expect_status(event_stream, {200}, "GET event stream")
                content_type = event_stream.headers.get("content-type", "").lower()
                if "text/event-stream" not in content_type:
                    raise ProbeError("GET event stream Content-Type 不是 text/event-stream")

            tools_payload, _ = _post_rpc(
                client,
                config,
                session_headers,
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                "tools/list",
            )
            tools_result = _result_object(tools_payload, "tools/list")
            tools = tools_result.get("tools")
            if not isinstance(tools, list) or any(
                not isinstance(tool, dict) for tool in tools
            ):
                raise ProbeError("tools/list 返回的 tools 结构无效")
            tool_names = {str(tool.get("name") or "") for tool in tools}
            if tool_names != EXPECTED_READ_TOOLS:
                raise ProbeError("tools/list 与预期六个只读工具不一致")
            if tool_names & FORBIDDEN_WRITE_TOOLS:
                raise ProbeError("tools/list 暴露了禁止的写入或敏感工具")
            for tool in tools:
                annotations = tool.get("annotations")
                if not isinstance(annotations, dict):
                    raise ProbeError("tools/list 缺少只读 annotations")
                if annotations.get("readOnlyHint") is not True:
                    raise ProbeError("tools/list 存在未标记只读的工具")
                if annotations.get("destructiveHint") is not False:
                    raise ProbeError("tools/list 存在可能破坏数据的工具")

            today_data = _call_tool(
                client,
                config,
                session_headers,
                rpc_id=3,
                name="get_my_today_actions",
                arguments={},
            )
            member_summary: dict[str, Any] | None = None
            if config.member_name:
                member_data = _call_tool(
                    client,
                    config,
                    session_headers,
                    rpc_id=4,
                    name="find_member",
                    arguments={"name": config.member_name, "limit": 5},
                )
                member_summary = _summarize_tool_data(member_data)

            delete_response = client.delete(config.endpoint, headers=session_headers)
            _expect_status(delete_response, {200, 204}, "session delete")
        except httpx.HTTPError as exc:
            raise ProbeError(f"MCP session 请求失败：{type(exc).__name__}") from exc

    report: dict[str, Any] = {
        "status": "passed",
        "target_environment": config.target_environment,
        "transport": "streamable_http",
        "protocol_version": protocol_version,
        "server_name": server_info["name"],
        "session_header": "present",
        "get_event_stream": "supported",
        "tools": sorted(tool_names),
        "today_actions": _summarize_tool_data(today_data),
        "request_id": request_id,
    }
    if member_summary is not None:
        report["member_probe"] = member_summary
    return report


def main() -> int:
    try:
        report = run_probe(ProbeConfig.from_environment())
    except (ProbeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
