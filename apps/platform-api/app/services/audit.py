from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from app.db import execute


def _audit_json_default(value: Any) -> str:
    """Keep audit payloads JSON-safe across SQLite and MySQL drivers.

    SQLite commonly returns timestamp fields as strings, whereas the production
    MySQL driver returns ``datetime`` instances.  Audit records must never make
    an otherwise valid business transaction fail merely because a before/after
    snapshot contains such a timestamp.
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _audit_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=_audit_json_default)


def write_audit(
    connection,
    *,
    actor_user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    org_unit_id: str | None = None,
    purpose: str | None = None,
    result: str = "SUCCESS",
    before: Any = None,
    after: Any = None,
    request_id: str | None = None,
) -> None:
    execute(
        connection,
        "INSERT INTO audit_logs "
        "(actor_user_id, action, resource_type, resource_id, org_unit_id, purpose, result, "
        "before_json, after_json, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            actor_user_id,
            action,
            resource_type,
            resource_id,
            org_unit_id,
            purpose,
            result,
            _audit_json(before),
            _audit_json(after),
            request_id,
            datetime.now(UTC).isoformat(),
        ),
    )
