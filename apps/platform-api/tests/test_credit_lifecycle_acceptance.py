"""Isolated cross-module acceptance; never authorizes or connects production."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_one, transaction
from app.main import app
from app.services import credit_settlement_batches as batches
from app.services.learning_credits import credit_ledger_overview, list_credit_entries, reverse_credit_entry
from app.services.study_meetings import submit_study_meeting
from app.services.historical_credit_import import register_suzhou_credit_workbook
from app.services.historical_credit_review import accept_year_only_period, upsert_class_mapping_candidates
from credit_batch_test_support import create_credit_batch_reviewer
from test_historical_credit_batch_mysql import _history_workbook
from test_learning_credit_ledger import _admin_id, _use_credit_plan
from test_study_meeting_evidence import create, upload
from test_v12_mvp import _seed_group_leader_fixture
from test_wechat_learning_summary import _bind


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    # Only the test process: real deployments and IAM policy are unchanged.
    for key in (
        "STUDY_MEETING_SUBMISSION_ENABLED", "STUDY_MEETING_EVIDENCE_ENABLED",
        "STUDY_MEETING_COURSE_EDIT_ENABLED", "LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED",
        "LEARNING_CREDIT_BATCH_APPROVAL_ENABLED", "WECHAT_MEMBER_BINDING_ENABLED",
    ):
        monkeypatch.setenv(key, "true")
    for key in ("LEARNING_CREDIT_SETTLEMENT_ENABLED", "LEARNING_CREDIT_BATCH_POST_ENABLED",
                "LEARNING_CREDIT_HISTORICAL_POST_ENABLED"):
        monkeypatch.setenv(key, "false")
    monkeypatch.setenv("STUDY_EVIDENCE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("STUDY_EVIDENCE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WECHAT_MINIPROGRAM_APP_ID", "credit-lifecycle-test")
    monkeypatch.setenv("WECHAT_MINIPROGRAM_APP_SECRET", "synthetic-test-only")
    fixture = _seed_group_leader_fixture()
    fixture["actor"] = _admin_id()
    fixture["reviewer"] = create_credit_batch_reviewer()
    permissions = [f"plans:credit_settlement_{name}" for name in
                   ("preview", "manage", "approve", "post", "close", "reverse")]
    permissions.append("plans:historical_credit_import_manage")
    monkeypatch.setattr("app.services.learning_credits.user_context", lambda _: {"permissions": permissions})
    monkeypatch.setattr("app.services.learning_credits.accessible_org_ids", lambda _: {fixture["class_id"]})
    monkeypatch.setattr("app.services.wechat_identity.exchange_wechat_code", lambda _: {
        "appid": "credit-lifecycle-test", "openid": f"lifecycle-{fixture['suffix']}",
    })
    return fixture


def count():
    return int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])


def approve_and_post(fixture, batch, monkeypatch, *, historical=False):
    before = count()
    batches.submit_settlement_batch_for_approval(actor_user_id=fixture["actor"], batch_id=batch["id"])
    with pytest.raises(PermissionError, match="不能审批自己的"):
        batches.approve_settlement_batch(actor_user_id=fixture["actor"], batch_id=batch["id"])
    approved = batches.approve_settlement_batch(actor_user_id=fixture["reviewer"], batch_id=batch["id"])
    assert approved["status"] == "APPROVED" and count() == before
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
    if historical:
        monkeypatch.setenv("LEARNING_CREDIT_HISTORICAL_POST_ENABLED", "true")
    posted = batches.post_settlement_batch(actor_user_id=fixture["actor"], batch_id=batch["id"])
    replay = batches.post_settlement_batch(actor_user_id=fixture["actor"], batch_id=batch["id"])
    assert posted["status"] == "POSTED" and replay["idempotent"] is True
    assert count() == before + posted["posted_entry_count"]
    closed = batches.close_settlement_batch(actor_user_id=fixture["actor"], batch_id=batch["id"])
    assert closed["status"] == "CLOSED"
    assert count() == before + posted["posted_entry_count"]


def read(client, path, headers):
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_group_batch_operator_member_and_reversal_share_one_ledger(lifecycle, monkeypatch):
    fixture = lifecycle
    _use_credit_plan(fixture)
    session = create(fixture)
    upload(fixture, session)
    submit_study_meeting(member_id=fixture["member_id"], session_id=session["id"])
    before = count()
    batch = batches.dry_run_study_meeting_batch(actor_user_id=fixture["actor"], session_id=session["id"])
    assert count() == before
    with TestClient(app) as client:
        headers = _bind(fixture, client)
        summary_path = "/api/v1/wechat/credit-summary"
        history_path = "/api/v1/wechat/credit-entries"
        assert read(client, summary_path, headers)["total_points"] == "0.00"
        approve_and_post(fixture, batch, monkeypatch)
        assert credit_ledger_overview(actor_user_id=fixture["actor"])["total_points"] == "8.00"
        summary = read(client, summary_path, headers)
        history = read(client, history_path, headers)
        assert summary["total_points"] == "4.00" and len(history["entries"]) == 1
        visible = history["entries"][0]
        entries = list_credit_entries(actor_user_id=fixture["actor"])
        assert len(entries) == 2
        assert read(client, f"{history_path}/{visible['entry_ref']}", headers)["points"] == "4.00"
        other = next(row for row in entries if row["member_id"] != fixture["member_id"])
        assert client.get(f"{history_path}/{other['id']}", headers=headers).status_code == 404
        original = fetch_one("SELECT * FROM learning_credit_entries WHERE id=?", (int(visible["entry_ref"]),))
        reversal = reverse_credit_entry(actor_user_id=fixture["actor"], entry_id=original["id"], reason="synthetic acceptance correction")
        replay = reverse_credit_entry(actor_user_id=fixture["actor"], entry_id=original["id"], reason="synthetic acceptance correction")
        assert reversal["id"] == replay["id"] and count() == before + 3
        assert fetch_one("SELECT * FROM learning_credit_entries WHERE id=?", (original["id"],)) == original
        assert read(client, summary_path, headers)["total_points"] == "0.00"
        assert credit_ledger_overview(actor_user_id=fixture["actor"])["total_points"] == "4.00"
        detail = read(client, f"{history_path}/{reversal['id']}", headers)
        assert detail["original_entry_ref"] == visible["entry_ref"] and detail["is_reversal"]
        assert "synthetic acceptance correction" not in str(detail)


def test_history_import_batches_keep_original_value_and_precision_in_member_views(lifecycle, monkeypatch):
    fixture = lifecycle
    imported = register_suzhou_credit_workbook(content=_history_workbook(), original_filename=f"acceptance-{uuid4().hex}.xlsx")
    import_id = int(imported["batch"]["id"])
    with transaction() as connection:
        execute(connection, "UPDATE learning_credit_import_rows SET matched_member_id=?,match_status='AUTO_MATCHED',"
                "match_snapshot_id='acceptance',match_snapshot_fingerprint='acceptance',match_algorithm_version='test-v1' WHERE batch_id=?",
                (fixture["member_id"], import_id))
        execute(connection, "UPDATE learning_credit_import_items SET matched_member_id=? WHERE batch_id=?", (fixture["member_id"], import_id))
    upsert_class_mapping_candidates(batch_id=import_id, mappings=[{
        "source_sheet": "历史结算测试班", "raw_class_name": "历史结算测试班", "org_unit_id": fixture["class_id"],
        "mapping_status": "EXACT", "mapping_reason": "ISOLATED_ACCEPTANCE", "candidate_org_unit_ids": [fixture["class_id"]],
    }], snapshot_id="acceptance", snapshot_fingerprint="acceptance", actor_user_id=fixture["actor"])
    accept_year_only_period(batch_id=import_id, expected_count=1, actor_user_id=fixture["actor"], reason="Synthetic source only specifies year")
    before = count()
    dry_run = batches.dry_run_historical_credit_batches(actor_user_id=fixture["actor"], import_batch_id=import_id)
    assert dry_run["ready_points"] == "5.00" and count() == before
    with TestClient(app) as client:
        headers = _bind(fixture, client)
        assert read(client, "/api/v1/wechat/credit-entries", headers)["entries"] == []
        for batch in dry_run["settlement_batches"]:
            approve_and_post(fixture, batch, monkeypatch, historical=True)
        history = read(client, "/api/v1/wechat/credit-entries", headers)
        assert count() == before + 2
        assert {row["period_display"] for row in history["entries"]} == {"2026年", "2026年1月"}
        assert {row["points"] for row in history["entries"]} == {"1.00", "4.00"}
        assert all("未重算" in row["rule_basis"] for row in history["entries"])
        assert read(client, "/api/v1/wechat/credit-summary", headers)["total_points"] == "5.00"
        assert credit_ledger_overview(actor_user_id=fixture["actor"])["total_points"] == "5.00"
        for row in history["entries"]:
            detail = read(client, f"/api/v1/wechat/credit-entries/{row['entry_ref']}", headers)
            assert detail["rule_version"] == "LEGACY_SUZHOU_2026_V1"
            assert detail["period_display"] == row["period_display"]
