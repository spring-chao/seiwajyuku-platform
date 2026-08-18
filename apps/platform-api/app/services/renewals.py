from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.core.privacy import encrypt_text, phone_hash
from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context


CENTER_IDS = {
    "姑苏相城分中心": "org-gusu", "昆山分中心": "org-kunshan", "吴江分中心": "org-wujiang",
    "新吴分中心": "org-xinwu", "园区分中心": "org-yuanqu", "张家港分中心": "org-zhangjiagang",
}

MASTER_HEADER_ALIASES = {
    "姓名": ("姓名", "名字", "name"),
    "手机号码": ("手机号码", "手机号", "phone"),
    "所在分中心": ("所在分中心", "所属分中心", "center"),
}
IMPORTABLE_MATCH_STATUSES = frozenset(
    {"MASTER_PHONE_EXACT", "MASTER_NAME_CENTER_EXACT", "MATCHED"}
)
RENEWAL_STATUSES = frozenset(
    {
        "PENDING_FIRST_CONTACT",
        "CONTACTED_WAITING_REPLY",
        "IN_COMMUNICATION",
        "RENEWED",
        "NOT_RENEWING",
        "DEFERRED",
        "EXITED",
    }
)
PREVIEW_ROW_FIELDS = (
    "row_no",
    "name",
    "center_name",
    "class_name",
    "due_month",
    "match_status",
    "issue_code",
    "proposed_status",
    "history_note",
    "assistance_note",
)

# Renewal attribution is a member-management fact.  A member's development
# relation is the authoritative center for renewal and regional reporting;
# the primary member relation is the safe fallback for older profiles that do
# not yet have a development relation.  The workbook's center is only used
# during the one-time matching/import flow and is never used for daily reads.
MEMBER_RENEWAL_ORG_SQL = (
    "COALESCE(NULLIF(m.development_org_unit_id, ''), m.org_unit_id)"
)
MEMBER_CLASS_NAME_SQL = (
    "(SELECT ou.name FROM member_org_relations mor "
    "JOIN org_units ou ON ou.id=mor.org_unit_id "
    "WHERE mor.member_id=m.id AND mor.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
    "AND mor.valid_until IS NULL AND ou.is_active=1 "
    "ORDER BY mor.is_primary DESC, mor.id DESC LIMIT 1)"
)
MEMBER_GROUP_NAME_SQL = (
    "(SELECT ou.name FROM member_org_relations mor "
    "JOIN org_units ou ON ou.id=mor.org_unit_id "
    "WHERE mor.member_id=m.id AND mor.relation_type='STUDY_GROUP' "
    "AND mor.valid_until IS NULL AND ou.is_active=1 "
    "ORDER BY mor.is_primary DESC, mor.id DESC LIMIT 1)"
)


def _cycle_with_member_scope(cycle_id: int) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT c.*, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS member_org_unit_id "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id WHERE c.id=?",
        (cycle_id,),
    )


