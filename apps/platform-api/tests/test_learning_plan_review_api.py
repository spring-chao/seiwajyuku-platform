from __future__ import annotations

from app.api.learning_plans import _learning_plan_review_payload


def test_learning_plan_review_payload_contains_all_checkpoints_and_tasks() -> None:
    payload = _learning_plan_review_payload()

    assert payload["plan_key"] == "standard-3y"
    assert payload["version_label"] == "2026"
    assert payload["status"] == "PENDING"
    assert payload["required_checkpoint_count"] == 36
    assert payload["confirmed_checkpoint_count"] == 0
    assert len(payload["checkpoints"]) == 36
    assert all("tasks" in checkpoint for checkpoint in payload["checkpoints"])
    assert sum(len(checkpoint["tasks"]) for checkpoint in payload["checkpoints"]) == 500
    assert set(payload["source_workbooks"]) == {"1", "2", "3"}
