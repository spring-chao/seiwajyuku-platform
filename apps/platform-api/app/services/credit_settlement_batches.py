"""Gated study-meeting credit batches; no public API calls this module yet."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.db import atomic_transaction, connect, execute, transaction
from app.services.audit import write_audit
from app.services import learning_credits as credits
from app.services import class_meeting_credits as class_credits
from app.services import learning_activity_credits as activity_credits
from app.services import historical_credit_import as historical_credits


HISTORICAL_BATCH_TYPE = "HISTORICAL_IMPORT"
HISTORICAL_SOURCE_TYPE = historical_credits.HISTORICAL_SOURCE_RULE_VERSION
_CONFIRMED_CLASS_MAPPING_STATUSES = {"EXACT", "AUTO_RESOLVED", "CONFIRMED_ALIAS"}
_BATCH_STATUSES = {
    "DRAFT", "DRY_RUN", "PENDING_APPROVAL", "APPROVED", "POSTING", "POSTED",
    "PARTIAL_FAILED", "CLOSED", "CANCELLED",
}
_BATCH_TYPES = {"REGULAR", "HISTORICAL_IMPORT", "CORRECTION"}


def _batch_tables_available(connection: Any) -> bool:
    if isinstance(connection, sqlite3.Connection):
        row = execute(
            connection,
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' "
            "AND name IN ('learning_credit_settlement_batches','learning_credit_settlement_batch_items')",
        ).fetchone()
    else:
        row = execute(
            connection,
            "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE() "
            "AND table_name IN ('learning_credit_settlement_batches','learning_credit_settlement_batch_items')",
        ).fetchone()
    return int(row["n"]) == 2


def _require_batch_manage(actor_user_id: int, *, batch_type: str | None = None) -> set[str]:
    user = credits.user_context(actor_user_id) or {}
    permissions = set(user.get("permissions", []))
    if "plans:credit_settlement_manage" not in permissions:
        raise PermissionError("无权查看或维护学分结算批次")
    if batch_type == HISTORICAL_BATCH_TYPE and "plans:historical_credit_import_manage" not in permissions:
        raise PermissionError("无权查看历史学分结算批次")
    return permissions


def _masked_member_name(value: str | None) -> str:
    name = (value or "").strip()
    if not name:
        return "*"
    if len(name) == 1:
        return f"{name}*"
    if len(name) == 2:
        return f"{name[0]}*"
    return f"{name[0]}{'*' * (len(name) - 2)}{name[-1]}"


def _batch_list_payload(row: Any) -> dict[str, Any]:
    try:
        result_snapshot = json.loads(row["result_snapshot_json"] or "{}")
        raw_duplicate_count = result_snapshot.get("duplicate_entry_count")
        duplicate_entry_count = (
            int(raw_duplicate_count)
            if isinstance(raw_duplicate_count, int) and raw_duplicate_count >= 0
            else None
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        duplicate_entry_count = None
    return {
        "id": int(row["id"]),
        "batch_no": row["batch_no"],
        "batch_type": row["batch_type"],
        "source_type": row["source_type"],
        "class_org_unit_id": row["class_org_unit_id"],
        "class_name": row["class_name"],
        "period_precision": row["period_precision"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "period_year": row["period_year"],
        "period_month": row["period_month"],
        "status": row["status"],
        "proposed_entry_count": int(row["proposed_entry_count"]),
        "proposed_points": format(Decimal(str(row["proposed_points"])), ".2f"),
        "blocked_count": int(row["blocked_count"]),
        "duplicate_entry_count": duplicate_entry_count,
        "posted_entry_count": int(row["posted_entry_count"]),
        "posted_points": format(Decimal(str(row["posted_points"])), ".2f"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source_fingerprint": row["source_fingerprint"],
        "rule_fingerprint": row["rule_fingerprint"],
    }


def list_settlement_batches(
    *, actor_user_id: int, status: str | None = None,
    batch_type: str | None = None, class_org_unit_id: str | None = None,
    limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    """List batches visible in the actor's organization scope, without PII."""

    if status is not None and status not in _BATCH_STATUSES:
        raise credits.LearningCreditError("结算批次状态筛选无效")
    if batch_type is not None and batch_type not in _BATCH_TYPES:
        raise credits.LearningCreditError("结算批次类型筛选无效")
    if not 1 <= limit <= 100 or offset < 0:
        raise credits.LearningCreditError("结算批次分页范围无效")
    permissions = _require_batch_manage(actor_user_id, batch_type=batch_type)
    settings = credits.get_settings()
    connection = connect()
    try:
        gates = {
            "dry_run_enabled": settings.learning_credit_batch_dry_run_enabled,
            "approval_enabled": settings.learning_credit_batch_approval_enabled,
            "post_enabled": settings.learning_credit_batch_post_enabled,
            "settlement_enabled": settings.learning_credit_settlement_enabled,
            "historical_post_enabled": settings.learning_credit_historical_post_enabled,
        }
        if not _batch_tables_available(connection):
            return {
                "storage_available": False, "feature_gates": gates,
                "total_count": 0, "batches": [],
                "status_counts": {value: 0 for value in _BATCH_STATUSES},
            }

        base_conditions: list[str] = []
        base_params: list[Any] = []
        if batch_type:
            base_conditions.append("b.batch_type=?")
            base_params.append(batch_type)
        elif "plans:historical_credit_import_manage" not in permissions:
            base_conditions.append("b.batch_type<>'HISTORICAL_IMPORT'")
        if class_org_unit_id:
            base_conditions.append("b.class_org_unit_id=?")
            base_params.append(class_org_unit_id)
        allowed = credits.accessible_org_ids(actor_user_id)
        if allowed is not None:
            scoped_ids = sorted(str(value) for value in allowed)
            if not scoped_ids:
                base_conditions.append("1=0")
            else:
                base_conditions.append("b.class_org_unit_id IN (" + ",".join("?" for _ in scoped_ids) + ")")
                base_params.extend(scoped_ids)
        conditions = [*base_conditions]
        params = [*base_params]
        if status:
            conditions.append("b.status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        base_where = " WHERE " + " AND ".join(base_conditions) if base_conditions else ""
        count_row = execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_settlement_batches b" + where,
            tuple(params),
        ).fetchone()
        rows = execute(
            connection,
            "SELECT b.*,c.name AS class_name FROM learning_credit_settlement_batches b "
            "LEFT JOIN org_units c ON c.id=b.class_org_unit_id" + where +
            " ORDER BY b.created_at DESC,b.id DESC LIMIT ? OFFSET ?",
            tuple([*params, limit, offset]),
        ).fetchall()
        status_rows = execute(
            connection,
            "SELECT b.status,COUNT(*) AS n FROM learning_credit_settlement_batches b" +
            base_where + " GROUP BY b.status",
            tuple(base_params),
        ).fetchall()
        status_counts = {value: 0 for value in _BATCH_STATUSES}
        status_counts.update({str(row["status"]): int(row["n"]) for row in status_rows})
        return {
            "storage_available": True,
            "feature_gates": gates,
            "total_count": int(count_row["n"]),
            "status_counts": status_counts,
            "batches": [_batch_list_payload(row) for row in rows],
        }
    finally:
        connection.close()


def get_settlement_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    """Read one frozen batch and masked item identities for operations review."""

    _require_batch_manage(actor_user_id)
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT b.*,c.name AS class_name FROM learning_credit_settlement_batches b "
            "LEFT JOIN org_units c ON c.id=b.class_org_unit_id WHERE b.id=?",
            (batch_id,),
        ).fetchone()
        if not row:
            raise credits.LearningCreditError("结算批次不存在")
        if row["batch_type"] == HISTORICAL_BATCH_TYPE:
            _require_batch_manage(actor_user_id, batch_type=HISTORICAL_BATCH_TYPE)
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        items = execute(
            connection,
            "SELECT i.*,m.name AS member_name FROM learning_credit_settlement_batch_items i "
            "LEFT JOIN members m ON m.id=i.member_id WHERE i.batch_id=? ORDER BY i.id",
            (batch_id,),
        ).fetchall()
        item_payloads = []
        for item in items:
            source = json.loads(item["source_snapshot_json"] or "{}")
            if row["batch_type"] == HISTORICAL_BATCH_TYPE:
                source_ref = {
                    "source_sheet": source.get("source_sheet"),
                    "source_row_number": source.get("source_row_number"),
                    "source_column_name": source.get("source_column_name"),
                }
            else:
                source_ref = {"source_type": item["source_type"]}
            item_payloads.append(
                {
                    "id": int(item["id"]),
                    "member_id": int(item["member_id"]) if item["member_id"] is not None else None,
                    "member_name_masked": _masked_member_name(item["member_name"]),
                    "source_ref": source_ref,
                    "rule_key": item["rule_key"],
                    "rule_version": item["rule_version"],
                    "credit_category": item["credit_category"],
                    "credit_type": item["credit_type"],
                    "points": format(Decimal(str(item["points"])), ".2f") if item["points"] is not None else None,
                    "period_precision": item["occurred_precision"],
                    "period_year": int(item["occurred_year"]),
                    "period_month": int(item["occurred_month"]) if item["occurred_month"] is not None else None,
                    "status": item["status"],
                    "blocking_reason": item["blocking_reason"],
                    "error_code": item["error_code"],
                    "ledger_entry_id": int(item["ledger_entry_id"]) if item["ledger_entry_id"] is not None else None,
                }
            )
        result = _batch_list_payload(row)
        result.update(
            {
                "approved_by": row["approved_by"],
                "approved_at": row["approved_at"],
                "approval_fingerprint": row["approval_fingerprint"],
                "created_by_current_actor": (
                    row["created_by"] is not None
                    and int(row["created_by"]) == int(actor_user_id)
                ),
                "items": item_payloads,
            }
        )
        return result


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _points(value: Any) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise credits.LearningCreditError("结算提案分值必须是两位小数以内的有限数")
    return amount


def _preview_snapshots(preview: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    session = preview["session"]
    facts = []
    rules = []
    for item in preview["entries"]:
        status = "READY" if item["status"] == "SKIPPED_DUPLICATE" else item["status"]
        facts.append(
            {
                "member_id": int(item["member_id"]),
                "entry_kind": item["entry_kind"],
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "learning_cycle_id": item.get("learning_cycle_id"),
                "idempotency_key": item.get("idempotency_key"),
                "course_key": item.get("course_key"),
                "completion_status": item.get("completion_status"),
                "status": status,
            }
        )
        rules.append(
            {
                "idempotency_key": item.get("idempotency_key"),
                "rule_snapshot": item.get("rule_snapshot"),
            }
        )
    facts.sort(key=lambda row: (str(row["idempotency_key"]), row["member_id"]))
    rules.sort(key=lambda row: str(row["idempotency_key"]))
    return (
        {
            "session_id": int(session["id"]),
            "session_code": session["session_code"],
            "session_status": session["status"],
            "class_org_unit_id": session["class_org_unit_id"],
            "learning_cycle_id": int(session["learning_cycle_id"]),
            "meeting_date": str(session["meeting_date"])[:10],
            "facts": facts,
        },
        {"rules": rules},
    )


def _batch_summary(connection: Any, batch_id: int, *, idempotent: bool) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT id,batch_no,status,proposed_entry_count,proposed_points,blocked_count,"
        "posted_entry_count,posted_points,source_fingerprint,rule_fingerprint "
        "FROM learning_credit_settlement_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    return {
        "id": int(row["id"]),
        "batch_no": row["batch_no"],
        "status": row["status"],
        "proposed_entry_count": int(row["proposed_entry_count"]),
        "proposed_points": format(Decimal(str(row["proposed_points"])), ".2f"),
        "blocked_count": int(row["blocked_count"]),
        "posted_entry_count": int(row["posted_entry_count"]),
        "posted_points": format(Decimal(str(row["posted_points"])), ".2f"),
        "source_fingerprint": row["source_fingerprint"],
        "rule_fingerprint": row["rule_fingerprint"],
        "idempotent": idempotent,
    }