def list_assignees(actor_user_id: int, org_unit_id: str | None = None) -> list[dict[str, Any]]:
    """Return active users who can manage renewals in the requested org scope."""
    actor_allowed = accessible_org_ids(actor_user_id)
    if org_unit_id and actor_allowed is not None and org_unit_id not in actor_allowed:
        raise PermissionError("组织不在当前用户授权范围内")
    rows = fetch_all(
        "SELECT id, username, display_name FROM app_users WHERE is_active=1 ORDER BY display_name, id"
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        context = user_context(row["id"])
        if not context or "renewals:manage" not in context["permissions"]:
            continue
        if org_unit_id:
            assignee_allowed = accessible_org_ids(row["id"])
            if assignee_allowed is not None and org_unit_id not in assignee_allowed:
                continue
        result.append(row)
    return result


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _month(value: Any) -> int | None:
    text = str(value or "").strip().replace("月", "")
    return int(text) if text.isdigit() and 1 <= int(text) <= 12 else None


def _member_renewal_month(value: Any) -> int | None:
    """Return the recurring month maintained in the member master profile."""
    match = re.fullmatch(r"\d{4}-(\d{2})", str(value or "").strip())
    if not match:
        return None
    month = int(match.group(1))
    return month if 1 <= month <= 12 else None


def _initial_cycle_status(due_month: int, renewal_year: int, now: datetime) -> str:
    """Treat months before the current month as already renewed for this year."""
    if renewal_year == now.year and due_month < now.month:
        return "RENEWED"
    return "PENDING_FIRST_CONTACT"


def maybe_create_historical_cycle(
    connection: Any,
    *,
    member_id: int,
    actor_user_id: int,
    member_status: str,
    renewal_month: Any,
    org_unit_id: str,
    renewal_year: int | None = None,
    now: datetime | None = None,
) -> int | None:
    """Reconcile one audited historical cycle after member maintenance.

    This is deliberately a single-member helper. It runs inside the member
    transaction, so maintaining a past renewal month automatically closes the
    corresponding current-year cycle without introducing a bulk write on a
    read-only coverage request.
    """
    if str(member_status or "").upper() != "ACTIVE":
        return None
    due_month = _member_renewal_month(renewal_month)
    current = now or datetime.now(UTC)
    target_year = renewal_year or current.year
    if not due_month or _initial_cycle_status(due_month, target_year, current) != "RENEWED":
        return None
    existing = execute(
        connection,
        "SELECT id, status, due_month, completed_at FROM renewal_cycles "
        "WHERE member_id=? AND renewal_year=?",
        (member_id, target_year),
    ).fetchone()
    if existing:
        existing_status = str(existing["status"] or "").upper()
        if existing_status == "RENEWED":
            return int(existing["id"])
        # Do not overwrite an explicit negative, paused, or exited decision.
        # Open follow-up states are safe to close because maintaining a past
        # renewal month is the operator's explicit confirmation that this
        # year's renewal has already happened.
        if existing_status not in {
            "PENDING_FIRST_CONTACT",
            "CONTACTED_WAITING_REPLY",
            "IN_COMMUNICATION",
        }:
            return None
        completed_at = current.isoformat()
        execute(
            connection,
            "UPDATE renewal_cycles SET org_unit_id=?, due_month=?, status='RENEWED', "
            "completed_at=?, updated_at=? WHERE id=?",
            (org_unit_id, due_month, completed_at, completed_at, existing["id"]),
        )
        execute(
            connection,
            "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, "
            "reason, changed_by, created_at) VALUES (?, ?, 'RENEWED', ?, ?, ?)",
            (
                existing["id"],
                existing_status,
                "学员管理维护历史续费月份，已有周期自动标记为已续费",
                actor_user_id,
                completed_at,
            ),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.cycle.auto_complete_historical",
            resource_type="renewal_cycle",
            resource_id=str(existing["id"]),
            org_unit_id=org_unit_id,
            purpose="学员管理维护历史续费月份，自动完成已有当前年度周期",
            before={
                "status": existing_status,
                "due_month": existing["due_month"],
                "completed_at": existing["completed_at"],
            },
            after={
                "status": "RENEWED",
                "due_month": due_month,
                "completed_at": completed_at,
                "source": "member_renewal_month_maintenance",
            },
        )
        return int(existing["id"])
    created_at = current.isoformat()
    cycle_id = execute(
        connection,
        "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, "
        "status, completed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            member_id,
            target_year,
            org_unit_id,
            due_month,
            "RENEWED",
            created_at,
            created_at,
            created_at,
        ),
    ).lastrowid
    execute(
        connection,
        "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, "
        "reason, changed_by, created_at) VALUES (?, NULL, 'RENEWED', ?, ?, ?)",
        (
            cycle_id,
            "学员管理维护续费月份，历史月份自动标记为已续费",
            actor_user_id,
            created_at,
        ),
    )
    write_audit(
        connection,
        actor_user_id=actor_user_id,
        action="renewals.cycle.auto_create_historical",
        resource_type="renewal_cycle",
        resource_id=str(cycle_id),
        org_unit_id=org_unit_id,
        purpose="学员管理维护历史续费月份，自动补齐当前年度已续费周期",
        after={
            "member_id": member_id,
            "renewal_year": target_year,
            "due_month": due_month,
            "status": "RENEWED",
            "source": "member_renewal_month_maintenance",
        },
    )
    return int(cycle_id)


def _status(note: Any) -> str:
    text = str(note or "").strip()
    if not text:
        return "PENDING_FIRST_CONTACT"
    if "已续费" in text:
        return "RENEWED"
    if "退出" in text:
        return "EXITED"
    if "不续费" in text:
        return "NOT_RENEWING"
    if "暂停" in text or "休学" in text:
        return "DEFERRED"
    if "未接" in text or "未回复" in text:
        return "CONTACTED_WAITING_REPLY"
    if "邀请" in text or "提醒" in text:
        return "CONTACTED_WAITING_REPLY"
    return "IN_COMMUNICATION"


def _clean(value: Any) -> str:
    return str(value or "").strip().replace("\n", "").replace(" ", "")


def _master_columns(headers: list[str]) -> dict[str, int]:
    normalized = {_clean(name).lower(): index for index, name in enumerate(headers)}
    columns: dict[str, int] = {}
    for canonical, aliases in MASTER_HEADER_ALIASES.items():
        for alias in aliases:
            index = normalized.get(_clean(alias).lower())
            if index is not None:
                columns[canonical] = index
                break
    return columns


def _phone(value: Any) -> str:
    return "".join(char for char in _clean(value) if char.isdigit())[-11:]


