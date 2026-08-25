from __future__ import annotations

from app.api.learning_plans import (
    _learning_plan_group_meeting_payload,
    _learning_plan_review_payload,
)


def test_learning_plan_review_payload_contains_all_checkpoints_and_tasks() -> None:
    payload = _learning_plan_review_payload()

    assert payload["plan_key"] == "standard-3y"
    assert payload["version_label"] == "2026"
    assert payload["status"] == "CONFIRMED"
    assert payload["required_checkpoint_count"] == 36
    assert payload["confirmed_checkpoint_count"] == 36
    assert len(payload["checkpoints"]) == 36
    assert all("tasks" in checkpoint for checkpoint in payload["checkpoints"])
    assert sum(len(checkpoint["tasks"]) for checkpoint in payload["checkpoints"]) == 500
    assert set(payload["source_workbooks"]) == {"1", "2", "3"}


def test_group_meeting_catalog_contains_all_tasks_and_fingerprints() -> None:
    payload = _learning_plan_group_meeting_payload()

    assert payload["review_status"] == "CONFIRMED"
    assert payload["task_count"] == 575
    assert len(payload["tasks"]) == 575
    assert {task["task_type"] for task in payload["tasks"]} == {"GROUP_MEETING"}
    assert {task["cohort_month"] for task in payload["tasks"]} == {1, 4, 7, 10}
    assert payload["source_json_sha256"] == (
        "404e1a9b3ea5037c9d5dd01d112186a629243115ee5c64da8ae0602cd572e8a2"
    )
