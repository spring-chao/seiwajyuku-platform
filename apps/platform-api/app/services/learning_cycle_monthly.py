"""Compatibility entry points for the retired monthly progression job.

Existing timers and read callers can keep calling these functions. Natural
month changes never write cycle state or apply historical calendar repairs;
uploaded class meetings advance only after successful credit settlement.
"""

from typing import Any


def enabled() -> bool:
    # An old deployment flag or timer must not reactivate the former policy.
    return False


def monthly_status(binding: dict, cycle: dict | None, at: str, *, connection=None) -> dict[str, Any]:
    return {"enabled": False, "timezone": "Asia/Shanghai", "status": "SETTLEMENT_DRIVEN",
            "progression_mode": "CLASS_MEETING_SETTLEMENT", "due_cycles": 0, "next_refresh_at": None}


def refresh_in_connection(connection, class_org_unit_id: str, *, at: str) -> dict[str, Any]:
    return {"status": "DISABLED", "advanced": 0, "progression_mode": "CLASS_MEETING_SETTLEMENT"}


def refresh_class(class_org_unit_id: str, *, at: str | None = None) -> dict[str, Any]:
    return refresh_in_connection(None, class_org_unit_id, at=at or "")


def refresh_all() -> dict[str, int]:
    return {"scanned": 0, "updated": 0, "failed": 0}


def refresh_sweep(*, startup_repair: bool = False) -> dict[str, Any]:
    return {"enabled": False, "scanned": 0, "updated": 0, "failed": 0, "repair_required": 0, "review_required": 0,
            "progression_mode": "CLASS_MEETING_SETTLEMENT"}


async def run_monthly_refresh() -> None:
    return