def _master_index(path: Path) -> tuple[dict[str, list[dict]], dict[tuple[str, str], list[dict]]]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        if "2026 新在册表" not in workbook.sheetnames:
            raise ValueError("主档案缺少“2026 新在册表”工作表")
        sheet = workbook["2026 新在册表"]
        headers = [_clean(cell.value) for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        col = _master_columns(headers)
        required = {"姓名", "手机号码", "所在分中心"}
        if not required.issubset(col):
            missing = "、".join(sorted(required - set(col)))
            raise ValueError(f"主档案缺少必要列：{missing}")
        by_phone: dict[str, list[dict]] = {}; by_name_center: dict[tuple[str, str], list[dict]] = {}
        for row_no, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            record = {
                "姓名": values[col["姓名"]],
                "手机号码": values[col["手机号码"]],
                "所在分中心": values[col["所在分中心"]],
                "source_row": row_no,
            }
            phone = _phone(record.get("手机号码")); key = (_clean(record.get("姓名")), _clean(record.get("所在分中心")))
            if phone: by_phone.setdefault(phone, []).append(record)
            if all(key): by_name_center.setdefault(key, []).append(record)
        return by_phone, by_name_center
    finally:
        workbook.close()


def _linked_member_id(
    phone: str,
    name: str,
    org_id: str | None,
    by_phone_hash: dict[str, list[dict[str, Any]]],
    by_name_org: dict[tuple[str, str], list[dict[str, Any]]],
) -> int | None:
    """Resolve a preview row to one existing production member, if unique."""
    if phone:
        try:
            candidates = by_phone_hash.get(phone_hash(phone), [])
        except ValueError:
            candidates = []
        if len(candidates) == 1:
            return int(candidates[0]["id"])
        if org_id:
            scoped = [row for row in candidates if row["org_unit_id"] == org_id]
            if len(scoped) == 1:
                return int(scoped[0]["id"])
    if name and org_id:
        candidates = by_name_org.get((name, org_id), [])
        if len(candidates) == 1:
            return int(candidates[0]["id"])
    return None


def preview_workbook(path: str | Path, master_path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path)
    workbook = load_workbook(source, data_only=True, read_only=True)
    try:
        if "2026年续费基数" not in workbook.sheetnames:
            raise ValueError("未找到“2026年续费基数”工作表")
        sheet = workbook["2026年续费基数"]
        headers = [str(cell.value or "").strip() for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        required = {"名字", "所在分中心", "2025年缴费月份"}
        if not required.issubset(headers):
            raise ValueError("续费基数表缺少姓名、分中心或2025年缴费月份列")
        col = {name: index for index, name in enumerate(headers)}
        master_by_phone, master_by_name_center = _master_index(Path(master_path)) if master_path else ({}, {})
        existing = fetch_all("SELECT id, name, org_unit_id, phone_hash FROM members")
        by_phone_hash: dict[str, list[dict[str, Any]]] = {}
        by_name_org: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in existing:
            if row["phone_hash"]:
                by_phone_hash.setdefault(row["phone_hash"], []).append(row)
            by_name_org.setdefault((row["name"], row["org_unit_id"]), []).append(row)
        rows: list[dict[str, Any]] = []
        summary = {
            "total": 0,
            "matched": 0,
            "needs_review": 0,
            "invalid": 0,
            "assistance_review": 0,
            "production_linked": 0,
            "production_unlinked": 0,
            "importable": 0,
        }
        for row_no, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            name = str(values[col["名字"]] or "").strip()
            center_name = str(values[col["所在分中心"]] or "").strip()
            class_name = str(values[col["所属班级"]] or "").strip() if "所属班级" in col else ""
            # 先锋班、黄埔班仅为直属学习班级；续费发展仍按六大分中心统计。
            org_id, reporting_name = CENTER_IDS.get(center_name), center_name
            due_month = _month(values[col["2025年缴费月份"]])
            note = str(values[col["事务所跟进续费情况"]] or "").strip() if "事务所跟进续费情况" in col else ""
            assistance = str(values[col["需要协助"]] or "").strip() if "需要协助" in col else ""
            raw = {headers[i]: values[i] for i in range(len(headers)) if values[i] not in (None, "")}
            summary["total"] += 1
            org_id = CENTER_IDS.get(center_name)
            source_phone = _phone(values[col["手机号码"]]) if "手机号码" in col else ""
            master_phone = master_by_phone.get(source_phone, []) if source_phone else []
            master_name = master_by_name_center.get((_clean(name), _clean(center_name)), []) if name and center_name else []
            existing_members = by_name_org.get((name, org_id), []) if name and org_id else []
            member = existing_members[0] if len(existing_members) == 1 else None
            linked_member_id = _linked_member_id(
                source_phone, _clean(name), org_id, by_phone_hash, by_name_org
            )
            if not name or not org_id or not due_month:
                match_status, issue = "INVALID", "MISSING_REQUIRED_FIELD"
                summary["invalid"] += 1
            elif len(master_phone) == 1:
                match_status, issue, member = "MASTER_PHONE_EXACT", None, master_phone[0]
                summary["matched"] += 1
            elif len(master_phone) > 1:
                match_status, issue = "NEEDS_REVIEW", "MASTER_PHONE_DUPLICATE"
                summary["needs_review"] += 1
            elif len(master_name) == 1:
                match_status, issue, member = "MASTER_NAME_CENTER_EXACT", None, master_name[0]
                summary["matched"] += 1
            elif len(master_name) > 1:
                match_status, issue = "NEEDS_REVIEW", "MASTER_NAME_CENTER_DUPLICATE"
                summary["needs_review"] += 1
            elif member:
                match_status, issue = "MATCHED", None
                summary["matched"] += 1
            else:
                match_status, issue = "NEEDS_REVIEW", "MEMBER_NOT_MATCHED"
                summary["needs_review"] += 1
            if assistance:
                summary["assistance_review"] += 1
            rows.append({"row_no": row_no, "name": name, "org_unit_id": org_id, "center_name": reporting_name, "source_center_name": center_name, "class_name": class_name,
                         "member_id": linked_member_id or (member.get("id") if member else None),
                         "master_source_row": member.get("source_row") if member else None, "due_month": due_month,
                         "match_status": match_status, "issue_code": issue, "proposed_status": _status(note),
                         "history_note": note, "assistance_note": assistance, "raw": raw})
        summary["production_linked"] = sum(
            row["member_id"] is not None for row in rows
        )
        summary["importable"] = sum(
            row["member_id"] is not None
            and row["match_status"] in IMPORTABLE_MATCH_STATUSES
            for row in rows
        )
        summary["production_unlinked"] = sum(
            row["member_id"] is None
            and row["match_status"] in IMPORTABLE_MATCH_STATUSES
            for row in rows
        )
        return {"source_name": source.name, "source_sha256": _hash(source), "summary": summary, "rows": rows}
    finally:
        workbook.close()


def preview_result_view(preview: dict[str, Any]) -> dict[str, Any]:
    """Return the complete review queues without exposing raw workbook rows."""
    rows = [
        {field: row.get(field) for field in PREVIEW_ROW_FIELDS}
        for row in preview["rows"]
    ]
    review_rows = [
        row for row in rows if row["match_status"] in {"NEEDS_REVIEW", "INVALID"}
    ]
    assistance_rows = [row for row in rows if row.get("assistance_note")]
    matched_samples = [
        row for row in rows if row["match_status"] in IMPORTABLE_MATCH_STATUSES
    ][:20]
    issue_summary: dict[str, int] = {}
    for row in review_rows:
        code = row.get("issue_code") or "UNKNOWN"
        issue_summary[code] = issue_summary.get(code, 0) + 1
    return {
        "summary": preview["summary"],
        "review_rows": review_rows,
        "assistance_rows": assistance_rows,
        "matched_samples": matched_samples,
        "issue_summary": issue_summary,
    }


def save_preview(preview: dict[str, Any], actor_user_id: int) -> int:
    now = datetime.now(UTC).isoformat()
    encrypted_preview = encrypt_text(
        json.dumps(preview, ensure_ascii=False, default=str)
    )
    redacted_preview = {
        "redacted": True,
        "source_sha256": preview["source_sha256"],
        "summary": preview["summary"],
        "row_count": len(preview["rows"]),
    }
    with transaction() as connection:
        batch_id = execute(connection, "INSERT INTO renewal_import_batches(source_name, source_sha256, status, preview_json, created_by, created_at) VALUES (?, ?, 'PREVIEWED', ?, ?, ?)",
                           (preview["source_name"], preview["source_sha256"], json.dumps(redacted_preview, ensure_ascii=False), actor_user_id, now)).lastrowid
        execute(
            connection,
            "UPDATE renewal_import_batches SET preview_ciphertext=? WHERE id=?",
            (encrypted_preview, batch_id),
        )
        for row in preview["rows"]:
            raw_json = json.dumps(row["raw"], ensure_ascii=False, default=str)
            history_note = row.get("history_note") or None
            assistance_note = row.get("assistance_note") or None
            execute(
                connection,
                "INSERT INTO renewal_import_staging("
                "batch_id,row_no,match_status,member_id,org_unit_id,due_month,proposed_status,"
                "history_note,assistance_note,raw_json,issue_code,created_at,"
                "history_note_ciphertext,assistance_note_ciphertext,raw_json_ciphertext) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?)",
                (
                    batch_id, row["row_no"], row["match_status"], row["member_id"],
                    row["org_unit_id"], row["due_month"], row["proposed_status"],
                    row["issue_code"], now,
                    encrypt_text(history_note) if history_note else None,
                    encrypt_text(assistance_note) if assistance_note else None,
                    encrypt_text(raw_json),
                ),
            )
        return batch_id


def apply_preview(batch_id: int, actor_user_id: int, renewal_year: int, confirmation: str) -> dict[str, int]:
    if confirmation != "确认正式导入续费周期":
        raise PermissionError("确认文字不匹配，已禁止正式导入")
    batch = fetch_one(
        "SELECT id, status, source_name FROM renewal_import_batches WHERE id=?",
        (batch_id,),
    )
    if not batch:
        raise ValueError("续费预检批次不存在")
    if batch["status"] != "PREVIEWED":
        raise ValueError("该批次已处理，不能重复正式导入")
    allowed = accessible_org_ids(actor_user_id)
    staged_total = fetch_one(
        "SELECT COUNT(*) AS count FROM renewal_import_staging WHERE batch_id=?",
        (batch_id,),
    )["count"]
    placeholders = ",".join("?" for _ in IMPORTABLE_MATCH_STATUSES)
    rows = fetch_all(
        "SELECT s.id, s.member_id, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, "
        "s.due_month, s.proposed_status FROM renewal_import_staging s "
        "JOIN members m ON m.id=s.member_id "
        f"WHERE s.batch_id=? AND s.member_id IS NOT NULL AND s.org_unit_id IS NOT NULL "
        f"AND s.match_status IN ({placeholders})",
        (batch_id, *sorted(IMPORTABLE_MATCH_STATUSES)),
    )
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    if not rows:
        raise ValueError("没有已关联生产学员且通过匹配门禁的可导入记录")
    # A renewal workbook can contain more than one eligible line for the same
    # production member (for example, a duplicated source line or a corrected
    # due month).  renewal_cycles deliberately has a unique member/year key;
    # importing the raw staging rows would therefore abort the whole batch on
    # the second line.  Keep the first staging line deterministically and count
    # later lines as skipped.  The precheck remains read-only and the batch
    # audit records the deduplication count for manual follow-up.
    unique_rows: list[dict[str, Any]] = []
    seen_member_ids: set[int] = set()
    duplicate_skipped = 0
    for row in rows:
        member_id = int(row["member_id"])
        if member_id in seen_member_ids:
            duplicate_skipped += 1
            continue
        seen_member_ids.add(member_id)
        unique_rows.append(row)
    rows = unique_rows
    member_ids = sorted(seen_member_ids)
    member_placeholders = ",".join("?" for _ in member_ids)
    existing_count = fetch_one(
        f"SELECT COUNT(*) AS count FROM renewal_cycles WHERE renewal_year=? "
        f"AND member_id IN ({member_placeholders})",
        (renewal_year, *member_ids),
    )["count"]
    if existing_count:
        raise ValueError(
            "目标年度已存在续费周期，首次整批导入已停止；请先生成差异确认包"
        )
    now = datetime.now(UTC).isoformat()
    created = 0
    with transaction() as connection:
        for row in rows:
            cycle_id = execute(
                connection,
                "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, "
                "status, source_batch_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["member_id"], renewal_year, row["org_unit_id"], row["due_month"],
                    row["proposed_status"], batch_id, now, now,
                ),
            ).lastrowid
            execute(
                connection,
                "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, "
                "reason, changed_by, created_at) VALUES (?, NULL, ?, ?, ?, ?)",
                (
                    cycle_id,
                    row["proposed_status"],
                    f"续费名单正式导入（批次 #{batch_id}）",
                    actor_user_id,
                    now,
                ),
            )
            created += 1
        execute(
            connection,
            "UPDATE renewal_import_batches SET status='APPLIED', applied_at=? WHERE id=?",
            (now, batch_id),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.import.apply",
            resource_type="renewal_import_batch",
            resource_id=str(batch_id),
            purpose="续费名单正式导入",
            after={
                "renewal_year": renewal_year,
                "created": created,
                "updated": 0,
                "duplicate_staging_rows_skipped": duplicate_skipped,
            },
        )
    return {
        "created": created,
        "updated": 0,
        "skipped": staged_total - len(rows),
    }


def rollback_import(
    batch_id: int,
    actor_user_id: int,
    confirmation: str,
) -> dict[str, int]:
    if confirmation != "确认回滚续费导入批次":
        raise PermissionError("确认文字不匹配，已禁止回滚")
    batch = fetch_one(
        "SELECT id, status, applied_at FROM renewal_import_batches WHERE id=?",
        (batch_id,),
    )
    if not batch:
        raise ValueError("续费导入批次不存在")
    if batch["status"] != "APPLIED":
        raise ValueError("只有已正式导入且未回滚的批次可以回滚")
    cycles = fetch_all(
        "SELECT c.id, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, "
        "c.created_at, c.updated_at FROM renewal_cycles c "
        "JOIN members m ON m.id=c.member_id "
        "WHERE c.source_batch_id=? ORDER BY c.id",
        (batch_id,),
    )
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and any(
        cycle["org_unit_id"] not in allowed for cycle in cycles
    ):
        raise PermissionError("批次包含当前账号授权范围外的续费周期")
    if any(cycle["created_at"] != cycle["updated_at"] for cycle in cycles):
        raise ValueError("批次中的续费周期已被修改，必须先人工生成联合回滚清单")
    cycle_ids = [cycle["id"] for cycle in cycles]
    if cycle_ids:
        placeholders = ",".join("?" for _ in cycle_ids)
        followup_count = fetch_one(
            f"SELECT COUNT(*) AS count FROM renewal_followups "
            f"WHERE renewal_cycle_id IN ({placeholders})",
            tuple(cycle_ids),
        )["count"]
        if followup_count:
            raise ValueError("批次中的续费周期已有跟进记录，禁止自动回滚")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        if cycle_ids:
            placeholders = ",".join("?" for _ in cycle_ids)
            execute(
                connection,
                f"DELETE FROM renewal_status_history WHERE renewal_cycle_id IN ({placeholders})",
                tuple(cycle_ids),
            )
            execute(
                connection,
                f"DELETE FROM renewal_cycles WHERE id IN ({placeholders})",
                tuple(cycle_ids),
            )
        execute(
            connection,
            "UPDATE renewal_import_batches SET status='ROLLED_BACK' WHERE id=?",
            (batch_id,),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.import.rollback",
            resource_type="renewal_import_batch",
            resource_id=str(batch_id),
            purpose="续费名单整批回滚",
            before={"status": "APPLIED", "cycle_count": len(cycle_ids)},
            after={"status": "ROLLED_BACK", "rolled_back_at": now},
        )
    return {"deleted_cycles": len(cycle_ids)}


def list_cycles(
    user_id: int,
    year: int = 2026,
    status: str | None = None,
    *,
    org_unit_id: str | None = None,
    due_month: int | None = None,
    member_name: str | None = None,
    renewal_status: str = "UNRENEWED",
    include_past: bool = False,
) -> list[dict[str, Any]]:
    """List renewal cycles with scoped, privacy-safe operational filters.

    The default view is intentionally limited to the current year's remaining
    months and cycles that are not marked RENEWED. Callers can select a month
    explicitly or opt into all months for historical review.
    """
    conditions = ["c.renewal_year=?"]
    params: list[Any] = [year]
    if status:
        conditions.append("c.status=?")
        params.append(status)
    else:
        if renewal_status == "RENEWED":
            conditions.append("c.status='RENEWED'")
        elif renewal_status == "UNRENEWED":
            conditions.append("c.status<>'RENEWED'")
        elif renewal_status != "ALL":
            raise ValueError("是否续费筛选值无效")
    if org_unit_id:
        conditions.append(f"{MEMBER_RENEWAL_ORG_SQL}=?")
        params.append(org_unit_id)
    if due_month is not None:
        if not 1 <= due_month <= 12:
            raise ValueError("月份必须在1至12之间")
        conditions.append("c.due_month=?")
        params.append(due_month)
    elif not include_past and year == datetime.now(UTC).year:
        conditions.append("c.due_month>=? AND c.due_month<=12")
        params.append(datetime.now(UTC).month)
    if member_name and member_name.strip():
        conditions.append("m.name LIKE ?")
        params.append(f"%{member_name.strip()}%")
    rows = fetch_all(
        "SELECT c.id, c.member_id, m.member_code, m.name AS member_name, c.renewal_year, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, o.name AS org_name, "
        "c.org_unit_id AS imported_org_unit_id, imported_org.name AS imported_org_name, "
        "m.org_unit_id AS member_org_unit_id, m.development_org_unit_id AS member_development_org_unit_id, "
        f"{MEMBER_CLASS_NAME_SQL} AS member_class_name, "
        f"{MEMBER_GROUP_NAME_SQL} AS member_group_name, "
        "c.due_month, c.phase, c.status, c.result, "
        "c.assigned_user_id, u.display_name AS assigned_user_name, c.completed_at, c.updated_at "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id "
        f"JOIN org_units o ON o.id={MEMBER_RENEWAL_ORG_SQL} "
        "LEFT JOIN org_units imported_org ON imported_org.id=c.org_unit_id "
        "LEFT JOIN app_users u ON u.id=c.assigned_user_id WHERE "
        + " AND ".join(conditions)
        + " ORDER BY c.due_month, c.id",
        tuple(params),
    )
    allowed = accessible_org_ids(user_id)
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return rows


def list_cycle_coverage(
    user_id: int,
    year: int = 2026,
    *,
    org_unit_id: str | None = None,
    member_name: str | None = None,
    include_synced: bool = False,
    actionable_only: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """Compare member master data with annual renewal-cycle coverage.

    This is intentionally read-only. Missing cycles remain visible instead of
    being silently excluded from the operations page; creating a cycle is a
    separate, audited action.
    """
    if not 1 <= limit <= 500:
        raise ValueError("同步检查条数必须在1至500之间")
    conditions = ["1=1"]
    params: list[Any] = [year]
    if org_unit_id:
        conditions.append(f"{MEMBER_RENEWAL_ORG_SQL}=?")
        params.append(org_unit_id)
    if member_name and member_name.strip():
        conditions.append("m.name LIKE ?")
        params.append(f"%{member_name.strip()}%")
    rows = fetch_all(
        "SELECT m.id AS member_id, m.member_code, m.name AS member_name, "
        "m.status AS member_status, m.renewal_month, "
        f"{MEMBER_CLASS_NAME_SQL} AS member_class_name, "
        f"{MEMBER_GROUP_NAME_SQL} AS member_group_name, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, o.name AS org_name, "
        "c.id AS cycle_id, c.due_month, c.status AS cycle_status, c.updated_at "
        "FROM members m "
        f"JOIN org_units o ON o.id={MEMBER_RENEWAL_ORG_SQL} "
        "LEFT JOIN renewal_cycles c ON c.member_id=m.id AND c.renewal_year=? "
        "WHERE " + " AND ".join(conditions) + " ORDER BY o.name, m.name, m.id",
        tuple(params),
    )
    allowed = accessible_org_ids(user_id)
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]

    decorated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        member_status = str(item["member_status"] or "").upper()
        member_active = member_status == "ACTIVE"
        recurring_month = _member_renewal_month(item.get("renewal_month"))
        if item.get("cycle_id"):
            if member_active:
                sync_status = "SYNCED"
            elif member_status == "SUSPENDED":
                sync_status = "SYNCED_SUSPENDED"
            else:
                sync_status = "SYNCED_INACTIVE"
        elif not member_active:
            sync_status = "SUSPENDED" if member_status == "SUSPENDED" else "INACTIVE"
        elif recurring_month:
            sync_status = "READY_TO_CREATE"
            item["due_month"] = recurring_month
        else:
            sync_status = "MISSING_RENEWAL_MONTH"
        item["sync_status"] = sync_status
        item["can_create_cycle"] = sync_status == "READY_TO_CREATE"
        decorated.append(item)

    active_rows = [
        item
        for item in decorated
        if str(item["member_status"] or "").upper() == "ACTIVE"
    ]
    summary = {
        "member_total": len(decorated),
        "active_member_total": len(active_rows),
        "cycle_total": sum(1 for item in decorated if item.get("cycle_id")),
        "ready_to_create_count": sum(
            1 for item in decorated if item["sync_status"] == "READY_TO_CREATE"
        ),
        "missing_renewal_month_count": sum(
            1
            for item in decorated
            if item["sync_status"] == "MISSING_RENEWAL_MONTH"
        ),
        "inactive_member_count": sum(
            1
            for item in decorated
            if item["sync_status"] in {"INACTIVE", "SYNCED_INACTIVE"}
        ),
        "suspended_member_count": sum(
            1
            for item in decorated
            if item["sync_status"] in {"SUSPENDED", "SYNCED_SUSPENDED"}
        ),
    }
    visible = (
        decorated
        if include_synced
        else [item for item in decorated if item["sync_status"] != "SYNCED"]
    )
    if actionable_only:
        visible = [
            item
            for item in visible
            if item["can_create_cycle"]
            or item["sync_status"] == "MISSING_RENEWAL_MONTH"
        ]
    return {
        "year": year,
        "summary": summary,
        "rows": visible[:limit],
        "truncated": len(visible) > limit,
    }


def create_cycle_from_member(
    member_id: int,
    actor_user_id: int,
    *,
    renewal_year: int,
    confirmation: str,
) -> int:
    """Create one missing annual cycle from confirmed member-master fields."""
    if confirmation != "确认从学员主档建立续费周期":
        raise PermissionError("确认文字不匹配，已禁止建立续费周期")
    member = fetch_one(
        "SELECT m.id, m.status, m.renewal_month, "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id "
        "FROM members m WHERE m.id=?",
        (member_id,),
    )
    if not member:
        raise ValueError("学员不存在")
    if str(member["status"] or "").upper() != "ACTIVE":
        raise ValueError("只有在册学员可以建立新的续费周期")
    due_month = _member_renewal_month(member.get("renewal_month"))
    if not due_month:
        raise ValueError("请先在学员管理补充有效的续费月份")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and member["org_unit_id"] not in allowed:
        raise PermissionError("学员不在当前账号的续费组织范围内")
    if fetch_one(
        "SELECT id FROM renewal_cycles WHERE member_id=? AND renewal_year=?",
        (member_id, renewal_year),
    ):
        raise ValueError("该学员本年度续费周期已存在")
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat()
    initial_status = _initial_cycle_status(due_month, renewal_year, now_dt)
    completed_at = now if initial_status == "RENEWED" else None
    status_reason = (
        "由学员管理续费月份建立，历史月份自动标记为已续费"
        if initial_status == "RENEWED"
        else "由学员管理续费月份建立"
    )
    with transaction() as connection:
        cycle_id = execute(
            connection,
            "INSERT INTO renewal_cycles(member_id, renewal_year, org_unit_id, due_month, "
            "status, completed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                member_id,
                renewal_year,
                member["org_unit_id"],
                due_month,
                initial_status,
                completed_at,
                now,
                now,
            ),
        ).lastrowid
        execute(
            connection,
            "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, "
            "reason, changed_by, created_at) VALUES (?, NULL, "
            "?, ?, ?, ?)",
            (cycle_id, initial_status, status_reason, actor_user_id, now),
        )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.cycle.create_from_member",
            resource_type="renewal_cycle",
            resource_id=str(cycle_id),
            org_unit_id=member["org_unit_id"],
            purpose="从学员管理主档补齐单个续费周期",
            after={
                "member_id": member_id,
                "renewal_year": renewal_year,
                "due_month": due_month,
                "status": initial_status,
            },
        )
    return int(cycle_id)


