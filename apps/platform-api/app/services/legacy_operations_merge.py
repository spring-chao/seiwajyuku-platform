from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from app.db import execute, fetch_all, transaction
from app.services.audit import write_audit
from app.services.iam import user_context


SOURCE_SYSTEM = "seiwajyuku_system"
WORKBOOK_SOURCE_SYSTEM = "activity_workbooks"
BUNDLE_VERSION = 1
MAX_FACTS = 200_000
IMPORT_TYPE = "LEGACY_OPERATIONS_ACTIVITY_FACTS"

SOURCE_TABLES = {
    "group_sessions": "GROUP_SESSION",
    "class_sessions": "CLASS_SESSION",
    "courses": "COURSE",
    "report_meetings": "REPORT_MEETING",
    "study_tours": "STUDY_TOUR",
    "reading_checkins": "READING_CHECKIN",
    "reading_shares": "READING_SHARE",
}
WORKBOOK_SOURCE_TABLES = {
    **SOURCE_TABLES,
    "learning_meetings": "LEARNING_MEETING",
    "class_study_days": "CLASS_STUDY_DAY",
    "class_openings": "CLASS_OPENING",
    "other_activities": "OTHER_ACTIVITY",
}
ALLOWED_STATUSES = {"PRESENT", "ABSENT", "COMPLETED", "RECORDED"}
ALLOWED_FACT_KEYS = {
    "source_table",
    "external_id",
    "member_code",
    "occurred_on",
    "participation_status",
    "title",
    "duration_minutes",
    "source_updated_at",
}
ALLOWED_ROOT_KEYS = {
    "bundle_version",
    "source_system",
    "generated_at",
    "privacy_contract",
    "source_counts",
    "facts",
}
PROHIBITED_KEYS = {
    "phone",
    "mobile",
    "name",
    "notes",
    "reflection",
    "evaluation",
    "feedback",
    "content",
    "content_summary",
    "speech_topic",
    "quality_score",
    "score",
}


def bundle_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _clean_text(value: Any, *, max_length: int, field: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) > max_length:
        raise ValueError(f"{field} 超过 {max_length} 字符")
    return text or None


def _normalize_fact(
    raw: Any, index: int, source_tables: dict[str, str] | None = None
) -> dict[str, Any]:
    source_tables = SOURCE_TABLES if source_tables is None else source_tables
    if not isinstance(raw, dict):
        raise ValueError(f"facts[{index}] 必须为对象")
    unexpected = set(raw) - ALLOWED_FACT_KEYS
    prohibited = unexpected & PROHIBITED_KEYS
    if prohibited:
        raise ValueError(
            f"facts[{index}] 含禁止迁移字段: {', '.join(sorted(prohibited))}"
        )
    if unexpected:
        raise ValueError(
            f"facts[{index}] 含未定义字段: {', '.join(sorted(unexpected))}"
        )
    source_table = str(raw.get("source_table") or "").strip()
    if source_table not in source_tables:
        raise ValueError(f"facts[{index}] source_table 不受支持")
    external_id = _clean_text(
        raw.get("external_id"), max_length=191, field=f"facts[{index}].external_id"
    )
    member_code = _clean_text(
        raw.get("member_code"), max_length=128, field=f"facts[{index}].member_code"
    )
    if not external_id:
        raise ValueError(f"facts[{index}].external_id 不能为空")
    occurred_on = str(raw.get("occurred_on") or "").strip()
    try:
        date.fromisoformat(occurred_on)
    except ValueError as exc:
        raise ValueError(f"facts[{index}].occurred_on 必须为 YYYY-MM-DD") from exc
    status = str(raw.get("participation_status") or "RECORDED").strip().upper()
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"facts[{index}].participation_status 不受支持")
    duration = raw.get("duration_minutes")
    if duration not in (None, ""):
        if isinstance(duration, bool):
            raise ValueError(f"facts[{index}].duration_minutes 必须为整数")
        try:
            duration = int(duration)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"facts[{index}].duration_minutes 必须为整数") from exc
        if duration < 0 or duration > 100_000:
            raise ValueError(f"facts[{index}].duration_minutes 超出范围")
    else:
        duration = None
    return {
        "source_table": source_table,
        "external_id": external_id,
        "member_code": member_code,
        "activity_type": source_tables[source_table],
        "occurred_on": occurred_on,
        "participation_status": status,
        "title": _clean_text(raw.get("title"), max_length=255, field=f"facts[{index}].title"),
        "duration_minutes": duration,
        "source_updated_at": _clean_text(
            raw.get("source_updated_at"),
            max_length=64,
            field=f"facts[{index}].source_updated_at",
        ),
    }


