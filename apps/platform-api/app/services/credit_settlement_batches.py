"""Unified learning-credit batch orchestration, initially DRY-RUN only.

No public API calls this module yet. Production writes remain closed by the
existing settlement and mutation flags; posting/approval are not implemented.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from app.db import execute, transaction
from app.services.audit import write_audit
from app.services import learning_credits as credits


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
        "ledger_entries_delta": 0,
    }


def dry_run_study_meeting_batch(*, actor_user_id: int, session_id: int) -> dict[str, Any]:
    """Persist one frozen preview batch; never insert a ledger entry."""

    credits._settlement_enabled()
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
