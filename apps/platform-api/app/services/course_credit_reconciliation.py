"""Fail-closed reconciliation for the 2026 persisted course-credit rows.

This module is intentionally storage-neutral.  The CLI consumes a sanitized
read-only production export, produces an auditable plan, and has no default
database writer.  A future APPLY adapter must provide both fingerprints again
inside the same transaction before changing a production rule row.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.course_credit_canonical import (
    canonical_fingerprint,
    expected_persisted_rule,
    load_canonical_policy,
)


DIFF_TYPES = {
    "EXACT_MATCH",
    "MISSING_IN_PRODUCTION",
    "VALUE_DIFFERENT",
    "NAME_DIFFERENT",
    "YEAR_DIFFERENT",
    "ALIAS_DIFFERENT",
    "STATUS_DIFFERENT",
    "EXTRA_IN_PRODUCTION",
    "UNUSED_PLACEHOLDER",
    "CONFLICT",
}

UNUSED_PLACEHOLDER_KEYS = frozenset(
    {
        "AUTO-QR-EXCELLENT-IMPROVEMENT",
        "AUTO-QR-HAPPINESS-CARE",
        "AUTO-QR-IMPROVEMENT-INNOVATION",
    }
)

PLACEHOLDER_REFERENCE_FIELDS = (
    "generic_rule_mapping",
    "course_rule_mapping",
    "study_meeting_course_reference",
    "completion_fact",
    "ledger_reference",
)


def _aliases(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        try:
            decoded = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return _aliases(decoded)
    return []


def _production_rule(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id", row.get("rule_id")),
        "course_key": str(row.get("course_key") or "").strip(),
        "course_name": str(row.get("course_name") or "").strip(),
        "year_index": row.get("year_index"),
        "credit_points": int(row.get("credit_points") or 0),
        "status": str(row.get("status", row.get("rule_status")) or "").strip(),
        "source": str(row.get("source") or "").strip(),
        "aliases": _aliases(row.get("aliases", row.get("aliases_json", []))),
    }


def _before_image(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the complete deletion evidence when the export contains it."""

    image = _production_rule(row)
    for field in (
        "aliases_json",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    ):
        if field in row:
            image[field] = row[field]
    return image


def _placeholder_block_reason(
    *,
    version: dict[str, Any],
    rule: dict[str, Any],
    reference_counts: dict[str, Any] | None,
) -> str | None:
    """Return a reason when a named placeholder is not safe to remove."""

    if rule["course_key"] not in UNUSED_PLACEHOLDER_KEYS:
        return "NOT_ALLOWLISTED_PLACEHOLDER"
    version_status = str(version.get("status", version.get("version_status")) or "").strip()
    if version_status != "DRAFT":
        return "RULE_VERSION_NOT_DRAFT"
    if rule["status"] != "PENDING":
        return "RULE_STATUS_NOT_PENDING"
    if rule["credit_points"] != 0:
        return "RULE_POINTS_NOT_ZERO"
    evidence = (reference_counts or {}).get(rule["course_key"])
    if not isinstance(evidence, dict):
        return "REFERENCE_EVIDENCE_MISSING"
    if "all_reference_count" not in evidence:
        return "REFERENCE_TOTAL_MISSING"
    try:
        if int(evidence["all_reference_count"]) != 0:
            return "REFERENCE_TOTAL_NOT_ZERO"
        for field in PLACEHOLDER_REFERENCE_FIELDS:
            if field not in evidence or int(evidence[field]) != 0:
                return f"REFERENCE_{field.upper()}_NOT_ZERO_OR_MISSING"
    except (TypeError, ValueError):
        return "REFERENCE_COUNT_INVALID"
    return None


