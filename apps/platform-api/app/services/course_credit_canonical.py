"""Canonical business definition for the 2026 course-credit policy.

The editable catalog is a UI-facing projection and the SQL migration contains
an intentional fail-closed seed.  Neither is a second business source.  This
module gives reconciliation tools and tests one stable loader and one
fingerprint for the business-confirmed rules.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


CANONICAL_PLAN_KEY = "STANDARD_3Y_2026"
CANONICAL_VERSION_LABEL = "2026.1"
CANONICAL_CONTENT_STATUS = "BUSINESS_CONFIRMED"
CANONICAL_DEPLOYMENT_STATUS = "DRAFT"
EXPECTED_RULE_COUNT = 25
EXPECTED_CONFIRMATION_DATE = "2026-09-01"


def canonical_path() -> Path:
    for parent in Path(__file__).resolve().parents:
        path = parent / "data" / "learning-plans" / "course-credit-rules-2026.json"
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 course-credit-rules-2026.json")


def _aliases(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _rule_fields(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "course_key": str(rule.get("course_key") or "").strip(),
        "course_name": str(rule.get("course_name") or "").strip(),
        "year_index": rule.get("year_index"),
        "credit_points": int(rule.get("credit_points")),
        "status": str(rule.get("status") or "").strip(),
        "aliases": _aliases(rule.get("aliases")),
        "source": str(rule.get("source") or "").strip(),
    }


def load_canonical_policy(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the one business-confirmed 2026 course policy."""

    resolved = path or canonical_path()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("plan_key") != CANONICAL_PLAN_KEY:
        raise ValueError("canonical 课程规则 plan_key 不正确")
    if payload.get("version_label") != CANONICAL_VERSION_LABEL:
        raise ValueError("canonical 课程规则 version_label 不正确")
    rules = payload.get("rules")
    if not isinstance(rules, list) or len(rules) != EXPECTED_RULE_COUNT:
        raise ValueError(f"canonical 课程规则必须恰好有 {EXPECTED_RULE_COUNT} 条")

    normalized_rules = [_rule_fields(rule) for rule in rules]
    keys = [rule["course_key"] for rule in normalized_rules]
    if not all(keys) or len(set(keys)) != len(keys):
        raise ValueError("canonical 课程规则 course_key 必须非空且唯一")
    if any(rule["status"] != "CONFIRMED" for rule in normalized_rules):
        raise ValueError("canonical 课程规则必须全部是业务确认状态 CONFIRMED")
    confirmation = payload.get("business_confirmation")
    if not isinstance(confirmation, dict):
        raise ValueError("canonical 课程规则缺少 business_confirmation")
    if confirmation.get("confirmed_at") != EXPECTED_CONFIRMATION_DATE:
        raise ValueError("canonical 课程规则业务确认日期不符合当前基线")
    if confirmation.get("reference_count") != EXPECTED_RULE_COUNT:
        raise ValueError("canonical 课程规则 business_confirmation.reference_count 不正确")

    return {
        "plan_key": CANONICAL_PLAN_KEY,
        "version_label": CANONICAL_VERSION_LABEL,
        "content_status": CANONICAL_CONTENT_STATUS,
        "deployment_status": str(payload.get("status") or CANONICAL_DEPLOYMENT_STATUS),
        "source_path": str(resolved),
        "source_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "business_confirmation": confirmation,
        "group_meeting_base_credit": payload.get("group_meeting_base_credit"),
        "rules": sorted(normalized_rules, key=lambda item: item["course_key"]),
    }


def canonical_rule_rows(policy: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return list((policy or load_canonical_policy())["rules"])


def canonical_fingerprint(policy: dict[str, Any] | None = None) -> str:
    """Fingerprint business fields, not JSON formatting or file ordering."""

    value = policy or load_canonical_policy()
    stable = {
        "plan_key": value["plan_key"],
        "version_label": value["version_label"],
        "content_status": value["content_status"],
        "business_confirmation": {
            "confirmed_at": value["business_confirmation"].get("confirmed_at"),
            "reference_count": value["business_confirmation"].get("reference_count"),
        },
        "rules": [
            {
                "course_key": rule["course_key"],
                "course_name": rule["course_name"],
                "year_index": rule["year_index"],
                "credit_points": rule["credit_points"],
                "status": rule["status"],
                "aliases": sorted(rule["aliases"]),
                "source": rule["source"],
            }
            for rule in sorted(value["rules"], key=lambda item: item["course_key"])
        ],
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def expected_persisted_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Translate content confirmation to the row status expected by 0064."""

    return {
        **rule,
        "status": "CONFIGURED" if rule["status"] == "CONFIRMED" else rule["status"],
    }