def _historical_source_groups(
    connection: Any,
    *,
    actor_user_id: int,
    import_batch_id: int,
    allow_existing_ledger_keys: set[str] | None = None,
) -> tuple[dict[tuple[str, str, int, int | None], dict[str, Any]], dict[str, int], dict[str, Any]]:
    """Build immutable legacy proposals partitioned by confirmed class and period."""

    dry_run = historical_credits.dry_run_historical_credit_import(
        import_batch_id, allow_existing_ledger_keys=allow_existing_ledger_keys,
    )
    source_batch = dry_run["batch"]
    if (
        source_batch.get("import_type") != historical_credits.HISTORICAL_IMPORT_TYPE
        or source_batch.get("source_rule_version") != HISTORICAL_SOURCE_TYPE
    ):
        raise credits.LearningCreditError("历史来源类型或固定来源规则版本不匹配")

    mappings = execute(
        connection,
        "SELECT id,source_sheet,raw_class_name,confirmed_org_unit_id,mapping_status "
        "FROM learning_credit_import_class_mappings WHERE batch_id=?",
        (import_batch_id,),
    ).fetchall()
    mapping_by_key: dict[tuple[str, str | None], Any] = {}
    ambiguous_mapping_keys: set[tuple[str, str | None]] = set()
    for mapping in mappings:
        key = (str(mapping["source_sheet"]), mapping["raw_class_name"])
        if key in mapping_by_key:
            ambiguous_mapping_keys.add(key)
        mapping_by_key[key] = mapping

    groups: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    extra_blockers: dict[str, int] = {}
    org_units: dict[str, Any | None] = {}
    members: dict[int, Any | None] = {}
    ready_ids = {int(item["id"]) for item in dry_run["details"]}
    source_items = [*dry_run["details"], *dry_run["blocked_details"]]

    def block(reason: str) -> None:
        extra_blockers[reason] = extra_blockers.get(reason, 0) + 1

    for item in source_items:
        mapping_key = (str(item["source_sheet"]), item.get("raw_class_name"))
        mapping = mapping_by_key.get(mapping_key)
        if mapping_key in ambiguous_mapping_keys or not mapping:
            block("CLASS_MAPPING_REQUIRED")
            continue
        class_id = mapping["confirmed_org_unit_id"]
        if (
            mapping["mapping_status"] not in _CONFIRMED_CLASS_MAPPING_STATUSES
            or not class_id
        ):
            block("CLASS_MAPPING_REQUIRED")
            continue
        class_id = str(class_id)
        if class_id not in org_units:
            org_units[class_id] = execute(
                connection,
                "SELECT id,unit_type FROM org_units WHERE id=?",
                (class_id,),
            ).fetchone()
        unit = org_units[class_id]
        if not unit or unit["unit_type"] not in {"CLASS", "SPECIAL_COHORT"}:
            block("CLASS_MAPPING_TARGET_INVALID")
            continue
        if not credits._scope_allows(actor_user_id, class_id):
            block("OUT_OF_SCOPE")
            continue

        precision = str(item.get("occurred_precision") or "")
        year = int(item["occurred_year"]) if item.get("occurred_year") is not None else None
        month = int(item["occurred_month"]) if item.get("occurred_month") is not None else None
        if precision not in {"MONTH", "YEAR"} or year is None or (
            (precision == "MONTH" and month is None)
            or (precision == "YEAR" and month is not None)
        ):
            block("PERIOD_PRECISION_INVALID")
            continue

        member_id = int(item["matched_member_id"]) if item.get("matched_member_id") is not None else None
        extra_reason = None
        if member_id is not None:
            if member_id not in members:
                members[member_id] = execute(
                    connection, "SELECT id,status FROM members WHERE id=?", (member_id,)
                ).fetchone()
            if not members[member_id] or str(members[member_id]["status"]).upper() != "ACTIVE":
                extra_reason = "MEMBER_NOT_ACTIVE"

        idempotency_key = historical_credits.historical_credit_idempotency_key(
            file_sha256=str(source_batch["file_sha256"]),
            source_sheet=str(item["source_sheet"]),
            source_row_number=int(item["source_row_number"]),
            source_column_index=int(item["source_column_index"]),
        )
        ready = int(item["id"]) in ready_ids and extra_reason is None
        reason = extra_reason or item.get("blocked_reason")
        source_snapshot = {
            "import_batch_id": int(import_batch_id),
            "source_item_id": int(item["id"]),
            "source_row_id": int(item["source_row_id"]),
            "source_file_sha256": str(source_batch["file_sha256"]),
            "source_rule_version": HISTORICAL_SOURCE_TYPE,
            "source_sheet": str(item["source_sheet"]),
            "source_row_number": int(item["source_row_number"]),
            "source_column_index": int(item["source_column_index"]),
            "source_column_name": str(item["source_column_name"]),
            "raw_class_name": item.get("raw_class_name"),
            "class_mapping_id": int(mapping["id"]),
            "class_mapping_status": str(mapping["mapping_status"]),
            "class_org_unit_id": class_id,
            "member_id": member_id,
            "legacy_credit_type": str(item["legacy_credit_type"]),
            "original_points": format(_points(item["points"]), ".2f"),
            "occurred_precision": precision,
            "occurred_year": year,
            "occurred_month": month,
        }
        rule_snapshot = {
            "source_rule_version": HISTORICAL_SOURCE_TYPE,
            "legacy_credit_type": str(item["legacy_credit_type"]),
            "original_points": format(_points(item["points"]), ".2f"),
            "calculation": "PRESERVE_SOURCE_VALUE_NO_RECALCULATION",
            "source_file_sha256": str(source_batch["file_sha256"]),
        }
        group_key = (class_id, precision, year, month)
        group = groups.setdefault(
            group_key,
            {"class_org_unit_id": class_id, "period_precision": precision,
             "period_year": year, "period_month": month, "items": []},
        )
        group["items"].append(
            {
                **source_snapshot,
                "source_id": f"{HISTORICAL_SOURCE_TYPE}:{idempotency_key.removeprefix('LC-HIST:')}",
                "idempotency_key": idempotency_key,
                "member_id": member_id,
                "credit_category": str(item["credit_category"]),
                "credit_type": str(item["legacy_credit_type"]),
                "rule_key": HISTORICAL_SOURCE_TYPE,
                "rule_version": HISTORICAL_SOURCE_TYPE,
                "rule_version_id": None,
                "points": _points(item["points"]),
                "rule_snapshot": rule_snapshot,
                "status": "READY" if ready else "BLOCKED",
                "blocked_reason": None if ready else (reason or "HISTORICAL_REVIEW_REQUIRED"),
            }
        )

    for group in groups.values():
        group["items"].sort(key=lambda item: item["idempotency_key"])
        facts = [
            {
                "source_item_id": item["source_item_id"],
                "source_id": item["source_id"],
                "idempotency_key": item["idempotency_key"],
                "member_id": item["member_id"],
                "class_mapping_id": item["class_mapping_id"],
                "class_mapping_status": item["class_mapping_status"],
                "credit_category": item["credit_category"],
                "credit_type": item["credit_type"],
                "original_points": item["original_points"],
                "status": item["status"],
                "blocked_reason": item["blocked_reason"],
                "source_sheet": item["source_sheet"],
                "source_row_number": item["source_row_number"],
                "source_column_index": item["source_column_index"],
            }
            for item in group["items"]
        ]
        rules = [
            {"idempotency_key": item["idempotency_key"], "rule_snapshot": item["rule_snapshot"]}
            for item in group["items"]
        ]
        group["source_snapshot"] = {
            "import_batch_id": int(import_batch_id),
            "source_file_sha256": str(source_batch["file_sha256"]),
            "source_rule_version": HISTORICAL_SOURCE_TYPE,
            "class_org_unit_id": group["class_org_unit_id"],
            "period_precision": group["period_precision"],
            "period_year": group["period_year"],
            "period_month": group["period_month"],
            "facts": facts,
        }
        group["rule_snapshot"] = {"rules": rules}
        group["source_fingerprint"] = _fingerprint(group["source_snapshot"])
        group["rule_fingerprint"] = _fingerprint(group["rule_snapshot"])
        group["preview"] = {
            "entries": [
                {
                    "member_id": item["member_id"],
                    "idempotency_key": item["idempotency_key"],
                    "points": item["points"],
                    "status": item["status"],
                    "postable": item["status"] == "READY",
                }
                for item in group["items"]
            ],
            "totals": {
                "blocked_entry_count": sum(item["status"] == "BLOCKED" for item in group["items"]),
            },
        }
    return groups, extra_blockers, dry_run


