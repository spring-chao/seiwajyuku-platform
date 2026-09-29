"""Gated study-meeting credit batches; no public API calls this module yet."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from app.db import atomic_transaction, execute, transaction
from app.services.audit import write_audit
from app.services import learning_credits as credits
from app.services import class_meeting_credits as class_credits
from app.services import learning_activity_credits as activity_credits


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


def _assert_current_preview(
    connection: Any, row: Any, *, actor_user_id: int, posted_keys: set[str] | None = None
) -> None:
    source_snapshot = json.loads(row["source_snapshot_json"])
    if row["source_type"] == "STUDY_MEETING":
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
    posted_keys = posted_keys or set()
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
        or preview["totals"]["blocked_entry_count"] != 0
        or any(i["status"] == "SKIPPED_DUPLICATE" for i in active)
        or any(i["status"] != "SKIPPED_DUPLICATE" for i in preview["entries"] if i.get("idempotency_key") in posted_keys)
        or set(posted_points) != posted_keys
        or sum(i["postable"] for i in active) + len(posted_keys) != int(row["proposed_entry_count"])
        or sum((_points(i["points"]) for i in active if i["postable"]), Decimal("0"))
        + sum(posted_points.values(), Decimal("0")) != Decimal(str(row["proposed_points"]))
    ):
        raise credits.LearningCreditError("结算事实或规则已变化，必须重新DRY-RUN")


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


def _submit_batch_for_approval(*, actor_user_id: int, batch_id: int, source_type: str) -> dict[str, Any]:
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
        if not row or row["source_type"] != source_type or row["batch_type"] != "REGULAR":
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] != "DRY_RUN":
            raise credits.LearningCreditError("只有DRY_RUN批次可以提交审批")
        if int(row["blocked_count"]) or not int(row["proposed_entry_count"]):
            raise credits.LearningCreditError("存在阻塞项或没有待入账提案，不能提交审批")
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


def _approve_batch(*, actor_user_id: int, batch_id: int, source_type: str) -> dict[str, Any]:
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
        if not row or row["source_type"] != source_type or row["batch_type"] != "REGULAR":
            raise credits.LearningCreditError("结算批次不存在或来源不匹配")
        if not credits._scope_allows(actor_user_id, row["class_org_unit_id"]):
            raise PermissionError("结算批次不在当前组织授权范围内")
        if row["status"] != "PENDING_APPROVAL":
            raise credits.LearningCreditError("只有待审批批次可以批准")
        if int(row["blocked_count"]) or not int(row["proposed_entry_count"]):
            raise credits.LearningCreditError("存在阻塞项或没有待入账提案，不能审批")
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
        "WHERE i.batch_id=? ORDER BY i.id",
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


def _post_batch(
    *, actor_user_id: int, batch_id: int, source_type: str, resume_partial_failure: bool = False
) -> dict[str, Any]:
    """POST frozen items once, with per-item transactions and reconciliation.

    An interrupted POSTING state is deliberately not retried automatically.
    Only an explicitly requested PARTIAL_FAILED resume processes failed items.
    """

    if not credits.get_settings().learning_credit_batch_post_enabled:
        raise credits.LearningCreditFeatureDisabled("学分结算批次正式入账尚未开启")
    credits._settlement_enabled()
    user = credits.user_context(actor_user_id) or {}
    if not {"plans:credit_settlement_post", "plans:credit_settlement_manage"}.issubset(
        user.get("permissions", [])
    ):
        raise PermissionError("无权正式入账结算批次")
    with transaction() as connection:
        row = execute(
            connection, "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
            (batch_id,),
        ).fetchone()
        if not row or row["source_type"] != source_type or row["batch_type"] != "REGULAR":
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
        if row["approved_by"] is None or not row["approval_fingerprint"] or int(row["blocked_count"]):
            raise credits.LearningCreditError("批次审批或阻塞门禁不完整")
        items = execute(
            connection,
            "SELECT * FROM learning_credit_settlement_batch_items "
            "WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
        if _approval_fingerprint(row, items) != row["approval_fingerprint"]:
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
                    "SELECT * FROM learning_credit_settlement_batches WHERE id=?",
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
                entry = credits.post_credit_entry(
                    actor_user_id=actor_user_id,
                    item={
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
                    },
                )
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
        except Exception as exc:  # noqa: BLE001 - persist only safe error category
            with transaction() as connection:
                execute(
                    connection,
                    "UPDATE learning_credit_settlement_batch_items SET status='FAILED',error_code=?,"
                    "updated_at=? WHERE id=? AND batch_id=? AND status IN ('APPROVED','FAILED')",
                    (type(exc).__name__[:128], credits._db_timestamp(connection), item_id, batch_id),
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
