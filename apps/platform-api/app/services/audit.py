from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.db import execute


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
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            request_id,
            datetime.now(UTC).isoformat(),
        ),
    )
