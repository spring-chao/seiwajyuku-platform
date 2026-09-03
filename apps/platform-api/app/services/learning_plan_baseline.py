"""Read-only comparison helpers for the September 2026 class baseline.

The baseline is deliberately an artifact rather than a production write plan.
Class names are useful for review, but only ``class_org_unit_id`` is allowed
to join a baseline row to runtime data.  This prevents same-name classes from
being merged or changed accidentally.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


BASELINE_FILENAME = "class-learning-plan-migration-baseline-2026-09.json"
EXPECTED_ISSUE_TYPES = {
    "expected_current_cycle": "EXPECTED_CYCLE_MISMATCH",
    "expected_cohort_month": "EXPECTED_TEMPLATE_MISMATCH",
    "expected_plan_version": "EXPECTED_PLAN_VERSION_MISMATCH",
    "expected_runtime_status": "EXPECTED_STATUS_MISMATCH",
    "meeting_status": "EXPECTED_STATUS_MISMATCH",
    "group_meeting_policy": "EXPECTED_STATUS_MISMATCH",
}
LEARNING_PLAN_SCOPE_IN = "IN_SCOPE"
LEARNING_PLAN_SCOPE_OUT = "OUT_OF_SCOPE"
BINDING_REQUIREMENT_REQUIRED = "REQUIRED"
BINDING_REQUIREMENT_NOT_REQUIRED = "NOT_REQUIRED"


def is_learning_plan_binding_required(item: dict[str, Any]) -> bool:
    """Return whether the class participates in plan-binding health gates.

    Existing baseline rows predate the explicit scope fields and therefore
    default to the normal, required behavior.  Either explicit out-of-scope
    marker is sufficient to opt a class out; this keeps the read-only baseline
    compatible while making the business decision unambiguous.
    """

    scope = str(item.get("learning_plan_scope") or "").strip().upper()
    requirement = str(item.get("binding_requirement") or "").strip().upper()
    return not (
        scope == LEARNING_PLAN_SCOPE_OUT
        or requirement == BINDING_REQUIREMENT_NOT_REQUIRED
    )


def find_baseline_path() -> Path | None:
    """Locate the checked-in baseline from a source checkout or container."""

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "data" / "learning-plans" / BASELINE_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    """Load and minimally validate the baseline artifact.

    A missing artifact means the deployment has no business baseline yet and
    therefore produces no expectation comparison.  A malformed checked-in
    artifact is an error: silently skipping it would make a health scan look
    safer than it is.
    """

    baseline_path = path or find_baseline_path()
    if baseline_path is None:
        return {"schema_version": 1, "classes": [], "status": "NOT_PROVIDED"}
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"学习计划业务基线无法读取: {baseline_path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("classes"), list):
        raise ValueError("学习计划业务基线必须包含 classes 数组")
    return payload


def baseline_by_class_id(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index only rows with an explicit ID; never fall back to class names."""

    indexed: dict[str, dict[str, Any]] = {}
    for item in baseline.get("classes", []):
        if not isinstance(item, dict):
            continue
        class_id = str(item.get("class_org_unit_id") or "").strip()
        if not class_id:
            continue
        if class_id in indexed:
            raise ValueError(f"学习计划业务基线存在重复 class_org_unit_id: {class_id}")
        indexed[class_id] = item
    return indexed


def public_expectation(item: dict[str, Any]) -> dict[str, Any]:
    """Return the review-safe expectation fields exposed by the health API."""

    raw_scope = str(item.get("learning_plan_scope") or "").strip().upper()
    raw_requirement = str(item.get("binding_requirement") or "").strip().upper()
    scope = raw_scope or (
        LEARNING_PLAN_SCOPE_OUT
        if raw_requirement == BINDING_REQUIREMENT_NOT_REQUIRED
        else LEARNING_PLAN_SCOPE_IN
    )
    requirement = raw_requirement or (
        BINDING_REQUIREMENT_NOT_REQUIRED
        if scope == LEARNING_PLAN_SCOPE_OUT
        else BINDING_REQUIREMENT_REQUIRED
    )

    return {
        "class_name": item.get("class_name"),
        "learning_plan_scope": scope,
        "binding_requirement": requirement,
        "expected_plan_version": item.get("expected_plan_version"),
        "expected_cohort_month": item.get("expected_cohort_month"),
        "expected_current_cycle": item.get("expected_current_cycle"),
        "meeting_status": item.get("meeting_status"),
        "group_meeting_policy": item.get("group_meeting_policy"),
        "expected_runtime_status": item.get("expected_runtime_status"),
        "evidence_source": item.get("evidence_source"),
        "confidence": item.get("confidence"),
        "adjustment_reason": item.get("adjustment_reason"),
        "migration_status": item.get("migration_status"),
        "candidate_org_unit_ids": item.get("candidate_org_unit_ids"),
        "id_resolution_note": item.get("id_resolution_note"),
    }


def actual_snapshot(
    *,
    binding: dict[str, Any] | None,
    current_cycle: dict[str, Any] | None,
    runtime_status: str | None,
) -> dict[str, Any]:
    """Normalize service/API health data for a deterministic comparison."""

    return {
        "actual_plan_version": (
            binding.get("version_label") if binding else None
        ),
        "actual_cohort_month": binding.get("cohort_month") if binding else None,
        "actual_current_cycle": (
            current_cycle.get("learning_cycle_index") if current_cycle else None
        ),
        "actual_meeting_status": (
            current_cycle.get("class_meeting_status") if current_cycle else None
        ),
        "actual_group_meeting_policy": (
            current_cycle.get("group_meeting_policy") if current_cycle else None
        ),
        "actual_runtime_status": runtime_status,
    }


def compare_expectation(
    expectation: dict[str, Any], actual: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return business mismatches without proposing or performing a write."""

    if not is_learning_plan_binding_required(expectation):
        return []
    if expectation.get("migration_status") == "MANUAL_REVIEW_REQUIRED":
        return []
    comparisons = (
        ("expected_plan_version", "actual_plan_version"),
        ("expected_cohort_month", "actual_cohort_month"),
        ("expected_current_cycle", "actual_current_cycle"),
        ("expected_runtime_status", "actual_runtime_status"),
        ("meeting_status", "actual_meeting_status"),
        ("group_meeting_policy", "actual_group_meeting_policy"),
    )
    mismatches: list[dict[str, Any]] = []
    for expected_field, actual_field in comparisons:
        expected = expectation.get(expected_field)
        if expected is None:
            continue
        actual_value = actual.get(actual_field)
        if actual_value is not None and str(expected) == str(actual_value):
            continue
        mismatches.append(
            {
                "issue_type": EXPECTED_ISSUE_TYPES[expected_field],
                "field": expected_field,
                "expected": expected,
                "actual": actual_value,
            }
        )
    return mismatches


def baseline_summary(baseline: dict[str, Any]) -> dict[str, Any]:
    """Summarize ID resolution for the read-only health response."""

    rows = [item for item in baseline.get("classes", []) if isinstance(item, dict)]
    resolved = [item for item in rows if str(item.get("class_org_unit_id") or "").strip()]
    return {
        "status": baseline.get("status", "NOT_PROVIDED"),
        "schema_version": baseline.get("schema_version"),
        "total_entries": len(rows),
        "resolved_id_entries": len(resolved),
        "unresolved_id_entries": len(rows) - len(resolved),
        "filename": BASELINE_FILENAME,
    }