def _assert_current_preview(
    connection: Any, row: Any, *, actor_user_id: int, posted_keys: set[str] | None = None
) -> None:
    source_snapshot = json.loads(row["source_snapshot_json"])
    posted_keys = posted_keys or set()
    if row["batch_type"] == HISTORICAL_BATCH_TYPE:
        if row["source_type"] != HISTORICAL_SOURCE_TYPE:
            raise credits.LearningCreditError("历史结算批次来源不匹配")
        groups, _, _ = _historical_source_groups(
            connection,
            actor_user_id=actor_user_id,
            import_batch_id=int(source_snapshot["import_batch_id"]),
            allow_existing_ledger_keys=posted_keys,
        )
        group_key = (
            str(source_snapshot["class_org_unit_id"]),
            str(source_snapshot["period_precision"]),
            int(source_snapshot["period_year"]),
            int(source_snapshot["period_month"]) if source_snapshot.get("period_month") is not None else None,
        )
        group = groups.get(group_key)
        if not group:
            raise credits.LearningCreditError("历史来源分组已不存在，必须重新DRY-RUN")
        preview = group["preview"]
        for item in preview["entries"]:
            if item.get("idempotency_key") in posted_keys:
                item["status"] = "SKIPPED_DUPLICATE"
                item["postable"] = False
        live_source, live_rules = group["source_snapshot"], group["rule_snapshot"]
        preview["totals"]["blocked_entry_count"] = sum(
            item["status"] == "BLOCKED" for item in preview["entries"]
        )
    elif row["source_type"] == "STUDY_MEETING":
        preview = credits._build_study_meeting_preview(
            connection, int(source_snapshot["session_id"])
        )
        live_source, live_rules = _preview_snapshots(preview)
    elif row["source_type"] == "CLASS_MEETING":
        preview = class_credits._build_preview(
            connection, int(source_snapshot["event_group_id"])
        )
        live_source, live_rules = _class_preview_snapshots(preview)
    elif row["source_type"] in {activity_credits.DAILY_READING, activity_credits.EXCELLENT_SHARE}:
        preview, live_source, live_rules = _activity_preview(
            connection, actor_user_id=actor_user_id, activity_type=row["source_type"],
            class_org_unit_id=source_snapshot["class_org_unit_id"],
            occurred_from=source_snapshot["occurred_from"],
            occurred_to=source_snapshot["occurred_to"],
        )
    else:
        raise credits.LearningCreditError("不支持的结算批次来源")
    active = [item for item in preview["entries"] if item.get("idempotency_key") not in posted_keys]
    posted_rows = execute(
        connection,
        "SELECT idempotency_key,points FROM learning_credit_settlement_batch_items "
        "WHERE batch_id=? AND status='POSTED'",
        (row["id"],),
    ).fetchall()
    posted_points = {
        item["idempotency_key"]: _points(item["points"]) for item in posted_rows
    }
    if (
        _fingerprint(live_source) != row["source_fingerprint"]
        or _fingerprint(live_rules) != row["rule_fingerprint"]
        or int(preview["totals"]["blocked_entry_count"]) != int(row["blocked_count"])
        or any(i["status"] == "SKIPPED_DUPLICATE" for i in active)
        or any(i["status"] != "SKIPPED_DUPLICATE" for i in preview["entries"] if i.get("idempotency_key") in posted_keys)
        or set(posted_points) != posted_keys
        or sum(i["postable"] for i in active) + len(posted_keys) != int(row["proposed_entry_count"])
        or sum((_points(i["points"]) for i in active if i["postable"]), Decimal("0"))
        + sum(posted_points.values(), Decimal("0")) != Decimal(str(row["proposed_points"]))
    ):
        raise credits.LearningCreditError("结算事实或规则已变化，必须重新DRY-RUN")


def dry_run_historical_credit_batches(
    *, actor_user_id: int, import_batch_id: int,
) -> dict[str, Any]:
    """Freeze eligible legacy source cells into class/period settlement batches."""

    settings = credits.get_settings()
    if not settings.learning_credit_batch_dry_run_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次DRY-RUN写入尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    permissions = set(user.get("permissions", []))
    if not {
        "plans:credit_settlement_manage",
        "plans:historical_credit_import_manage",
    }.issubset(permissions):
        raise PermissionError("无权创建历史学分结算批次")

    with transaction() as connection:
        before = int(execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_entries"
        ).fetchone()["n"])
        existing_historical = execute(
            connection,
            "SELECT id,status,source_snapshot_json FROM learning_credit_settlement_batches "
            "WHERE source_type=? AND batch_type=? AND status IN ('POSTING','PARTIAL_FAILED')",
            (HISTORICAL_SOURCE_TYPE, HISTORICAL_BATCH_TYPE),
        ).fetchall()
        unresolved = []
        for row in existing_historical:
            snapshot = json.loads(row["source_snapshot_json"] or "{}")
            if int(snapshot.get("import_batch_id", 0)) == int(import_batch_id):
                unresolved.append(row)
        if unresolved:
            return {
                "mode": "DRY_RUN",
                "source_batch_id": int(import_batch_id),
                "source_rule_version": HISTORICAL_SOURCE_TYPE,
                "settlement_batch_count": 0,
                "settlement_batches": [
                    _batch_summary(connection, int(row["id"]), idempotent=True)
                    for row in unresolved
                ],
                "blocking_reason": "EXISTING_HISTORICAL_BATCH_REQUIRES_RECONCILIATION",
                "ledger_write": False,
                "staging_write": False,
            }
        groups, extra_blockers, dry_run = _historical_source_groups(
            connection, actor_user_id=actor_user_id, import_batch_id=import_batch_id,
        )
        created_batches = []
        total_ready = 0
        total_ready_points = Decimal("0")
        total_group_blocked = 0
        for group in sorted(
            groups.values(),
            key=lambda value: (
                value["class_org_unit_id"], value["period_year"],
                value["period_precision"], value["period_month"] or 0,
            ),
        ):
            ready_items = [item for item in group["items"] if item["status"] == "READY"]
            blocked_items = [item for item in group["items"] if item["status"] == "BLOCKED"]
            proposed_points = sum((_points(item["points"]) for item in ready_items), Decimal("0"))
            total_ready += len(ready_items)
            total_ready_points += proposed_points
            total_group_blocked += len(blocked_items)
            existing = execute(
                connection,
                "SELECT id,rule_fingerprint FROM learning_credit_settlement_batches "
                "WHERE source_type=? AND source_fingerprint=? AND batch_type=? LIMIT 1",
                (HISTORICAL_SOURCE_TYPE, group["source_fingerprint"], HISTORICAL_BATCH_TYPE),
            ).fetchone()
            if existing:
                if existing["rule_fingerprint"] != group["rule_fingerprint"]:
                    raise credits.LearningCreditError("历史来源对应的冻结原值快照已变化")
                created_batches.append(
                    _batch_summary(connection, int(existing["id"]), idempotent=True)
                )
                continue

            class_id = str(group["class_org_unit_id"])
            period_label = (
                f"{group['period_year']:04d}{group['period_month']:02d}"
                if group["period_precision"] == "MONTH"
                else f"{group['period_year']:04d}Y"
            )
            batch_no = (
                f"LC-HIST-{hashlib.sha256(class_id.encode()).hexdigest()[:8]}-"
                f"{period_label}-{group['source_fingerprint'][:12]}"
            )
            now = credits._db_timestamp(connection)
            result_snapshot = {
                "source_item_count": len(group["items"]),
                "proposed_entry_count": len(ready_items),
                "blocked_count": len(blocked_items),
                "original_points_preserved": True,
                "source_rule_version": HISTORICAL_SOURCE_TYPE,
                "source_file_sha256": group["source_snapshot"]["source_file_sha256"],
            }
            cursor = execute(
                connection,
                "INSERT INTO learning_credit_settlement_batches "
                "(batch_no,batch_type,source_type,class_org_unit_id,period_precision,period_year,period_month,"
                "status,proposed_entry_count,proposed_points,blocked_count,source_snapshot_json,rule_snapshot_json,"
                "result_snapshot_json,source_fingerprint,rule_fingerprint,created_by,created_at,updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch_no, HISTORICAL_BATCH_TYPE, HISTORICAL_SOURCE_TYPE, class_id,
                    group["period_precision"], group["period_year"], group["period_month"],
                    len(ready_items), str(proposed_points), len(blocked_items),
                    _canonical(group["source_snapshot"]), _canonical(group["rule_snapshot"]),
                    _canonical(result_snapshot), group["source_fingerprint"],
                    group["rule_fingerprint"], actor_user_id, now, now,
                ),
            )
            batch_id = int(cursor.lastrowid)
            for item in group["items"]:
                item_status = "PROPOSED" if item["status"] == "READY" else "BLOCKED"
                execute(
                    connection,
                    "INSERT INTO learning_credit_settlement_batch_items "
                    "(batch_id,member_id,source_type,source_id,source_snapshot_json,rule_key,rule_version,"
                    "rule_version_id,rule_snapshot_json,credit_category,credit_type,points,occurred_at,"
                    "occurred_precision,occurred_year,occurred_month,idempotency_key,status,blocking_reason,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,?)",
                    (
                        batch_id, item["member_id"], HISTORICAL_SOURCE_TYPE, item["source_id"],
                        _canonical({key: item[key] for key in (
                            "import_batch_id", "source_item_id", "source_row_id", "source_file_sha256",
                            "source_rule_version", "source_sheet", "source_row_number",
                            "source_column_index", "source_column_name", "raw_class_name",
                            "class_mapping_id", "class_mapping_status", "class_org_unit_id",
                            "member_id", "legacy_credit_type", "original_points", "occurred_precision",
                            "occurred_year", "occurred_month",
                        )}),
                        item["rule_key"], item["rule_version"], item["rule_version_id"],
                        _canonical(item["rule_snapshot"]), item["credit_category"], item["credit_type"],
                        str(_points(item["points"])) if item_status == "PROPOSED" else None,
                        item["occurred_precision"], item["occurred_year"], item["occurred_month"],
                        item["idempotency_key"], item_status, item["blocked_reason"], now, now,
                    ),
                )
            execute(
                connection,
                "UPDATE learning_credit_settlement_batches SET status='DRY_RUN',updated_at=? "
                "WHERE id=? AND status='DRAFT'",
                (now, batch_id),
            )
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="learning_credit.batch.historical_dry_run",
                resource_type="learning_credit_settlement_batch",
                resource_id=str(batch_id),
                org_unit_id=class_id,
                purpose="冻结已审核历史原值提案；保留来源期间精度，不重算且不入账",
                after={
                    "source_fingerprint": group["source_fingerprint"],
                    "proposed_entry_count": len(ready_items),
                    "blocked_count": len(blocked_items),
                    "period_precision": group["period_precision"],
                },
            )
            created_batches.append(_batch_summary(connection, batch_id, idempotent=False))

        after = int(execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_entries"
        ).fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("历史批次DRY-RUN不得写入正式学分账本")

        blockers = dict(dry_run["blocked_reason_counts"])
        for reason, count in extra_blockers.items():
            current = blockers.setdefault(reason, {"count": 0, "points": 0})
            current["count"] += count
        return {
            "mode": "DRY_RUN",
            "source_batch_id": int(import_batch_id),
            "source_rule_version": HISTORICAL_SOURCE_TYPE,
            "batch_type": HISTORICAL_BATCH_TYPE,
            "settlement_batch_count": len(created_batches),
            "settlement_batches": created_batches,
            "ready_item_count": total_ready,
            "ready_points": format(total_ready_points, ".2f"),
            "group_blocked_item_count": total_group_blocked,
            "source_blocked_item_count": int(dry_run["blocked_item_count"]),
            "already_posted_item_count": int(dry_run["already_posted_item_count"]),
            "blocked_reason_counts": blockers,
            "track_counts": dry_run["track_counts"],
            "september_dual_track_items": dry_run["september_dual_track_items"],
            "september_dual_track_points": dry_run["september_dual_track_points"],
            "learning_credit_entries_delta": after - before,
            "ledger_write": False,
            "staging_write": False,
        }


