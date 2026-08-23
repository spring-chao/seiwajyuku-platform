from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ["AGENT_API_ENABLED"] = "true"
os.environ["AGENT_CLIENT_ID"] = "test-workbuddy"
os.environ["AGENT_CLIENT_SECRET"] = "t" * 40

from app.core.security import create_token  # noqa: E402
from app.db import execute, fetch_one, transaction  # noqa: E402
from app.main import app  # noqa: E402
from app.services.followups import add_followup_record, create_task  # noqa: E402
from app.services.iam import create_user, user_context  # noqa: E402
from app.services.members import create_member  # noqa: E402


class _ReusableTestClient:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def __enter__(self) -> TestClient:
        return self._client

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False


@pytest.fixture(scope="module")
def shared_client():
    with TestClient(app) as client:
        yield _ReusableTestClient(client)


def _fixture() -> dict[str, int | str]:
    suffix = uuid4().hex[:8]
    center_id = f"agent-center-{suffix}"
    other_center_id = f"agent-other-{suffix}"
    member_phone = f"139{int(suffix, 16) % 100000000:08d}"
    other_phone = f"138{(int(suffix, 16) + 1) % 100000000:08d}"
    now = datetime.now(UTC).isoformat()
    admin_id = int(fetch_one("SELECT id FROM app_users WHERE username='admin'")["id"])
    with transaction() as connection:
        for org_id, code, name in (
            (center_id, f"AGENT_CENTER_{suffix}", "Agent 测试分中心"),
            (other_center_id, f"AGENT_OTHER_{suffix}", "Agent 其他分中心"),
        ):
            execute(
                connection,
                "INSERT INTO org_units(id, unit_code, name, unit_type, parent_id, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, 'REGIONAL_CENTER', 'org-suzhou', 1, ?, ?)",
                (org_id, code, name, now, now),
            )
    user_id = create_user(
        admin_id,
        username=f"agent-user-{suffix}",
        display_name="Agent 测试运营",
        password="agent-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": center_id}],
    )
    member_id = create_member(
        admin_id,
        member_code=f"AGENT-MEMBER-{suffix}",
        name="Agent 测试学长",
        org_unit_id=center_id,
        development_org_unit_id=None,
        phone=member_phone,
        join_date="2022-03-01",
    )
    other_member_id = create_member(
        admin_id,
        member_code=f"AGENT-OTHER-{suffix}",
        name="Agent 其他学长",
        org_unit_id=other_center_id,
        development_org_unit_id=None,
        phone=other_phone,
    )
    return {
        "admin_id": admin_id,
        "user_id": user_id,
        "member_id": member_id,
        "other_member_id": other_member_id,
        "center_id": center_id,
        "other_center_id": other_center_id,
        "member_phone": member_phone,
    }