def update_cycle(
    cycle_id: int,
    actor_user_id: int,
    *,
    status: str | None = None,
    phase: str | None = None,
    result: str | None = None,
    assigned_user_id: int | None = None,
) -> None:
    if status is not None:
        status = status.strip().upper()
        if status not in RENEWAL_STATUSES:
            raise ValueError("续费状态无效")
    if phase is not None:
        phase = phase.strip()
        if len(phase) > 32:
            raise ValueError("续费阶段不能超过32个字符")
    if result is not None:
        result = result.strip()
        if len(result) > 64:
            raise ValueError("续费结果不能超过64个字符")
    cycle = _cycle_with_member_scope(cycle_id)
    if not cycle:
        raise ValueError("续费周期不存在")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and cycle["member_org_unit_id"] not in allowed:
        raise PermissionError("续费周期不在组织授权范围内")
    if assigned_user_id is not None:
        assignee = fetch_one("SELECT id, is_active FROM app_users WHERE id=?", (assigned_user_id,))
        if not assignee or not assignee["is_active"]:
            raise ValueError("责任人账号当前不可用")
        assignee_context = user_context(assigned_user_id)
        if not assignee_context or "renewals:manage" not in assignee_context["permissions"]:
            raise ValueError("责任人当前没有续费运营权限")
        assignee_allowed = accessible_org_ids(assigned_user_id)
        if assignee_allowed is not None and cycle["member_org_unit_id"] not in assignee_allowed:
            raise ValueError("责任人不在续费归属组织范围内")
    fields = {key: value for key, value in {
        "status": status, "phase": phase, "result": result,
        "assigned_user_id": assigned_user_id,
    }.items() if value is not None}
    if not fields:
        raise ValueError("至少提供一项续费周期变更")
    now = datetime.now(UTC).isoformat()
    fields["updated_at"] = now
    if status:
        fields["completed_at"] = (
            now if status in {"RENEWED", "NOT_RENEWING", "EXITED"} else None
        )
    with transaction() as connection:
        assignments = ", ".join(f"{key}=?" for key in fields)
        execute(connection, f"UPDATE renewal_cycles SET {assignments} WHERE id=?", (*fields.values(), cycle_id))
        if status and status != cycle["status"]:
            execute(
                connection,
                "INSERT INTO renewal_status_history(renewal_cycle_id, from_status, to_status, "
                "changed_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (cycle_id, cycle["status"], status, actor_user_id, now),
            )
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.cycle.update",
            resource_type="renewal_cycle",
            resource_id=str(cycle_id),
            org_unit_id=cycle["member_org_unit_id"],
            after=fields,
        )