def _approved_items(connection: Any, row: Any) -> list[Any]:
    items = execute(
        connection,
        "SELECT * FROM learning_credit_settlement_batch_items "
        "WHERE batch_id=? AND status='PROPOSED' ORDER BY id",
        (row["id"],),
    ).fetchall()
    if len(items) != int(row["proposed_entry_count"]) or sum(
        (Decimal(str(item["points"])) for item in items), Decimal("0")
    ) != Decimal(str(row["proposed_points"])):
        raise credits.LearningCreditError("批次提案与冻结汇总不一致")
    return list(items)


def _approval_fingerprint(row: Any, items: list[Any]) -> str:
    return _fingerprint(
        {
            "batch_id": int(row["id"]),
            "source_fingerprint": row["source_fingerprint"],
            "rule_fingerprint": row["rule_fingerprint"],
            "items": [
                {key: item[key] for key in (
                    "id", "member_id", "source_type", "source_id", "source_snapshot_json",
                    "rule_key", "rule_version", "rule_version_id", "rule_snapshot_json",
                    "credit_category", "credit_type", "points", "occurred_at",
                    "occurred_precision", "occurred_year", "occurred_month", "idempotency_key",
                )}
                for item in items
            ],
            "proposed_points": format(Decimal(str(row["proposed_points"])), ".2f"),
        }
    )


def dry_run_study_meeting_batch(*, actor_user_id: int, session_id: int) -> dict[str, Any]:
    """Persist one frozen preview batch; never insert a ledger entry."""

    if not credits.get_settings().learning_credit_batch_dry_run_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次DRY-RUN写入尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权创建学分结算批次")
    with transaction() as connection:
        session = credits._session_context(connection, session_id)
        if not session:
            raise credits.LearningCreditError("学习会记录不存在")
        if not credits._scope_allows(actor_user_id, session["class_org_unit_id"]):
            raise PermissionError("学习会记录不在当前组织授权范围内")
        before = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        preview = credits._build_study_meeting_preview(connection, session_id)
        source_snapshot, rule_snapshot = _preview_snapshots(preview)
        source_fp, rule_fp = _fingerprint(source_snapshot), _fingerprint(rule_snapshot)
        existing = execute(
            connection,
            "SELECT id,rule_fingerprint FROM learning_credit_settlement_batches "
            "WHERE source_type='STUDY_MEETING' AND source_fingerprint=? "
            "AND batch_type='REGULAR' LIMIT 1",
            (source_fp,),
        ).fetchone()
        if existing:
            if existing["rule_fingerprint"] != rule_fp:
                raise credits.LearningCreditError("结算来源对应的冻结规则已变化")
            return _batch_summary(connection, int(existing["id"]), idempotent=True)

        postable = [item for item in preview["entries"] if item["status"] == "READY" and item["postable"]]
        blocked = [item for item in preview["entries"] if item["status"] == "BLOCKED"]
        proposed_points = sum((_points(item["points"]) for item in postable), Decimal("0"))
        meeting_date = date.fromisoformat(str(session["meeting_date"])[:10])
        now = credits._db_timestamp(connection)
        batch_no = f"LC-STUDY-{session_id}-{source_fp[:12]}"
        result_snapshot = {
            "blocking_reasons": preview["blocking_reasons"],
            "duplicate_entry_count": preview["totals"]["duplicate_entry_count"],
            "no_credit_entry_count": preview["totals"]["no_credit_entry_count"],
        }
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_settlement_batches "
            "(batch_no,batch_type,source_type,class_org_unit_id,period_precision,period_start,period_end,"
            "status,proposed_entry_count,proposed_points,blocked_count,source_snapshot_json,rule_snapshot_json,"
            "result_snapshot_json,source_fingerprint,rule_fingerprint,created_by,created_at,updated_at) "
            "VALUES (?, 'REGULAR', 'STUDY_MEETING', ?, 'EXACT_DATE', ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_no, session["class_org_unit_id"], meeting_date.isoformat(), meeting_date.isoformat(),
                len(postable), str(proposed_points), len(blocked), _canonical(source_snapshot),
                _canonical(rule_snapshot), _canonical(result_snapshot), source_fp, rule_fp,
                actor_user_id, now, now,
            ),
        )
        batch_id = int(cursor.lastrowid)
        for item in postable + blocked:
            item_status = "PROPOSED" if item["status"] == "READY" else "BLOCKED"
            source_item = {
                "session_id": session_id,
                "entry_kind": item["entry_kind"],
                "member_id": int(item["member_id"]),
                "source_type": item.get("source_type"),
                "source_id": item.get("source_id"),
                "learning_cycle_id": item.get("learning_cycle_id"),
            }
            execute(
                connection,
                "INSERT INTO learning_credit_settlement_batch_items "
                "(batch_id,member_id,source_type,source_id,source_snapshot_json,rule_key,rule_version,"
                "rule_version_id,rule_snapshot_json,credit_category,credit_type,points,occurred_at,"
                "occurred_precision,occurred_year,occurred_month,idempotency_key,status,blocking_reason,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id, int(item["member_id"]), item["source_type"], str(item["source_id"]),
                    _canonical(source_item), item.get("rule_key"), item.get("rule_version"),
                    item.get("rule_version_id"), _canonical(item.get("rule_snapshot") or {}),
                    item.get("credit_category"), item.get("credit_type"),
                    str(_points(item["points"])) if item_status == "PROPOSED" else None,
                    meeting_date.isoformat(), "EXACT_DATE", meeting_date.year, meeting_date.month,
                    item.get("idempotency_key"), item_status,
                    "PREVIEW_BLOCKED" if item_status == "BLOCKED" else None,
                    now, now,
                ),
            )
        execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='DRY_RUN',updated_at=? WHERE id=? AND status='DRAFT'",
            (now, batch_id),
        )
        after = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("DRY-RUN不得写入正式学分账本")
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="learning_credit.batch.dry_run", resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id), org_unit_id=session["class_org_unit_id"],
            purpose="冻结学习会学分结算提案，不入账",
            after={"proposed_entry_count": len(postable), "blocked_count": len(blocked), "source_fingerprint": source_fp},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def _class_preview_snapshots(preview: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    meeting = preview["meeting"]
    facts: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for item in preview["entries"]:
        facts.append({
            "member_id": int(item["member_id"]),
            "source_type": item["source_type"],
            "source_id": item["source_id"],
            "idempotency_key": item["idempotency_key"],
            "status": "READY" if item["status"] == "SKIPPED_DUPLICATE" else item["status"],
            "points": item["points"],
            "score_details": item["score_details"],
            "blocking_reason": item["blocking_reason"],
        })
        rule = dict(item["rule_snapshot"])
        rule.pop("score_records", None)
        rules.append({"idempotency_key": item["idempotency_key"], "rule": rule})
    facts.sort(key=lambda item: item["idempotency_key"])
    rules.sort(key=lambda item: item["idempotency_key"])
    return (
        {
            "event_group_id": int(meeting["id"]),
            "class_org_unit_id": meeting["study_org_unit_id"],
            "event_date": str(meeting["event_date"])[:10],
            "meeting_status": meeting["status"],
            "binding_id": meeting["binding_id"],
            "sessions": meeting["sessions"],
            "excluded_record_count": int(preview["excluded_record_count"]),
            "facts": facts,
        },
        {"rules": rules},
    )


def _activity_preview(
    connection: Any, *, actor_user_id: int, activity_type: str,
    class_org_unit_id: str, occurred_from: str, occurred_to: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if activity_type not in {activity_credits.DAILY_READING, activity_credits.EXCELLENT_SHARE}:
        raise credits.LearningCreditError("不支持的学习活动学分来源")
    if not class_org_unit_id or not credits._scope_allows(actor_user_id, class_org_unit_id):
        raise PermissionError("学习活动事实不在当前组织授权范围内")
    start, end = activity_credits._validate_window(occurred_from, occurred_to)
    if not start or not end:
        raise credits.LearningCreditError("结算批次必须指定完整的日期窗口")
    facts = activity_credits._load_activity_facts(
        connection, actor_user_id=actor_user_id, activity_type=activity_type,
        member_id=None, class_org_unit_id=class_org_unit_id,
        occurred_from=start, occurred_to=end, limit=501,
    )
    if len(facts) > 500:
        raise credits.LearningCreditError("日期窗口超过500条学习事实，不能截断结算批次")
    if any(fact.get("_date_error") for fact in facts):
        raise credits.LearningCreditError("学习活动事实日期无效，不能冻结结算批次")
    entries = (
        activity_credits._daily_entries(connection, facts)
        if activity_type == activity_credits.DAILY_READING
        else activity_credits._excellent_entries(connection, facts)
    )
    facts_snapshot = [
        {
            "id": int(fact["id"]),
            "member_id": int(fact["member_id"]),
            "class_org_unit_id": fact["class_org_unit_id"],
            "binding_id": fact.get("binding_id"),
            "occurred_on": str(fact["occurred_on"])[:10],
            "participation_status": fact["participation_status"],
            "source_type": fact["source_type"],
            "source_id": fact["source_id"],
            "metadata_hash": _fingerprint(fact.get("metadata") or {}),
            "updated_at": str(fact["updated_at"]),
        }
        for fact in facts
    ]
    source_snapshot = {
        "activity_type": activity_type,
        "class_org_unit_id": class_org_unit_id,
        "occurred_from": start,
        "occurred_to": end,
        "facts": facts_snapshot,
    }
    rules = [
        {"idempotency_key": item["idempotency_key"], "rule_snapshot": item["rule_snapshot"]}
        for item in entries
    ]
    rules.sort(key=lambda item: item["idempotency_key"])
    preview = {
        "entries": entries,
        "totals": {
            "blocked_entry_count": sum(item["status"] == "BLOCKED" for item in entries),
            "duplicate_entry_count": sum(item["status"] == "SKIPPED_DUPLICATE" for item in entries),
            "no_credit_entry_count": sum(item["status"] == "NO_CREDIT" for item in entries),
        },
        "blocking_reasons": sorted({
            reason for item in entries if item["status"] == "BLOCKED"
            for reason in item.get("reasons", [])
        }),
    }
    return preview, source_snapshot, {"rules": rules}


def _dry_run_activity_batch(
    *, actor_user_id: int, activity_type: str, class_org_unit_id: str,
    occurred_from: str, occurred_to: str,
) -> dict[str, Any]:
    """Freeze a bounded activity proposal; this function cannot POST ledger rows."""

    if not credits.get_settings().learning_credit_batch_dry_run_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次DRY-RUN写入尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权创建学分结算批次")
    with transaction() as connection:
        before = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        preview, source_snapshot, rule_snapshot = _activity_preview(
            connection, actor_user_id=actor_user_id, activity_type=activity_type,
            class_org_unit_id=class_org_unit_id,
            occurred_from=occurred_from, occurred_to=occurred_to,
        )
        source_fp, rule_fp = _fingerprint(source_snapshot), _fingerprint(rule_snapshot)
        existing = execute(
            connection,
            "SELECT id,rule_fingerprint FROM learning_credit_settlement_batches "
            "WHERE source_type=? AND source_fingerprint=? AND batch_type='REGULAR' LIMIT 1",
            (activity_type, source_fp),
        ).fetchone()
        if existing:
            if existing["rule_fingerprint"] != rule_fp:
                raise credits.LearningCreditError("结算来源对应的冻结规则已变化")
            return _batch_summary(connection, int(existing["id"]), idempotent=True)
        postable = [item for item in preview["entries"] if item["status"] == "READY" and item["postable"]]
        blocked = [item for item in preview["entries"] if item["status"] == "BLOCKED"]
        proposed_points = sum((_points(item["points"]) for item in postable), Decimal("0"))
        start = date.fromisoformat(source_snapshot["occurred_from"])
        end = date.fromisoformat(source_snapshot["occurred_to"])
        now = credits._db_timestamp(connection)
        batch_no = f"LC-{activity_type}-{class_org_unit_id[:12]}-{source_fp[:12]}"
        result_snapshot = {
            "blocking_reasons": preview["blocking_reasons"],
            "fact_count": len(source_snapshot["facts"]),
            "duplicate_entry_count": preview["totals"]["duplicate_entry_count"],
            "no_credit_entry_count": preview["totals"]["no_credit_entry_count"],
        }
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_settlement_batches "
            "(batch_no,batch_type,source_type,class_org_unit_id,period_precision,period_start,period_end,"
            "status,proposed_entry_count,proposed_points,blocked_count,source_snapshot_json,rule_snapshot_json,"
            "result_snapshot_json,source_fingerprint,rule_fingerprint,created_by,created_at,updated_at) "
            "VALUES (?, 'REGULAR', ?, ?, 'EXACT_DATE', ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_no, activity_type, class_org_unit_id, start.isoformat(), end.isoformat(),
                len(postable), str(proposed_points), len(blocked), _canonical(source_snapshot),
                _canonical(rule_snapshot), _canonical(result_snapshot), source_fp, rule_fp,
                actor_user_id, now, now,
            ),
        )
        batch_id = int(cursor.lastrowid)
        for item in postable + blocked:
            item_status = "PROPOSED" if item["status"] == "READY" else "BLOCKED"
            occurred = date.fromisoformat(str(item["occurred_on"])[:10])
            # Activity facts have civil-date precision, not an observed instant.
            # Keep that business date in the ledger; the preview's UTC
            # midnight conversion can fall on the preceding calendar day.
            source_item = {
                "fact_ids": item["fact_ids"],
                "activity_type": activity_type,
                "member_id": int(item["member_id"]),
                "class_org_unit_id": class_org_unit_id,
                "occurred_on": occurred.isoformat(),
            }
            execute(
                connection,
                "INSERT INTO learning_credit_settlement_batch_items "
                "(batch_id,member_id,source_type,source_id,source_snapshot_json,rule_key,rule_version,"
                "rule_version_id,rule_snapshot_json,credit_category,credit_type,points,occurred_at,"
                "occurred_precision,occurred_year,occurred_month,idempotency_key,status,blocking_reason,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id, int(item["member_id"]), item["source_type"], str(item["source_id"]),
                    _canonical(source_item), item.get("rule_key"), item.get("rule_version"),
                    item.get("rule_version_id"), _canonical(item.get("rule_snapshot") or {}),
                    item.get("credit_category"), item.get("credit_type"),
                    str(_points(item["points"])) if item_status == "PROPOSED" else None,
                    occurred.isoformat(), "EXACT_DATE", occurred.year, occurred.month,
                    item.get("idempotency_key"), item_status,
                    "PREVIEW_BLOCKED" if item_status == "BLOCKED" else None,
                    now, now,
                ),
            )
        execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='DRY_RUN',updated_at=? WHERE id=? AND status='DRAFT'",
            (now, batch_id),
        )
        after = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("DRY-RUN不得写入正式学分账本")
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="learning_credit.batch.dry_run", resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id), org_unit_id=class_org_unit_id,
            purpose="冻结学习活动学分结算提案，不入账",
            after={"activity_type": activity_type, "proposed_entry_count": len(postable),
                   "blocked_count": len(blocked), "source_fingerprint": source_fp},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def dry_run_daily_reading_batch(
    *, actor_user_id: int, class_org_unit_id: str, occurred_from: str, occurred_to: str,
) -> dict[str, Any]:
    return _dry_run_activity_batch(
        actor_user_id=actor_user_id, activity_type=activity_credits.DAILY_READING,
        class_org_unit_id=class_org_unit_id, occurred_from=occurred_from, occurred_to=occurred_to,
    )


