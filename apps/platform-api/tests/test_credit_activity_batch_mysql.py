"""Disposable MySQL gate for daily-reading and excellent-share batch freezing."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.db import fetch_one
from app.services.credit_settlement_batches import (
    approve_daily_reading_batch,
    approve_excellent_share_batch,
    dry_run_daily_reading_batch,
    dry_run_excellent_share_batch,
    post_daily_reading_batch,
    post_excellent_share_batch,
    submit_daily_reading_batch_for_approval,
    submit_excellent_share_batch_for_approval,
)
from app.services.learning_activity_credits import DAILY_READING, EXCELLENT_SHARE
from test_learning_activity_credits import _admin_id, _calendar, _fact, _fixture


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
    reason="activity batch MySQL gate requires a disposable isolated MySQL runtime",
)


def test_activity_batches_mysql_freeze_without_ledger_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    fixture = _fixture()
    _calendar(fixture, year=2029, day_types={"2029-05-01": "NORMAL_WORKDAY"})
    _fact(fixture, activity_type=DAILY_READING, occurred_on="2029-05-01")
    for day in range(1, 7):
        _fact(fixture, activity_type=EXCELLENT_SHARE, occurred_on=f"2029-05-{day:02d}")
    before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    args = {
        "actor_user_id": _admin_id(),
        "class_org_unit_id": str(fixture["class_id"]),
        "occurred_from": "2029-05-01",
        "occurred_to": "2029-05-31",
    }
    daily = dry_run_daily_reading_batch(**args)
    shares = dry_run_excellent_share_batch(**args)
    repeated = dry_run_excellent_share_batch(**args)
    assert daily["status"] == shares["status"] == "DRY_RUN"
    assert daily["proposed_entry_count"] == 1
    assert shares["proposed_entry_count"] == 5
    assert repeated["idempotent"] is True
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before
    submit_daily_reading_batch_for_approval(actor_user_id=_admin_id(), batch_id=daily["id"])
    submit_excellent_share_batch_for_approval(actor_user_id=_admin_id(), batch_id=shares["id"])
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": ["plans:credit_settlement_approve"]},
    ):
        approve_daily_reading_batch(actor_user_id=_admin_id(), batch_id=daily["id"])
        approve_excellent_share_batch(actor_user_id=_admin_id(), batch_id=shares["id"])
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_POST_ENABLED", "true")
    with patch(
        "app.services.learning_credits.user_context",
        return_value={"permissions": ["plans:credit_settlement_manage", "plans:credit_settlement_post"]},
    ):
        daily_posted = post_daily_reading_batch(actor_user_id=_admin_id(), batch_id=daily["id"])
        shares_posted = post_excellent_share_batch(actor_user_id=_admin_id(), batch_id=shares["id"])
        replay = post_excellent_share_batch(actor_user_id=_admin_id(), batch_id=shares["id"])
    assert daily_posted["status"] == shares_posted["status"] == "POSTED"
    assert daily_posted["posted_entry_count"] == 1
    assert shares_posted["posted_entry_count"] == 5
    assert replay["idempotent"] is True
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before + 6
