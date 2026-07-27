from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.db import execute, fetch_all, transaction
from app.services.iam import accessible_org_ids


CENTER_IDS = {
    "姑苏相城分中心": "org-gusu", "昆山分中心": "org-kunshan", "吴江分中心": "org-wujiang",
    "新吴分中心": "org-xinwu", "园区分中心": "org-yuanqu", "张家港分中心": "org-zhangjiagang",
}


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _month(value: Any) -> int | None:
    text = str(value or "").strip().replace("月", "")
    return int(text) if text.isdigit() and 1 <= int(text) <= 12 else None


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


def _phone(value: Any) -> str:
    return "".join(char for char in _clean(value) if char.isdigit())[-11:]


def _master_index(path: Path) -> tuple[dict[str, list[dict]], dict[tuple[str, str], list[dict]]]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        if "2026 新在册表" not in workbook.sheetnames:
            raise ValueError("主档案缺少“2026 新在册表”工作表")
        sheet = workbook["2026 新在册表"]
        headers = [_clean(cell.value) for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        col = {name: index for index, name in enumerate(headers)}
        required = {"姓名", "手机号码", "所在分中心"}
        if not required.issubset(col):
            raise ValueError("主档案缺少姓名、手机号码或所在分中心列")
        by_phone: dict[str, list[dict]] = {}; by_name_center: dict[tuple[str, str], list[dict]] = {}
        for row_no, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), 2):
            record = {headers[i]: values[i] for i in range(len(headers)) if values[i] not in (None, "")}
            record["source_row"] = row_no
            phone = _phone(record.get("手机号码")); key = (_clean(record.get("姓名")), _clean(record.get("所在分中心")))
            if phone: by_phone.setdefault(phone, []).append(record)
            if all(key): by_name_center.setdefault(key, []).append(record)
        return by_phone, by_name_center
    finally:
        workbook.close()


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
        existing = fetch_all("SELECT id, name, org_unit_id, phone_hash FROM members") if not master_path else []
        by_name_org = {(row["name"], row["org_unit_id"]): row for row in existing}
        rows: list[dict[str, Any]] = []
        summary = {"total": 0, "matched": 0, "needs_review": 0, "invalid": 0, "assistance_review": 0}
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
            member = by_name_org.get((name, org_id)) if name and org_id else None
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
                         "member_id": member.get("id") if member else None, "master_source_row": member.get("source_row") if member else None, "due_month": due_month,
                         "match_status": match_status, "issue_code": issue, "proposed_status": _status(note),
                         "history_note": note, "assistance_note": assistance, "raw": raw})
        return {"source_name": source.name, "source_sha256": _hash(source), "summary": summary, "rows": rows}
    finally:
        workbook.close()


def save_preview(preview: dict[str, Any], actor_user_id: int) -> int:
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        batch_id = execute(connection, "INSERT INTO renewal_import_batches(source_name, source_sha256, status, preview_json, created_by, created_at) VALUES (?, ?, 'PREVIEWED', ?, ?, ?)",
                           (preview["source_name"], preview["source_sha256"], json.dumps(preview, ensure_ascii=False), actor_user_id, now)).lastrowid
        for row in preview["rows"]:
            execute(connection, "INSERT INTO renewal_import_staging(batch_id,row_no,match_status,member_id,org_unit_id,due_month,proposed_status,history_note,assistance_note,raw_json,issue_code,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (batch_id, row["row_no"], row["match_status"], row["member_id"], row["org_unit_id"], row["due_month"], row["proposed_status"], row["history_note"], row["assistance_note"], json.dumps(row["raw"], ensure_ascii=False, default=str), row["issue_code"], now))
        return batch_id


def list_overview(user_id: int, year: int = 2026) -> dict[str, Any]:
    allowed = accessible_org_ids(user_id)
    rows = fetch_all("SELECT c.org_unit_id, o.name AS org_name, c.due_month, c.status, COUNT(*) AS count FROM renewal_cycles c JOIN org_units o ON o.id=c.org_unit_id WHERE c.renewal_year=? GROUP BY c.org_unit_id,o.name,c.due_month,c.status", (year,))
    if allowed is not None:
        rows = [row for row in rows if row["org_unit_id"] in allowed]
    return {"year": year, "rows": rows}