def dry_run_excellent_share_batch(
    *, actor_user_id: int, class_org_unit_id: str, occurred_from: str, occurred_to: str,
) -> dict[str, Any]:
    return _dry_run_activity_batch(
        actor_user_id=actor_user_id, activity_type=activity_credits.EXCELLENT_SHARE,
        class_org_unit_id=class_org_unit_id, occurred_from=occurred_from, occurred_to=occurred_to,
    )


def dry_run_class_meeting_batch(*, actor_user_id: int, event_group_id: int) -> dict[str, Any]:
    """Persist a class-meeting score proposal; never write the ledger."""

    if not credits.get_settings().learning_credit_batch_dry_run_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次DRY-RUN写入尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权创建学分结算批次")
    with transaction() as connection:
        meeting = class_credits._meeting_context(connection, event_group_id)
        if not meeting:
            raise credits.LearningCreditError("班会活动不存在")
        if not class_credits._scope_allows_group(actor_user_id, meeting):
            raise PermissionError("班会活动不在当前组织授权范围内")
        before = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        preview = class_credits._build_preview(connection, event_group_id)
        source_snapshot, rule_snapshot = _class_preview_snapshots(preview)
        source_fp, rule_fp = _fingerprint(source_snapshot), _fingerprint(rule_snapshot)
        existing = execute(
            connection,
            "SELECT id,rule_fingerprint FROM learning_credit_settlement_batches "
            "WHERE source_type='CLASS_MEETING' AND source_fingerprint=? "
            "AND batch_type='REGULAR' LIMIT 1",
            (source_fp,),
        ).fetchone()
        if existing:
            if existing["rule_fingerprint"] != rule_fp:
                raise credits.LearningCreditError("结算来源对应的冻结规则已变化")
            return _batch_summary(connection, int(existing["id"]), idempotent=True)

        postable = [item for item in preview["entries"] if item["status"] == "READY" and item["postable"]]
        blocked = [item for item in preview["entries"] if item["status"] == "BLOCKED"]
        proposed_points = sum((_points(item["points"]) for item in postable), Decimal("0"))
        event_date = date.fromisoformat(source_snapshot["event_date"])
        now = credits._db_timestamp(connection)
        batch_no = f"LC-CLASS-{event_group_id}-{source_fp[:12]}"
        result_snapshot = {
            "blocking_reasons": preview["blocking_reasons"],
            "duplicate_entry_count": preview["totals"]["duplicate_entry_count"],
            "no_credit_entry_count": preview["totals"]["no_credit_entry_count"],
            "excluded_record_count": preview["excluded_record_count"],
        }
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_settlement_batches "
            "(batch_no,batch_type,source_type,class_org_unit_id,period_precision,period_start,period_end,"
            "status,proposed_entry_count,proposed_points,blocked_count,source_snapshot_json,rule_snapshot_json,"
            "result_snapshot_json,source_fingerprint,rule_fingerprint,created_by,created_at,updated_at) "
            "VALUES (?, 'REGULAR', 'CLASS_MEETING', ?, 'EXACT_DATE', ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch_no, source_snapshot["class_org_unit_id"], event_date.isoformat(), event_date.isoformat(),
                len(postable), str(proposed_points), len(blocked), _canonical(source_snapshot),
                _canonical(rule_snapshot), _canonical(result_snapshot), source_fp, rule_fp,
                actor_user_id, now, now,
            ),
        )
        batch_id = int(cursor.lastrowid)
        for item in postable + blocked:
            item_status = "PROPOSED" if item["status"] == "READY" else "BLOCKED"
            source_item = {
                "event_group_id": event_group_id,
                "member_id": int(item["member_id"]),
                "score_details": item["score_details"],
                "learning_cycle_id": item["learning_cycle_id"],
            }
            execute(
                connection,
                "INSERT INTO learning_credit_settlement_batch_items "
                "(batch_id,member_id,source_type,source_id,source_snapshot_json,rule_key,rule_version,"
                "rule_version_id,rule_snapshot_json,credit_category,credit_type,points,occurred_at,"
                "occurred_precision,occurred_year,occurred_month,idempotency_key,status,blocking_reason,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch_id, int(item["member_id"]), item["source_type"], str(item["source_id"]),
                    _canonical(source_item), item.get("rule_key"), item.get("rule_version"),
                    item.get("rule_version_id"), _canonical(item.get("rule_snapshot") or {}),
                    item.get("credit_category"), item.get("credit_type"),
                    str(_points(item["points"])) if item_status == "PROPOSED" else None,
                    event_date.isoformat(), "EXACT_DATE", event_date.year, event_date.month,
                    item.get("idempotency_key"), item_status,
                    "PREVIEW_BLOCKED" if item_status == "BLOCKED" else None,
                    now, now,
                ),
            )
        execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='DRY_RUN',updated_at=? WHERE id=? AND status='DRAFT'",
            (now, batch_id),
        )
        after = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("DRY-RUN不得写入正式学分账本")
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="learning_credit.batch.dry_run", resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id), org_unit_id=source_snapshot["class_org_unit_id"],
            purpose="冻结班会评分结算提案，不入账",
            after={"proposed_entry_count": len(postable), "blocked_count": len(blocked), "source_fingerprint": source_fp},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def _submit_batch_for_approval(
    *, actor_user_id: int, batch_id: int, source_type: str,
    batch_type: str = "REGULAR",
) -> dict[str, Any]:
    """Freeze an unblocked DRY-RUN for independent approval; no ledger POST."""

    if not credits.get_settings().learning_credit_batch_dry_run_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次DRY-RUN写入尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权提交学分结算批次")
    if batch_type == HISTORICAL_BATCH_TYPE and "plans:historical_credit_import_manage" not in user.get("permissions", []):
        raise PermissionError("无权提交历史学分结算批次")
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row or row["source_type"] != source_type or row["batch_type"] != batch_type:
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] != "DRY_RUN":
            raise credits.LearningCreditError("只有DRY_RUN批次可以提交审批")
        if not int(row["proposed_entry_count"]):
            raise credits.LearningCreditError("没有就绪的待入账提案，不能提交审批")
        _assert_current_preview(connection, row, actor_user_id=actor_user_id)
        _approved_items(connection, row)
        before = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        now = credits._db_timestamp(connection)
        changed = execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='PENDING_APPROVAL',updated_at=? "
            "WHERE id=? AND status='DRY_RUN'",
            (now, batch_id),
        )
        if changed.rowcount != 1:
            raise credits.LearningCreditError("批次提交状态已变化")
        after = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("提交审批不得写入正式学分账本")
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="learning_credit.batch.submit_approval", resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id), org_unit_id=row["class_org_unit_id"],
            purpose="提交冻结的学分提案审批，不入账",
            after={"status": "PENDING_APPROVAL", "source_fingerprint": row["source_fingerprint"]},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def submit_study_meeting_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _submit_batch_for_approval(actor_user_id=actor_user_id, batch_id=batch_id, source_type="STUDY_MEETING")


