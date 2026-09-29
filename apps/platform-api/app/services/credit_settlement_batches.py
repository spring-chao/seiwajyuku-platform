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


def _assert_current_preview(connection: Any, row: Any) -> None:
    source_snapshot = json.loads(row["source_snapshot_json"])
    preview = credits._build_study_meeting_preview(
        connection, int(source_snapshot["session_id"])
    )
    live_source, live_rules = _preview_snapshots(preview)
    if (
        _fingerprint(live_source) != row["source_fingerprint"]
        or _fingerprint(live_rules) != row["rule_fingerprint"]
        or preview["totals"]["proposed_entry_count"] != int(row["proposed_entry_count"])
        or preview["totals"]["blocked_entry_count"] != 0
        or preview["totals"]["duplicate_entry_count"] != 0
        or sum((_points(i["points"]) for i in preview["entries"] if i["postable"]), Decimal("0"))
        != Decimal(str(row["proposed_points"]))
    ):
        raise credits.LearningCreditError("结算事实或规则已变化，必须重新DRY-RUN")


def _approved_items(connection: Any, row: Any) -> list[Any]:
    items = execute(
        connection,
        "SELECT id,idempotency_key,points FROM learning_credit_settlement_batch_items "
        "WHERE batch_id=? AND status='PROPOSED' ORDER BY id",
        (row["id"],),
    ).fetchall()
    if len(items) != int(row["proposed_entry_count"]) or sum(
        (Decimal(str(item["points"])) for item in items), Decimal("0")
    ) != Decimal(str(row["proposed_points"])):
        raise credits.LearningCreditError("批次提案与冻结汇总不一致")
    return list(items)


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


def submit_study_meeting_batch_for_approval(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    """Freeze an unblocked DRY-RUN for independent approval; no ledger POST."""

    if not credits.get_settings().learning_credit_batch_dry_run_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次DRY-RUN写入尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_manage" not in user.get("permissions", []):
        raise PermissionError("无权提交学分结算批次")
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row or row["source_type"] != "STUDY_MEETING" or row["batch_type"] != "REGULAR":
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] != "DRY_RUN":
            raise credits.LearningCreditError("只有DRY_RUN批次可以提交审批")
        if int(row["blocked_count"]) or not int(row["proposed_entry_count"]):
            raise credits.LearningCreditError("存在阻塞项或没有待入账提案，不能提交审批")
        _assert_current_preview(connection, row)
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
            purpose="提交冻结的学习会学分提案审批，不入账",
            after={"status": "PENDING_APPROVAL", "source_fingerprint": row["source_fingerprint"]},
        )
        return _batch_summary(connection, batch_id, idempotent=False)


def approve_study_meeting_batch(*, actor_user_id: int, batch_id: int) -> dict[str, Any]:
    """Approve an unchanged, unblocked batch; no ledger POST is possible here."""

    if not credits.get_settings().learning_credit_batch_approval_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次审批尚未开启")
    credits._write_allowed()
    user = credits.user_context(actor_user_id) or {}
    if "plans:credit_settlement_approve" not in user.get("permissions", []):
        raise PermissionError("无权审批学分结算批次")
    with transaction() as connection:
        row = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row or row["source_type"] != "STUDY_MEETING" or row["batch_type"] != "REGULAR":
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] != "PENDING_APPROVAL":
            raise credits.LearningCreditError("只有待审批批次可以批准")
        if int(row["blocked_count"]) or not int(row["proposed_entry_count"]):
            raise credits.LearningCreditError("存在阻塞项或没有待入账提案，不能审批")
        _assert_current_preview(connection, row)
        items = _approved_items(connection, row)
        before = int(execute(connection, "SELECT COUNT(*) AS n FROM learning_credit_entries").fetchone()["n"])
        approval_fp = _fingerprint(
            {
                "batch_id": batch_id,
                "source_fingerprint": row["source_fingerprint"],
                "rule_fingerprint": row["rule_fingerprint"],
                "item_ids": [int(item["id"]) for item in items],
                "idempotency_keys": [item["idempotency_key"] for item in items],
                "proposed_points": format(Decimal(str(row["proposed_points"])), ".2f"),
            }
        )
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
            purpose="审批冻结的学习会学分提案，不入账",
            after={"approval_fingerprint": approval_fp, "proposed_entry_count": len(items)},
        )
        return _batch_summary(connection, batch_id, idempotent=False)
