"""C2B/C2C review-workbench operations for historical credit staging.

Every function in this module writes only the local import staging/audit
tables.  There is deliberately no historical-ledger POST operation here.
"""

from __future__ import annotations

import json
from calendar import monthrange
from datetime import UTC, datetime
from typing import Any, Iterable

from app.db import execute, fetch_all, transaction
from app.services.audit import write_audit
from app.services.historical_credit_matching import (
    MATCHING_ALGORITHM_VERSION,
    _covers,
    _diagnose_member_candidates,
)


DECISION_CLASS_MAPPING = "CLASS_MAPPING_CONFIRM"
DECISION_MEMBER_MATCH = "MEMBER_MATCH_CONFIRM"
DECISION_AUTO_MATCH = "AUTO_MATCH_BULK_CONFIRM"
DECISION_TOTAL_APPROVE = "CREDIT_TOTAL_APPROVE"
DECISION_TOTAL_REJECT = "CREDIT_TOTAL_REJECT"
DECISION_SOURCE_CORRECTION = "CREDIT_SOURCE_CORRECTION"
DECISION_NO_CREDIT = "NO_CREDIT_CONFIRM"
DECISION_YEAR_ACCEPT = "PERIOD_YEAR_ACCEPT"
DECISION_MONTH_CONFIRM = "PERIOD_MONTH_CONFIRM"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _batch_exists(connection: Any, batch_id: int) -> dict[str, Any]:
    row = execute(
        connection,
        "SELECT id, source_year, source_rule_version, file_sha256 FROM learning_credit_import_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if not row:
        raise ValueError("历史学分导入批次不存在")
    return dict(row)


def _decision(
    connection: Any,
    *,
    batch_id: int,
    decision_type: str,
    target_type: str,
    target_id: str,
    before: Any,
    after: Any,
    evidence: Any,
    snapshot_id: str | None,
    snapshot_fingerprint: str | None,
    algorithm_version: str | None,
    actor_user_id: int | None,
    reason: str,
    idempotency_key: str,
) -> dict[str, Any]:
    existing = execute(
        connection,
        "SELECT * FROM learning_credit_import_decisions WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if existing:
        return {"id": int(existing["id"]), "idempotent": True}
    cursor = execute(
        connection,
        "INSERT INTO learning_credit_import_decisions "
        "(batch_id, decision_type, target_type, target_id, before_json, after_json, evidence_json, "
        "snapshot_id, snapshot_fingerprint, algorithm_version, actor_user_id, reason, idempotency_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            batch_id, decision_type, target_type, target_id, _json(before), _json(after), _json(evidence),
            snapshot_id, snapshot_fingerprint, algorithm_version, actor_user_id, reason,
            idempotency_key, _now(),
        ),
    )
    return {"id": int(cursor.lastrowid), "idempotent": False}


def upsert_class_mapping_candidates(
    *,
    batch_id: int,
    mappings: Iterable[dict[str, Any]],
    snapshot_id: str | None = None,
    snapshot_fingerprint: str | None = None,
    actor_user_id: int | None = None,
) -> dict[str, int]:
    """Persist deterministic candidates; aliases remain unconfirmed."""

    with transaction() as connection:
        _batch_exists(connection, batch_id)
        now = _now()
        count = 0
        for mapping in mappings:
            source_sheet = str(mapping.get("source_sheet") or "")
            raw_class_name = mapping.get("raw_class_name")
            candidates = [str(value) for value in mapping.get("candidate_org_unit_ids", [])]
            status = str(mapping.get("mapping_status") or "PENDING_REVIEW")
            if raw_class_name is None:
                existing = execute(
                    connection,
                    "SELECT id FROM learning_credit_import_class_mappings "
                    "WHERE batch_id=? AND source_sheet=? AND raw_class_name IS NULL",
                    (batch_id, source_sheet),
                ).fetchone()
            else:
                existing = execute(
                    connection,
                    "SELECT id FROM learning_credit_import_class_mappings "
                    "WHERE batch_id=? AND source_sheet=? AND raw_class_name=?",
                    (batch_id, source_sheet, raw_class_name),
                ).fetchone()
            values = (
                _json(candidates), mapping.get("org_unit_id"), status,
                str(mapping.get("mapping_reason") or "MAPPING_REVIEW_REQUIRED"), snapshot_id,
                snapshot_fingerprint, _json(mapping), now,
            )
            if existing:
                execute(
                    connection,
                    "UPDATE learning_credit_import_class_mappings SET candidate_org_unit_ids_json=?, "
                    "confirmed_org_unit_id=?, mapping_status=?, mapping_reason=?, snapshot_id=?, "
                    "snapshot_fingerprint=?, evidence_json=?, updated_at=? WHERE id=?",
                    (*values, existing["id"]),
                )
            else:
                execute(
                    connection,
                    "INSERT INTO learning_credit_import_class_mappings "
                    "(batch_id, source_sheet, raw_class_name, candidate_org_unit_ids_json, confirmed_org_unit_id, "
                    "mapping_status, mapping_reason, snapshot_id, snapshot_fingerprint, evidence_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, source_sheet, raw_class_name, *values[:-1], now, now),
                )
            count += 1
        if actor_user_id is not None:
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="learning.historical_credit_import.class_mapping_candidates",
                resource_type="learning_credit_import_batch",
                resource_id=str(batch_id),
                purpose="保存历史班级映射候选，不自动确认别名",
                after={"mapping_count": count, "snapshot_id": snapshot_id, "snapshot_fingerprint": snapshot_fingerprint},
            )
        return {"mapping_count": count, "ledger_writes": 0}


