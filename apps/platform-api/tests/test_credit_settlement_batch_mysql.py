"""Disposable MySQL gate for the 0067 study-meeting batch business path."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.db import fetch_one
from app.services.credit_settlement_batches import (
    approve_study_meeting_batch,
    dry_run_study_meeting_batch,
    post_study_meeting_batch,
    submit_study_meeting_batch_for_approval,
)
from app.services.learning_credits import LearningCreditError
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
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": ["plans:credit_settlement_approve"]},
    ):
        approved = approve_study_meeting_batch(actor_user_id=actor, batch_id=batch["id"])
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
