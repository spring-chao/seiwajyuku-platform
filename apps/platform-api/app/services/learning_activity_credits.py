"""C7 learning-activity facts, business calendars, and read-only previews.

Daily reading and excellent sharing are deliberately modeled as source facts
first.  This module never treats a button press or a client supplied score as
an accounting entry.  It resolves the learning round and its frozen generic
credit policy from the fact's local occurrence date, then returns an
ephemeral proposal for the existing ledger.

Formal C7 posting is intentionally not exposed yet.  The only C7 settlement
operation in this release is DRY-RUN, and every preview includes a ledger
row-count proof showing that it performed no writes.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.core.settings import get_settings
from app.db import connect, execute, transaction
from app.services.audit import write_audit
from app.services.credit_rule_mapping import bound_generic_rule
from app.services.iam import accessible_org_ids, user_context
from app.services.learning_credits import (
    LearningCreditError,
    STANDARD_LEARNING,
    _db_timestamp,
    _entry_payload,
    _existing_entry,
    _ledger_count,
    _require_preview_permission,
    _scope_allows,
    _write_allowed,
    _write_proof,
)


BUSINESS_CALENDAR_KEY = "CHINA_MAINLAND"
BUSINESS_CALENDAR_TIMEZONE = "Asia/Shanghai"
SHANGHAI = ZoneInfo(BUSINESS_CALENDAR_TIMEZONE)

DAILY_READING = "DAILY_READING"
EXCELLENT_SHARE = "EXCELLENT_SHARE"
ACTIVITY_TYPES = {DAILY_READING, EXCELLENT_SHARE}
FACT_STATUSES = {"RECORDED", "CONFIRMED", "REJECTED", "CANCELLED"}
CREDITABLE_FACT_STATUSES = {"RECORDED", "CONFIRMED"}
DAY_TYPES = {
    "NORMAL_WORKDAY",
    "WEEKEND",
    "HOLIDAY",
    "ADJUSTED_WORKDAY",
}
CALENDAR_WRITE_STATUSES = {"DRAFT", "PUBLISHED"}
SOURCE_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
VERSION_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DAILY_SOURCE_TYPE = "LEARNING_ACTIVITY_DAILY_READING"
EXCELLENT_SOURCE_TYPE = "LEARNING_ACTIVITY_EXCELLENT_SHARE"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_business_date(value: Any, label: str = "日期") -> date:
    if isinstance(value, datetime):
        raise LearningCreditError(f"{label}必须是YYYY-MM-DD日期，不接受时间戳")
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not DATE_RE.fullmatch(text):
        raise LearningCreditError(f"{label}必须是YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise LearningCreditError(f"{label}不是有效日期") from exc


def _date_text(value: Any, label: str = "日期") -> str:
    return _parse_business_date(value, label).isoformat()


def _parse_optional_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return _parse_business_date(value, "组织关系有效期")


def _parse_storage_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    # The platform stores MySQL DATETIME values in UTC without a timezone.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(SHANGHAI)


def _local_date_from_timestamp(value: Any) -> date | None:
    parsed = _parse_storage_timestamp(value)
    return parsed.date() if parsed else None


def _ledger_occurrence_at(connection, occurred_on: date) -> str:
    local_midnight = datetime.combine(occurred_on, time.min, tzinfo=SHANGHAI)
    return _db_timestamp(connection, local_midnight)


def _validate_year(value: Any) -> int:
    try:
        year = int(value)
    except (TypeError, ValueError) as exc:
        raise LearningCreditError("日历年份无效") from exc
    if year < 2000 or year > 2100:
        raise LearningCreditError("日历年份必须在2000到2100之间")
    return year


def _validate_window(
    occurred_from: str | None, occurred_to: str | None
) -> tuple[str | None, str | None]:
    start = _date_text(occurred_from, "开始日期") if occurred_from else None
    end = _date_text(occurred_to, "结束日期") if occurred_to else None
    if start and end and start > end:
        raise LearningCreditError("开始日期不能晚于结束日期")
    return start, end


def _require_permission(actor_user_id: int, permission: str, message: str) -> None:
    user = user_context(actor_user_id) or {}
    if permission not in user.get("permissions", []):
        raise PermissionError(message)


def _calendar_day(
    connection, occurred_on: date, *, calendar_key: str = BUSINESS_CALENDAR_KEY
) -> dict[str, Any] | None:
    row = execute(
        connection,
        "SELECT v.id AS calendar_version_id, v.calendar_key, v.calendar_year, "
        "v.version_label, v.timezone, v.status AS calendar_status, "
        "d.id AS calendar_day_id, d.business_date, d.day_type, d.note "
        "FROM learning_business_calendar_versions v "
        "JOIN learning_business_calendar_days d ON d.calendar_version_id=v.id "
        "WHERE v.calendar_key=? AND v.calendar_year=? "
        "AND v.status='PUBLISHED' AND d.business_date=? "
        "ORDER BY v.id DESC LIMIT 1",
        (calendar_key, occurred_on.year, occurred_on.isoformat()),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["calendar_version_id"] = int(result["calendar_version_id"])
    result["calendar_day_id"] = int(result["calendar_day_id"])
    result["calendar_year"] = int(result["calendar_year"])
    result["business_date"] = _date_text(result["business_date"], "日历日期")
    return result


def is_workday(
    connection,
    business_date: date,
    *,
    calendar_key: str = BUSINESS_CALENDAR_KEY,
) -> bool | None:
    """Resolve a date through the published annual calendar.

    ``None`` means the year/date is not configured (or contains an invalid
    day type), so callers must block rather than fall back to ``weekday()``.
    """

    row = _calendar_day(connection, business_date, calendar_key=calendar_key)
    return _is_workday_row(row)


def _is_workday_row(row: dict[str, Any] | None) -> bool | None:
    if not row or row.get("timezone") != BUSINESS_CALENDAR_TIMEZONE:
        return None
    day_type = row.get("day_type")
    if day_type in {"NORMAL_WORKDAY", "ADJUSTED_WORKDAY"}:
        return True
    if day_type in {"WEEKEND", "HOLIDAY"}:
        return False
    return None


def _calendar_payload(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "calendar_version_id": int(row["calendar_version_id"]),
        "calendar_key": row["calendar_key"],
        "calendar_year": int(row["calendar_year"]),
        "version_label": row["version_label"],
        "timezone": row["timezone"],
        "status": row["calendar_status"],
        "business_date": row["business_date"],
        "day_type": row["day_type"],
        "note": row.get("note"),
    }


def get_business_calendar(*, calendar_year: int) -> dict[str, Any]:
    """Return the latest published annual calendar without changing state."""

    year = _validate_year(calendar_year)
    connection = connect()
    try:
        version = execute(
            connection,
            "SELECT id, calendar_key, calendar_year, version_label, timezone, status, "
            "created_by, created_at, updated_at "
            "FROM learning_business_calendar_versions "
            "WHERE calendar_key=? AND calendar_year=? AND status='PUBLISHED' "
            "ORDER BY id DESC LIMIT 1",
            (BUSINESS_CALENDAR_KEY, year),
        ).fetchone()
        if not version:
            return {
                "calendar_key": BUSINESS_CALENDAR_KEY,
                "calendar_year": year,
                "timezone": BUSINESS_CALENDAR_TIMEZONE,
                "status": "MISSING",
                "version": None,
                "days": [],
                "configured_day_count": 0,
            }
        version_payload = dict(version)
        version_payload["id"] = int(version_payload["id"])
        rows = execute(
            connection,
            "SELECT id, business_date, day_type, note FROM learning_business_calendar_days "
            "WHERE calendar_version_id=? ORDER BY business_date",
            (version_payload["id"],),
        ).fetchall()
        days = []
        for row in rows:
            item = dict(row)
            item["id"] = int(item["id"])
            item["business_date"] = _date_text(item["business_date"], "日历日期")
            days.append(item)
        return {
            "calendar_key": version_payload["calendar_key"],
            "calendar_year": int(version_payload["calendar_year"]),
            "timezone": version_payload["timezone"],
            "status": version_payload["status"],
            "version": version_payload,
            "days": days,
            "configured_day_count": len(days),
        }
    finally:
        connection.close()


def save_business_calendar(
    *,
    actor_user_id: int,
    calendar_year: int,
    version_label: str,
    days: list[dict[str, Any]],
    status: str = "DRAFT",
) -> dict[str, Any]:
    """Create/update a draft or publish a complete annual calendar.

    A published version is immutable.  Publishing a new version retires the
    previous published version for the same calendar year.  A published
    calendar must contain every date in its year, which prevents an accidental
    partial import from being treated as a complete source of truth.
    """

    _require_permission(
        actor_user_id,
        "plans:business_calendar_manage",
        "无权维护年度工作日日历",
    )
    _write_allowed()
    year = _validate_year(calendar_year)
    if not VERSION_LABEL_RE.fullmatch(str(version_label or "")):
        raise LearningCreditError("日历版本号格式不正确")
    final_status = str(status or "DRAFT").upper()
    if final_status not in CALENDAR_WRITE_STATUSES:
        raise LearningCreditError("日历只能保存为DRAFT或PUBLISHED")
    if not isinstance(days, list) or len(days) > 366:
        raise LearningCreditError("年度日历最多只能包含366天")

    normalized_days: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for item in days:
        if not isinstance(item, dict):
            raise LearningCreditError("日历日期项格式不正确")
        business_date = _date_text(item.get("business_date"), "日历日期")
        parsed = date.fromisoformat(business_date)
        if parsed.year != year:
            raise LearningCreditError("日历日期必须属于所配置的年份")
        if business_date in seen_dates:
            raise LearningCreditError(f"日历存在重复日期：{business_date}")
        seen_dates.add(business_date)
        day_type = str(item.get("day_type") or "").upper()
        if day_type not in DAY_TYPES:
            raise LearningCreditError("日历日期类型无效")
        note = item.get("note")
        if note is not None and len(str(note)) > 500:
            raise LearningCreditError("日历说明不能超过500个字符")
        normalized_days.append(
            {"business_date": business_date, "day_type": day_type, "note": note}
        )
    if final_status == "PUBLISHED":
        first = date(year, 1, 1)
        last_exclusive = date(year + 1, 1, 1)
        expected = {
            (first.fromordinal(first.toordinal() + offset)).isoformat()
            for offset in range((last_exclusive - first).days)
        }
        if seen_dates != expected:
            raise LearningCreditError("发布年度日历必须完整覆盖该年度每一天")

    now = datetime.now(UTC).isoformat()
    normalized_days.sort(key=lambda item: item["business_date"])
    with transaction() as connection:
        existing = execute(
            connection,
            "SELECT * FROM learning_business_calendar_versions "
            "WHERE calendar_key=? AND calendar_year=? AND version_label=? LIMIT 1",
            (BUSINESS_CALENDAR_KEY, year, version_label),
        ).fetchone()
        if existing and str(existing["status"]).upper() != "DRAFT":
            raise LearningCreditError("已发布或已退役的日历版本不可修改，请创建新的版本号")
        if existing:
            version_id = int(existing["id"])
            before_version = dict(existing)
            before_day_count = int(
                execute(
                    connection,
                    "SELECT COUNT(*) AS count FROM learning_business_calendar_days "
                    "WHERE calendar_version_id=?",
                    (version_id,),
                ).fetchone()["count"]
                or 0
            )
            execute(
                connection,
                "UPDATE learning_business_calendar_versions SET status=?, updated_at=? WHERE id=?",
                (final_status, now, version_id),
            )
        else:
            before_version = None
            before_day_count = 0
            cursor = execute(
                connection,
                "INSERT INTO learning_business_calendar_versions "
                "(calendar_key, calendar_year, version_label, timezone, status, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    BUSINESS_CALENDAR_KEY,
                    year,
                    version_label,
                    BUSINESS_CALENDAR_TIMEZONE,
                    final_status,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            version_id = int(cursor.lastrowid)
        execute(
            connection,
            "DELETE FROM learning_business_calendar_days WHERE calendar_version_id=?",
            (version_id,),
        )
        for item in normalized_days:
            execute(
                connection,
                "INSERT INTO learning_business_calendar_days "
                "(calendar_version_id, business_date, day_type, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    item["business_date"],
                    item["day_type"],
                    item["note"],
                    now,
                    now,
                ),
            )
        if final_status == "PUBLISHED":
            execute(
                connection,
                "UPDATE learning_business_calendar_versions SET status='RETIRED', updated_at=? "
                "WHERE calendar_key=? AND calendar_year=? AND status='PUBLISHED' AND id<>?",
                (now, BUSINESS_CALENDAR_KEY, year, version_id),
            )
        after = execute(
            connection,
            "SELECT id, calendar_key, calendar_year, version_label, timezone, status, "
            "created_by, created_at, updated_at "
            "FROM learning_business_calendar_versions WHERE id=?",
            (version_id,),
        ).fetchone()
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.business_calendar.save",
            resource_type="learning_business_calendar_version",
            resource_id=str(version_id),
            purpose="维护中国大陆年度法定工作日日历",
            before={"version": before_version, "day_count": before_day_count},
            after={"version": dict(after) if after else None, "day_count": len(normalized_days)},
        )
        return {
            "version": dict(after) if after else None,
            "day_count": len(normalized_days),
            "status": final_status,
            "persisted": True,
        }


def _activity_fact_payload(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("id", "member_id", "binding_id", "created_by"):
        if result.get(key) is not None:
            result[key] = int(result[key])
    result["occurred_on"] = _date_text(result["occurred_on"], "事实发生日期")
    result["metadata"] = _decode_json(result.pop("metadata_json", "{}"))
    return result


def record_learning_activity_fact(
    *,
    actor_user_id: int,
    activity_type: str,
    member_id: int,
    class_org_unit_id: str,
    occurred_on: str,
    source_type: str,
    source_id: str,
    participation_status: str = "RECORDED",
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    binding_id: int | None = None,
    _allow_controlled_source: bool = False,
) -> dict[str, Any]:
    """Persist one source fact, never a score or a ledger entry.

    The payload intentionally has no points field.  The server records the
    trusted activity fact and later derives points from the frozen rule.
    """

    _require_permission(
        actor_user_id,
        "plans:credit_activity_fact_manage",
        "无权维护每日读书与优秀分享事实",
    )
    _write_allowed()
    final_type = str(activity_type or "").upper()
    if final_type not in ACTIVITY_TYPES:
        raise LearningCreditError("学习活动事实类型无效")
    final_status = str(participation_status or "RECORDED").upper()
    if final_status not in FACT_STATUSES:
        raise LearningCreditError("学习活动事实状态无效")
    if int(member_id) <= 0:
        raise LearningCreditError("学员编号无效")
    if not class_org_unit_id:
        raise LearningCreditError("事实必须包含班级组织范围")
    if not _scope_allows(actor_user_id, class_org_unit_id):
        raise PermissionError("学习活动事实不在当前组织授权范围内")
    normalized_date = _date_text(occurred_on, "事实发生日期")
    final_source_type = str(source_type or "").strip()
    final_source_id = str(source_id or "").strip()
    if not SOURCE_TYPE_RE.fullmatch(final_source_type):
        raise LearningCreditError("事实来源类型格式不正确")
    if (
        final_type == DAILY_READING
        and final_source_type == "HQ_READING_EXPORT"
        and not _allow_controlled_source
    ):
        raise LearningCreditError("总部每日读书事实必须通过受控Excel导入入口")
    if (
        final_type == EXCELLENT_SHARE
        and final_source_type == "EXCELLENT_SHARE_MANUAL_VERIFIED"
        and not _allow_controlled_source
    ):
        raise LearningCreditError("手工核验优秀分享必须通过受控核验入口")
    if not final_source_id or len(final_source_id) > 255:
        raise LearningCreditError("事实来源编号不能为空且不能超过255个字符")
    if title is not None and len(str(title)) > 255:
        raise LearningCreditError("事实标题不能超过255个字符")
    if metadata is not None and not isinstance(metadata, dict):
        raise LearningCreditError("事实元数据必须是对象")
    if metadata and {
        "points",
        "credit_points",
        "score",
    }.intersection(metadata):
        raise LearningCreditError("事实元数据不能携带学分或评分字段")
    metadata_json = _json(metadata or {})
    with transaction() as connection:
        class_row = execute(
            connection,
            "SELECT id, unit_type FROM org_units WHERE id=? "
            "AND unit_type IN ('CLASS', 'SPECIAL_COHORT') LIMIT 1",
            (class_org_unit_id,),
        ).fetchone()
        if not class_row:
            raise LearningCreditError("班级组织不存在")
        member_row = execute(
            connection,
            "SELECT id FROM members WHERE id=? LIMIT 1",
            (member_id,),
        ).fetchone()
        if not member_row:
            raise LearningCreditError("学员不存在")
        fact = {
            "activity_type": final_type,
            "member_id": int(member_id),
            "class_org_unit_id": class_org_unit_id,
            "occurred_on": normalized_date,
            "participation_status": final_status,
            "source_type": final_source_type,
            "source_id": final_source_id,
            "title": title,
            "metadata_json": metadata_json,
        }
        existing = execute(
            connection,
            "SELECT * FROM learning_credit_activity_facts WHERE source_type=? AND source_id=? LIMIT 1",
            (final_source_type, final_source_id),
        ).fetchone()
        if existing:
            current = dict(existing)
            comparable = {
                key: current.get(key)
                for key in (
                    "activity_type",
                    "member_id",
                    "class_org_unit_id",
                    "occurred_on",
                    "participation_status",
                    "title",
                    "metadata_json",
                )
            }
            if all(str(comparable.get(key)) == str(fact.get(key)) for key in comparable):
                result = _activity_fact_payload(existing)
                result["duplicate"] = True
                return result
            raise LearningCreditError("相同事实来源编号已存在，但内容不一致")

        resolved_binding, binding_reason = _resolve_binding_for_fact(
            connection,
            {
                **fact,
                "binding_id": binding_id,
            },
        )
        if binding_id is not None and binding_reason:
            raise LearningCreditError(f"{binding_reason}:指定学习轮次与事实不匹配")
        effective_binding_id = (
            int(resolved_binding["id"]) if resolved_binding and not binding_reason else None
        )
        now = datetime.now(UTC).isoformat()
        cursor = execute(
            connection,
            "INSERT INTO learning_credit_activity_facts "
            "(activity_type, member_id, class_org_unit_id, binding_id, occurred_on, "
            "participation_status, source_type, source_id, title, metadata_json, "
            "created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                final_type,
                int(member_id),
                class_org_unit_id,
                effective_binding_id,
                normalized_date,
                final_status,
                final_source_type,
                final_source_id,
                title,
                metadata_json,
                actor_user_id,
                now,
                now,
            ),
        )
        row = execute(
            connection,
            "SELECT * FROM learning_credit_activity_facts WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
        payload = _activity_fact_payload(row)
        payload["duplicate"] = False
        payload["binding_resolution"] = binding_reason or "RESOLVED"
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="learning.activity_fact.create",
            resource_type="learning_credit_activity_fact",
            resource_id=str(cursor.lastrowid),
            org_unit_id=class_org_unit_id,
            purpose="记录每日读书或优秀分享事实，不直接赋分",
            # Keep free-form title/metadata out of the audit log.  The fact
            # row remains the source record; the audit trail only needs the
            # identity, scope, date, and resolution attributes.
            after={
                "id": payload["id"],
                "activity_type": payload["activity_type"],
                "member_id": payload["member_id"],
                "class_org_unit_id": payload["class_org_unit_id"],
                "binding_id": payload.get("binding_id"),
                "occurred_on": payload["occurred_on"],
                "participation_status": payload["participation_status"],
                "source_type": payload["source_type"],
                "source_id": payload["source_id"],
                "binding_resolution": payload.get("binding_resolution"),
            },
        )
        return payload


def _load_activity_facts(
    connection,
    *,
    actor_user_id: int,
    activity_type: str,
    member_id: int | None,
    class_org_unit_id: str | None,
    occurred_from: str | None,
    occurred_to: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(actor_user_id)
    if class_org_unit_id and not _scope_allows(actor_user_id, class_org_unit_id):
        raise PermissionError("学习活动事实不在当前组织授权范围内")
    if allowed is not None and not allowed and not class_org_unit_id:
        return []
    conditions = ["f.activity_type=?"]
    params: list[Any] = [activity_type]
    if member_id is not None:
        if int(member_id) <= 0:
            raise LearningCreditError("学员编号无效")
        conditions.append("f.member_id=?")
        params.append(int(member_id))
    if class_org_unit_id:
        conditions.append("f.class_org_unit_id=?")
        params.append(class_org_unit_id)
    elif allowed is not None:
        placeholders = ",".join("?" for _ in sorted(allowed))
        conditions.append(f"f.class_org_unit_id IN ({placeholders})")
        params.extend(sorted(allowed))
    if occurred_from:
        conditions.append("f.occurred_on>=?")
        params.append(occurred_from)
    if occurred_to:
        conditions.append("f.occurred_on<=?")
        params.append(occurred_to)
    rows = execute(
        connection,
        "SELECT f.*, m.name AS member_name, o.name AS class_name "
        "FROM learning_credit_activity_facts f "
        "JOIN members m ON m.id=f.member_id "
        "JOIN org_units o ON o.id=f.class_org_unit_id "
        "WHERE " + " AND ".join(conditions) +
        " ORDER BY f.occurred_on, f.id LIMIT ?",
        (*params, limit),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            parsed = _parse_business_date(item.get("occurred_on"), "事实发生日期")
            item["occurred_on"] = parsed.isoformat()
            item["_occurred_date"] = parsed
        except LearningCreditError as exc:
            item["_date_error"] = str(exc)
            item["_occurred_date"] = None
        item["id"] = int(item["id"])
        item["member_id"] = int(item["member_id"])
        if item.get("binding_id") is not None:
            item["binding_id"] = int(item["binding_id"])
        item["metadata"] = _decode_json(item.get("metadata_json"))
        result.append(item)
    return result


def _binding_rows(connection, class_org_unit_id: str) -> list[dict[str, Any]]:
    rows = execute(
        connection,
        "SELECT b.*, p.plan_key, p.plan_name, p.version_label, "
        "p.status AS plan_status "
        "FROM class_learning_bindings b "
        "JOIN learning_plan_versions p ON p.id=b.plan_version_id "
        "WHERE b.class_org_unit_id=? ORDER BY b.started_at, b.id",
        (class_org_unit_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _binding_interval_contains(binding: dict[str, Any], occurred_on: date) -> bool:
    started = _local_date_from_timestamp(binding.get("started_at"))
    if not started:
        return False
    ended = _local_date_from_timestamp(binding.get("ended_at"))
    if ended is None and str(binding.get("status") or "").upper() != "ACTIVE":
        # A legacy ended row without an end boundary cannot safely own a
        # historical fact.  Leave it visible as a resolution blocker instead
        # of pretending that it is still open.
        return False
    return occurred_on >= started and (ended is None or occurred_on <= ended)


def _resolve_binding_for_fact(
    connection, fact: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    occurred_on = fact.get("_occurred_date") or _parse_business_date(
        fact.get("occurred_on"), "事实发生日期"
    )
    rows = _binding_rows(connection, str(fact["class_org_unit_id"]))
    requested_id = fact.get("binding_id")
    if requested_id is not None:
        match = next((row for row in rows if int(row["id"]) == int(requested_id)), None)
        if not match:
            return None, "BINDING_NOT_IN_CLASS"
        if not _binding_interval_contains(match, occurred_on):
            return None, "BINDING_DATE_MISMATCH"
        return match, None
    candidates = [row for row in rows if _binding_interval_contains(row, occurred_on)]
    if not candidates:
        return None, "LEARNING_ROUND_MISSING"
    if len(candidates) > 1:
        return None, "BINDING_AMBIGUOUS"
    return candidates[0], None


def _member_relation_reason(
    connection, member_id: int, class_org_unit_id: str, occurred_on: date
) -> str | None:
    rows = execute(
        connection,
        "SELECT valid_from, valid_until FROM member_org_relations "
        "WHERE member_id=? AND org_unit_id=? AND relation_type='STUDY_CLASS'",
        (member_id, class_org_unit_id),
    ).fetchall()
    active = []
    for row in rows:
        try:
            valid_from = _parse_optional_date(row["valid_from"])
            valid_until = _parse_optional_date(row["valid_until"])
        except LearningCreditError:
            return "MEMBER_CLASS_RELATION_INVALID"
        if (valid_from is None or valid_from <= occurred_on) and (
            valid_until is None or valid_until >= occurred_on
        ):
            active.append(row)
    if not active:
        return "MEMBER_CLASS_RELATION_MISSING"
    if len(active) > 1:
        return "MEMBER_CLASS_RELATION_AMBIGUOUS"
    return None


def _resolve_rule(
    connection,
    binding: dict[str, Any] | None,
    *,
    rule_key: str,
    settlement_model: str,
) -> tuple[dict[str, Any] | None, str | None]:
    if not binding or binding.get("credit_rule_version_id") is None:
        return None, "RULE_MAPPING_MISSING"
    rule = bound_generic_rule(
        connection, int(binding["credit_rule_version_id"]), rule_key
    )
    if not rule:
        return None, "RULE_MAPPING_MISSING"
    version_status = str(rule.get("rule_version_status") or "").upper()
    if version_status not in {"PUBLISHED", "RETIRED"}:
        return None, "RULE_VERSION_NOT_PUBLISHED"
    definition = rule.get("rule") or {}
    if definition.get("status") != "ACTIVE":
        return None, "RULE_DISABLED"
    if definition.get("credit_category") != STANDARD_LEARNING:
        return None, "RULE_CATEGORY_MISMATCH"
    if definition.get("credit_type") != rule_key:
        return None, "RULE_TYPE_MISMATCH"
    if definition.get("settlement_model") != settlement_model:
        return None, "RULE_MODEL_MISMATCH"
    try:
        points = float(definition.get("points"))
    except (TypeError, ValueError) as exc:
        raise LearningCreditError(f"{rule_key}规则分值无效") from exc
    if points <= 0:
        return None, "RULE_POINTS_MISSING"
    cap: float | None = None
    if settlement_model == "MONTHLY_CAP":
        try:
            cap = float(definition.get("cap_points"))
        except (TypeError, ValueError) as exc:
            raise LearningCreditError(f"{rule_key}规则月封顶值无效") from exc
        if cap <= 0:
            return None, "RULE_CAP_MISSING"
    return {
        "rule_version_id": int(rule["rule_version_id"]),
        "rule_set_key": rule.get("rule_set_key"),
        "rule_version": rule.get("rule_version"),
        "rule_version_status": version_status,
        "rule_id": int(rule["rule_id"]),
        "rule_key": rule_key,
        "credit_category": definition.get("credit_category"),
        "credit_type": definition.get("credit_type"),
        "settlement_model": definition.get("settlement_model"),
        "points": points,
        "cap_points": cap,
        "rule_snapshot": _decode_json(definition.get("rule_snapshot_json")),
    }, None


def _fact_context(
    connection,
    fact: dict[str, Any],
    *,
    rule_key: str,
    settlement_model: str,
    cache: dict[tuple[Any, ...], dict[str, Any]],
) -> dict[str, Any]:
    occurred_on = fact.get("_occurred_date")
    cache_key = (
        fact.get("member_id"),
        fact.get("class_org_unit_id"),
        occurred_on.isoformat() if isinstance(occurred_on, date) else fact.get("occurred_on"),
        fact.get("binding_id"),
        rule_key,
    )
    if cache_key in cache:
        return cache[cache_key]
    if fact.get("_date_error"):
        result = {"binding": None, "rule": None, "reason": "INVALID_OCCURRED_DATE"}
        cache[cache_key] = result
        return result
    relation_reason = _member_relation_reason(
        connection,
        int(fact["member_id"]),
        str(fact["class_org_unit_id"]),
        occurred_on,
    )
    if relation_reason:
        result = {"binding": None, "rule": None, "reason": relation_reason}
        cache[cache_key] = result
        return result
    binding, binding_reason = _resolve_binding_for_fact(connection, fact)
    if binding_reason:
        result = {"binding": None, "rule": None, "reason": binding_reason}
        cache[cache_key] = result
        return result
    rule, rule_reason = _resolve_rule(
        connection,
        binding,
        rule_key=rule_key,
        settlement_model=settlement_model,
    )
    result = {"binding": binding, "rule": rule, "reason": rule_reason}
    cache[cache_key] = result
    return result


def _entry_common(
    connection,
    *,
    fact: dict[str, Any],
    fact_ids: list[int],
    activity_type: str,
    source_type: str,
    rule_key: str,
    rule: dict[str, Any] | None,
    binding: dict[str, Any] | None,
    idempotency_key: str,
    existing: dict[str, Any] | None,
    calendar: dict[str, Any] | None = None,
    period_month: str | None = None,
) -> dict[str, Any]:
    occurred_on = fact.get("occurred_on")
    rule_version_id = int(rule["rule_version_id"]) if rule else None
    rule_snapshot = {
        "rule_key": rule_key,
        "credit_category": STANDARD_LEARNING,
        "credit_type": activity_type,
        "settlement_model": rule.get("settlement_model") if rule else None,
        "points": rule.get("points") if rule else None,
        "cap_points": rule.get("cap_points") if rule else None,
        "rule_set_key": rule.get("rule_set_key") if rule else None,
        "rule_version": rule.get("rule_version") if rule else None,
        "rule_version_id": rule_version_id,
        "rule_version_status": rule.get("rule_version_status") if rule else None,
        "plan_key": binding.get("plan_key") if binding else None,
        "plan_version": binding.get("version_label") if binding else None,
        "binding_id": int(binding["id"]) if binding else None,
        "learning_round": int(binding.get("learning_round") or 1) if binding else None,
        "occurred_on": occurred_on,
        "timezone": BUSINESS_CALENDAR_TIMEZONE,
        "fact_ids": fact_ids,
    }
    if calendar is not None:
        rule_snapshot["business_calendar"] = _calendar_payload(calendar)
    if period_month is not None:
        rule_snapshot["period_month"] = period_month
    return {
        "entry_kind": activity_type,
        "activity_type": activity_type,
        "member_id": int(fact["member_id"]),
        "member_name": fact.get("member_name"),
        "class_org_unit_id": fact.get("class_org_unit_id"),
        "class_name": fact.get("class_name"),
        "binding_id": int(binding["id"]) if binding else None,
        "learning_round": int(binding.get("learning_round") or 1) if binding else None,
        "plan_key": binding.get("plan_key") if binding else None,
        "plan_version": binding.get("version_label") if binding else None,
        "fact_ids": fact_ids,
        "source_fact_count": len(fact_ids),
        "occurred_on": occurred_on,
        "occurred_at": _ledger_occurrence_at(
            connection, _parse_business_date(occurred_on, "事实发生日期")
        ) if not fact.get("_date_error") else None,
        "credit_category": STANDARD_LEARNING,
        "credit_type": activity_type,
        "source_type": source_type,
        "source_id": idempotency_key,
        "rule_key": rule_key,
        "rule_version": str(rule.get("rule_version") or "") if rule else "",
        "rule_version_id": rule_version_id,
        "rule_snapshot": rule_snapshot,
        "idempotency_key": idempotency_key,
        "existing_entry": _entry_payload(existing) if existing else None,
        "calendar": _calendar_payload(calendar),
        "period_month": period_month,
        "timezone": BUSINESS_CALENDAR_TIMEZONE,
        "points": 0.0,
        "eligible": False,
        "postable": False,
        "status": "BLOCKED",
        "reasons": [],
    }


def _daily_entries(connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        groups[(int(fact["member_id"]), str(fact.get("occurred_on") or ""))].append(fact)
    cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for (_, occurred_text), group in sorted(groups.items(), key=lambda item: item[0]):
        first = group[0]
        fact_ids = [int(item["id"]) for item in group]
        idempotency_key = f"DAILY_READING:{int(first['member_id'])}:{occurred_text}"
        existing = _existing_entry(connection, idempotency_key)
        active = [
            item
            for item in group
            if str(item.get("participation_status") or "").upper()
            in CREDITABLE_FACT_STATUSES
        ]
        if not active:
            entry = _entry_common(
                connection,
                fact=first,
                fact_ids=fact_ids,
                activity_type=DAILY_READING,
                source_type=DAILY_SOURCE_TYPE,
                rule_key=DAILY_READING,
                rule=None,
                binding=None,
                idempotency_key=idempotency_key,
                existing=existing,
            )
            entry["status"] = "SKIPPED_DUPLICATE" if existing else "NO_CREDIT"
            entry["reasons"] = (
                ["已存在相同幂等键的账本记录"]
                if existing
                else ["FACT_NOT_CREDITABLE"]
            )
            result.append(entry)
            continue
        classes = {str(item["class_org_unit_id"]) for item in active}
        if len(classes) > 1:
            entry = _entry_common(
                connection,
                fact=first,
                fact_ids=fact_ids,
                activity_type=DAILY_READING,
                source_type=DAILY_SOURCE_TYPE,
                rule_key=DAILY_READING,
                rule=None,
                binding=None,
                idempotency_key=idempotency_key,
                existing=existing,
            )
            entry["status"] = "SKIPPED_DUPLICATE" if existing else "BLOCKED"
            entry["reasons"] = (
                ["已存在相同幂等键的账本记录"]
                if existing
                else ["MULTIPLE_CLASS_FACTS"]
            )
            result.append(entry)
            continue
        representative = active[0]
        context = _fact_context(
            connection,
            representative,
            rule_key=DAILY_READING,
            settlement_model="DAILY_ONCE",
            cache=cache,
        )
        binding = context["binding"]
        rule = context["rule"]
        occurred_on = representative.get("_occurred_date")
        calendar = _calendar_day(connection, occurred_on) if occurred_on else None
        workday = _is_workday_row(calendar) if calendar and occurred_on else None
        entry = _entry_common(
            connection,
            fact=representative,
            fact_ids=fact_ids,
            activity_type=DAILY_READING,
            source_type=DAILY_SOURCE_TYPE,
            rule_key=DAILY_READING,
            rule=rule,
            binding=binding,
            idempotency_key=idempotency_key,
            existing=existing,
            calendar=calendar,
        )
        reason = context.get("reason")
        if existing:
            entry["status"] = "SKIPPED_DUPLICATE"
            entry["reasons"] = ["已存在相同幂等键的账本记录"]
        elif reason:
            entry["status"] = "BLOCKED"
            entry["reasons"] = [reason]
        elif not calendar:
            entry["status"] = "BLOCKED"
            entry["reasons"] = ["BUSINESS_CALENDAR_MISSING"]
        elif workday is None:
            entry["status"] = "BLOCKED"
            entry["reasons"] = ["BUSINESS_CALENDAR_INVALID"]
        elif not workday:
            entry["status"] = "NO_CREDIT"
            entry["reasons"] = ["NOT_BUSINESS_WORKDAY"]
        else:
            entry["status"] = "READY"
            entry["points"] = float(rule["points"])
            entry["eligible"] = True
            entry["postable"] = True
        result.append(entry)
    return result


def _existing_month_points(connection, member_id: int, period_month: str) -> float:
    year, month = (int(item) for item in period_month.split("-"))
    rows = execute(
        connection,
        "SELECT points, occurred_at FROM learning_credit_entries "
        "WHERE member_id=? AND credit_type=? AND status IN ('POSTED', 'REVERSED')",
        (member_id, EXCELLENT_SHARE),
    ).fetchall()
    total = 0.0
    for row in rows:
        local_date = _local_date_from_timestamp(row["occurred_at"])
        if local_date and local_date.year == year and local_date.month == month:
            try:
                total += float(row["points"] or 0)
            except (TypeError, ValueError):
                continue
    return total


def _excellent_entries(connection, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    by_period: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        occurred = fact.get("_occurred_date")
        period = occurred.strftime("%Y-%m") if occurred else str(fact.get("occurred_on") or "")[:7]
        context = _fact_context(
            connection,
            fact,
            rule_key=EXCELLENT_SHARE,
            settlement_model="MONTHLY_CAP",
            cache=cache,
        )
        binding = context["binding"]
        rule = context["rule"]
        idempotency_key = f"EXCELLENT_SHARE:{int(fact['member_id'])}:{int(fact['id'])}"
        existing = _existing_entry(connection, idempotency_key)
        entry = _entry_common(
            connection,
            fact=fact,
            fact_ids=[int(fact["id"])],
            activity_type=EXCELLENT_SHARE,
            source_type=EXCELLENT_SOURCE_TYPE,
            rule_key=EXCELLENT_SHARE,
            rule=rule,
            binding=binding,
            idempotency_key=idempotency_key,
            existing=existing,
            period_month=period,
        )
        entry["_period_key"] = (int(fact["member_id"]), period)
        entry["_rule_profile"] = (
            int(rule["rule_version_id"]),
            float(rule["points"]),
            float(rule["cap_points"]),
        ) if rule else None
        status = str(fact.get("participation_status") or "").upper()
        if existing:
            entry["status"] = "SKIPPED_DUPLICATE"
            entry["reasons"] = ["已存在相同幂等键的账本记录"]
        elif status not in CREDITABLE_FACT_STATUSES:
            entry["status"] = "NO_CREDIT"
            entry["reasons"] = ["FACT_NOT_CREDITABLE"]
        elif context.get("reason"):
            entry["status"] = "BLOCKED"
            entry["reasons"] = [context["reason"]]
        else:
            entry["status"] = "READY"
            entry["points"] = float(rule["points"])
            entry["eligible"] = True
            entry["postable"] = True
        by_period[(int(fact["member_id"]), period)].append(entry)

    result: list[dict[str, Any]] = []
    for period_key in sorted(by_period):
        entries = by_period[period_key]
        profiles = {
            entry["_rule_profile"]
            for entry in entries
            if entry["status"] == "READY" and entry.get("_rule_profile")
        }
        if len(profiles) > 1:
            for entry in entries:
                if entry["status"] == "READY":
                    entry["status"] = "BLOCKED"
                    entry["points"] = 0.0
                    entry["eligible"] = False
                    entry["postable"] = False
                    entry["reasons"] = ["MULTIPLE_MONTHLY_RULE_POLICIES"]
        elif profiles:
            _, points, cap = next(iter(profiles))
            used = _existing_month_points(connection, period_key[0], period_key[1])
            for entry in entries:
                if entry["status"] != "READY":
                    continue
                if used + points <= cap + 1e-9:
                    used += points
                    continue
                entry["status"] = "NO_CREDIT"
                entry["points"] = 0.0
                entry["eligible"] = False
                entry["postable"] = False
                entry["reasons"] = ["MONTHLY_CAP_REACHED"]
        result.extend(entries)
    result.sort(key=lambda entry: (str(entry.get("occurred_on") or ""), int(entry["member_id"]), entry["fact_ids"][0]))
    for entry in result:
        entry.pop("_period_key", None)
        entry.pop("_rule_profile", None)
    return result


def _preview_payload(
    connection,
    *,
    activity_type: str,
    entries: list[dict[str, Any]],
    occurred_from: str | None,
    occurred_to: str | None,
    before: int,
) -> dict[str, Any]:
    proposed = [entry for entry in entries if entry.get("postable")]
    blocking_reasons = list(
        dict.fromkeys(
            reason
            for entry in entries
            if entry.get("status") == "BLOCKED"
            for reason in entry.get("reasons", [])
        )
    )
    clean_entries = entries
    return {
        "mode": "DRY_RUN",
        "persisted": False,
        "settlement_enabled": bool(get_settings().learning_credit_settlement_enabled),
        "formal_settlement_allowed": False,
        "activity_type": activity_type,
        "timezone": BUSINESS_CALENDAR_TIMEZONE,
        "calendar_key": BUSINESS_CALENDAR_KEY if activity_type == DAILY_READING else None,
        "occurred_from": occurred_from,
        "occurred_to": occurred_to,
        "entries": clean_entries,
        "totals": {
            "fact_count": sum(int(entry.get("source_fact_count") or 0) for entry in entries),
            "proposal_count": len(entries),
            "proposed_points": sum(float(entry.get("points") or 0) for entry in proposed),
            "proposed_entry_count": len(proposed),
            "ready_count": sum(entry.get("status") == "READY" for entry in entries),
            "blocked_entry_count": sum(entry.get("status") == "BLOCKED" for entry in entries),
            "no_credit_entry_count": sum(entry.get("status") == "NO_CREDIT" for entry in entries),
            "duplicate_entry_count": sum(entry.get("status") == "SKIPPED_DUPLICATE" for entry in entries),
        },
        "blocking_reasons": blocking_reasons,
        "write_proof": _write_proof(connection, before),
    }


def dry_run_daily_reading(
    *,
    actor_user_id: int,
    member_id: int | None = None,
    class_org_unit_id: str | None = None,
    occurred_from: str | None = None,
    occurred_to: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    _require_preview_permission(actor_user_id)
    if limit < 1 or limit > 500:
        raise LearningCreditError("DRY-RUN数量必须在1到500之间")
    start, end = _validate_window(occurred_from, occurred_to)
    connection = connect()
    try:
        before = _ledger_count(connection)
        facts = _load_activity_facts(
            connection,
            actor_user_id=actor_user_id,
            activity_type=DAILY_READING,
            member_id=member_id,
            class_org_unit_id=class_org_unit_id,
            occurred_from=start,
            occurred_to=end,
            limit=limit,
        )
        return _preview_payload(
            connection,
            activity_type=DAILY_READING,
            entries=_daily_entries(connection, facts),
            occurred_from=start,
            occurred_to=end,
            before=before,
        )
    finally:
        connection.close()


def dry_run_excellent_shares(
    *,
    actor_user_id: int,
    member_id: int | None = None,
    class_org_unit_id: str | None = None,
    occurred_from: str | None = None,
    occurred_to: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    _require_preview_permission(actor_user_id)
    if limit < 1 or limit > 500:
        raise LearningCreditError("DRY-RUN数量必须在1到500之间")
    start, end = _validate_window(occurred_from, occurred_to)
    connection = connect()
    try:
        before = _ledger_count(connection)
        facts = _load_activity_facts(
            connection,
            actor_user_id=actor_user_id,
            activity_type=EXCELLENT_SHARE,
            member_id=member_id,
            class_org_unit_id=class_org_unit_id,
            occurred_from=start,
            occurred_to=end,
            limit=limit,
        )
        return _preview_payload(
            connection,
            activity_type=EXCELLENT_SHARE,
            entries=_excellent_entries(connection, facts),
            occurred_from=start,
            occurred_to=end,
            before=before,
        )
    finally:
        connection.close()