def _rerun_confirmed_class_mapping(
    connection: Any, *, batch_id: int, source_sheet: str, raw_class_name: str | None,
    confirmed_org_unit_id: str, snapshot_id: str | None, snapshot_fingerprint: str | None,
) -> dict[str, int]:
    """Re-run the deterministic matcher for one newly confirmed class alias."""

    members = {
        str(row["id"]): dict(row)
        for row in execute(connection, "SELECT id, name, status FROM members").fetchall()
    }
    relations = [
        dict(row) for row in execute(
            connection,
            "SELECT member_id, org_unit_id, relation_type, valid_from, valid_until "
            "FROM member_org_relations WHERE relation_type='STUDY_CLASS'",
        ).fetchall()
    ]
    class_candidates = [
        dict(row) for row in execute(
            connection, "SELECT id, parent_id FROM org_units WHERE unit_type IN ('CLASS', 'SPECIAL_COHORT')",
        ).fetchall()
    ]
    tables = {"class_candidates": class_candidates}
    if raw_class_name is None:
        rows = execute(
            connection,
            "SELECT * FROM learning_credit_import_rows WHERE batch_id=? AND source_sheet=? "
            "AND raw_class_name IS NULL ORDER BY id",
            (batch_id, source_sheet),
        ).fetchall()
    else:
        rows = execute(
            connection,
            "SELECT * FROM learning_credit_import_rows WHERE batch_id=? AND source_sheet=? "
            "AND raw_class_name=? ORDER BY id",
            (batch_id, source_sheet, raw_class_name),
        ).fetchall()
    counts = {"AUTO_MATCHED": 0, "AMBIGUOUS": 0, "NOT_FOUND": 0, "CONFLICT": 0}
    now = _now()
    for row in rows:
        source_items = execute(
            connection,
            "SELECT occurred_year, occurred_month FROM learning_credit_import_items "
            "WHERE batch_id=? AND import_row_id=? ORDER BY id",
            (batch_id, row["id"]),
        ).fetchall()
        periods = []
        for item in source_items:
            year = int(item["occurred_year"])
            month = item["occurred_month"]
            if month is None:
                periods.append((datetime(year, 1, 1).date(), datetime(year, 12, 31).date()))
            else:
                periods.append((
                    datetime(year, int(month), 1).date(),
                    datetime(year, int(month), monthrange(year, int(month))[1]).date(),
                ))
        if not periods:
            periods = [(datetime(2026, 1, 1).date(), datetime(2026, 12, 31).date())]
        class_relations = [
            relation for relation in relations
            if str(relation.get("org_unit_id")) == str(confirmed_org_unit_id)
        ]
        candidate_ids = sorted({
            int(relation["member_id"])
            for relation in class_relations
            if str(relation["member_id"]) in members
            and str(members[str(relation["member_id"])].get("name") or "").strip() == str(row["raw_name"] or "").strip()
        })
        status = "NOT_FOUND"
        reason = "MEMBER_MAPPING_REQUIRED"
        member_id = None
        if len(candidate_ids) == 1:
            member_id = candidate_ids[0]
            selected = [relation for relation in class_relations if int(relation["member_id"]) == member_id]
            if str(members[str(member_id)].get("status") or "ACTIVE") != "ACTIVE" or not all(
                any(_covers(relation, *period) for relation in selected) for period in periods
            ):
                reason = "INACTIVE_MEMBER_CANDIDATE"
                member_id = None
            else:
                status = "AUTO_MATCHED"
                reason = "EXACT_NAME_UNIQUE_WITH_HISTORICAL_CLASS_RELATION"
        elif len(candidate_ids) > 1:
            status = "AMBIGUOUS"
            reason = "MULTIPLE_GLOBAL_CANDIDATES"
        else:
            reason, candidate_ids = _diagnose_member_candidates(
                tables=tables, members=members, relations=relations,
                class_id=confirmed_org_unit_id, raw_name=row["raw_name"], periods=periods,
            )
        metadata = json.loads(row["metadata_json"] or "{}")
        metadata.update({
            "identity_status": status,
            "candidate_member_ids": candidate_ids,
            "class_mapping": {
                "source_sheet": source_sheet,
                "raw_class_name": raw_class_name,
                "org_unit_id": confirmed_org_unit_id,
                "mapping_status": "CONFIRMED_ALIAS",
            },
            "match_snapshot_id": snapshot_id,
            "match_snapshot_fingerprint": snapshot_fingerprint,
            "match_algorithm_version": MATCHING_ALGORITHM_VERSION,
        })
        execute(
            connection,
            "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status=?, match_reason=?, "
            "review_status=?, metadata_json=?, match_snapshot_id=?, match_snapshot_fingerprint=?, "
            "match_algorithm_version=?, updated_at=? WHERE id=?",
            (member_id, status, reason, "PENDING" if status == "AUTO_MATCHED" else "REVIEW_REQUIRED",
             _json(metadata), snapshot_id, snapshot_fingerprint, MATCHING_ALGORITHM_VERSION, now, row["id"]),
        )
        execute(
            connection,
            "UPDATE learning_credit_import_items SET matched_member_id=?, updated_at=? "
            "WHERE batch_id=? AND import_row_id=?",
            (member_id, now, batch_id, row["id"]),
        )
        counts["CONFLICT" if status == "CONFLICT" else status] = counts.get(
            "CONFLICT" if status == "CONFLICT" else status, 0
        ) + 1
    return counts


