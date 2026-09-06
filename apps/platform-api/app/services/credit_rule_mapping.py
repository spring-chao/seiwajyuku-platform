"""Explicit learning-plan to credit-policy mappings.

Learning-plan versions and credit-policy versions intentionally use different
identity namespaces.  This module is the only place where a new learning
round resolves those namespaces into the two frozen version ids stored on
``class_learning_bindings``.

The mapping table is introduced by G5.3.  Returning ``None`` when an older
database has not applied that migration keeps read-only legacy screens
available while making the missing mapping visible to settlement callers.
"""

from __future__ import annotations

from typing import Any

from app.db import execute


STANDARD_PLAN_KEY = "standard-3y"
STANDARD_PLAN_VERSION_LABEL = "2026"
STANDARD_GENERIC_RULE_SET_KEY = "STANDARD_3Y_2026"
STANDARD_GENERIC_RULE_VERSION_LABEL = "2026.1"
STANDARD_COURSE_RULE_PLAN_KEY = "STANDARD_3Y_2026"
STANDARD_COURSE_RULE_VERSION_LABEL = "2026.1"


def resolve_credit_rule_mapping(
    connection,
    *,
    plan_key: str,
    version_label: str,
) -> dict[str, Any] | None:
    """Resolve a published mapping for a *new* learning round.

    The explicit mapping row is authoritative.  The joined policy identities
    are returned for audit/snapshot purposes, but are never inferred by
    comparing the learning-plan strings with either policy identity.
    """

    try:
        row = execute(
            connection,
            "SELECT m.id AS mapping_id, m.plan_key, m.plan_version_label, "
            "m.status AS mapping_status, "
            "m.generic_rule_version_id, gv.rule_set_key AS generic_rule_set_key, "
            "gv.version_label AS generic_rule_version_label, "
            "gv.status AS generic_rule_version_status, "
            "m.course_credit_rule_version_id, "
            "cv.plan_key AS course_rule_plan_key, "
            "cv.version_label AS course_rule_version_label, "
            "cv.status AS course_rule_version_status "
            "FROM learning_plan_credit_rule_mappings m "
            "JOIN learning_credit_rule_versions gv "
            "ON gv.id=m.generic_rule_version_id "
            "JOIN learning_plan_credit_rule_versions cv "
            "ON cv.id=m.course_credit_rule_version_id "
            "WHERE m.plan_key=? AND m.plan_version_label=? "
            "AND m.status='ACTIVE' "
            "AND gv.status='PUBLISHED' AND cv.status='PUBLISHED' "
            "LIMIT 1",
            (plan_key, version_label),
        ).fetchone()
    except Exception as exc:
        # The resolver is used by lifecycle writes as well as read-only
        # projections.  A pre-G5.3 schema is an unmapped plan, not a reason to
        # fabricate a policy id or to make the old page fail at import time.
        message = str(exc).lower()
        if (
            "no such table" in message
            or "doesn't exist" in message
            or "unknown table" in message
        ):
            return None
        raise
    if not row:
        return None
    return dict(row)


def bound_course_rule_version(connection, version_id: int | None) -> dict[str, Any] | None:
    """Load the exact frozen course-policy version referenced by a binding."""

    if version_id is None:
        return None
    row = execute(
        connection,
        "SELECT id, plan_key, version_label, status, based_on_version_label, "
        "created_by, created_at, updated_at "
        "FROM learning_plan_credit_rule_versions WHERE id=? LIMIT 1",
        (version_id,),
    ).fetchone()
    return dict(row) if row else None


def bound_generic_rule(
    connection, version_id: int | None, rule_key: str
) -> dict[str, Any] | None:
    """Load one active rule from the exact frozen generic policy version."""

    if version_id is None:
        return None
    row = execute(
        connection,
        "SELECT v.id AS rule_version_id, v.rule_set_key, "
        "v.version_label AS rule_version, v.status AS rule_version_status, "
        "r.id AS rule_id, r.rule_key, r.credit_category, r.credit_type, "
        "r.settlement_model, r.points, r.cap_points, r.rule_snapshot_json, "
        "r.status AS rule_status "
        "FROM learning_credit_rule_versions v "
        "JOIN learning_credit_rules r ON r.rule_version_id=v.id "
        "AND r.rule_key=? AND r.status='ACTIVE' "
        "WHERE v.id=? LIMIT 1",
        (rule_key, version_id),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    # Keep version-level fields flat while preserving the shape used by the
    # original proposal builder for the rule definition.
    result["rule"] = {
        "id": result.get("rule_id"),
        "rule_key": result.get("rule_key"),
        "credit_category": result.get("credit_category"),
        "credit_type": result.get("credit_type"),
        "settlement_model": result.get("settlement_model"),
        "points": result.get("points"),
        "cap_points": result.get("cap_points"),
        "rule_snapshot_json": result.get("rule_snapshot_json"),
        "status": result.get("rule_status"),
    }
    return result
