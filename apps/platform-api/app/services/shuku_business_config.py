from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.db import fetch_one


JIANGNAN_ROOT_ORG_UNIT_ID = "org-jiangnan"
ANNUAL_MEMBER_SERVICE_FEE_APPLIES_TO = ("ENROLLMENT", "RENEWAL")


def normalize_fee_amount(value: Any) -> str | None:
    """Return a stable, display-safe decimal string for a configured fee."""

    if value in (None, ""):
        return None
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, ValueError):
        return str(value)


def resolve_shuku_root_id(org_unit_id: str | None) -> str | None:
    """Resolve an organization node to its formal Jiangnan shuku root.

    The relationship is derived from stable organization IDs and parentage;
    display names are never used for matching.  The bounded walk also fails
    closed when historical data contains a cycle or a disconnected node.
    """

    current_id = str(org_unit_id or "").strip()
    if not current_id:
        return None
    visited: set[str] = set()
    for _ in range(128):
        if not current_id or current_id in visited:
            return None
        visited.add(current_id)
        row = fetch_one(
            "SELECT id, unit_type, parent_id, is_active "
            "FROM org_units WHERE id=? LIMIT 1",
            (current_id,),
        )
        if not row or not row.get("is_active"):
            return None
        if (
            str(row.get("unit_type") or "").upper() == "ROOT"
            and str(row.get("parent_id") or "") == JIANGNAN_ROOT_ORG_UNIT_ID
        ):
            return str(row["id"])
        current_id = str(row.get("parent_id") or "").strip()
    return None


def get_annual_member_service_fee(
    org_unit_id: str | None,
) -> dict[str, Any]:
    """Read the one annual service-fee fact shared by enrollment and renewal."""

    missing = {
        "status": "BUSINESS_CONFIG_REQUIRED",
        "code": "BUSINESS_CONFIG_REQUIRED",
        "amount": None,
        "unit": None,
        "applies_to": [],
    }
    shuku_root_id = resolve_shuku_root_id(org_unit_id)
    if not shuku_root_id:
        return missing
    try:
        row = fetch_one(
            "SELECT t.fee_amount, t.fee_unit "
            "FROM enrollment_shuku_profile_terms t "
            "JOIN enrollment_shuku_profiles p "
            "ON p.shuku_org_unit_id=t.shuku_org_unit_id "
            "WHERE t.shuku_org_unit_id=? AND t.is_active=1 AND p.is_active=1 "
            "LIMIT 1",
            (shuku_root_id,),
        )
    except Exception as exc:
        message = str(exc).lower()
        if "no such table" in message or "doesn't exist" in message:
            return missing
        raise
    amount = normalize_fee_amount(row.get("fee_amount")) if row else None
    unit = str(row.get("fee_unit") or "").strip() if row else ""
    if not amount or not unit:
        return missing
    return {
        "status": "READY",
        "code": None,
        "amount": amount,
        "unit": unit,
        "applies_to": list(ANNUAL_MEMBER_SERVICE_FEE_APPLIES_TO),
    }