def add_followup(
    cycle_id: int,
    actor_user_id: int,
    *,
    channel: str,
    summary: str,
    intention: str | None = None,
    needs_support: bool = False,
    next_action: str | None = None,
    next_followup_at: str | None = None,
) -> int:
    cycle = _cycle_with_member_scope(cycle_id)
    if not cycle:
        raise ValueError("续费周期不存在")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and cycle["member_org_unit_id"] not in allowed:
        raise PermissionError("续费周期不在组织授权范围内")
    if channel.strip().upper() not in {"PHONE", "WECHAT", "MEETING", "VISIT", "OTHER"}:
        raise ValueError("不支持的联系渠道")
    if len(summary.strip()) < 4:
        raise ValueError("跟进摘要至少填写4个字符")
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        followup_id = execute(
            connection,
            "INSERT INTO renewal_followups(renewal_cycle_id, followed_at, followed_by, channel, "
            "summary, intention, needs_support, next_action, next_followup_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cycle_id, now, actor_user_id, channel.strip().upper(), summary.strip(),
                intention, 1 if needs_support else 0, next_action, next_followup_at, now,
            ),
        ).lastrowid
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="renewals.followup.create",
            resource_type="renewal_cycle",
            resource_id=str(cycle_id),
            org_unit_id=cycle["member_org_unit_id"],
            after={"followup_id": followup_id, "channel": channel.strip().upper()},
        )
        return followup_id