def confirm_class_mapping(
    *,
    batch_id: int,
    mapping_id: int,
    confirmed_org_unit_id: str,
    actor_user_id: int | None,
    reason: str,
    snapshot_id: str,
    snapshot_fingerprint: str,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("确认班级映射必须填写原因")
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        row = execute(
            connection,
            "SELECT * FROM learning_credit_import_class_mappings WHERE id=? AND batch_id=?",
            (mapping_id, batch_id),
        ).fetchone()
        if not row:
            raise ValueError("历史班级映射不存在")
        before = dict(row)
        candidate_org_unit_ids = {
            str(value) for value in json.loads(row["candidate_org_unit_ids_json"] or "[]")
        }
        if candidate_org_unit_ids and str(confirmed_org_unit_id) not in candidate_org_unit_ids:
            raise ValueError("确认的班级不在当前快照候选范围内")
        idempotency_key = f"CLASS_MAPPING_CONFIRM:{batch_id}:{mapping_id}:{confirmed_org_unit_id}:{snapshot_fingerprint}"
        existing_decision = execute(
            connection,
            "SELECT id FROM learning_credit_import_decisions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        rerun = {"AUTO_MATCHED": 0, "AMBIGUOUS": 0, "NOT_FOUND": 0, "CONFLICT": 0}
        if not existing_decision:
            now = _now()
            execute(
                connection,
                "UPDATE learning_credit_import_class_mappings SET confirmed_org_unit_id=?, mapping_status='CONFIRMED_ALIAS', "
                "confirmed_by=?, confirmed_at=?, updated_at=? WHERE id=?",
                (confirmed_org_unit_id, actor_user_id, now, now, mapping_id),
            )
            rerun = _rerun_confirmed_class_mapping(
                connection,
                batch_id=batch_id,
                source_sheet=str(row["source_sheet"]),
                raw_class_name=row["raw_class_name"],
                confirmed_org_unit_id=confirmed_org_unit_id,
                snapshot_id=snapshot_id,
                snapshot_fingerprint=snapshot_fingerprint,
            )
        decision = _decision(
            connection, batch_id=batch_id, decision_type=DECISION_CLASS_MAPPING,
            target_type="class_mapping", target_id=str(mapping_id), before=before,
            after={
                "mapping_status": "CONFIRMED_ALIAS",
                "confirmed_org_unit_id": confirmed_org_unit_id,
                "rerun": rerun,
            },
            evidence={"candidate_org_unit_ids": json.loads(row["candidate_org_unit_ids_json"] or "[]")},
            snapshot_id=snapshot_id, snapshot_fingerprint=snapshot_fingerprint,
            algorithm_version=MATCHING_ALGORITHM_VERSION, actor_user_id=actor_user_id, reason=reason,
            idempotency_key=idempotency_key,
        )
        return {"decision": decision, "mapping_id": mapping_id, "rerun": rerun, "ledger_writes": 0}


def bulk_confirm_high_confidence_matches(
    *,
    batch_id: int,
    expected_count: int,
    snapshot_id: str,
    snapshot_fingerprint: str,
    algorithm_version: str,
    actor_user_id: int | None,
    reason: str,
) -> dict[str, Any]:
    """Confirm a complete, fingerprinted auto-match batch atomically."""

    if expected_count < 0 or not snapshot_id or not snapshot_fingerprint or not algorithm_version:
        raise ValueError("批量确认必须提供快照、指纹和算法版本")
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        idempotency_key = f"AUTO_MATCH_BULK_CONFIRM:{batch_id}:{snapshot_fingerprint}:{algorithm_version}:{expected_count}"
        existing_decision = execute(
            connection,
            "SELECT id FROM learning_credit_import_decisions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing_decision:
            return {"confirmed_count": expected_count, "decision": {"id": int(existing_decision["id"]), "idempotent": True}, "ledger_writes": 0}
        count = int(execute(
            connection,
            "SELECT COUNT(*) AS count FROM learning_credit_import_rows "
            "WHERE batch_id=? AND match_status='AUTO_MATCHED' AND match_snapshot_id=? "
            "AND match_snapshot_fingerprint=? AND match_algorithm_version=?",
            (batch_id, snapshot_id, snapshot_fingerprint, algorithm_version),
        ).fetchone()["count"])
        total_auto = int(execute(
            connection,
            "SELECT COUNT(*) AS count FROM learning_credit_import_rows WHERE batch_id=? AND match_status='AUTO_MATCHED'",
            (batch_id,),
        ).fetchone()["count"])
        if count != expected_count or total_auto != expected_count:
            raise ValueError(
                "AUTO_MATCHED 快照指纹或数量变化，已中止批量确认："
                f"预期 {expected_count}，当前总数 {total_auto}，指纹匹配 {count}"
            )
        target = f"AUTO_MATCHED:{expected_count}"
        decision = _decision(
            connection, batch_id=batch_id, decision_type=DECISION_AUTO_MATCH,
            target_type="import_rows", target_id=target,
            before={"match_status": "AUTO_MATCHED", "count": count},
            after={"match_status": "CONFIRMED", "count": count},
            evidence={"expected_count": expected_count}, snapshot_id=snapshot_id,
            snapshot_fingerprint=snapshot_fingerprint, algorithm_version=algorithm_version,
            actor_user_id=actor_user_id, reason=reason,
            idempotency_key=idempotency_key,
        )
        if not decision["idempotent"]:
            now = _now()
            execute(
                connection,
                "UPDATE learning_credit_import_rows SET match_status='CONFIRMED', review_status='CONFIRMED', updated_at=? "
                "WHERE batch_id=? AND match_status='AUTO_MATCHED'",
                (now, batch_id),
            )
            if actor_user_id is not None:
                write_audit(
                    connection,
                    actor_user_id=actor_user_id,
                    action="learning.historical_credit_import.auto_match_bulk_confirm",
                    resource_type="learning_credit_import_batch",
                    resource_id=str(batch_id),
                    purpose=reason,
                    after={"count": count, "snapshot_id": snapshot_id, "snapshot_fingerprint": snapshot_fingerprint, "algorithm_version": algorithm_version},
                )
        return {"confirmed_count": count, "decision": decision, "ledger_writes": 0}


def confirm_member_match(
    *, batch_id: int, row_id: int, member_id: int, actor_user_id: int | None,
    reason: str, snapshot_id: str, snapshot_fingerprint: str,
) -> dict[str, Any]:
    """Manually confirm one candidate after business review.

    This is deliberately separate from the high-confidence bulk path.  The
    caller must supply the snapshot evidence and a reason; the source row and
    its proposed items remain otherwise unchanged.
    """

    if not reason.strip() or not snapshot_id or not snapshot_fingerprint:
        raise ValueError("人工确认成员必须提供快照、指纹和原因")
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        row = execute(
            connection,
            "SELECT * FROM learning_credit_import_rows WHERE id=? AND batch_id=?",
            (row_id, batch_id),
        ).fetchone()
        if not row:
            raise ValueError("历史学分导入行不存在")
        member = execute(
            connection,
            "SELECT id, name, status FROM members WHERE id=?",
            (member_id,),
        ).fetchone()
        if not member:
            raise ValueError("人工确认的成员不存在")
        decision = _decision(
            connection, batch_id=batch_id, decision_type=DECISION_MEMBER_MATCH,
            target_type="import_row", target_id=str(row_id), before=dict(row),
            after={"match_status": "CONFIRMED", "matched_member_id": member_id},
            evidence={"member_id": member_id, "member_name": member["name"]},
            snapshot_id=snapshot_id, snapshot_fingerprint=snapshot_fingerprint,
            algorithm_version=None, actor_user_id=actor_user_id, reason=reason,
            idempotency_key=f"MEMBER_MATCH_CONFIRM:{batch_id}:{row_id}:{member_id}:{snapshot_fingerprint}",
        )
        if not decision["idempotent"]:
            now = _now()
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["manual_confirmation"] = {
                "member_id": member_id,
                "snapshot_id": snapshot_id,
                "snapshot_fingerprint": snapshot_fingerprint,
            }
            execute(
                connection,
                "UPDATE learning_credit_import_rows SET matched_member_id=?, match_status='CONFIRMED', "
                "review_status='CONFIRMED', match_reason='MANUAL_CONFIRMED', metadata_json=?, "
                "match_snapshot_id=?, match_snapshot_fingerprint=?, updated_at=? WHERE id=?",
                (member_id, _json(metadata), snapshot_id, snapshot_fingerprint, now, row_id),
            )
            execute(
                connection,
                "UPDATE learning_credit_import_items SET matched_member_id=?, updated_at=? "
                "WHERE batch_id=? AND import_row_id=?",
                (member_id, now, batch_id, row_id),
            )
            if actor_user_id is not None:
                write_audit(
                    connection,
                    actor_user_id=actor_user_id,
                    action="learning.historical_credit_import.member_match_confirm",
                    resource_type="learning_credit_import_row",
                    resource_id=str(row_id),
                    purpose=reason,
                    after={"member_id": member_id, "snapshot_id": snapshot_id, "snapshot_fingerprint": snapshot_fingerprint},
                )
        return {"confirmed_count": 1, "decision": decision, "ledger_writes": 0}


def approve_calculated_totals(
    *, batch_id: int, row_ids: Iterable[int], actor_user_id: int | None, reason: str
) -> dict[str, Any]:
    ids = sorted({int(value) for value in row_ids})
    if not ids or not reason.strip():
        raise ValueError("确认计算总分必须提供行号和原因")
    decisions: list[dict[str, Any]] = []
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        for row_id in ids:
            row = execute(
                connection,
                "SELECT * FROM learning_credit_import_rows WHERE id=? AND batch_id=? AND validation_status='TOTAL_MISSING'",
                (row_id, batch_id),
            ).fetchone()
            if not row or row["calculated_total_points"] is None:
                raise ValueError(f"行 {row_id} 不是可批准的 TOTAL_MISSING")
            decision = _decision(
                connection, batch_id=batch_id, decision_type=DECISION_TOTAL_APPROVE,
                target_type="import_row", target_id=str(row_id), before=dict(row),
                after={"resolved_total_points": row["calculated_total_points"], "credit_review_status": "APPROVED"},
                evidence={"calculation_source": "parsed_detail_cells"}, snapshot_id=None,
                snapshot_fingerprint=None, algorithm_version=None, actor_user_id=actor_user_id,
                reason=reason, idempotency_key=f"CREDIT_TOTAL_APPROVE:{batch_id}:{row_id}",
            )
            if not decision["idempotent"]:
                now = _now()
                execute(
                    connection,
                    "UPDATE learning_credit_import_rows SET resolved_total_points=?, credit_review_status='APPROVED', "
                    "credit_review_reason=?, credit_reviewed_by=?, credit_reviewed_at=?, updated_at=? WHERE id=?",
                    (row["calculated_total_points"], reason, actor_user_id, now, now, row_id),
                )
            decisions.append(decision)
    return {"approved_count": len(ids), "decisions": decisions, "ledger_writes": 0}


def resolve_credit_anomalies(
    *, batch_id: int, row_ids: Iterable[int], resolution: str,
    actor_user_id: int | None, reason: str,
) -> dict[str, Any]:
    """Record a rejection or source-correction decision without changing raw totals."""

    if resolution not in {"REJECTED", "NEEDS_SOURCE_CORRECTION"}:
        raise ValueError("分值异常处理结果必须是 REJECTED 或 NEEDS_SOURCE_CORRECTION")
    ids = sorted({int(value) for value in row_ids})
    if not ids or not reason.strip():
        raise ValueError("分值异常处理必须提供行号和原因")
    decision_type = DECISION_TOTAL_REJECT if resolution == "REJECTED" else DECISION_SOURCE_CORRECTION
    decisions: list[dict[str, Any]] = []
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        for row_id in ids:
            row = execute(
                connection,
                "SELECT * FROM learning_credit_import_rows WHERE id=? AND batch_id=? "
                "AND validation_status='TOTAL_MISSING'",
                (row_id, batch_id),
            ).fetchone()
            if not row:
                raise ValueError(f"行 {row_id} 不是可审核的 TOTAL_MISSING")
            decision = _decision(
                connection, batch_id=batch_id, decision_type=decision_type,
                target_type="import_row", target_id=str(row_id), before=dict(row),
                after={"credit_review_status": resolution}, evidence={}, snapshot_id=None,
                snapshot_fingerprint=None, algorithm_version=None, actor_user_id=actor_user_id,
                reason=reason, idempotency_key=f"{decision_type}:{batch_id}:{row_id}",
            )
            if not decision["idempotent"]:
                now = _now()
                review_status = "REJECTED" if resolution == "REJECTED" else "REVIEW_REQUIRED"
                execute(
                    connection,
                    "UPDATE learning_credit_import_rows SET credit_review_status=?, credit_review_reason=?, "
                    "credit_reviewed_by=?, credit_reviewed_at=?, review_status=?, updated_at=? WHERE id=?",
                    (resolution, reason, actor_user_id, now, review_status, now, row_id),
                )
            decisions.append(decision)
    return {"resolved_count": len(ids), "resolution": resolution, "decisions": decisions, "ledger_writes": 0}


def confirm_no_credit(
    *, batch_id: int, row_ids: Iterable[int], actor_user_id: int | None, reason: str
) -> dict[str, Any]:
    ids = sorted({int(value) for value in row_ids})
    if not ids or not reason.strip():
        raise ValueError("确认无学分必须提供行号和原因")
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        for row_id in ids:
            row = execute(
                connection,
                "SELECT * FROM learning_credit_import_rows WHERE id=? AND batch_id=? AND validation_status='ZERO'",
                (row_id, batch_id),
            ).fetchone()
            if not row:
                raise ValueError(f"行 {row_id} 不是 ZERO")
            decision = _decision(
                connection, batch_id=batch_id, decision_type=DECISION_NO_CREDIT,
                target_type="import_row", target_id=str(row_id), before=dict(row),
                after={"credit_review_status": "NO_CREDIT_CONFIRMED"}, evidence={},
                snapshot_id=None, snapshot_fingerprint=None, algorithm_version=None,
                actor_user_id=actor_user_id, reason=reason,
                idempotency_key=f"NO_CREDIT_CONFIRM:{batch_id}:{row_id}",
            )
            if not decision["idempotent"]:
                now = _now()
                execute(
                    connection,
                    "UPDATE learning_credit_import_rows SET credit_review_status='NO_CREDIT_CONFIRMED', "
                    "credit_review_reason=?, credit_reviewed_by=?, credit_reviewed_at=?, review_status='NO_CREDIT', updated_at=? "
                    "WHERE id=?",
                    (reason, actor_user_id, now, now, row_id),
                )
    return {"confirmed_count": len(ids), "ledger_writes": 0}


def accept_year_only_period(
    *,
    batch_id: int,
    expected_count: int,
    actor_user_id: int | None,
    reason: str,
    legacy_credit_type: str | None = None,
    source_sheet: str | None = None,
    source_column_name: str | None = None,
) -> dict[str, Any]:
    if expected_count < 0 or not reason.strip():
        raise ValueError("接受年度精度必须提供预期数量和原因")
    conditions = [
        "batch_id=?", "occurred_precision='YEAR'", "source_month IS NULL",
        "period_review_status='PERIOD_REVIEW_REQUIRED'",
    ]
    params: list[Any] = [batch_id]
    for column, value in (("legacy_credit_type", legacy_credit_type), ("source_sheet", source_sheet), ("source_column_name", source_column_name)):
        if value is not None:
            conditions.append(f"{column}=?")
            params.append(value)
    with transaction() as connection:
        batch = _batch_exists(connection, batch_id)
        selector = "|".join(str(value or "*") for value in (legacy_credit_type, source_sheet, source_column_name))
        idempotency_key = f"PERIOD_YEAR_ACCEPT:{batch_id}:{selector}:{expected_count}"
        existing_decision = execute(
            connection,
            "SELECT id FROM learning_credit_import_decisions WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing_decision:
            return {"accepted_count": expected_count, "decision": {"id": int(existing_decision["id"]), "idempotent": True}, "ledger_writes": 0}
        count = int(execute(
            connection,
            "SELECT COUNT(*) AS count FROM learning_credit_import_items WHERE " + " AND ".join(conditions),
            tuple(params),
        ).fetchone()["count"])
        if count != expected_count:
            raise ValueError(f"YEAR-only 数量变化：预期 {expected_count}，实际 {count}")
        decision = _decision(
            connection, batch_id=batch_id, decision_type=DECISION_YEAR_ACCEPT,
            target_type="import_items", target_id=selector,
            before={"period_review_status": "PERIOD_REVIEW_REQUIRED", "count": count},
            after={"occurred_precision": "YEAR", "occurred_year": int(batch["source_year"]), "period_review_status": "YEAR_ACCEPTED", "count": count},
            evidence={"selector": selector}, snapshot_id=None, snapshot_fingerprint=None,
            algorithm_version=None, actor_user_id=actor_user_id, reason=reason,
            idempotency_key=idempotency_key,
        )
        if not decision["idempotent"]:
            now = _now()
            execute(
                connection,
                "UPDATE learning_credit_import_items SET occurred_year=?, occurred_month=NULL, occurred_precision='YEAR', "
                "period_review_status='YEAR_ACCEPTED', updated_at=? WHERE " + " AND ".join(conditions),
                (batch["source_year"], now, *params),
            )
        return {"accepted_count": count, "decision": decision, "ledger_writes": 0}


def confirm_month_period(
    *, batch_id: int, item_ids: Iterable[int], actor_user_id: int | None, reason: str
) -> dict[str, Any]:
    ids = sorted({int(value) for value in item_ids})
    if not ids or not reason.strip():
        raise ValueError("确认月份必须提供项目和原因")
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        placeholders = ",".join("?" for _ in ids)
        rows = execute(
            connection,
            "SELECT * FROM learning_credit_import_items WHERE batch_id=? AND id IN (" + placeholders + ") "
            "AND occurred_precision='MONTH' AND source_month IS NOT NULL",
            (batch_id, *ids),
        ).fetchall()
        if len(rows) != len(ids):
            raise ValueError("存在不可确认月份的历史学分项目")
        now = _now()
        execute(
            connection,
            "UPDATE learning_credit_import_items SET period_review_status='MONTH_CONFIRMED', updated_at=? "
            "WHERE batch_id=? AND id IN (" + placeholders + ")",
            (now, batch_id, *ids),
        )
        decision = _decision(
            connection, batch_id=batch_id, decision_type=DECISION_MONTH_CONFIRM,
            target_type="import_items", target_id=",".join(str(value) for value in ids),
            before={"period_review_status": "READY", "count": len(ids)},
            after={"period_review_status": "MONTH_CONFIRMED", "count": len(ids)},
            evidence={"item_ids": ids}, snapshot_id=None, snapshot_fingerprint=None,
            algorithm_version=None, actor_user_id=actor_user_id, reason=reason,
            idempotency_key=f"PERIOD_MONTH_CONFIRM:{batch_id}:{','.join(str(value) for value in ids)}",
        )
        return {"confirmed_count": len(ids), "decision": decision, "ledger_writes": 0}


def _candidate_packets(connection: Any, identity_rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    candidate_ids: set[int] = set()
    for row in identity_rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        for value in metadata.get("candidate_member_ids", []):
            try:
                candidate_ids.add(int(value))
            except (TypeError, ValueError):
                continue
    if not candidate_ids:
        return {}
    placeholders = ",".join("?" for _ in candidate_ids)
    members = {
        int(row["id"]): dict(row)
        for row in execute(
            connection,
            "SELECT id, name, status, phone_masked FROM members WHERE id IN (" + placeholders + ")",
            tuple(sorted(candidate_ids)),
        ).fetchall()
    }
    relations = execute(
        connection,
        "SELECT r.member_id, r.org_unit_id, r.valid_from, r.valid_until, "
        "u.name AS class_name, u.parent_id AS center_id "
        "FROM member_org_relations r LEFT JOIN org_units u ON u.id=r.org_unit_id "
        "WHERE r.relation_type='STUDY_CLASS' AND r.member_id IN (" + placeholders + ")",
        tuple(sorted(candidate_ids)),
    ).fetchall()
    packets: dict[int, list[dict[str, Any]]] = {int(row["id"]): [] for row in identity_rows}
    for row in identity_rows:
        metadata = json.loads(row.get("metadata_json") or "{}")
        for value in metadata.get("candidate_member_ids", []):
            try:
                member_id = int(value)
            except (TypeError, ValueError):
                continue
            member = members.get(member_id)
            if not member:
                continue
            member_relations = [relation for relation in relations if int(relation["member_id"]) == member_id]
            packets[int(row["id"])].append({
                "member_id": member_id,
                "candidate_name": member["name"],
                "status": member["status"],
                "platform_classes": [
                    {
                        "class_name": relation["class_name"],
                        "center_id": relation["center_id"],
                        "valid_from": relation["valid_from"],
                        "valid_until": relation["valid_until"],
                    }
                    for relation in member_relations
                ],
                "phone_masked": member["phone_masked"],
                "candidate_reason": row.get("match_reason"),
            })
    return packets


def get_historical_credit_review_workbench(batch_id: int) -> dict[str, Any]:
    with transaction() as connection:
        _batch_exists(connection, batch_id)
        identity_rows = [dict(row) for row in execute(
            connection,
            "SELECT id, source_sheet, source_row_number, raw_name, raw_class_name, raw_group_name, "
            "match_status, matched_member_id, match_reason, metadata_json, match_snapshot_id, "
            "match_snapshot_fingerprint, match_algorithm_version "
            "FROM learning_credit_import_rows WHERE batch_id=? AND match_status NOT IN ('AUTO_MATCHED', 'CONFIRMED') "
            "ORDER BY source_sheet, source_row_number",
            (batch_id,),
        ).fetchall()]
        packets = _candidate_packets(connection, identity_rows)
        for row in identity_rows:
            row["candidate_packets"] = packets.get(int(row["id"]), [])
        return {
            "batch_id": batch_id,
            "class_mappings": [dict(row) for row in execute(
                connection,
                "SELECT * FROM learning_credit_import_class_mappings WHERE batch_id=? "
                "ORDER BY source_sheet, raw_class_name, id",
                (batch_id,),
            ).fetchall()],
            "identity_review": identity_rows,
            "credit_review": [dict(row) for row in execute(
                connection,
                "SELECT id, source_sheet, source_row_number, raw_name, raw_total_points, calculated_total_points, "
                "validation_status, credit_review_status, resolved_total_points, credit_review_reason "
                "FROM learning_credit_import_rows WHERE batch_id=? AND validation_status<>'PASS' "
                "ORDER BY source_sheet, source_row_number",
                (batch_id,),
            ).fetchall()],
            "period_review": [dict(row) for row in execute(
                connection,
                "SELECT id, import_row_id, source_sheet, source_row_number, source_column_name, points, "
                "occurred_precision, occurred_year, occurred_month, period_review_status "
                "FROM learning_credit_import_items WHERE batch_id=? AND period_review_status NOT IN ('READY', 'MONTH_CONFIRMED') "
                "ORDER BY occurred_year, occurred_month, id",
                (batch_id,),
            ).fetchall()],
            "decisions": [dict(row) for row in execute(
                connection,
                "SELECT * FROM learning_credit_import_decisions WHERE batch_id=? ORDER BY id",
                (batch_id,),
            ).fetchall()],
            "ledger_writes": 0,
        }
