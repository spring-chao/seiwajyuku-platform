"""Read-only annual renewal outcome and trustworthy timing analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from typing import Any

from app.db import fetch_all
from app.services.iam import accessible_org_ids
from app.services.renewals import (
    MEMBER_RENEWAL_ORG_SQL,
    determine_calendar_stage,
)


LIVE_STATUS_TRANSITION = "LIVE_STATUS_TRANSITION"
IMPORT_SNAPSHOT = "IMPORT_SNAPSHOT"
HISTORICAL_AUTO_RECONCILIATION = "HISTORICAL_AUTO_RECONCILIATION"
UNKNOWN = "UNKNOWN"
EVIDENCE_TYPES = (
    LIVE_STATUS_TRANSITION,
    IMPORT_SNAPSHOT,
    HISTORICAL_AUTO_RECONCILIATION,
    UNKNOWN,
)
TERMINAL_STATUSES = frozenset({"RENEWED", "NOT_RENEWING", "EXITED"})
TIMING_STAGE_LABELS = {
    "PREPARE": "观3之前",
    "OBSERVE_3": "观3",
    "RENEW_2": "续2",
    "FOLLOW_1": "追1",
    "DUE_NOW": "到期月",
    "RECOVERY": "到期后",
}
TIMING_STAGES = tuple(TIMING_STAGE_LABELS)
HISTORICAL_REASON_MARKERS = (
    "历史月份自动",
    "历史续费月份",
    "自动标记为已续费",
    "auto_historical",
    "historical_reconciliation",
)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_datetime(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else None


def _timestamps_correspond(completed_at: Any, history_created_at: Any) -> bool:
    completed = _parse_datetime(completed_at)
    changed = _parse_datetime(history_created_at)
    if not completed or not changed:
        return False
    return abs((completed - changed).total_seconds()) <= 5


def _contains_marker(reason: Any, markers: tuple[str, ...]) -> bool:
    text = str(reason or "").strip().lower()
    return bool(text) and any(marker.lower() in text for marker in markers)


def _classify_completion_evidence(cycle: dict[str, Any], histories: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify evidence without treating a stored completed_at as proof by itself."""
    status = str(cycle.get("status") or "").upper()
    latest_history = histories[-1] if histories else None
    historical_history = next(
        (item for item in reversed(histories) if _contains_marker(item.get("reason"), HISTORICAL_REASON_MARKERS)),
        None,
    )
    if historical_history:
        evidence_type = HISTORICAL_AUTO_RECONCILIATION
    elif cycle.get("source_batch_id") is not None:
        evidence_type = IMPORT_SNAPSHOT
    else:
        evidence_type = UNKNOWN

    reliable = False
    if status == "RENEWED" and cycle.get("completed_at") and not historical_history:
        live_history = next(
            (
                item
                for item in reversed(histories)
                if str(item.get("to_status") or "").upper() == "RENEWED"
                and item.get("from_status") is not None
                and _timestamps_correspond(cycle.get("completed_at"), item.get("created_at"))
            ),
            None,
        )
        if live_history and cycle.get("source_batch_id") is None:
            evidence_type = LIVE_STATUS_TRANSITION
            reliable = True

    return {
        "evidence_type": evidence_type,
        "completion_time_reliable": reliable,
        "result_recorded": status in TERMINAL_STATUSES,
        "history_id": latest_history.get("id") if latest_history else None,
    }


def _empty_stage_counts() -> dict[str, int]:
    return {stage: 0 for stage in TIMING_STAGES}


def _empty_outcome_counts() -> dict[str, int]:
    return {}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _organization_row(org_unit_id: str | None, org_name: str | None) -> dict[str, Any]:
    return {
        "org_unit_id": org_unit_id,
        "org_name": org_name or ("未归属组织" if not org_unit_id else org_unit_id),
        "total_cycles": 0,
        "renewed_count": 0,
        "not_renewing_count": 0,
        "exited_count": 0,
        "deferred_count": 0,
        "open_count": 0,
        "reliable_completion_count": 0,
        "unreliable_completion_count": 0,
        "before_due_count": 0,
        "due_month_count": 0,
        "after_due_count": 0,
        "before_due_rate_among_reliable_renewals": None,
    }