def submit_class_meeting_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _submit_batch_for_approval(actor_user_id=actor_user_id, batch_id=batch_id, source_type="CLASS_MEETING")


def submit_daily_reading_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _submit_batch_for_approval(
        actor_user_id=actor_user_id, batch_id=batch_id, source_type=activity_credits.DAILY_READING,
    )


def submit_excellent_share_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _submit_batch_for_approval(
        actor_user_id=actor_user_id, batch_id=batch_id, source_type=activity_credits.EXCELLENT_SHARE,
    )


def submit_historical_credit_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _submit_batch_for_approval(
        actor_user_id=actor_user_id, batch_id=batch_id,
        source_type=HISTORICAL_SOURCE_TYPE, batch_type=HISTORICAL_BATCH_TYPE,
    )


def _approve_batch(
    *, actor_user_id: int, batch_id: int, source_type: str,
    batch_type: str = "REGULAR",
) -> dict[str, Any]:
    """Approve unchanged ready proposals; blocked items remain outside approval."""

    if not credits.get_settings().learning_credit_batch_approval_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次审批尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_approve" not in user.get("permissions", []):
        raise PermissionError("无权审批学分结算批次")
    if batch_type == HISTORICAL_BATCH_TYPE and "plans:historical_credit_import_manage" not in user.get("permissions", []):
        raise PermissionError("无权审批历史学分结算批次")
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row or row["source_type"] != source_type or row["batch_type"] != batch_type:
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["created_by"] is not None and int(row["created_by"]) == int(actor_user_id):
            raise PermissionError("批次创建人不能审批自己的结算批次")
        if row["status"] != "PENDING_APPROVAL":
            raise credits.LearningCreditError("只有待审批批次可以批准")
        if not int(row["proposed_entry_count"]):
            raise credits.LearningCreditError("没有就绪的待入账提案，不能审批")
        _assert_current_preview(connection, row, actor_user_id=actor_user_id)
        items = _approved_items(connection, row)
        before = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        approval_fp = _approval_fingerprint(row, items)
        now = credits._db_timestamp(connection)
        changed = execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='APPROVED',approved_by=?,"
            "approved_at=?,approval_fingerprint=?,updated_at=? "
            "WHERE id=? AND status='PENDING_APPROVAL' AND approval_fingerprint IS NULL",
            (actor_user_id, now, approval_fp, now, batch_id),
        )
        if changed.rowcount != 1:
            raise credits.LearningCreditError("批次审批状态已变化")
        item_update = execute(
            connection,
            "UPDATE learning_credit_settlement_batch_items SET status='APPROVED',updated_at=? "
            "WHERE batch_id=? AND status='PROPOSED'",
            (now, batch_id),
        )
        if item_update.rowcount != len(items):
            raise credits.LearningCreditError("批次提案状态已变化")
        after = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("审批不得写入正式学分账本")
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="learning_credit.batch.approve", resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id), org_unit_id=row["class_org_unit_id"],
            purpose="审批冻结的学分提案，不入账",
            after={"approval_fingerprint": approval_fp, "proposed_entry_count": len(items)},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def approve_study_meeting_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _approve_batch(actor_user_id=actor_user_id, batch_id=batch_id, source_type="STUDY_MEETING")


def approve_class_meeting_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _approve_batch(actor_user_id=actor_user_id, batch_id=batch_id, source_type="CLASS_MEETING")


def approve_daily_reading_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _approve_batch(
        actor_user_id=actor_user_id, batch_id=batch_id, source_type=activity_credits.DAILY_READING,
    )


def approve_excellent_share_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _approve_batch(
        actor_user_id=actor_user_id, batch_id=batch_id, source_type=activity_credits.EXCELLENT_SHARE,
    )


def approve_historical_credit_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    return _approve_batch(
        actor_user_id=actor_user_id, batch_id=batch_id,
        source_type=HISTORICAL_SOURCE_TYPE, batch_type=HISTORICAL_BATCH_TYPE,
    )


def _reconcile_posted_items(connection: Any, row: Any) -> tuple[int, Decimal]:
    items = execute(
        connection,
        "SELECT i.status,i.idempotency_key,i.points,i.member_id,i.ledger_entry_id,"
        "i.source_type,i.source_id,i.credit_category,i.credit_type,i.rule_key,i.rule_version_id,"
        "i.rule_snapshot_json,"
        "e.id AS entry_id,e.idempotency_key AS entry_key,e.member_id AS entry_member,"
        "e.points AS entry_points,e.status AS entry_status,e.source_type AS entry_source_type,"
        "e.source_id AS entry_source_id,e.credit_category AS entry_category,"
        "e.credit_type AS entry_type,e.rule_key AS entry_rule_key,"
        "e.rule_version_id AS entry_rule_version_id,e.rule_snapshot_json AS entry_rule_snapshot "
        "FROM learning_credit_settlement_batch_items i "
        "LEFT JOIN learning_credit_entries e ON e.id=i.ledger_entry_id "
        "WHERE i.batch_id=? AND i.status<>'BLOCKED' ORDER BY i.id",
        (row["id"],),
    ).fetchall()
    if len(items) != int(row["proposed_entry_count"]):
        raise credits.LearningCreditError("批次账本对账条数不一致")
    count, points = 0, Decimal("0")
    for item in items:
        if item["status"] == "POSTED":
            if (
                item["entry_id"] is None
                or item["entry_status"] != "POSTED"
                or item["entry_key"] != item["idempotency_key"]
                or int(item["entry_member"]) != int(item["member_id"])
                or Decimal(str(item["entry_points"])) != Decimal(str(item["points"]))
                or item["entry_source_type"] != item["source_type"]
                or str(item["entry_source_id"]) != str(item["source_id"])
                or item["entry_category"] != item["credit_category"]
                or item["entry_type"] != item["credit_type"]
                or item["entry_rule_key"] != item["rule_key"]
                or str(item["entry_rule_version_id"]) != str(item["rule_version_id"])
                or _canonical(json.loads(item["entry_rule_snapshot"]))
                != _canonical(json.loads(item["rule_snapshot_json"]))
            ):
                raise credits.LearningCreditError("批次条目与正式账本不一致")
            count += 1
            points += Decimal(str(item["entry_points"]))
        elif item["ledger_entry_id"] is not None or item["status"] not in {"APPROVED", "FAILED"}:
            raise credits.LearningCreditError("批次条目状态与账本关联不一致")
    return count, points


