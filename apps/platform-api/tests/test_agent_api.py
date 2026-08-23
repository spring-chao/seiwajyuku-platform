from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    }


def test_agent_rest_reuses_scope_and_masks_phone() -> None:
    fixture = _fixture()
    headers = _headers(int(fixture["user_id"]))
    with TestClient(app) as client:
        found = client.get(
            "/api/agent/v1/members/search",
            params={"name": "Agent 测试", "phone": fixture["member_phone"]},
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

        summary = client.get(
            f"/api/agent/v1/members/{fixture['member_id']}/summary",
            headers=headers,
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["data"]["phone_masked"] == f"{fixture['member_phone'][:3]}****{fixture['member_phone'][-4:]}"
        assert "phone" not in summary.json()["data"] or summary.json()["data"].get("phone") is None


def test_agent_followup_context_redacts_free_text_and_audits_request() -> None:
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
    with TestClient(app) as client:
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


def test_agent_mcp_lists_only_read_tools_and_calls_with_delegated_user() -> None:
    fixture = _fixture()
    headers = _headers(int(fixture["user_id"]), request_id="agent-mcp-request")
    with TestClient(app) as client:
        initialized = client.post(
            "/mcp/seiwajuku",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers=headers,
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "seiwajyuku-agent-readonly"

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
        assert denied.json()["error"]["code"] == -32601