def parse_bundle(content: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("合并包必须是 UTF-8 JSON 文件") from exc
    if not isinstance(payload, dict):
        raise ValueError("合并包根节点必须为对象")
    unexpected_root = set(payload) - ALLOWED_ROOT_KEYS
    if unexpected_root:
        raise ValueError(
            f"合并包含未定义根字段: {', '.join(sorted(unexpected_root))}"
        )
    if payload.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(f"仅支持 bundle_version={BUNDLE_VERSION}")
    source_system = payload.get("source_system")
    if not isinstance(source_system, str) or source_system not in {SOURCE_SYSTEM, WORKBOOK_SOURCE_SYSTEM}:
        raise ValueError("source_system 必须为 seiwajyuku_system 或 activity_workbooks")
    source_tables = (
        WORKBOOK_SOURCE_TABLES if source_system == WORKBOOK_SOURCE_SYSTEM else SOURCE_TABLES
    )
    privacy = payload.get("privacy_contract")
    expected_privacy = {
        "matching_key": "member_code",
        "contains_names": False,
        "contains_phones": False,
        "contains_narratives": False,
    }
    if privacy is not None and privacy != expected_privacy:
        raise ValueError("privacy_contract 与统一平台隐私契约不一致")
    source_counts = payload.get("source_counts")
    if source_counts is not None:
        if not isinstance(source_counts, dict) or set(source_counts) - set(source_tables):
            raise ValueError("source_counts 含未定义数据表")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in source_counts.values()
        ):
            raise ValueError("source_counts 必须为非负整数")
    facts = payload.get("facts")
    if not isinstance(facts, list):
        raise ValueError("facts 必须为数组")
    if not facts:
        raise ValueError("合并包不包含任何活动事实")
    if len(facts) > MAX_FACTS:
        raise ValueError(f"单个合并包最多包含 {MAX_FACTS} 条事实")
    normalized = [_normalize_fact(raw, index, source_tables) for index, raw in enumerate(facts)]
    return {
        "bundle_version": BUNDLE_VERSION,
        "source_system": source_system,
        "generated_at": _clean_text(
            payload.get("generated_at"), max_length=64, field="generated_at"
        ),
        "facts": normalized,
    }


def _evaluate(
    facts: list[dict[str, Any]], member_rows: list[Any], existing_rows: list[Any]
) -> dict[str, Any]:
    members = {
        row["member_code"]: {
            "member_id": row["id"],
            "org_unit_id": row["org_unit_id"],
        }
        for row in member_rows
        if row["member_code"]
    }
    existing = {
        (row["source_table"], row["external_id"])
        for row in existing_rows
    }
    issues: list[dict[str, Any]] = []
    matched = duplicates = missing_code = unmatched = 0
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fact in facts:
        key = (fact["source_table"], fact["external_id"])
        if key in existing or key in seen:
            duplicates += 1
            continue
        seen.add(key)
        code = fact["member_code"]
        if not code:
            missing_code += 1
            if len(issues) < 100:
                issues.append({
                    "code": "MISSING_MEMBER_CODE",
                    "source_table": fact["source_table"],
                    "external_id": fact["external_id"],
                })
            continue
        member = members.get(code)
        if not member:
            unmatched += 1
            if len(issues) < 100:
                issues.append({
                    "code": "MEMBER_NOT_FOUND",
                    "member_code": code,
                    "source_table": fact["source_table"],
                    "external_id": fact["external_id"],
                })
            continue
        matched += 1
        rows.append({**fact, **member})
    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row["activity_type"]] = by_type.get(row["activity_type"], 0) + 1
    return {
        "rows": rows,
        "issues": issues,
        "summary": {
            "total": len(facts),
            "importable": len(rows),
            "matched": matched,
            "duplicates": duplicates,
            "missing_member_code": missing_code,
            "unmatched_member": unmatched,
            "issue_sample_count": len(issues),
            "by_activity_type": by_type,
        },
    }


def _require_known_member_codes(facts: list[dict[str, Any]], member_rows: list[Any]) -> None:
    known_codes = {row["member_code"] for row in member_rows if row["member_code"]}
    if any(not fact["member_code"] or fact["member_code"] not in known_codes for fact in facts):
        raise ValueError("活动表存在未核验的学员编号，请先完成人员匹配后再正式导入")


def _preview_parsed_bundle(
    bundle: dict[str, Any], content: bytes, source_name: str, *, require_known_codes: bool = False
) -> dict[str, Any]:
    member_rows = fetch_all("SELECT id, member_code, org_unit_id FROM members")
    if require_known_codes:
        _require_known_member_codes(bundle["facts"], member_rows)
    evaluated = _evaluate(
        bundle["facts"],
        member_rows,
        fetch_all(
            "SELECT source_table, external_id FROM member_activity_facts "
            "WHERE source_system=?",
            (bundle["source_system"],),
        ),
    )
    return {
        "source_name": source_name,
        "source_sha256": bundle_sha256(content),
        "generated_at": bundle["generated_at"],
        "summary": evaluated["summary"],
        "issues": evaluated["issues"],
        "privacy": {
            "contains_names": False,
            "contains_phones": False,
            "contains_narratives": False,
            "matching_key": "member_code",
        },
    }


def preview_bundle(content: bytes, source_name: str) -> dict[str, Any]:
    return _preview_parsed_bundle(parse_bundle(content), content, source_name)