def reconcile_stale_posting_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    """Close an abandoned POSTING state after a read-only ledger reconciliation.

    This operation never creates or edits ledger rows. It is intentionally
    unavailable until the batch-post gate is enabled and the recorded
    heartbeat has been idle for at least 30 minutes.
    """

    if not credits.get_settings().learning_credit_batch_post_enabled:
        raise credits.LearningCreditFeatureDisabled("学分批次对账恢复尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    permissions = set(user.get("permissions", []))
    if not {
        "plans:credit_settlement_manage",
        "plans:credit_settlement_reconcile",
    }.issubset(permissions):
        raise PermissionError("无权对账恢复学分结算批次")

    with transaction() as connection:
        lock_clause = " FOR UPDATE" if not isinstance(connection, sqlite3.Connection) else ""
        row = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batches WHERE id=?" + lock_clause,
            (batch_id,),
        ).fetchone()
        if not row:
            raise credits.LearningCreditError("结算批次不存在")
        if row["batch_type"] == HISTORICAL_BATCH_TYPE and "plans:historical_credit_import_manage" not in permissions:
            raise PermissionError("无权对账恢复历史学分结算批次")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] != "POSTING":
            raise credits.LearningCreditError("只有入账中批次可以执行中断对账恢复")
        try:
            heartbeat = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise credits.LearningCreditError("批次心跳时间无效，必须人工核验") from exc
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        age = datetime.now(UTC) - heartbeat.astimezone(UTC)
        if age < timedelta(minutes=30):
            raise credits.LearningCreditError("批次最近30分钟仍有心跳，不能执行中断恢复")

        before = int(execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_entries"
        ).fetchone()["n"])
        count, points = _reconcile_posted_items(connection, row)
        non_posted = execute(
            connection,
            "SELECT idempotency_key FROM learning_credit_settlement_batch_items "
            "WHERE batch_id=? AND status<>'BLOCKED' AND status<>'POSTED'",
            (batch_id,),
        ).fetchall()
        for item in non_posted:
            if item["idempotency_key"] and execute(
                connection,
                "SELECT id FROM learning_credit_entries WHERE idempotency_key=? LIMIT 1",
                (item["idempotency_key"],),
            ).fetchone():
                raise credits.LearningCreditError("未标记入账的批次条目已存在账本记录，必须人工核验")

        final_status = (
            "POSTED"
            if count == int(row["proposed_entry_count"])
            and points == Decimal(str(row["proposed_points"]))
            else "PARTIAL_FAILED"
        )
        now = credits._db_timestamp(connection)
        changed = execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status=?,posted_entry_count=?,"
            "posted_points=?,updated_at=? WHERE id=? AND status='POSTING' AND updated_at=?",
            (final_status, count, str(points), now, batch_id, row["updated_at"]),
        )
        if changed.rowcount != 1:
            raise credits.LearningCreditError("批次心跳或状态已变化，必须重新读取")
        after = int(execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_entries"
        ).fetchone()["n"])
        if after != before:
            raise credits.LearningCreditError("中断对账恢复不得写入正式学分账本")
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning_credit.batch.reconcile_posting",
            resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id),
            org_unit_id=row["class_org_unit_id"],
            purpose="只读核对账本后关闭超时POSTING状态；未写入账本",
            after={"status": final_status, "posted_entry_count": count, "posted_points": str(points)},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def close_settlement_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    """Close a posted batch only after a read-only exact ledger reconciliation."""

    settings = credits.get_settings()
    if not settings.learning_credit_batch_post_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次封账尚未开启")
    credits._settlement_enabled()
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    permissions = set(user.get("permissions", []))
    if not {
        "plans:credit_settlement_manage",
        "plans:credit_settlement_close",
    }.issubset(permissions):
        raise PermissionError("无权封账学分结算批次")

    with transaction() as connection:
        lock_clause = " FOR UPDATE" if not isinstance(connection, sqlite3.Connection) else ""
        row = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batches WHERE id=?" + lock_clause,
            (batch_id,),
        ).fetchone()
        if not row:
            raise credits.LearningCreditError("结算批次不存在")
        if row["batch_type"] == HISTORICAL_BATCH_TYPE and "plans:historical_credit_import_manage" not in permissions:
            raise PermissionError("无权封账历史学分结算批次")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] == "CLOSED":
            return _batch_summary(connection, batch_id, idempotent=True)
        if row["status"] != "POSTED":
            raise credits.LearningCreditError("只有已入账批次可以封账")

        ledger_before = int(execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_entries"
        ).fetchone()["n"])
        count, points = _reconcile_posted_items(connection, row)
        if (
            count != int(row["proposed_entry_count"])
            or points != Decimal(str(row["proposed_points"]))
            or int(row["posted_entry_count"]) != count
            or Decimal(str(row["posted_points"])) != points
        ):
            raise credits.LearningCreditError("批次与正式账本对账不一致，不能封账")
        now = credits._db_timestamp(connection)
        changed = execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='CLOSED',updated_at=? "
            "WHERE id=? AND status='POSTED'",
            (now, batch_id),
        )
        if changed.rowcount != 1:
            raise credits.LearningCreditError("批次封账状态已变化")
        ledger_after = int(execute(
            connection, "SELECT COUNT(*) AS n FROM learning_credit_entries"
        ).fetchone()["n"])
        if ledger_after != ledger_before:
            raise credits.LearningCreditError("封账不得写入正式学分账本")
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning_credit.batch.close",
            resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id),
            org_unit_id=row["class_org_unit_id"],
            purpose="只读核对批次提案与正式账本后封账；未写入账本",
            before={"status": "POSTED"},
            after={
                "status": "CLOSED",
                "posted_entry_count": count,
                "posted_points": str(points),
                "blocked_count": int(row["blocked_count"]),
            },
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def _validate_historical_source_item(
    connection: Any, *, batch_row: Any, batch_item: Any, source_item: dict[str, Any],
) -> None:
    source_batch_id = int(source_item.get("import_batch_id") or 0)
    source_item_id = int(source_item.get("source_item_id") or 0)
    lock_clause = " FOR UPDATE" if not isinstance(connection, sqlite3.Connection) else ""
    row = execute(
        connection,
        "SELECT i.id,i.batch_id,i.import_row_id,i.matched_member_id,i.source_sheet,i.source_row_number,"
        "i.source_column_index,i.source_column_name,i.credit_category,i.legacy_credit_type,i.points,"
        "i.source_rule_version,i.status,i.occurred_precision,i.occurred_year,i.occurred_month,"
        "i.period_review_status,r.raw_class_name,r.match_status,r.matched_member_id AS row_member_id,"
        "r.validation_status,r.credit_review_status,b.file_sha256,b.source_rule_version AS batch_rule_version "
        "FROM learning_credit_import_items i "
        "JOIN learning_credit_import_rows r ON r.id=i.import_row_id "
        "JOIN learning_credit_import_batches b ON b.id=i.batch_id "
        "WHERE i.id=? AND i.batch_id=?" + lock_clause,
        (source_item_id, source_batch_id),
    ).fetchone()
    if not row:
        raise credits.LearningCreditError("历史来源staging条目不存在")
    mapping_sql = (
        "SELECT id,confirmed_org_unit_id,mapping_status FROM learning_credit_import_class_mappings "
        "WHERE id=? AND batch_id=? AND source_sheet=? AND raw_class_name IS NULL"
        if row["raw_class_name"] is None
        else "SELECT id,confirmed_org_unit_id,mapping_status FROM learning_credit_import_class_mappings "
        "WHERE id=? AND batch_id=? AND source_sheet=? AND raw_class_name=?"
    )
    mapping_sql += lock_clause
    mapping_params = (
        (source_item["class_mapping_id"], source_batch_id, row["source_sheet"])
        if row["raw_class_name"] is None
        else (source_item["class_mapping_id"], source_batch_id, row["source_sheet"], row["raw_class_name"])
    )
    mapping = execute(connection, mapping_sql, mapping_params).fetchone()
    class_id = str(source_item.get("class_org_unit_id") or "")
    class_unit = execute(
        connection, "SELECT id,unit_type FROM org_units WHERE id=?" + lock_clause, (class_id,)
    ).fetchone()
    member_id = int(batch_item["member_id"]) if batch_item["member_id"] is not None else None
    member = execute(
        connection, "SELECT id,status FROM members WHERE id=?" + lock_clause, (member_id,)
    ).fetchone() if member_id is not None else None
    idempotency_key = historical_credits.historical_credit_idempotency_key(
        file_sha256=str(row["file_sha256"]),
        source_sheet=str(row["source_sheet"]),
        source_row_number=int(row["source_row_number"]),
        source_column_index=int(row["source_column_index"]),
    )
    expected_source_id = f"{HISTORICAL_SOURCE_TYPE}:{idempotency_key.removeprefix('LC-HIST:')}"
    row_is_reviewed = (
        row["validation_status"] == "PASS"
        or (
            row["validation_status"] == "TOTAL_MISSING"
            and row["credit_review_status"] == "APPROVED"
        )
    )
    period_is_reviewed = (
        row["occurred_precision"] == "MONTH"
        and row["period_review_status"] in {"READY", "MONTH_CONFIRMED"}
        or row["occurred_precision"] == "YEAR"
        and row["period_review_status"] == "YEAR_ACCEPTED"
    )
    if (
        batch_row["batch_type"] != HISTORICAL_BATCH_TYPE
        or batch_row["source_type"] != HISTORICAL_SOURCE_TYPE
        or source_item.get("source_rule_version") != HISTORICAL_SOURCE_TYPE
        or row["batch_rule_version"] != HISTORICAL_SOURCE_TYPE
        or row["source_rule_version"] != HISTORICAL_SOURCE_TYPE
        or mapping is None
        or mapping["mapping_status"] not in _CONFIRMED_CLASS_MAPPING_STATUSES
        or str(mapping["confirmed_org_unit_id"]) != class_id
        or not class_unit
        or class_unit["unit_type"] not in {"CLASS", "SPECIAL_COHORT"}
        or member is None
        or str(member["status"]).upper() != "ACTIVE"
        or row["status"] not in {"PENDING_REVIEW", "READY"}
        or row["source_sheet"] != source_item["source_sheet"]
        or int(row["source_row_number"]) != int(source_item["source_row_number"])
        or int(row["source_column_index"]) != int(source_item["source_column_index"])
        or row["source_column_name"] != source_item["source_column_name"]
        or str(row["file_sha256"]) != source_item["source_file_sha256"]
        or int(row["import_row_id"]) != int(source_item["source_row_id"])
        or int(row["matched_member_id"]) != member_id
        or int(row["row_member_id"]) != member_id
        or row["match_status"] not in {"AUTO_MATCHED", "CONFIRMED"}
        or not row_is_reviewed
        or row["credit_review_status"] in {"REJECTED", "NEEDS_SOURCE_CORRECTION"}
        or not period_is_reviewed
        or int(row["occurred_year"]) != int(batch_item["occurred_year"])
        or row["occurred_precision"] != batch_item["occurred_precision"]
        or (int(row["occurred_month"]) if row["occurred_month"] is not None else None)
        != (int(batch_item["occurred_month"]) if batch_item["occurred_month"] is not None else None)
        or int(batch_item["member_id"]) != int(row["matched_member_id"])
        or row["credit_category"] != batch_item["credit_category"]
        or row["legacy_credit_type"] != batch_item["credit_type"]
        or Decimal(str(row["points"])) != Decimal(str(batch_item["points"]))
        or idempotency_key != batch_item["idempotency_key"]
        or expected_source_id != batch_item["source_id"]
        or class_id != str(batch_row["class_org_unit_id"])
    ):
        raise credits.LearningCreditError("历史来源、审核、映射或原始分值已变化，拒绝入账")