def _headers(user_id: int, *, request_id: str = "agent-test-request") -> dict[str, str]:
    context = user_context(user_id)
    token = create_token(
        user_id,
        context["token_version"],
        "access",
        timedelta(minutes=5),
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Agent-Client-ID": "test-workbuddy",
        "X-Agent-Client-Secret": "t" * 40,
        "X-Agent-Channel": "wecom",
        "X-Agent-Session-ID": "agent-test-session",
        "X-Request-ID": request_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _initialize_mcp_session(client: TestClient, headers: dict[str, str]) -> dict[str, str]:
    response = client.post(
        "/mcp/seiwajuku",
        json={
            "jsonrpc": "2.0",
            "id": "initialize",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "platform-api-tests", "version": "1.0"},
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["serverInfo"]["name"] == "seiwajyuku-agent-readonly"
    session_id = response.headers.get("mcp-session-id")
    assert session_id
    return {**headers, "Mcp-Session-Id": session_id, "MCP-Protocol-Version": "2025-06-18"}


def test_agent_rest_reuses_scope_and_masks_phone(shared_client) -> None:
    fixture = _fixture()
    headers = _headers(int(fixture["user_id"]))
    with shared_client as client:
        found = client.post(
            "/api/agent/v1/members/match",
            json={"name": "Agent 测试", "phone": fixture["member_phone"]},
            headers=headers,
        )
        assert found.status_code == 200, found.text
        rows = found.json()["data"]
        assert [row["member_id"] for row in rows] == [fixture["member_id"]]
        assert rows[0]["phone_masked"] == f"{fixture['member_phone'][:3]}****{fixture['member_phone'][-4:]}"
        assert fixture["member_phone"] not in found.text

        outside = client.get(
            "/api/agent/v1/members/search",
            params={"name": "Agent 其他"},
            headers=headers,
        )
        assert outside.status_code == 200
        assert outside.json()["data"] == []

        query_phone = client.get(
            "/api/agent/v1/members/search",
            params={"name": "Agent 测试", "phone": fixture["member_phone"]},
            headers=headers,
        )
        assert query_phone.status_code == 400

        summary = client.get(
            f"/api/agent/v1/members/{fixture['member_id']}/summary",
            headers=headers,
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["data"]["phone_masked"] == f"{fixture['member_phone'][:3]}****{fixture['member_phone'][-4:]}"
        assert "phone" not in summary.json()["data"] or summary.json()["data"].get("phone") is None

        outside_summary = client.get(
            f"/api/agent/v1/members/{fixture['other_member_id']}/summary",
            headers=headers,
        )
        assert outside_summary.status_code == 403


def test_agent_followup_context_redacts_free_text_and_audits_request(shared_client) -> None:
    fixture = _fixture()
    user_id = int(fixture["user_id"])
    task_id = create_task(
        int(fixture["admin_id"]),
        member_id=int(fixture["member_id"]),
        task_type="WECHAT",
        service_purpose="Agent 关怀上下文测试",
        assigned_user_id=user_id,
        due_at=None,
    )
    add_followup_record(
        task_id,
        user_id,
        channel="WECHAT",
        contacted_at=datetime.now(UTC).isoformat(),
        outcome_code="CONNECTED",
        subject_statement=f"学长说手机号{fixture['member_phone']}可联系",
        objective_facts="近期企业经营预算为100万元，月底再沟通",
        staff_judgment="保持关怀",
        next_action="下周再次联系",
        next_followup_at="2099-09-05",
    )
    headers = _headers(user_id, request_id="agent-followup-request")
    with shared_client as client:
        response = client.get(
            f"/api/agent/v1/members/{fixture['member_id']}/followup-context",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        serialized = json.dumps(body, ensure_ascii=False)
        assert fixture["member_phone"] not in serialized
        assert "100万元" not in serialized
        assert "[手机号已脱敏]" in serialized
        assert "[明确金额已脱敏]" in serialized

    audit = fetch_one(
        "SELECT request_id, after_json FROM audit_logs "
        "WHERE action='agent.tool.invoke' AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-followup-request",),
    )
    assert audit is not None
    metadata = json.loads(audit["after_json"])
    assert metadata["tool_name"] == "get_followup_context"
    assert metadata["channel"] == "wecom"
    assert metadata["read_only"] is True


def test_agent_mcp_lists_only_read_tools_and_calls_with_delegated_user(shared_client) -> None:
    fixture = _fixture()
    headers = _headers(int(fixture["user_id"]), request_id="agent-mcp-request")
    with shared_client as client:
        headers = _initialize_mcp_session(client, headers)

        listed = client.post(
            "/mcp/seiwajuku",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert listed.status_code == 200
        tools = listed.json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == {
            "get_my_today_actions",
            "find_member",
            "get_member_summary",
            "get_member_timeline",
            "get_renewal_context",
            "get_followup_context",
        }
        assert all(tool["annotations"]["readOnlyHint"] for tool in tools)

        called = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "find_member",
                    "arguments": {"name": "Agent 测试"},
                },
            },
            headers=headers,
        )
        assert called.status_code == 200, called.text
        data = called.json()["result"]["structuredContent"]["data"]
        assert data[0]["member_id"] == fixture["member_id"]

        denied = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "create_followup_record",
                    "arguments": {},
                },
            },
            headers=headers,
        )
        assert denied.status_code == 200
        assert denied.json()["result"]["isError"] is True


