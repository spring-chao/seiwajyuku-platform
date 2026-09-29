"""Disposable MySQL gate for a class-meeting settlement DRY-RUN batch."""

from __future__ import annotations

import os

import pytest

from app.db import fetch_one
from app.services.credit_settlement_batches import dry_run_class_meeting_batch
from test_class_meeting_credits import _admin_id, _create_class_meeting
from test_v12_mvp import _seed_group_leader_fixture


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("mysql+pymysql://"),
    reason="class batch MySQL gate requires a disposable isolated MySQL runtime",
)


def test_class_meeting_batch_mysql_freezes_without_ledger_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED", "true")
    monkeypatch.setenv("LEARNING_CREDIT_SETTLEMENT_ENABLED", "false")
    fixture = _seed_group_leader_fixture()
    group_id = _create_class_meeting(fixture)
    before = int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"])
    batch = dry_run_class_meeting_batch(actor_user_id=_admin_id(), event_group_id=group_id)
    repeated = dry_run_class_meeting_batch(actor_user_id=_admin_id(), event_group_id=group_id)
    assert batch["status"] == "DRY_RUN"
    assert batch["proposed_entry_count"] == 2
    assert batch["proposed_points"] == "35.00"
    assert repeated["idempotent"] is True
    assert repeated["id"] == batch["id"]
    assert int(fetch_one("SELECT COUNT(*) AS n FROM learning_credit_entries")["n"]) == before