def _post_batch(
    *, actor_user_id: int, batch_id: int, source_type: str, resume_partial_failure: bool = False,
    batch_type: str = "REGULAR",
) -> dict[str, Any]:
    """POST frozen items once, with per-item transactions and reconciliation.

    An interrupted POSTING state is deliberately not retried automatically.
    Only an explicitly requested PARTIAL_FAILED resume processes failed items.
    """

    if not credits.get_settings().learning_credit_batch_post_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次正式入账尚未开启")
    credits._settlement_enabled()
    if batch_type == HISTORICAL_BATCH_TYPE and not credits.get_settings().learning_credit_historical_post_enabled:
        raise credits.LearningCreditFeatureDisabled("历史学分批次正式入账尚未开启")
    user = credits.user_context(actor_user_id) or {}
    if not {"plans:credit_settlement_post", "plans:credit_settlement_manage"}.issubset(
        user.get("permissions", [])
    ):
        raise PermissionError("无权正式入账结算批次")
    if batch_type == HISTORICAL_BATCH_TYPE and "plans:historical_credit_import_manage" not in user.get("permissions", []):
        raise PermissionError("无权正式入账历史学分批次")
    with transaction() as connection:
        row = execute(
            connection, "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row or row["source_type"] != source_type or row["batch_type"] != batch_type:
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] == "POSTED":
            count, points = _reconcile_posted_items(connection, row)
            if count != int(row["proposed_entry_count"]) or points != Decimal(str(row["proposed_points"])):
                raise credits.LearningCreditError("已入账批次对账失败")
            return _batch_summary(connection, batch_id, idempotent=True)
        if row["status"] == "PARTIAL_FAILED" and not resume_partial_failure:
            raise credits.LearningCreditError("部分失败批次必须明确请求续跑")
        if row["status"] not in {"APPROVED", "PARTIAL_FAILED"}:
            raise credits.LearningCreditError("只有已审批或部分失败批次可以入账")
        if row["approved_by"] is None or not row["approval_fingerprint"]:
            raise credits.LearningCreditError("批次审批或阻塞门禁不完整")
        items = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batch_items "
            "WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
        approved_items = [item for item in items if item["status"] != "BLOCKED"]
        if _approval_fingerprint(row, approved_items) != row["approval_fingerprint"]:
            raise credits.LearningCreditError("批次审批指纹不匹配")
        posted_keys = {item["idempotency_key"] for item in items if item["status"] == "POSTED"}
        _assert_current_preview(connection, row, actor_user_id=actor_user_id, posted_keys=posted_keys)
        _reconcile_posted_items(connection, row)
        prior_status = row["status"]
        changed = execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status='POSTING',updated_at=? "
            "WHERE id=? AND status=?",
            (credits._db_timestamp(connection), batch_id, prior_status),
        )
        if changed.rowcount != 1:
            raise credits.LearningCreditError("批次入账状态已被其他执行者占用")
        pending_ids = [int(item["id"]) for item in items if item["status"] in {"APPROVED", "FAILED"}]

    for item_id in pending_ids:
        try:
            with atomic_transaction() as connection:
                # A locking read must be the first MySQL read in this transaction.
                # Serializing by member lets the monthly-cap preview see a
                # concurrent batch's committed ledger rows before posting.
                lock_clause = " FOR UPDATE" if not isinstance(connection, sqlite3.Connection) else ""
                locked_item = execute(
                    connection,
                    "SELECT i.member_id FROM learning_credit_settlement_batch_items i "
                    "JOIN members m ON m.id=i.member_id WHERE i.id=? AND i.batch_id=?"
                    + lock_clause,
                    (item_id, batch_id),
                ).fetchone()
                if not locked_item:
                    raise credits.LearningCreditError("待入账条目或学员不存在")
                active_batch = execute(
                    connection,
                    "SELECT * FROM learning_credit_settlement_batches WHERE id=?" + lock_clause,
                    (batch_id,),
                ).fetchone()
                if not active_batch or active_batch["status"] != "POSTING":
                    raise credits.LearningCreditError("批次入账上下文已变化")
                posted_rows = execute(
                    connection,
                    "SELECT idempotency_key FROM learning_credit_settlement_batch_items "
                    "WHERE batch_id=? AND status='POSTED'",
                    (batch_id,),
                ).fetchall()
                _assert_current_preview(
                    connection, active_batch,
                    actor_user_id=actor_user_id,
                    posted_keys={entry["idempotency_key"] for entry in posted_rows},
                )
                item = execute(
                    connection,
                    "SELECT * FROM learning_credit_settlement_batch_items WHERE id=? AND batch_id=?",
                    (item_id, batch_id),
                ).fetchone()
                if not item or item["status"] not in {"APPROVED", "FAILED"} or item["ledger_entry_id"] is not None:
                    raise credits.LearningCreditError("批次条目状态已变化")
                source = json.loads(item["source_snapshot_json"])
                entry_item = {
                        "member_id": int(item["member_id"]),
                        "credit_category": item["credit_category"],
                        "credit_type": item["credit_type"],
                        "points": str(item["points"]),
                        "source_type": item["source_type"],
                        "source_id": item["source_id"],
                        "class_org_unit_id": row["class_org_unit_id"],
                        "learning_cycle_id": source.get("learning_cycle_id"),
                        "rule_key": item["rule_key"],
                        "rule_version": item["rule_version"],
                        "rule_version_id": item["rule_version_id"],
                        "rule_snapshot": json.loads(item["rule_snapshot_json"]),
                        "occurred_at": item["occurred_at"],
                        "occurred_precision": item["occurred_precision"],
                        "occurred_year": item["occurred_year"],
                        "occurred_month": item["occurred_month"],
                        "idempotency_key": item["idempotency_key"],
                    }
                if batch_type == HISTORICAL_BATCH_TYPE:
                    _validate_historical_source_item(
                        connection, batch_row=active_batch, batch_item=item, source_item=source,
                    )
                    entry = credits._insert_entry(
                        connection, entry_item, status="POSTED", actor_user_id=actor_user_id,
                    )
                elif item["credit_type"] == credits.COURSE_COMPLETION:
                    # Course-completion rule versions live in their own table;
                    # the immutable live-preview/fingerprint gate above is the
                    # authority for these already-frozen proposals.
                    entry = credits._insert_entry(
                        connection, entry_item, status="POSTED", actor_user_id=actor_user_id,
                    )
                else:
                    entry = credits.post_credit_entry(actor_user_id=actor_user_id, item=entry_item)
                if entry["idempotency_key"] != item["idempotency_key"] or Decimal(str(entry["points"])) != Decimal(str(item["points"])):
                    raise credits.LearningCreditError("正式账本结果与冻结条目不一致")
                changed = execute(
                    connection,
                    "UPDATE learning_credit_settlement_batch_items SET status='POSTED',ledger_entry_id=?,"
                    "error_code=NULL,updated_at=? WHERE id=? AND status IN ('APPROVED','FAILED')",
                    (entry["id"], credits._db_timestamp(connection), item_id),
                )
                if changed.rowcount != 1:
                    raise credits.LearningCreditError("批次条目被并发修改")
                execute(
                    connection,
                    "UPDATE learning_credit_settlement_batches SET updated_at=? "
                    "WHERE id=? AND status='POSTING'",
                    (credits._db_timestamp(connection), batch_id),
                )
                if batch_type == HISTORICAL_BATCH_TYPE:
                    stage_changed = execute(
                        connection,
                        "UPDATE learning_credit_import_items SET status='POSTED',updated_at=? "
                        "WHERE id=? AND batch_id=? AND status IN ('PENDING_REVIEW','READY')",
                        (
                            credits._db_timestamp(connection), int(source["source_item_id"]),
                            int(source["import_batch_id"]),
                        ),
                    )
                    if stage_changed.rowcount != 1:
                        raise credits.LearningCreditError("历史来源staging状态无法与账本同事务收口")
        except Exception as exc:  # noqa: BLE001 - persist only safe error category
            with transaction() as connection:
                heartbeat = credits._db_timestamp(connection)
                execute(
                    connection,
                    "UPDATE learning_credit_settlement_batch_items SET status='FAILED',error_code=?,"
                    "updated_at=? WHERE id=? AND batch_id=? AND status IN ('APPROVED','FAILED')",
                    (type(exc).__name__[:128], heartbeat, item_id, batch_id),
                )
                execute(
                    connection,
                    "UPDATE learning_credit_settlement_batches SET updated_at=? "
                    "WHERE id=? AND status='POSTING'",
                    (heartbeat, batch_id),
                )
            break

    with transaction() as connection:
        current = execute(
            connection, "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if current["status"] != "POSTING":
            raise credits.LearningCreditError("批次入账终态未知，必须人工核验")
        count, points = _reconcile_posted_items(connection, current)
        final_status = "POSTED" if count == int(current["proposed_entry_count"]) and points == Decimal(str(current["proposed_points"])) else "PARTIAL_FAILED"
        changed = execute(
            connection,
            "UPDATE learning_credit_settlement_batches SET status=?,posted_entry_count=?,"
            "posted_points=?,updated_at=? WHERE id=? AND status='POSTING'",
            (final_status, count, str(points), credits._db_timestamp(connection), batch_id),
        )
        if changed.rowcount != 1:
            raise credits.LearningCreditError("批次入账终态已变化")
        write_audit(
            connection, actor_user_id=actor_user_id,
            action="learning_credit.batch.post", resource_type="learning_credit_settlement_batch",
            resource_id=str(batch_id), org_unit_id=current["class_org_unit_id"],
            purpose="按已审批冻结条目正式入账；失败仅续跑未入账条目",
            after={"status": final_status, "posted_entry_count": count, "posted_points": str(points)},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def post_study_meeting_batch(
    *, actor_user_id: int, batch_id: int, resume_partial_failure: bool = False
) -> dict[str, Any]:
    return _post_batch(
        actor_user_id=actor_user_id, batch_id=batch_id,
        source_type="STUDY_MEETING", resume_partial_failure=resume_partial_failure,
    )


def post_class_meeting_batch(
    *, actor_user_id: int, batch_id: int, resume_partial_failure: bool = False
) -> dict[str, Any]:
    return _post_batch(
        actor_user_id=actor_user_id, batch_id=batch_id,
        source_type="CLASS_MEETING", resume_partial_failure=resume_partial_failure,
    )


def post_daily_reading_batch(
    *, actor_user_id: int, batch_id: int, resume_partial_failure: bool = False
) -> dict[str, Any]:
    return _post_batch(
        actor_user_id=actor_user_id, batch_id=batch_id,
        source_type=activity_credits.DAILY_READING,
        resume_partial_failure=resume_partial_failure,
    )


def post_excellent_share_batch(
    *, actor_user_id: int, batch_id: int, resume_partial_failure: bool = False
) -> dict[str, Any]:
    return _post_batch(
        actor_user_id=actor_user_id, batch_id=batch_id,
        source_type=activity_credits.EXCELLENT_SHARE,
        resume_partial_failure=resume_partial_failure,
    )


def post_historical_credit_batch(
    *, actor_user_id: int, batch_id: int, resume_partial_failure: bool = False,
) -> dict[str, Any]:
    return _post_batch(
        actor_user_id=actor_user_id,
        batch_id=batch_id,
        source_type=HISTORICAL_SOURCE_TYPE,
        batch_type=HISTORICAL_BATCH_TYPE,
        resume_partial_failure=resume_partial_failure,
    )


def _batch_action_identity(*, actor_user_id: int, batch_id: int) -> tuple[str, str]:
    _require_batch_manage(actor_user_id)
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT id,batch_type,source_type,class_org_unit_id "
            "FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row:
            raise credits.LearningCreditError("结算批次不存在")
        if row["batch_type"] == HISTORICAL_BATCH_TYPE:
            _require_batch_manage(actor_user_id, batch_type=HISTORICAL_BATCH_TYPE)
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        return str(row["batch_type"]), str(row["source_type"])


def submit_settlement_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    batch_type, source_type = _batch_action_identity(actor_user_id=actor_user_id, batch_id=batch_id)
    if batch_type == HISTORICAL_BATCH_TYPE:
        return submit_historical_credit_batch_for_approval(actor_user_id=actor_user_id, batch_id=batch_id)
    submitters = {
        "STUDY_MEETING": submit_study_meeting_batch_for_approval,
        "CLASS_MEETING": submit_class_meeting_batch_for_approval,
        activity_credits.DAILY_READING: submit_daily_reading_batch_for_approval,
        activity_credits.EXCELLENT_SHARE: submit_excellent_share_batch_for_approval,
    }
    action = submitters.get(source_type)
    if not action:
        raise credits.LearningCreditError("不支持的结算批次来源")
    return action(actor_user_id=actor_user_id, batch_id=batch_id)


def approve_settlement_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    batch_type, source_type = _batch_action_identity(actor_user_id=actor_user_id, batch_id=batch_id)
    if batch_type == HISTORICAL_BATCH_TYPE:
        return approve_historical_credit_batch(actor_user_id=actor_user_id, batch_id=batch_id)
    approvers = {
        "STUDY_MEETING": approve_study_meeting_batch,
        "CLASS_MEETING": approve_class_meeting_batch,
        activity_credits.DAILY_READING: approve_daily_reading_batch,
        activity_credits.EXCELLENT_SHARE: approve_excellent_share_batch,
    }
    action = approvers.get(source_type)
    if not action:
        raise credits.LearningCreditError("不支持的结算批次来源")
    return action(actor_user_id=actor_user_id, batch_id=batch_id)


def post_settlement_batch(
    *, actor_user_id: int, batch_id: int, resume_partial_failure: bool = False,
) -> dict[str, Any]:
    batch_type, source_type = _batch_action_identity(actor_user_id=actor_user_id, batch_id=batch_id)
    if batch_type == HISTORICAL_BATCH_TYPE:
        return post_historical_credit_batch(
            actor_user_id=actor_user_id, batch_id=batch_id,
            resume_partial_failure=resume_partial_failure,
        )
    posters = {
        "STUDY_MEETING": post_study_meeting_batch,
        "CLASS_MEETING": post_class_meeting_batch,
        activity_credits.DAILY_READING: post_daily_reading_batch,
        activity_credits.EXCELLENT_SHARE: post_excellent_share_batch,
    }
    action = posters.get(source_type)
    if not action:
        raise credits.LearningCreditError("不支持的结算批次来源")
    return action(
        actor_user_id=actor_user_id, batch_id=batch_id,
        resume_partial_failure=resume_partial_failure,
    )