def get_annual_renewal_analytics(
    user_id: int,
    year: int,
    *,
    org_unit_id: str | None = None,
) -> dict[str, Any]:
    """Return scoped annual outcomes and timing only for reliable completions."""
    if not 2020 <= int(year) <= 2100:
        raise ValueError("续费年度必须在2020至2100之间")
    allowed = accessible_org_ids(user_id)
    if org_unit_id and allowed is not None and org_unit_id not in allowed:
        raise PermissionError("组织不在当前用户授权范围内")

    cycles = fetch_all(
        "SELECT c.id AS cycle_id, c.member_id, c.renewal_year, c.due_month, c.status, "
        "c.completed_at, c.source_batch_id, m.name AS member_name, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, o.name AS org_name "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id "
        f"LEFT JOIN org_units o ON o.id={MEMBER_RENEWAL_ORG_SQL} "
        "WHERE c.renewal_year=? ORDER BY c.id",
        (int(year),),
    )
    scoped_cycles = [
        row
        for row in cycles
        if (allowed is None or row.get("org_unit_id") in allowed)
        and (not org_unit_id or row.get("org_unit_id") == org_unit_id)
    ]
    cycle_ids = [int(row["cycle_id"]) for row in scoped_cycles]
    histories_by_cycle: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if cycle_ids:
        placeholders = ",".join("?" for _ in cycle_ids)
        histories = fetch_all(
            "SELECT id, renewal_cycle_id, from_status, to_status, reason, created_at "
            f"FROM renewal_status_history WHERE renewal_cycle_id IN ({placeholders}) "
            "ORDER BY id",
            tuple(cycle_ids),
        )
        for history in histories:
            histories_by_cycle[int(history["renewal_cycle_id"])].append(history)

    outcome_counts = Counter(str(row.get("status") or "UNKNOWN").upper() for row in scoped_cycles)
    renewed_count = outcome_counts.get("RENEWED", 0)
    not_renewing_count = outcome_counts.get("NOT_RENEWING", 0)
    exited_count = outcome_counts.get("EXITED", 0)
    deferred_count = outcome_counts.get("DEFERRED", 0)
    open_count = sum(
        count for status, count in outcome_counts.items() if status not in TERMINAL_STATUSES
    )
    evidence_counts = Counter({evidence: 0 for evidence in EVIDENCE_TYPES})
    timing_counts = _empty_stage_counts()
    evidence_samples: list[dict[str, Any]] = []
    org_rows: dict[str | None, dict[str, Any]] = {}
    before_due_count = due_month_count = after_due_count = 0
    reliable_count = 0

    for cycle in scoped_cycles:
        cycle_id = int(cycle["cycle_id"])
        status = str(cycle.get("status") or "UNKNOWN").upper()
        evidence = _classify_completion_evidence(cycle, histories_by_cycle.get(cycle_id, []))
        evidence_counts[evidence["evidence_type"]] += 1 if status == "RENEWED" else 0
        reliable = bool(evidence["completion_time_reliable"] and status == "RENEWED")
        if status == "RENEWED" and not reliable:
            evidence["completion_time_reliable"] = False
        org_id = cycle.get("org_unit_id")
        if org_id not in org_rows:
            org_rows[org_id] = _organization_row(org_id, cycle.get("org_name"))
        org = org_rows[org_id]
        org["total_cycles"] += 1
        status_key = status.lower() + "_count"
        if status_key in org:
            org[status_key] += 1
        if status not in TERMINAL_STATUSES:
            org["open_count"] += 1
        if status == "RENEWED":
            if reliable:
                reliable_count += 1
                org["reliable_completion_count"] += 1
                completion = _parse_datetime(cycle.get("completed_at"))
                calendar_stage = determine_calendar_stage(
                    int(cycle["renewal_year"]), int(cycle["due_month"]), as_of=completion.date() if completion else None
                )
                stage = calendar_stage["code"]
                timing_counts[stage] += 1
                if stage in {"PREPARE", "OBSERVE_3", "RENEW_2", "FOLLOW_1"}:
                    before_due_count += 1
                    org["before_due_count"] += 1
                elif stage == "DUE_NOW":
                    due_month_count += 1
                    org["due_month_count"] += 1
                else:
                    after_due_count += 1
                    org["after_due_count"] += 1
                evidence["completion_stage"] = stage
                evidence["completion_stage_label"] = TIMING_STAGE_LABELS[stage]
                evidence["completion_date"] = completion.date().isoformat() if completion else None
            else:
                org["unreliable_completion_count"] += 1
            evidence_samples.append(
                {
                    "cycle_id": cycle_id,
                    "member_id": cycle["member_id"],
                    "member_name": cycle.get("member_name"),
                    "org_unit_id": org_id,
                    "org_name": cycle.get("org_name") or ("未归属组织" if not org_id else org_id),
                    "due_month": cycle["due_month"],
                    "status": status,
                    "evidence_type": evidence["evidence_type"],
                    "completion_time_reliable": reliable,
                    "result_recorded": evidence["result_recorded"],
                    "completion_stage": evidence.get("completion_stage"),
                    "completion_stage_label": evidence.get("completion_stage_label"),
                    "completion_date": evidence.get("completion_date"),
                }
            )

    for org in org_rows.values():
        org["before_due_rate_among_reliable_renewals"] = _rate(
            org["before_due_count"], org["reliable_completion_count"]
        )
    organizations = sorted(
        org_rows.values(), key=lambda row: (str(row.get("org_name") or ""), str(row.get("org_unit_id") or ""))
    )
    completion_quality = {
        "renewed_count": renewed_count,
        "reliable_completion_count": reliable_count,
        "unreliable_completion_count": renewed_count - reliable_count,
        "reliable_completion_rate": _rate(reliable_count, renewed_count),
        "evidence_counts": dict(evidence_counts),
    }
    outcome_summary = {
        "total_cycles": len(scoped_cycles),
        "renewed_count": renewed_count,
        "not_renewing_count": not_renewing_count,
        "exited_count": exited_count,
        "deferred_count": deferred_count,
        "open_count": open_count,
        "outcome_status_counts": dict(sorted(outcome_counts.items())),
    }
    timing_distribution = {
        "reliable_renewed_total": reliable_count,
        "stage_counts": timing_counts,
        "stage_labels": TIMING_STAGE_LABELS,
        "before_due_count": before_due_count,
        "due_month_count": due_month_count,
        "after_due_count": after_due_count,
        "before_due_rate_among_reliable_renewals": _rate(before_due_count, reliable_count),
    }
    return {
        "year": int(year),
        "as_of": datetime.now(UTC).isoformat(),
        "org_unit_id": org_unit_id,
        "outcome_summary": outcome_summary,
        "completion_quality": completion_quality,
        "timing_distribution": timing_distribution,
        "organizations": organizations,
        "evidence_samples": evidence_samples,
        "policy": "年度结果基于当前状态；续费节奏仅统计具有可信完成时点的已续费周期，不代表全部已续费学长。",
        # Flat aliases keep the read-only contract easy for exports and small clients.
        **outcome_summary,
        **completion_quality,
        "stage_counts": timing_counts,
        "before_due_count": before_due_count,
        "due_month_count": due_month_count,
        "after_due_count": after_due_count,
        "before_due_rate_among_reliable_renewals": timing_distribution[
            "before_due_rate_among_reliable_renewals"
        ],
    }