def test_agent_mcp_validates_required_types_and_extra_properties(shared_client) -> None:
    fixture = _fixture()
    headers = _headers(int(fixture["user_id"]), request_id="agent-invalid-request")
    with shared_client as client:
        headers = _initialize_mcp_session(client, headers)
        missing = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "get_member_summary", "arguments": {}},
            },
            headers=headers,
        )
        assert missing.status_code == 200
        assert missing.json()["result"]["isError"] is True

        extra = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "find_member",
                    "arguments": {"name": "Agent 测试", "unexpected": True},
                },
            },
            headers=headers,
        )
        assert extra.status_code == 200
        assert extra.json()["result"]["isError"] is True

        wrong_type = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "get_member_summary",
                    "arguments": {"member_id": "not-an-integer"},
                },
            },
            headers=headers,
        )
        assert wrong_type.status_code == 200
        assert wrong_type.json()["result"]["isError"] is True

        invalid_params = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": "not-an-object",
            },
            headers=headers,
        )
        assert invalid_params.status_code == 400

    audit = fetch_one(
        "SELECT result FROM audit_logs WHERE action='agent.tool.invoke' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-invalid-request",),
    )
    assert audit is not None
    assert audit["result"] == "INVALID_ARGUMENT"


def test_agent_auth_failures_are_audited_without_secret_material(shared_client) -> None:
    fixture = _fixture()
    headers = _headers(int(fixture["user_id"]), request_id="agent-auth-failure")
    headers["X-Agent-Client-Secret"] = "wrong-secret"
    with shared_client as client:
        invalid_secret = client.get(
            "/api/agent/v1/today-actions",
            headers=headers,
        )
        assert invalid_secret.status_code == 401

        bad_token = {
            **headers,
            "Authorization": "Bearer invalid-token",
            "X-Agent-Client-Secret": "t" * 40,
            "X-Request-ID": "agent-invalid-jwt",
        }
        invalid_jwt = client.get(
            "/api/agent/v1/today-actions",
            headers=bad_token,
        )
        assert invalid_jwt.status_code == 401

    auth_audit = fetch_one(
        "SELECT result, after_json FROM audit_logs WHERE action='agent.auth' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-auth-failure",),
    )
    assert auth_audit is not None
    assert auth_audit["result"] == "AUTH_FAILED"
    assert "wrong-secret" not in (auth_audit["after_json"] or "")

    jwt_audit = fetch_one(
        "SELECT result FROM audit_logs WHERE action='agent.auth' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-invalid-jwt",),
    )
    assert jwt_audit is not None
    assert jwt_audit["result"] == "AUTH_FAILED"


def test_agent_channel_and_inactive_user_fail_closed_and_are_audited(shared_client) -> None:
    fixture = _fixture()
    channel_headers = _headers(int(fixture["user_id"]), request_id="agent-channel-failure")
    channel_headers["X-Agent-Channel"] = "untrusted-channel"
    with shared_client as client:
        invalid_channel = client.get(
            "/api/agent/v1/today-actions",
            headers=channel_headers,
        )
        assert invalid_channel.status_code == 401

    inactive_headers = _headers(int(fixture["user_id"]), request_id="agent-inactive-user")
    with transaction() as connection:
        execute(
            connection,
            "UPDATE app_users SET is_active=0 WHERE id=?",
            (int(fixture["user_id"]),),
        )
    with shared_client as client:
        inactive = client.get(
            "/api/agent/v1/today-actions",
            headers=inactive_headers,
        )
        assert inactive.status_code == 401

    channel_audit = fetch_one(
        "SELECT result FROM audit_logs WHERE action='agent.auth' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-channel-failure",),
    )
    inactive_audit = fetch_one(
        "SELECT result FROM audit_logs WHERE action='agent.auth' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-inactive-user",),
    )
    assert channel_audit is not None and channel_audit["result"] == "AUTH_FAILED"
    assert inactive_audit is not None and inactive_audit["result"] == "AUTH_FAILED"


