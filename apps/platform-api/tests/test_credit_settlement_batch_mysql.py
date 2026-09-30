"""Disposable MySQL gate for the 0067 study-meeting batch business path."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.db import execute, fetch_one, transaction
from app.services import learning_credits as credit_service
from app.services.credit_settlement_batches import (
    approve_study_meeting_batch,
    close_settlement_batch,
    dry_run_study_meeting_batch,
    post_study_meeting_batch,
    reconcile_stale_posting_batch,
    submit_study_meeting_batch_for_approval,
)
from app.services.learning_credits import LearningCreditError
from credit_batch_test_support import create_credit_batch_reviewer
from test_learning_credit_ledger import _admin_id, _submit_without_evidence, _use_credit_plan
from test_study_meeting_evidence import create
from test_v12_mvp import _seed_group_leader_fixture


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
    reason="batch MySQL gate requires a disposable isolated MySQL runtime",
)


def test_study_meeting_batch_mysql_approval_post_and_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "STUDY_MEETING_SUBMISSION_ENABLED",
        "STUDY_MEETING_EVIDENCE_ENABLED",
        "STUDY_MEETING_COURSE_EDIT_ENABLED",
        "LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED",
        "LEARNING_CREDIT_BATCH_APPROVAL_ENABLED",
    ):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "false")

    fixture = _seed_group_leader_fixture()
    _use_credit_plan(fixture)
    session = create(fixture)
    _submit_without_evidence(session)
    actor = _admin_id()
    before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    batch = dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
    assert batch["status"] == "DRY_RUN"
    submitted = submit_study_meeting_batch_for_approval(actor_user_id=actor, batch_id=batch["id"])
    assert submitted["status"] == "PENDING_APPROVAL"
    reviewer = create_credit_batch_reviewer()
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": ["plans:credit_settlement_approve"]},
    ), patch("app.services.learning_credits.accessible_org_ids", return_value=None):
        approved = approve_study_meeting_batch(actor_user_id=reviewer, batch_id=batch["id"])
    assert approved["status"] == "APPROVED"
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before

    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    permissions = ["plans:credit_settlement_manage", "plans:credit_settlement_post"]
    with patch("app.services.learning_credits.user_context", return_value={"permissions": permissions}):
        with pytest.raises(LearningCreditError, match="批次正式入账尚未开启"):
            post_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
        monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
        posted = post_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
        repeated = post_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
    assert posted["status"] == "POSTED"
    assert posted["posted_entry_count"] == 2
    assert posted["posted_points"] == "8.00"
    assert repeated["idempotent"] is True
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before + 2
    close_permissions = ["plans:credit_settlement_manage", "plans:credit_settlement_close"]
    with patch("app.services.learning_credits.user_context", return_value={"permissions": close_permissions}):
        closed = close_settlement_batch(actor_user_id=actor, batch_id=batch["id"])
        replay_close = close_settlement_batch(actor_user_id=actor, batch_id=batch["id"])
    assert closed["status"] == "CLOSED"
    assert replay_close["idempotent"] is True
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before + 2


def test_mysql_stale_posting_recovery_is_ledger_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "STUDY_MEETING_SUBMISSION_ENABLED",
        "STUDY_MEETING_EVIDENCE_ENABLED",
        "STUDY_MEETING_COURSE_EDIT_ENABLED",
        "LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED",
        "LEARNING_CREDIT_BATCH_APPROVAL_ENABLED",
        "LEARNING_CREDIT_BATCH_POST_ENABLED",
    ):
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")

    fixture = _seed_group_leader_fixture()
    _use_credit_plan(fixture)
    session = create(fixture)
    _submit_without_evidence(session)
    actor = _admin_id()
    reviewer = create_credit_batch_reviewer()
    before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    batch = dry_run_study_meeting_batch(actor_user_id=actor, session_id=session["id"])
    submit_study_meeting_batch_for_approval(actor_user_id=actor, batch_id=batch["id"])
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": ["plans:credit_settlement_approve"]},
    ), patch("app.services.learning_credits.accessible_org_ids", return_value=None):
        approve_study_meeting_batch(actor_user_id=reviewer, batch_id=batch["id"])

    with transaction() as connection:
        stale_heartbeat = credit_service._db_timestamp(
            connection, datetime.now(UTC) - timedelta(minutes=31),
        )
        execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='POSTING',updated_at=? WHERE id=?",
            (stale_heartbeat, batch["id"]),
        )
    reconcile_permissions = [
        "plans:credit_settlement_manage", "plans:credit_settlement_reconcile",
    ]
    post_permissions = ["plans:credit_settlement_manage", "plans:credit_settlement_post"]
    with patch("app.services.learning_credits.user_context", return_value={"permissions": reconcile_permissions}):
        recovered = reconcile_stale_posting_batch(actor_user_id=actor, batch_id=batch["id"])
    assert recovered["status"] == "PARTIAL_FAILED"
    assert recovered["posted_entry_count"] == 0
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before

    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    with patch("app.services.learning_credits.user_context", return_value={"permissions": post_permissions}):
        resumed = post_study_meeting_batch(
            actor_user_id=actor, batch_id=batch["id"], resume_partial_failure=True,
        )
    assert resumed["status"] == "POSTED"
    assert resumed["posted_entry_count"] == 2
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before + 2