def production_fingerprint(
    version: dict[str, Any], rules: list[dict[str, Any]]
) -> str:
    stable = {
        "version": {
            "id": version.get("id", version.get("version_id")),
            "plan_key": version.get("plan_key"),
            "version_label": version.get("version_label"),
            "status": version.get("status", version.get("version_status")),
            "based_on_version_label": version.get("based_on_version_label"),
        },
        "rules": sorted(
            [_production_rule(rule) for rule in rules],
            key=lambda item: (item["course_key"], str(item.get("id") or "")),
        ),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _field_differences(
    expected: dict[str, Any], actual: dict[str, Any]
) -> list[str]:
    differences: list[str] = []
    if actual["course_name"] != expected["course_name"]:
        differences.append("NAME_DIFFERENT")
    if actual["year_index"] != expected["year_index"]:
        differences.append("YEAR_DIFFERENT")
    if actual["credit_points"] != expected["credit_points"]:
        differences.append("VALUE_DIFFERENT")
    if actual["status"] != expected["status"]:
        differences.append("STATUS_DIFFERENT")
    if sorted(actual["aliases"]) != sorted(expected["aliases"]):
        differences.append("ALIAS_DIFFERENT")
    return differences


def reconcile_course_rules(
    *,
    version: dict[str, Any],
    production_rules: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
    reference_counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = policy or load_canonical_policy()
    expected_rules = [expected_persisted_rule(rule) for rule in canonical["rules"]]
    normalized_production = [_production_rule(rule) for rule in production_rules]
    before_images = {
        (rule["course_key"], str(rule.get("id") or "")): _before_image(raw)
        for raw, rule in zip(production_rules, normalized_production)
    }
    production_by_key: dict[str, list[dict[str, Any]]] = {}
    for rule in normalized_production:
        production_by_key.setdefault(rule["course_key"], []).append(rule)

    conflicts: list[dict[str, Any]] = []
    for key, rows in sorted(production_by_key.items()):
        if not key or len(rows) > 1:
            conflicts.append(
                {
                    "course_key": key or None,
                    "action": "BLOCK",
                    "difference_types": ["CONFLICT"],
                    "reason": "生产规则 course_key 为空或重复，禁止自动收口",
                    "before": rows,
                }
            )

    plan: list[dict[str, Any]] = []
    exact_match_count = 0
    missing_count = 0
    different_count = 0
    for expected in expected_rules:
        key = expected["course_key"]
        rows = production_by_key.get(key, [])
        if not rows:
            missing_count += 1
            plan.append(
                {
                    "course_key": key,
                    "action": "ADD",
                    "difference_types": ["MISSING_IN_PRODUCTION"],
                    "before": None,
                    "after": expected,
                    "canonical_source": canonical["source_path"],
                }
            )
            continue
        if len(rows) != 1:
            continue
        actual = rows[0]
        differences = _field_differences(expected, actual)
        if not differences:
            exact_match_count += 1
            plan.append(
                {
                    "course_key": key,
                    "action": "KEEP",
                    "difference_types": ["EXACT_MATCH"],
                    "before": actual,
                    "after": expected,
                }
            )
        else:
            different_count += 1
            plan.append(
                {
                    "course_key": key,
                    "action": "UPDATE",
                    "difference_types": differences,
                    "before": actual,
                    "after": expected,
                    "canonical_source": canonical["source_path"],
                }
            )

    canonical_keys = {rule["course_key"] for rule in expected_rules}
    extra_count = 0
    placeholder_removal_count = 0
    for actual in normalized_production:
        key = actual["course_key"]
        if key not in canonical_keys:
            before = before_images.get((key, str(actual.get("id") or "")), actual)
            placeholder_reason = _placeholder_block_reason(
                version=version, rule=actual, reference_counts=reference_counts
            )
            if key in UNUSED_PLACEHOLDER_KEYS and placeholder_reason is None:
                placeholder_removal_count += 1
                plan.append(
                    {
                        "course_key": key,
                        "action": "REMOVE_UNUSED_PLACEHOLDER",
                        "difference_types": ["UNUSED_PLACEHOLDER"],
                        "before": before,
                        "after": None,
                        "reason": "REMOVE_UNUSED_PLACEHOLDER_BEFORE_CANONICAL_FREEZE",
                    }
                )
                continue
            extra_count += 1
            plan.append(
                {
                    "course_key": key or None,
                    "action": "BLOCK",
                    "difference_types": ["EXTRA_IN_PRODUCTION"],
                    "before": before,
                    "after": None,
                    "reason": (
                        "未知生产规则不得自动删除；需单独业务确认"
                        if key not in UNUSED_PLACEHOLDER_KEYS
                        else f"占位规则删除前置条件不满足：{placeholder_reason}"
                    ),
                }
            )

    plan.extend(conflicts)
    plan.sort(key=lambda item: (str(item.get("course_key") or ""), item["action"]))
    production_fp = production_fingerprint(version, normalized_production)
    canonical_fp = canonical_fingerprint(canonical)
    blocked = bool(extra_count or conflicts)
    return {
        "mode": "DRY_RUN",
        "plan_key": canonical["plan_key"],
        "version_label": canonical["version_label"],
        "current_version_id": version.get("id", version.get("version_id")),
        "current_status": version.get("status", version.get("version_status")),
        "production_rule_count": len(normalized_production),
        "canonical_rule_count": len(expected_rules),
        "exact_match_count": exact_match_count,
        "missing_count": missing_count,
        "different_count": different_count,
        "extra_count": extra_count,
        "placeholder_removal_count": placeholder_removal_count,
        "conflict_count": len(conflicts),
        "production_fingerprint": production_fp,
        "canonical_fingerprint": canonical_fp,
        "content_status": canonical["content_status"],
        "deployment_status": canonical["deployment_status"],
        "apply_status": "BLOCKED_BY_EXTRA_OR_CONFLICT" if blocked else "READY_FOR_SEPARATE_APPROVAL",
        "write_performed": False,
        "plan": plan,
    }


def guarded_apply(
    reconciliation: dict[str, Any],
    *,
    expected_production_fingerprint: str,
    expected_canonical_fingerprint: str,
    actor: str,
    reason: str,
    reconciliation_batch_id: str,
    writer: Callable[[list[dict[str, Any]]], Any],
) -> Any:
    """Apply only after a second read and fingerprint check in a caller-owned transaction."""

    if reconciliation["production_fingerprint"] != expected_production_fingerprint:
        raise ValueError("生产规则在 DRY-RUN 与 APPLY 之间发生变化，必须中止")
    if reconciliation["canonical_fingerprint"] != expected_canonical_fingerprint:
        raise ValueError("canonical 规则在 DRY-RUN 与 APPLY 之间发生变化，必须中止")
    if reconciliation["extra_count"] or reconciliation["conflict_count"]:
        raise ValueError("存在 EXTRA_IN_PRODUCTION 或 CONFLICT，禁止 APPLY")
    if reconciliation["current_status"] != "DRAFT":
        raise ValueError("只有 DRAFT 生产版本允许进入规则收口 APPLY")
    if not actor.strip() or not reason.strip() or not reconciliation_batch_id.strip():
        raise ValueError("APPLY 审计必须包含 actor、reason 和 reconciliation_batch_id")
    timestamp = datetime.now(timezone.utc).isoformat()
    audited_plan = []
    for item in reconciliation["plan"]:
        if item["action"] not in {"ADD", "UPDATE", "REMOVE_UNUSED_PLACEHOLDER"}:
            continue
        audited_plan.append(
            {
                **item,
                "audit": {
                    "course_key": item.get("course_key"),
                    "before": item.get("before"),
                    "after": item.get("after"),
                    "canonical_source": item.get("canonical_source"),
                    "actor": actor,
                    "timestamp": timestamp,
                    "reason": reason,
                    "reconciliation_batch_id": reconciliation_batch_id,
                },
            }
        )
    return writer(audited_plan)