def test_agent_disabled_fails_closed_and_is_audited(monkeypatch, shared_client) -> None:
    fixture = _fixture()
    monkeypatch.setenv("AGENT_API_ENABLED", "false")
    headers = _headers(int(fixture["user_id"]), request_id="agent-disabled")
    with shared_client as client:
        response = client.get(
            "/api/agent/v1/today-actions",
            headers=headers,
        )
    assert response.status_code == 404
    audit = fetch_one(
        "SELECT result FROM audit_logs WHERE action='agent.auth' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-disabled",),
    )
    assert audit is not None
    assert audit["result"] == "DISABLED"


def test_agent_client_tool_allowlist_and_permission_denial_are_separate(monkeypatch, shared_client) -> None:
    fixture = _fixture()
    monkeypatch.setenv("AGENT_ALLOWED_TOOLS", "find_member")
    headers = _headers(int(fixture["user_id"]), request_id="agent-tool-denied")
    with shared_client as client:
        headers = _initialize_mcp_session(client, headers)
        listed = client.post(
            "/mcp/seiwajuku",
            json={"jsonrpc": "2.0", "id": 20, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert listed.status_code == 200
        assert {item["name"] for item in listed.json()["result"]["tools"]} == {"find_member"}

        denied = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "get_member_summary",
                    "arguments": {"member_id": int(fixture["member_id"])},
                },
            },
            headers=headers,
        )
        assert denied.status_code == 200
        assert denied.json()["result"]["isError"] is True

    audit = fetch_one(
        "SELECT result FROM audit_logs WHERE action='agent.tool.invoke' "
        "AND request_id=? ORDER BY id DESC LIMIT 1",
        ("agent-tool-denied",),
    )
    assert audit is not None
    assert audit["result"] == "DENIED"


def test_same_agent_client_keeps_two_operator_org_scopes_separate(shared_client) -> None:
    fixture = _fixture()
    admin_id = int(fixture["admin_id"])
    other_user_id = create_user(
        admin_id,
        username=f"agent-other-user-{uuid4().hex[:8]}",
        display_name="Agent 另一运营",
        password="agent-test-password",
        roles=["ops_center_operations"],
        scopes=[{"scope_type": "SUBTREE", "org_unit_id": fixture["center_id"]}],
    )
    # Move the second operator to the other center without changing the shared
    # Agent client credentials. The Bearer JWT remains the identity boundary.
    with transaction() as connection:
        execute(
            connection,
            "DELETE FROM data_scope_grants WHERE user_id=?",
            (other_user_id,),
        )
        execute(
            connection,
            "INSERT INTO data_scope_grants(user_id, scope_type, org_unit_id, created_at) "
            "VALUES (?, 'SUBTREE', ?, ?)",
            (other_user_id, fixture["other_center_id"], datetime.now(UTC).isoformat()),
        )

    headers_a = _headers(int(fixture["user_id"]), request_id="agent-dual-user-a")
    headers_b = _headers(other_user_id, request_id="agent-dual-user-b")
    with shared_client as client:
        headers_a = _initialize_mcp_session(client, headers_a)
        headers_b = _initialize_mcp_session(client, headers_b)
        result_a = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {"name": "find_member", "arguments": {"name": "Agent"}},
            },
            headers=headers_a,
        )
        result_b = client.post(
            "/mcp/seiwajuku",
            json={
                "jsonrpc": "2.0",
                "id": 31,
                "method": "tools/call",
                "params": {"name": "find_member", "arguments": {"name": "Agent"}},
            },
            headers=headers_b,
        )

    ids_a = {
        row["member_id"]
        for row in result_a.json()["result"]["structuredContent"]["data"]
    }
    ids_b = {
        row["member_id"]
        for row in result_b.json()["result"]["structuredContent"]["data"]
    }
    assert result_a.status_code == 200
    assert result_b.status_code == 200
    assert ids_a == {int(fixture["member_id"])}
    assert ids_b == {int(fixture["other_member_id"])}