def list_followups(cycle_id: int, actor_user_id: int) -> list[dict[str, Any]]:
    cycle = _cycle_with_member_scope(cycle_id)
    if not cycle:
        raise ValueError("续费周期不存在")
    allowed = accessible_org_ids(actor_user_id)
    if allowed is not None and cycle["member_org_unit_id"] not in allowed:
        raise PermissionError("续费周期不在组织授权范围内")
    return fetch_all(
        "SELECT id, followed_at, followed_by, channel, summary, intention, needs_support, "
        "next_action, next_followup_at FROM renewal_followups WHERE renewal_cycle_id=? "
        "ORDER BY followed_at DESC, id DESC",
        (cycle_id,),
    )


def list_overview(user_id: int, year: int = 2026) -> dict[str, Any]:
    allowed = accessible_org_ids(user_id)
    rows = fetch_all(
        "SELECT "
        f"{MEMBER_RENEWAL_ORG_SQL} AS org_unit_id, o.name AS org_name, "
        "c.due_month, c.status, COUNT(*) AS count "
        "FROM renewal_cycles c JOIN members m ON m.id=c.member_id "
        f"JOIN org_units o ON o.id={MEMBER_RENEWAL_ORG_SQL} "
        "WHERE c.renewal_year=? "
        f"GROUP BY {MEMBER_RENEWAL_ORG_SQL}, o.name, c.due_month, c.status",
        (year,),
    )
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return {"year": year, "rows": rows}
