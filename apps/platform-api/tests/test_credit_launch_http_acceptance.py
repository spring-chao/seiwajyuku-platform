"""Isolated first-batch rehearsal using real HTTP auth, IAM and ledger services.

Only the existing dev/test WeChat provider stub is enabled. Permissions, scopes,
batch actions and member queries use their real implementations, without mocks.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import fetch_all, fetch_one
from app.main import app
from test_class_meeting_credits import _create_class_meeting
from test_learning_credit_ledger import _use_credit_plan
from test_study_meeting_evidence import photo
from test_v12_mvp import _seed_group_leader_fixture


BASE = "/api/v1/learning-credits/settlement-batches"


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def staff(client, admin_headers, *, role, org_id):
    account = "credit-launch-" + uuid4().hex[:12]
    now = datetime.now(UTC)
    created = data(client.post(
        "/api/v1/staff-management/staff", headers=admin_headers,
        json={
            "name": "学分上线隔离验收人员", "login_account": account,
            "is_active": True, "phone": f"13{uuid4().int % 1_000_000_000:09d}",
            "gender": "FEMALE", "institution_id": "institution-suzhou",
            "position_keys": ["ops_center_learning"], "employment_status": "ACTIVE",
            "started_on": (now - timedelta(days=1)).isoformat(),
            "ended_on": (now + timedelta(days=30)).isoformat(),
            "grants": [{"role_key": role, "org_unit_id": org_id, "scope_type": "UNIT"}],
            "authorization_basis": "隔离环境学分首批闭环验收",
            "authorization_reason": "隔离环境授予独立学分能力，不涉及生产账号",
        },
    ))
    login = data(client.post("/api/v1/auth/login", json={
        "username": account, "password": created["temporary_password"],
    }))
    return {"Authorization": "Bearer " + login["access_token"]}


def ledger_count():
    return int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])


def test_first_batch_http_auth_scope_post_close_member_view_and_reversal(monkeypatch, tmp_path):
    for key in (
        "IDENTITY_AUTHORIZATION_ENABLED", "IDENTITY_ADMIN_WRITES_ENABLED",
        "STUDY_MEETING_SUBMISSION_ENABLED", "STUDY_MEETING_EVIDENCE_ENABLED",
        "STUDY_MEETING_COURSE_EDIT_ENABLED", "WECHAT_LOCAL_TEST_MODE",
        "WECHAT_MEMBER_BINDING_ENABLED", "LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED",
        "LEARNING_CREDIT_BATCH_APPROVAL_ENABLED",
    ):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "false")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    monkeypatch.setenv("LEARNING_CREDIT_HISTORICAL_POST_ENABLED", "false")
    monkeypatch.setenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))
    # The built-in local provider has one openid; a unique synthetic app keeps
    # this binding independent of other tests without patching identity lookup.
    monkeypatch.setenv("WECHAT_MINIPROGRAM_APP_ID", "credit-launch-" + uuid4().hex)
    fixture = _seed_group_leader_fixture()
    _use_credit_plan(fixture)
    admin = fetch_one("SELECT id,token_version FROM app_users WHERE username='admin'")
    admin_headers = {"Authorization": "Bearer " + create_token(
        admin["id"], admin["token_version"], "access", timedelta(minutes=5),
    )}
    before = ledger_count()
    with TestClient(app) as client:
        creator = staff(client, admin_headers, role="credit_settlement_approver", org_id=fixture["class_id"])
        reviewer = staff(client, admin_headers, role="credit_settlement_approver", org_id=fixture["class_id"])
        outsider = staff(client, admin_headers, role="credit_settlement_approver", org_id=fixture["other_class_id"])
        poster = staff(client, admin_headers, role="credit_settlement_poster", org_id=fixture["class_id"])
        closer = staff(client, admin_headers, role="credit_settlement_closer", org_id=fixture["class_id"])
        reverser = staff(client, admin_headers, role="credit_settlement_reverser", org_id=fixture["class_id"])
        binding = data(client.post("/api/v1/wechat/member-bindings/verify", json={
            "code": "isolated-credit-launch", "name": "V1.2组长", "phone": fixture["phone"],
        }))
        member = {"Authorization": "Bearer " + binding["access_token"]}
        session = data(client.post("/api/v1/study-meetings", headers=member, json={
            "group_org_unit_id": fixture["group_id"], "member_ids": [fixture["member_id"]],
            "cross_group_member_ids": [fixture["other_member_id"]],
            "has_course": False, "course_keys": [],
        }))
        data(client.post(f"/api/v1/study-meetings/{session['id']}/evidence", headers=member,
                         files={"photo": ("study.jpg", photo(), "image/jpeg")}))
        data(client.post(f"/api/v1/study-meetings/{session['id']}/submit", headers=member))
        batch = data(client.post(f"{BASE}/study-meetings/{session['id']}/dry-run", headers=creator))
        assert batch["proposed_entry_count"] == 2 and batch["proposed_points"] == "8.00"
        batch_url = f"{BASE}/{batch['id']}"
        data(client.post(batch_url + "/submit-approval", headers=creator))
        self_approval = client.post(batch_url + "/approve", headers=creator)
        assert self_approval.status_code == 403 and "创建" in self_approval.text
        assert client.post(batch_url + "/approve", headers=outsider).status_code == 403
        assert client.get(batch_url, headers=outsider).status_code == 403
        assert data(client.post(batch_url + "/approve", headers=reviewer))["status"] == "APPROVED"
        assert ledger_count() == before
        assert data(client.get("/api/v1/wechat/credit-summary", headers=member))["total_points"] == "0.00"
        assert client.post(batch_url + "/post", headers=reviewer, json={}).status_code == 403
        assert client.post(batch_url + "/post", headers=poster, json={}).status_code == 400
        monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
        assert client.post(batch_url + "/post", headers=poster, json={}).status_code == 400
        assert ledger_count() == before
        monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
        posted = data(client.post(batch_url + "/post", headers=poster, json={}))
        assert posted["status"] == "POSTED" and posted["posted_points"] == "8.00"
        assert data(client.post(batch_url + "/post", headers=poster, json={}))["idempotent"] is True
        assert ledger_count() == before + 2
        assert client.post(batch_url + "/close", headers=poster).status_code == 403
        assert data(client.post(batch_url + "/close", headers=closer))["status"] == "CLOSED"
        assert data(client.post(batch_url + "/close", headers=closer))["idempotent"] is True
        summary = data(client.get("/api/v1/wechat/credit-summary", headers=member))
        assert summary["total_points"] == "4.00"
        own = fetch_all("SELECT * FROM learning_credit_entries WHERE member_id=?", (fixture["member_id"],))
        foreign = fetch_one("SELECT id FROM learning_credit_entries WHERE member_id=?", (fixture["other_member_id"],))
        assert len(own) == 1
        original = own[0]
        assert client.get(f"/api/v1/wechat/credit-entries/{foreign['id']}", headers=member).status_code == 404
        detail = data(client.get(f"/api/v1/wechat/credit-entries/{original['id']}", headers=member))
        assert "phone" not in detail and "rule_snapshot_json" not in detail
        reverse_url = f"/api/v1/learning-credits/entries/{original['id']}/reverse"
        assert client.post(reverse_url, headers=poster, json={"reason": "隔离纠错验收"}).status_code == 403
        reversal = data(client.post(reverse_url, headers=reverser, json={"reason": "隔离纠错验收"}))
        assert Decimal(str(reversal["points"])) == Decimal("-4")
        assert data(client.post(reverse_url, headers=reverser, json={"reason": "隔离纠错验收"}))["id"] == reversal["id"]
        assert ledger_count() == before + 3
        assert fetch_one("SELECT * FROM learning_credit_entries WHERE id=?", (original["id"],)) == original
        assert data(client.get("/api/v1/wechat/credit-summary", headers=member))["total_points"] == "0.00"


def test_class_batch_http_create_freezes_existing_scores_without_post(monkeypatch):
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "false")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture)
    admin = fetch_one("SELECT id,token_version FROM app_users WHERE username='admin'")
    headers = {"Authorization": "Bearer " + create_token(
        admin["id"], admin["token_version"], "access", timedelta(minutes=5),
    )}
    before = ledger_count()
    with TestClient(app) as client:
        url = f"{BASE}/class-meetings/{group_id}/dry-run"
        batch = data(client.post(url, headers=headers))
        assert batch["status"] == "DRY_RUN"
        assert batch["proposed_entry_count"] == 2 and batch["proposed_points"] == "35.00"
        assert batch["blocked_count"] == 0
        repeated = data(client.post(url, headers=headers))
        assert repeated["id"] == batch["id"] and repeated["idempotent"] is True
        assert ledger_count() == before