def apply_bundle(
    content: bytes,
    source_name: str,
    actor_user_id: int,
    confirmation_reason: str,
    second_confirmed: bool,
) -> dict[str, Any]:
    reason = confirmation_reason.strip()
    if not second_confirmed or len(reason) < 8:
        raise ValueError("正式合并必须二次确认并填写至少8个字符的原因")
    actor = user_context(actor_user_id)
    if not actor or "integrations:manage" not in actor["permissions"]:
        raise PermissionError("缺少旧运营系统合并权限")
    bundle = parse_bundle(content)
    return _apply_parsed_bundle(bundle, content, actor_user_id, reason)


def _lock_service_import(connection, facts: list[dict[str, Any]]) -> str:
    if isinstance(connection, sqlite3.Connection):
        if not connection.in_transaction:
            execute(connection, "BEGIN IMMEDIATE")
        return ""
    # The same frozen package always locks the same existing member first.
    # A current read of its batch after this lock also handles all-duplicate packages.
    code = min(fact["member_code"] for fact in facts)
    member = execute(
        connection, "SELECT id FROM members WHERE member_code=? FOR UPDATE", (code,)
    ).fetchone()
    if not member:
        raise ValueError("活动表存在未核验的学员编号，请先完成人员匹配后再正式导入")
    return " FOR UPDATE"


def _apply_parsed_bundle(
    bundle: dict[str, Any], content: bytes, actor_user_id: int | None, reason: str, *,
    require_known_codes: bool = False, service_actor: bool = False,
    service_deadline_utc: datetime | None = None,
) -> dict[str, Any]:
    source_sha = bundle_sha256(content)
    stored_source_name = f"legacy-operations-{source_sha[:12]}.json"
    now = datetime.now(UTC).isoformat()
    with transaction() as connection:
        lock_suffix = _lock_service_import(connection, bundle["facts"]) if service_actor else ""
        if service_actor and (
            service_deadline_utc is None or datetime.now(UTC) >= service_deadline_utc
        ):
            raise PermissionError("该冻结活动包的机器导入范围无效或已到期")
        existing_batch = execute(
            connection,
            "SELECT id FROM import_batches WHERE import_type=? AND source_sha256=? "
            "AND status='APPLIED' ORDER BY id DESC LIMIT 1" + lock_suffix,
            (IMPORT_TYPE, source_sha),
        ).fetchone()
        if existing_batch:
            raise ValueError("该合并包已经执行，不能重复应用")
        member_rows = execute(
            connection, "SELECT id, member_code, org_unit_id FROM members"
        ).fetchall()
        if require_known_codes:
            _require_known_member_codes(bundle["facts"], member_rows)
        evaluated = _evaluate(
            bundle["facts"],
            member_rows,
            execute(
                connection,
                "SELECT source_table, external_id FROM member_activity_facts "
                "WHERE source_system=?",
                (bundle["source_system"],),
            ).fetchall(),
        )
        summary = evaluated["summary"]
        if bundle["source_system"] == WORKBOOK_SOURCE_SYSTEM and (
            summary["missing_member_code"] or summary["unmatched_member"]
        ):
            raise ValueError("活动表存在未核验的学员编号，请先完成人员匹配后再正式导入")
        cursor = execute(
            connection,
            "INSERT INTO import_batches(import_type, source_name, source_sha256, status, "
            "preview_json, created_by, created_at, applied_at) "
            "VALUES (?, ?, ?, 'APPLIED', ?, ?, ?, ?)",
            (
                IMPORT_TYPE,
                stored_source_name,
                source_sha,
                json.dumps(
                    {
                        "summary": summary,
                        "privacy": {
                            "matching_key": "member_code",
                            "raw_bundle_stored": False,
                            "narratives_imported": False,
                        },
                    },
                    ensure_ascii=False,
                ),
                actor_user_id,
                now,
                now,
            ),
        )
        batch_id = cursor.lastrowid
        for row in evaluated["rows"]:
            execute(
                connection,
                "INSERT INTO member_activity_facts(source_system, source_table, external_id, "
                "member_id, org_unit_id, activity_type, occurred_on, participation_status, "
                "title, duration_minutes, source_updated_at, import_batch_id, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bundle["source_system"],
                    row["source_table"],
                    row["external_id"],
                    row["member_id"],
                    row["org_unit_id"],
                    row["activity_type"],
                    row["occurred_on"],
                    row["participation_status"],
                    row["title"],
                    row["duration_minutes"],
                    row["source_updated_at"],
                    batch_id,
                    now,
                ),
            )
        audit_after = {"source_sha256": source_sha, **summary}
        if service_actor:
            audit_after.update({"actor_type": "SERVICE", "authentication": "INTEGRATION_API_KEY"})
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="integrations.participation_history.apply" if service_actor else "legacy_operations.merge.apply",
            resource_type="import_batch",
            resource_id=str(batch_id),
            purpose=reason,
            after=audit_after,
        )
    return {"batch_id": batch_id, **summary}
