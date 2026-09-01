from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from review_group_meeting_c6 import (  # noqa: E402
    _paths,
    apply_confirmation,
    verify_review,
)
from app.api.learning_plans import _learning_plan_group_meeting_flow_payload  # noqa: E402


ROOT = REPO_ROOT / "data" / "learning-plans"
REVIEW = ROOT / "standard-3y-2026.1.review.json"
BASE = ROOT / "standard-3y-2026.json"


def _review() -> dict:
    return json.loads(REVIEW.read_text(encoding="utf-8"))


def test_c6_manifest_has_all_exception_and_course_review_items() -> None:
    review = _review()

    assert review["status"] == "PENDING"
    assert review["candidate_version_label"] == "2026.1"
    assert len(review["mapping_conflicts"]) == 2
    assert len(review["mapping_missing"]) == 3
    assert len(review["qr_review_required"]) == 7
    assert len(review["course_nodes"]) == 32
    assert review["summary"]["cycle_count"] == 144
    assert len(review["source_fingerprint"]["base_group_flow_source_files"]) == 78


def test_c6_verify_rejects_top_level_confirmation_while_items_are_pending() -> None:
    review = _review()
    review["status"] = "CONFIRMED_CANDIDATE"

    with pytest.raises(ValueError, match="尚未完成业务确认"):
        verify_review(review, paths=_paths(ROOT))


def _confirm_all(review: dict, *, missing_status: str = "EXEMPTED") -> None:
    for item in review["mapping_conflicts"]:
        apply_confirmation(
            review,
            review_id=item["review_id"],
            status="MAPPED",
            reviewed_by="测试审核",
            flow_key=(item["candidate_flow_keys"] or ["test-flow"])[0],
        )
    for item in review["mapping_missing"]:
        apply_confirmation(
            review,
            review_id=item["review_id"],
            status=missing_status,
            reviewed_by="测试审核",
            flow_key=None,
        )
    for item in review["qr_review_required"] + review["course_nodes"]:
        if item.get("source_course_key"):
            status = "COURSE_CONFIRMED" if item.get("source_credit_points") is not None else "COURSE_CONFIRMED_CREDIT_PENDING"
            apply_confirmation(
                review,
                review_id=item["review_id"],
                status=status,
                reviewed_by="测试审核",
                course_key=item["source_course_key"],
                credit_points=item.get("source_credit_points"),
            )
        else:
            apply_confirmation(
                review,
                review_id=item["review_id"],
                status="NON_COURSE_QR",
                reviewed_by="测试审核",
            )
    for item in review["flow_samples"]:
        apply_confirmation(
            review,
            review_id=item["review_id"],
            status="CONFIRMED",
            reviewed_by="测试审核",
        )


def test_source_missing_blocks_candidate_confirmation() -> None:
    review = _review()
    _confirm_all(review, missing_status="SOURCE_MISSING")

    with pytest.raises(ValueError, match="SOURCE_MISSING"):
        verify_review(review, paths=_paths(ROOT))


def test_all_confirmed_derives_candidate_without_mutating_base() -> None:
    review = _review()
    base_before = BASE.read_bytes()
    _confirm_all(review)

    summary = verify_review(review, paths=_paths(ROOT), mutate_status=True)

    assert review["status"] == "CONFIRMED_CANDIDATE"
    assert review["candidate_status"] == "CONFIRMED_CANDIDATE"
    assert summary["unresolved_mapping_count"] == 0
    assert summary["qr_review_required_count"] == 0
    assert BASE.read_bytes() == base_before


def test_api_payload_exposes_c6_read_only_review() -> None:
    payload = _learning_plan_group_meeting_flow_payload()

    assert payload["c6_review_status"] == "PENDING"
    assert payload["c6_summary"]["mapping_conflict_count"] == 2
    assert len(payload["c6_review"]["course_nodes"]) == 32
