from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest


PROBE_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "verify_agent_mcp_streamable.py"
)
SPEC = importlib.util.spec_from_file_location("agent_mcp_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def _config(**overrides):
    values = {
        "endpoint": "https://agent-staging.example.test/mcp/seiwajuku",
        "expected_host": "agent-staging.example.test",
        "operator_token": "operator-token-never-print",
        "client_id": "workbuddy-staging",
        "client_secret": "client-secret-never-print-000000000000",
        "target_environment": "staging",
        "channel": "api",
        "external_session_id": "probe-session",
    }
    values.update(overrides)
    return probe.ProbeConfig(**values)


def _readonly_tools() -> list[dict]:
    return [
        {
            "name": name,
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
            },
        }
        for name in sorted(probe.EXPECTED_READ_TOOLS)
    ]


def test_probe_verifies_full_streamable_http_session_without_leaking_data() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b"",
            )
        if request.method == "DELETE":
            return httpx.Response(200)
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sdk-session-id"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": probe.PROTOCOL_VERSION,
                        "serverInfo": {
                            "name": "seiwajyuku-agent-readonly",
                            "version": "1.0",
                        },
                        "capabilities": {},
                    },
                },
            )
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if payload.get("method") == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": _readonly_tools()},
                },
            )
        if payload.get("method") == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "content": [{"type": "text", "text": "redacted"}],
                        "structuredContent": {
                            "success": True,
                            "data": {"items": [{"sensitive": "not-reported"}]},
                        },
                        "isError": False,
                    },
                },
            )
        raise AssertionError(payload)

    result = probe.run_probe(
        _config(), transport=httpx.MockTransport(handler)
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "passed"
    assert result["get_event_stream"] == "supported"
    assert result["today_actions"] == {"shape": "object", "item_count": 1}
    assert result["tools"] == sorted(probe.EXPECTED_READ_TOOLS)
    assert "operator-token-never-print" not in serialized
    assert "client-secret-never-print" not in serialized
    assert "not-reported" not in serialized
    assert any(request.method == "GET" for request in requests)
    assert any(request.method == "DELETE" for request in requests)
    assert next(
        request for request in requests if request.method == "GET"
    ).headers["accept"] == "text/event-stream"
    assert all(
        request.headers.get("mcp-protocol-version") == probe.PROTOCOL_VERSION
        for request in requests
        if request.method != "POST"
        or json.loads(request.content).get("method") != "initialize"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://seiwajyuku-platform-api.example.com/mcp/seiwajuku",
        "http://agent-staging.example.test/mcp/seiwajuku",
        "https://agent-staging.example.test/api/not-mcp",
    ],
)
def test_probe_rejects_non_staging_or_unsafe_targets(endpoint: str) -> None:
    with pytest.raises(probe.ProbeError):
        probe.run_probe(_config(endpoint=endpoint))


def test_probe_rejects_remote_host_not_explicitly_pinned() -> None:
    with pytest.raises(probe.ProbeError):
        probe.run_probe(_config(expected_host="different-staging.example.test"))


def test_probe_rejects_tool_surface_drift_without_echoing_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}
            )
        payload = json.loads(request.content)
        if payload.get("method") == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sdk-session-id"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": probe.PROTOCOL_VERSION,
                        "serverInfo": {"name": "seiwajyuku-agent-readonly"},
                    },
                },
            )
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if payload.get("method") == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": _readonly_tools()
                        + [
                            {
                                "name": "create_followup_record",
                                "annotations": {
                                    "readOnlyHint": False,
                                    "destructiveHint": False,
                                },
                            }
                        ]
                    },
                },
            )
        raise AssertionError(payload)

    config = _config()
    with pytest.raises(probe.ProbeError) as caught:
        probe.run_probe(config, transport=httpx.MockTransport(handler))
    message = str(caught.value)
    assert config.operator_token not in message
    assert config.client_secret not in message
