"""Read-only renewal/member status reconciliation helpers."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.db import fetch_all
from app.services.iam import accessible_org_ids
from app.services.renewal_analytics import _classify_completion_evidence, _json_datetime
from app.services.renewals import MEMBER_RENEWAL_ORG_SQL


def list_member_status_reconciliation(
    user_id: int,
    *,
    org_unit_id: str | None = None,
) -> dict[str, Any]:
    """List renewed cycles whose current member master is still inactive.

    This endpoint intentionally reports the inconsistency without changing the
    member, cycle, or history tables.  Organization attribution always follows
    ``members.org_unit_id``; the cycle's imported organization is not used for
    scope, filtering, or authorization.
    """
    allowed = accessible_org_ids(user_id)
    if org_unit_id and allowed is not None and org_unit_id not in allowed:
        raise PermissionError("组织不在当前用户授权范围内")

    conditions = ["c.status='RENEWED'", "UPPER(m.status)='INACTIVE'"]
    params: list[Any] = []
    if org_unit_id:
        conditions.append(f"{MEMBER_RENEWAL_ORG_SQL}=?")
        params.append(org_unit_id)

    rows = fetch_all(
        "SELECT m.id AS member_id, m.name AS member_name, m.status AS member_status, "
        "c.id AS renewal_cycle_id, c.renewal_year, c.status AS renewal_status, "
        "c.completed_at, c.source_batch_id, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id "
        "WHERE " + " AND ".join(conditions) + " "
        "ORDER BY c.renewal_year DESC, c.id DESC",
        tuple(params),
    )
    if allowed is not None:
        rows = [row for row in rows if row.get("org_unit_id") in allowed]

    history_conditions = ["c.status='RENEWED'", "UPPER(m.status)='INACTIVE'"]
    history_params: list[Any] = []
    if org_unit_id:
        history_conditions.append(f"{MEMBER_RENEWAL_ORG_SQL}=?")
        history_params.append(org_unit_id)
    histories = fetch_all(
        "SELECT h.id, h.renewal_cycle_id, h.from_status, h.to_status, h.reason, h.created_at "
        "FROM renewal_status_history h "
        "JOIN renewal_cycles c ON c.id=h.renewal_cycle_id "
        "JOIN members m ON m.id=c.member_id "
        "WHERE " + " AND ".join(history_conditions) + " ORDER BY h.renewal_cycle_id, h.id",
        tuple(history_params),
    )
    if allowed is not None:
        visible_cycle_ids = {int(row["renewal_cycle_id"]) for row in rows}
        histories = [
            history
            for history in histories
            if int(history["renewal_cycle_id"]) in visible_cycle_ids
        ]
    histories_by_cycle: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for history in histories:
        histories_by_cycle[int(history["renewal_cycle_id"])].append(history)

    result: list[dict[str, Any]] = []
    for row in rows:
        cycle = {
            "status": row["renewal_status"],
            "completed_at": row["completed_at"],
            "source_batch_id": row["source_batch_id"],
        }
        evidence = _classify_completion_evidence(
            cycle, histories_by_cycle.get(int(row["renewal_cycle_id"]), [])
        )
        result.append(
            {
                "member_id": row["member_id"],
                "member_name": row["member_name"],
                "member_status": row["member_status"],
                "renewal_cycle_id": row["renewal_cycle_id"],
                "renewal_year": row["renewal_year"],
                "renewal_status": row["renewal_status"],
                "completed_at": _json_datetime(row["completed_at"]),
                "evidence": evidence["evidence_type"],
                "completion_time_reliable": bool(
                    evidence["completion_time_reliable"]
                ),
            }
        )
    return {
        "count": len(result),
        "rows": result,
        "policy": "仅列出当前主档仍为停用但续费周期已记录为已续费的学员；本接口只读，不自动恢复状态。",
    }
