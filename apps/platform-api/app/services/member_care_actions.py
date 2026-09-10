from __future__ import annotations

import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.followups import OPEN_STATES, list_tasks
from app.services.iam import accessible_org_ids, user_context
from app.services.members import resolve_member_scope
from app.services.renewals import (
    MEMBER_CLASS_NAME_SQL,
    MEMBER_GROUP_NAME_SQL,
    MEMBER_RENEWAL_ORG_SQL,
    list_today_actions,
)


URGENCY_RANK = {"OVERDUE": 0, "TODAY": 1, "ATTENTION": 2, "WINDOW": 3}
SOURCE_RANK = {"RENEWAL": 0, "FOLLOWUP": 1, "BIRTHDAY": 2}
RENEWAL_REASON_URGENCY = {
    "FOLLOWUP_OVERDUE": "OVERDUE",
    "FOLLOWUP_TODAY": "TODAY",
    "SUPPORT_NEEDED": "ATTENTION",
    "NEXT_STEP_MISSING": "ATTENTION",
    "STAGE_UNTOUCHED": "WINDOW",
}
FOLLOWUP_TYPE_LABELS = {
    "CARE": ("CARE", "日常关怀"),
    "PHONE": ("PHONE", "电话关怀"),
    "WECHAT": ("WECHAT", "微信关怀"),
    "MEETING": ("MEETING", "面谈关怀"),
    "VISIT": ("ENTERPRISE_VISIT", "企业走访"),
    "COURSE": ("COURSE", "学习关怀"),
    "OTHER": ("OTHER", "日常关怀"),
}

LOGGER = logging.getLogger(__name__)
SOURCE_KEYS = ("renewal", "followup", "birthday")
COMPLETED_SOURCE_RANK = {"RENEWAL": 0, "FOLLOWUP": 1, "BIRTHDAY": 2}


def _source_error_code(error: BaseException) -> str:
    """Map storage/runtime failures to a safe, non-sensitive UI code."""
    text = str(error).lower()
    if "no such table" in text or "doesn't exist" in text or "unknown table" in text:
        return "SCHEMA_UNAVAILABLE"
    if "permission" in text or "forbidden" in text:
        return "FORBIDDEN"
    return "SOURCE_UNAVAILABLE"


def _source_coverage(
    permissions: set[str],
) -> dict[str, dict[str, Any]]:
    source_permissions = {
        "renewal": "renewals:read",
        "followup": "followups:manage",
        "birthday": "members:detail_view",
    }
    return {
        source: {
            "accessible": permission in permissions,
            "available": False,
        }
        for source, permission in source_permissions.items()
    }


def _mark_source_unavailable(
    coverage: dict[str, dict[str, Any]], source: str, error: BaseException
) -> None:
    entry = coverage[source]
    entry["available"] = False
    entry["error_code"] = _source_error_code(error)
    # Logs retain the diagnostic detail for operators without leaking SQL or
    # stack traces into the business response.
    LOGGER.exception("member care source %s unavailable", source)


def _calendar_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    parsed = _calendar_date(value)
    return parsed.isoformat() if parsed else None


def _member_contexts(
    member_ids: set[int], allowed_org_ids: set[str] | None
) -> dict[int, dict[str, Any]]:
    if not member_ids:
        return {}
    placeholders = ",".join("?" for _ in member_ids)
    rows = fetch_all(
        "SELECT m.id, m.name, m.org_unit_id, m.development_org_unit_id, "
        "o.name AS primary_org_name, "
        f"{MEMBER_CLASS_NAME_SQL} AS class_name, "
        f"{MEMBER_GROUP_NAME_SQL} AS group_name "
        "FROM members m JOIN org_units o ON o.id=m.org_unit_id "
        f"WHERE m.id IN ({placeholders})",
        tuple(sorted(member_ids)),
    )
    scoped_rows: list[tuple[int, Any, str]] = []
    for row in rows:
        try:
            scoped_org_id = resolve_member_scope(
                int(row["id"]), row["org_unit_id"], allowed_org_ids
            )
        except PermissionError:
            # A source may be visible through its own scope while the member
            # master is not visible there; fail closed instead of leaking
            # cross-organization member metadata.
            continue
        scoped_rows.append((int(row["id"]), row, scoped_org_id))
    if not scoped_rows:
        return {}
    scoped_org_ids = sorted({org_id for _, _, org_id in scoped_rows})
    org_rows = fetch_all(
        "SELECT id, name FROM org_units WHERE id IN ("
        + ",".join("?" for _ in scoped_org_ids)
        + ")",
        tuple(scoped_org_ids),
    )
    org_names = {str(row["id"]): row["name"] for row in org_rows}
    contexts: dict[int, dict[str, Any]] = {}
    for member_id, row, scoped_org_id in scoped_rows:
        contexts[int(row["id"])] = {
            "member_id": member_id,
            "member_name": row["name"],
            "org_unit_id": scoped_org_id,
            "org_name": org_names.get(scoped_org_id) or row["primary_org_name"],
            "class_name": row["class_name"],
            "group_name": row["group_name"],
        }
    return contexts


def _birthday_due_date(value: Any, year: int) -> date | None:
    """Return one safe birthday occurrence without exposing the birth year."""
    birthday = _calendar_date(value)
    if not birthday or not 1900 <= year <= 9999:
        return None
    return date(year, birthday.month, min(birthday.day, calendar.monthrange(year, birthday.month)[1]))


def _active_birthday_members(
    allowed_org_ids: set[str] | None,
) -> list[dict[str, Any]]:
    """Load active member-master birthday facts within the caller's scope.

    The member master determines who has a birthday.  Operational rhythm rows
    are deliberately not consulted here because a member without a class must
    remain eligible for care.
    """
    rows = fetch_all(
        "SELECT id, birthday FROM members WHERE status='ACTIVE' AND birthday IS NOT NULL "
        "ORDER BY id"
    )
    member_ids = {int(row["id"]) for row in rows}
    contexts = _member_contexts(member_ids, allowed_org_ids)
    return [
        {**contexts[int(row["id"])], "birthday": row["birthday"]}
        for row in rows
        if int(row["id"]) in contexts
    ]


def _birthday_operation_matches_due_date(row: dict[str, Any], due_date: date) -> bool:
    item_due_date = _calendar_date(row.get("due_date"))
    return item_due_date == due_date or (
        item_due_date is None
        and str(row.get("period") or "") == due_date.strftime("%Y-%m")
    )


def _birthday_workflow_states(
    candidates: list[dict[str, Any]],
) -> dict[tuple[int, int], dict[str, dict[str, Any] | None]]:
    """Return optional completion and rhythm state for master birthday dates.

    The result is read-only and intentionally treats legacy operation items as
    historical completion evidence.  The new completion table covers members
    who have no class rhythm item at all.
    """
    if not candidates:
        return {}
    member_ids = sorted({int(item["member_id"]) for item in candidates})
    years = sorted({item["due_date"].year for item in candidates})
    placeholders = ",".join("?" for _ in member_ids)
    try:
        completions = fetch_all(
            "SELECT id, member_id, birthday_year, due_date, channel, completed_at, completed_by "
            "FROM birthday_care_completions WHERE member_id IN ("
            + placeholders
            + ") AND birthday_year>=? AND birthday_year<=?",
            (*member_ids, years[0], years[-1]),
        )
    except Exception as error:
        # Completion records are an enhancement to the legacy rhythm source.
        # A database that has not yet run 0057 must still show birthday
        # candidates backed by operation_items instead of taking down the
        # entire care aggregate.
        if _source_error_code(error) != "SCHEMA_UNAVAILABLE":
            raise
        LOGGER.warning("birthday completion table unavailable; using rhythm fallback")
        completions = []
    completion_by_key = {
        (int(row["member_id"]), int(row["birthday_year"])): row
        for row in completions
    }
    operation_rows = fetch_all(
        "SELECT id, business_id, org_unit_id, period, status, due_date, actual_at "
        "FROM operation_items WHERE business_type='BIRTHDAY_CARE' "
        "AND business_id IN ("
        + placeholders
        + ") AND period>=? AND period<?",
        (*[str(member_id) for member_id in member_ids], f"{years[0]:04d}-01", f"{years[-1] + 1:04d}-01"),
    )
    operation_by_member: dict[int, list[dict[str, Any]]] = {}
    for row in operation_rows:
        try:
            operation_by_member.setdefault(int(row["business_id"]), []).append(row)
        except (TypeError, ValueError):
            continue

    states: dict[tuple[int, int], dict[str, dict[str, Any] | None]] = {}
    for candidate in candidates:
        member_id = int(candidate["member_id"])
        due_date = candidate["due_date"]
        matching = [
            row
            for row in operation_by_member.get(member_id, [])
            if _birthday_operation_matches_due_date(row, due_date)
        ]
        matching.sort(
            key=lambda row: (
                0 if str(row.get("status") or "").upper() == "COMPLETED" else 1,
                0 if _calendar_date(row.get("due_date")) == due_date else 1,
                -int(row["id"]),
            )
        )
        states[(member_id, due_date.year)] = {
            "completion": completion_by_key.get((member_id, due_date.year)),
            "operation_item": matching[0] if matching else None,
        }
    return states


def _birthday_items(user_id: int, today: date) -> list[dict[str, Any]]:
    allowed = accessible_org_ids(user_id)
    candidates: list[dict[str, Any]] = []
    window_end = today + timedelta(days=7)
    for member in _active_birthday_members(allowed):
        current_year_due = _birthday_due_date(member.get("birthday"), today.year)
        if not current_year_due:
            continue
        due_date = (
            current_year_due
            if current_year_due >= today
            else _birthday_due_date(member.get("birthday"), today.year + 1)
        )
        if due_date and today <= due_date <= window_end:
            candidates.append({**member, "due_date": due_date})

    states = _birthday_workflow_states(candidates)
    result = []
    for candidate in candidates:
        member_id = int(candidate["member_id"])
        due_date = candidate["due_date"]
        state = states[(member_id, due_date.year)]
        operation_item = state["operation_item"]
        if state["completion"] or (
            operation_item
            and str(operation_item.get("status") or "").upper() == "COMPLETED"
        ):
            continue
        days_until = (due_date - today).days
        if days_until == 0:
            urgency = "TODAY"
            reason = "今天生日"
        else:
            urgency = "WINDOW"
            reason = "明天生日" if days_until == 1 else f"{days_until}天后生日｜已进入关怀窗口"
        operation_item_id = (
            int(operation_item["id"])
            if operation_item
            and str(operation_item.get("status") or "").upper() != "CANCELLED"
            else None
        )
        result.append(
            {
                "member_id": member_id,
                # source_id is a stable action key, not a promise that an
                # operation item exists.  Frontends must use operation_item_id
                # only when they specifically need the rhythm record.
                "source_id": operation_item_id or member_id,
                "operation_item_id": operation_item_id,
                "action_type": "BIRTHDAY",
                "label": f"生日关怀｜{reason}",
                "reason": reason,
                "urgency": urgency,
                "due_date": due_date.isoformat(),
                "assigned_user_id": None,
                "assigned_user_name": None,
                "navigation_type": "BIRTHDAY",
                "navigation_id": member_id,
                "source_org_unit_id": candidate["org_unit_id"],
                "_source_order": len(result),
            }
        )
    return result


def complete_birthday_care(
    member_id: int,
    actor_user_id: int,
    *,
    birthday_year: int,
    due_date: str | date,
    channel: str,
    operation_item_id: int | None = None,
    now: date | datetime | None = None,
) -> dict[str, Any]:
    """Record one explicit birthday-care completion without fabricating a class.

    A pre-existing rhythm item remains its own workflow record.  When no such
    item exists, a minimal per-member/year completion fact provides the same
    durable, auditable close-out for an unassigned or unclassed member.
    """
    user = user_context(actor_user_id)
    permissions = set((user or {}).get("permissions", []))
    if "members:detail_view" not in permissions:
        raise PermissionError("当前角色不能查看或完成生日关怀")
    if "followups:manage" not in permissions:
        raise PermissionError("当前角色不能登记生日关怀完成")
    if not 1900 <= birthday_year <= 9999:
        raise ValueError("生日年度无效")
    normalized_channel = channel.strip().upper()
    if normalized_channel not in {"WECHAT", "PHONE"}:
        raise ValueError("生日关怀渠道仅支持 WECHAT 或 PHONE")
    requested_due_date = _calendar_date(due_date)
    if not requested_due_date or requested_due_date.year != birthday_year:
        raise ValueError("生日关怀日期与年度不一致")
    current = now or datetime.now(UTC)
    if isinstance(current, datetime):
        completed_at = (
            current.astimezone(UTC)
            if current.tzinfo
            else current.replace(tzinfo=UTC)
        )
    else:
        completed_at = datetime.combine(current, datetime.min.time(), tzinfo=UTC)
    today = completed_at.date()
    if requested_due_date > today + timedelta(days=7):
        raise ValueError("只能登记已经进入七天生日关怀窗口的事项")

    member = fetch_one(
        "SELECT id, status, birthday, org_unit_id FROM members WHERE id=?", (member_id,)
    )
    if not member:
        raise ValueError("学长不存在")
    if str(member.get("status") or "").upper() != "ACTIVE":
        raise ValueError("只有在册学长可以登记生日关怀完成")
    expected_due_date = _birthday_due_date(member.get("birthday"), birthday_year)
    if expected_due_date != requested_due_date:
        raise ValueError("生日关怀日期与学员主档生日不一致")
    allowed = accessible_org_ids(actor_user_id)
    scoped_org_id = resolve_member_scope(member_id, member["org_unit_id"], allowed)
    now_text = completed_at.isoformat()
    channel_label = "微信祝福" if normalized_channel == "WECHAT" else "电话关爱"

    with transaction() as connection:
        operation_item: dict[str, Any] | None = None
        if operation_item_id is not None:
            if operation_item_id <= 0:
                raise ValueError("运营事项编号无效")
            row = execute(
                connection,
                "SELECT id, business_id, business_type, org_unit_id, period, status, due_date, actual_at "
                "FROM operation_items WHERE id=?",
                (operation_item_id,),
            ).fetchone()
            operation_item = dict(row) if row else None
            if not operation_item:
                raise ValueError("生日运营事项不存在")
            try:
                item_member_id = int(operation_item.get("business_id"))
            except (TypeError, ValueError):
                item_member_id = -1
            if (
                operation_item.get("business_type") != "BIRTHDAY_CARE"
                or item_member_id != member_id
                or not _birthday_operation_matches_due_date(operation_item, requested_due_date)
            ):
                raise ValueError("生日运营事项与学员主档生日不匹配")
            if allowed is not None and operation_item["org_unit_id"] not in allowed:
                raise PermissionError("生日运营事项不在当前组织授权范围内")
            status = str(operation_item.get("status") or "").upper()
            if status == "CANCELLED":
                raise ValueError("生日运营事项已取消，不能在该事项上登记完成")
            if status == "COMPLETED":
                return {
                    "id": int(operation_item["id"]),
                    "record_type": "OPERATION_ITEM",
                    "operation_item_id": int(operation_item["id"]),
                    "completed_at": operation_item.get("actual_at"),
                    "channel": normalized_channel,
                    "created": False,
                }
            completion_note = f"生日关怀已完成｜{channel_label}"
            execute(
                connection,
                "UPDATE operation_items SET status='COMPLETED', actual_at=?, completion_note=?, "
                "manual_override=1, updated_at=? WHERE id=?",
                (now_text, completion_note, now_text, operation_item["id"]),
            )
            execute(
                connection,
                "INSERT INTO operation_progress_records(item_id, status, note, occurred_at, actor_user_id, source_type, created_at) "
                "VALUES (?, 'COMPLETED', ?, ?, ?, 'MEMBER_CARE', ?)",
                (operation_item["id"], completion_note, now_text, actor_user_id, now_text),
            )
            write_audit(
                connection,
                actor_user_id=actor_user_id,
                action="member_care.birthday.complete",
                resource_type="operation_item",
                resource_id=str(operation_item["id"]),
                org_unit_id=scoped_org_id,
                before={"status": status},
                after={
                    "member_id": member_id,
                    "birthday_year": birthday_year,
                    "due_date": requested_due_date.isoformat(),
                    "channel": normalized_channel,
                    "record_type": "OPERATION_ITEM",
                },
            )
            return {
                "id": int(operation_item["id"]),
                "record_type": "OPERATION_ITEM",
                "operation_item_id": int(operation_item["id"]),
                "completed_at": now_text,
                "channel": normalized_channel,
                "created": True,
            }

        existing = execute(
            connection,
            "SELECT id, due_date, channel, completed_at FROM birthday_care_completions "
            "WHERE member_id=? AND birthday_year=?",
            (member_id, birthday_year),
        ).fetchone()
        if existing:
            existing = dict(existing)
            return {
                "id": int(existing["id"]),
                "record_type": "BIRTHDAY_CARE_COMPLETION",
                "operation_item_id": None,
                "completed_at": existing["completed_at"],
                "channel": existing["channel"],
                "created": False,
            }
        try:
            completion_id = execute(
                connection,
                "INSERT INTO birthday_care_completions(member_id, birthday_year, due_date, channel, "
                "completed_at, completed_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    member_id,
                    birthday_year,
                    requested_due_date.isoformat(),
                    normalized_channel,
                    now_text,
                    actor_user_id,
                    now_text,
                    now_text,
                ),
            ).lastrowid
        except Exception:
            # The unique key is the last line of defence for concurrent retry
            # requests.  If another transaction won, return its fact rather
            # than turning a completed care action into an apparent failure.
            existing = execute(
                connection,
                "SELECT id, due_date, channel, completed_at FROM birthday_care_completions "
                "WHERE member_id=? AND birthday_year=?",
                (member_id, birthday_year),
            ).fetchone()
            if not existing:
                raise
            existing = dict(existing)
            return {
                "id": int(existing["id"]),
                "record_type": "BIRTHDAY_CARE_COMPLETION",
                "operation_item_id": None,
                "completed_at": existing["completed_at"],
                "channel": existing["channel"],
                "created": False,
            }
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="member_care.birthday.complete",
            resource_type="birthday_care_completion",
            resource_id=str(completion_id),
            org_unit_id=scoped_org_id,
            after={
                "member_id": member_id,
                "birthday_year": birthday_year,
                "due_date": requested_due_date.isoformat(),
                "channel": normalized_channel,
                "record_type": "BIRTHDAY_CARE_COMPLETION",
            },
        )
        return {
            "id": int(completion_id),
            "record_type": "BIRTHDAY_CARE_COMPLETION",
            "operation_item_id": None,
            "completed_at": now_text,
            "channel": normalized_channel,
            "created": True,
        }


def _followup_actions(user_id: int, today: date) -> list[dict[str, Any]]:
    rows = list_tasks(user_id)
    candidate_rows = [
        row
        for row in rows
        if str(row.get("status") or "").upper() in OPEN_STATES
        and _calendar_date(row.get("next_followup_at") or row.get("due_at"))
        and (_calendar_date(row.get("next_followup_at") or row.get("due_at")) <= today)
    ]
    completed_today_task_ids: set[int] = set()
    if candidate_rows:
        task_ids = sorted({int(row["id"]) for row in candidate_rows})
        placeholders = ",".join("?" for _ in task_ids)
        recorded_rows = fetch_all(
            "SELECT task_id, contacted_at FROM followup_records "
            f"WHERE task_id IN ({placeholders}) AND DATE(contacted_at)=?",
            (*task_ids, today.isoformat()),
        )
        visited_rows = fetch_all(
            "SELECT task_id, visited_at FROM enterprise_visit_records "
            f"WHERE task_id IN ({placeholders}) AND DATE(visited_at)=?",
            (*task_ids, today.isoformat()),
        )
        completed_today_task_ids = {
            int(row["task_id"])
            for row in [*recorded_rows, *visited_rows]
            if row.get("task_id") is not None
        }
    result = []
    for row in rows:
        if str(row.get("status") or "").upper() not in OPEN_STATES:
            continue
        raw_due = row.get("next_followup_at") or row.get("due_at")
        due_date = _calendar_date(raw_due)
        if not due_date or due_date > today:
            continue
        # Recording today's service closes today's action when no new
        # follow-up time was explicitly scheduled.  The task itself remains
        # open for the canonical follow-up workflow and health view.
        if (
            int(row["id"]) in completed_today_task_ids
            and not str(row.get("next_followup_at") or "").strip()
        ):
            continue
        task_type = str(row.get("task_type") or "OTHER").upper()
        action_type, type_label = FOLLOWUP_TYPE_LABELS.get(
            task_type, ("OTHER", "日常关怀")
        )
        if due_date < today:
            days_overdue = (today - due_date).days
            reason = f"{type_label}已逾期{days_overdue}天"
            urgency = "OVERDUE"
        else:
            reason = f"约定今天{type_label}"
            urgency = "TODAY"
        result.append(
            {
                "member_id": int(row["member_id"]),
                "source_id": int(row["id"]),
                "action_type": action_type,
                "label": f"{type_label}｜{reason}",
                "reason": reason,
                "urgency": urgency,
                "due_date": due_date.isoformat(),
                "assigned_user_id": row.get("assigned_user_id"),
                "assigned_user_name": row.get("assignee_name"),
                "navigation_type": (
                    "ENTERPRISE_VISIT" if action_type == "ENTERPRISE_VISIT" else "FOLLOWUP"
                ),
                "navigation_id": int(row["id"]),
                "source_org_unit_id": row["org_unit_id"],
                "_source_order": len(result),
            }
        )
    return result


def _renewal_actions(user_id: int, year: int, today: date) -> list[dict[str, Any]]:
    data = list_today_actions(user_id, year, as_of=today)
    result = []
    for item in data["items"]:
        primary_reason = item["primary_reason"]
        urgency = RENEWAL_REASON_URGENCY[primary_reason]
        reason_labels = [
            str(reason.get("label") or "")
            for reason in item.get("reasons", [])
            if reason.get("label")
        ]
        reason = " · ".join(reason_labels) or "续费阶段需要关注"
        result.append(
            {
                "member_id": int(item["member_id"]),
                "source_id": int(item["cycle_id"]),
                "action_type": "RENEWAL",
                "label": f"续费关爱｜{item['stage_label']}｜{reason}",
                "reason": reason,
                "urgency": urgency,
                "due_date": _date_text(item.get("next_followup_at")),
                "assigned_user_id": item.get("assigned_user_id"),
                "assigned_user_name": item.get("assigned_user_name"),
                "navigation_type": "RENEWAL",
                "navigation_id": int(item["cycle_id"]),
                "source_org_unit_id": item["org_unit_id"],
                "_source_order": len(result),
            }
        )
    return result


def _completed_birthday_actions(user_id: int, today: date) -> list[dict[str, Any]]:
    """Read birthday completions recorded today from the existing sources.

    Birthday care can be completed against either an operation-rhythm item or
    the member-level completion table.  Both are existing facts; this read
    model only presents them together and never creates a second completion
    record.
    """
    completion_rows: list[dict[str, Any]] = []
    try:
        completion_rows = fetch_all(
            "SELECT id, member_id, birthday_year, due_date, channel, completed_at "
            "FROM birthday_care_completions WHERE DATE(completed_at)=?",
            (today.isoformat(),),
        )
    except Exception as error:
        if _source_error_code(error) != "SCHEMA_UNAVAILABLE":
            raise
        LOGGER.warning("birthday completion table unavailable; skipping completion history")

    operation_rows = fetch_all(
        "SELECT id, business_id, due_date, actual_at, completion_note "
        "FROM operation_items WHERE business_type='BIRTHDAY_CARE' "
        "AND status='COMPLETED' AND actual_at IS NOT NULL "
        "AND DATE(actual_at)=?",
        (today.isoformat(),),
    )
    # The operation-item completion path is the authoritative row when the
    # item exists.  A member-level completion is preferred when both are
    # present, which prevents one real action from appearing twice.
    candidates: dict[tuple[int, int], dict[str, Any]] = {}
    for row in operation_rows:
        try:
            member_id = int(row.get("business_id"))
        except (TypeError, ValueError):
            continue
        due_date = _calendar_date(row.get("due_date"))
        completed_at = row.get("actual_at")
        year = due_date.year if due_date else today.year
        candidates[(member_id, year)] = {
            "member_id": member_id,
            "source_id": int(row["id"]),
            "action_type": "BIRTHDAY",
            "label": "生日关怀｜已完成",
            "reason": "已完成生日关怀",
            "completed_at": completed_at,
            "channel": None,
            "due_date": due_date.isoformat() if due_date else None,
            "navigation_type": "BIRTHDAY",
            "navigation_id": member_id,
            "operation_item_id": int(row["id"]),
            "source": "BIRTHDAY",
        }
    for row in completion_rows:
        try:
            member_id = int(row["member_id"])
            year = int(row["birthday_year"])
        except (TypeError, ValueError):
            continue
        candidates[(member_id, year)] = {
            "member_id": member_id,
            "source_id": int(row["id"]),
            "action_type": "BIRTHDAY",
            "label": "生日关怀｜已完成",
            "reason": "已完成生日关怀",
            "completed_at": row.get("completed_at"),
            "channel": row.get("channel"),
            "due_date": _date_text(row.get("due_date")),
            "navigation_type": "BIRTHDAY",
            "navigation_id": member_id,
            "operation_item_id": None,
            "source": "BIRTHDAY",
        }
    contexts = _member_contexts(
        {int(item["member_id"]) for item in candidates.values()},
        accessible_org_ids(user_id),
    )
    return [
        {**contexts[int(item["member_id"])], **item}
        for item in candidates.values()
        if int(item["member_id"]) in contexts
    ]


def _completed_followup_actions(user_id: int, today: date) -> list[dict[str, Any]]:
    """Read today's follow-up/visit records through the existing visibility rules."""
    tasks = list_tasks(user_id)
    task_by_id = {int(task["id"]): task for task in tasks}
    if not task_by_id:
        return []
    task_ids = sorted(task_by_id)
    placeholders = ",".join("?" for _ in task_ids)
    records = fetch_all(
        "SELECT id, task_id, member_id, channel, contacted_at, outcome_code "
        f"FROM followup_records WHERE task_id IN ({placeholders}) "
        "AND DATE(contacted_at)=? "
        "ORDER BY contacted_at DESC, id DESC",
        (*task_ids, today.isoformat()),
    )
    visits = fetch_all(
        "SELECT id, task_id, member_id, visited_at, location_type "
        f"FROM enterprise_visit_records WHERE task_id IN ({placeholders}) "
        "AND DATE(visited_at)=? "
        "ORDER BY visited_at DESC, id DESC",
        (*task_ids, today.isoformat()),
    )
    member_ids = {
        int(row["member_id"])
        for row in [*records, *visits]
        if row.get("member_id") is not None
    }
    member_ids.update(
        int(task["member_id"])
        for task in task_by_id.values()
        if str(task.get("status") or "").upper() == "CLOSED"
        and _calendar_date(task.get("updated_at")) == today
    )
    contexts = _member_contexts(member_ids, accessible_org_ids(user_id))
    result: list[dict[str, Any]] = []
    seen_task_today: set[int] = set()

    def append_item(
        row: dict[str, Any], *, visited: bool = False
    ) -> None:
        occurred_at = row.get("visited_at") if visited else row.get("contacted_at")
        if _calendar_date(occurred_at) != today:
            return
        task_id = int(row["task_id"])
        task = task_by_id.get(task_id)
        if not task:
            return
        member_id = int(row["member_id"])
        if member_id not in contexts:
            return
        seen_task_today.add(task_id)
        if visited:
            action_type = "ENTERPRISE_VISIT"
            type_label = "企业走访"
            navigation_type = "ENTERPRISE_VISIT"
            channel = row.get("location_type")
        else:
            task_type = str(task.get("task_type") or "OTHER").upper()
            action_type, type_label = FOLLOWUP_TYPE_LABELS.get(
                task_type, ("OTHER", "日常关怀")
            )
            navigation_type = "FOLLOWUP"
            channel = row.get("channel")
        result.append(
            {
                **contexts[member_id],
                "source": "FOLLOWUP",
                "source_id": int(row["id"]),
                "action_type": action_type,
                "label": f"{type_label}｜已完成",
                "reason": "已记录今天的服务动作",
                "completed_at": occurred_at,
                "channel": channel,
                "due_date": _date_text(occurred_at),
                "navigation_type": navigation_type,
                "navigation_id": task_id,
                "task_id": task_id,
            }
        )

    for row in records:
        append_item(row)
    for row in visits:
        append_item(row, visited=True)

    # A task may be closed after a previous day's record.  There is no
    # dedicated closed_at column, so the existing updated_at is the safe audit
    # timestamp for the explicit close operation.  Do not add a second row when
    # today's record already explains the completion.
    for task_id, task in task_by_id.items():
        if task_id in seen_task_today:
            continue
        if str(task.get("status") or "").upper() != "CLOSED":
            continue
        if _calendar_date(task.get("updated_at")) != today:
            continue
        member_id = int(task["member_id"])
        if member_id not in contexts:
            continue
        task_type = str(task.get("task_type") or "OTHER").upper()
        action_type, type_label = FOLLOWUP_TYPE_LABELS.get(
            task_type, ("OTHER", "日常关怀")
        )
        navigation_type = (
            "ENTERPRISE_VISIT" if action_type == "ENTERPRISE_VISIT" else "FOLLOWUP"
        )
        result.append(
            {
                **contexts[member_id],
                "source": "FOLLOWUP",
                "source_id": task_id,
                "action_type": action_type,
                "label": f"{type_label}｜已完成",
                "reason": "服务事项已关闭",
                "completed_at": task.get("updated_at"),
                "channel": None,
                "due_date": _date_text(task.get("updated_at")),
                "navigation_type": navigation_type,
                "navigation_id": task_id,
                "task_id": task_id,
            }
        )
    return result


def _completed_renewal_actions(user_id: int, today: date) -> list[dict[str, Any]]:
    """Read today's renewal follow-ups and terminal status changes."""
    rows = fetch_all(
        "SELECT f.id, f.renewal_cycle_id, f.followed_at, f.channel, "
        "c.member_id, c.renewal_year, c.due_month, c.status, c.completed_at "
        "FROM renewal_followups f JOIN renewal_cycles c "
        "ON c.id=f.renewal_cycle_id WHERE DATE(f.followed_at)=? "
        "ORDER BY f.followed_at DESC, f.id DESC",
        (today.isoformat(),),
    )
    cycle_rows = fetch_all(
        "SELECT id, member_id, renewal_year, due_month, status, completed_at "
        "FROM renewal_cycles WHERE completed_at IS NOT NULL AND DATE(completed_at)=?",
        (today.isoformat(),),
    )
    member_ids = {
        int(row["member_id"])
        for row in [*rows, *cycle_rows]
        if row.get("member_id") is not None
    }
    contexts = _member_contexts(member_ids, accessible_org_ids(user_id))
    result: list[dict[str, Any]] = []
    followed_cycle_ids: set[int] = set()
    for row in rows:
        if _calendar_date(row.get("followed_at")) != today:
            continue
        member_id = int(row["member_id"])
        if member_id not in contexts:
            continue
        cycle_id = int(row["renewal_cycle_id"])
        followed_cycle_ids.add(cycle_id)
        result.append(
            {
                **contexts[member_id],
                "source": "RENEWAL",
                "source_id": int(row["id"]),
                "action_type": "RENEWAL",
                "label": "续费关爱｜已完成跟进",
                "reason": "已记录今天的续费关爱",
                "completed_at": row.get("followed_at"),
                "channel": row.get("channel"),
                "due_date": _date_text(row.get("followed_at")),
                "navigation_type": "RENEWAL",
                "navigation_id": cycle_id,
                "renewal_cycle_id": cycle_id,
            }
        )
    for row in cycle_rows:
        if _calendar_date(row.get("completed_at")) != today:
            continue
        cycle_id = int(row["id"])
        if cycle_id in followed_cycle_ids:
            continue
        member_id = int(row["member_id"])
        if member_id not in contexts:
            continue
        result.append(
            {
                **contexts[member_id],
                "source": "RENEWAL",
                "source_id": cycle_id,
                "action_type": "RENEWAL",
                "label": "续费关爱｜已完成续费",
                "reason": "续费周期已完成",
                "completed_at": row.get("completed_at"),
                "channel": None,
                "due_date": _date_text(row.get("completed_at")),
                "navigation_type": "RENEWAL",
                "navigation_id": cycle_id,
                "renewal_cycle_id": cycle_id,
            }
        )
    return result


def _completed_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    completed_at = item.get("completed_at")
    if isinstance(completed_at, datetime):
        completed_timestamp = completed_at.replace(
            tzinfo=completed_at.tzinfo or UTC
        ).timestamp()
    else:
        try:
            parsed = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
            completed_timestamp = parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
        except (TypeError, ValueError, OverflowError):
            completed_timestamp = 0.0
    return (
        -completed_timestamp,
        COMPLETED_SOURCE_RANK.get(str(item.get("source") or ""), 99),
        str(item.get("member_name") or ""),
        int(item.get("source_id") or 0),
    )


def _action_priority_rank(action: dict[str, Any]) -> int:
    # 明天生日仍属于窗口，但要在普通窗口前提醒；不复用 ATTENTION，
    # 避免把生日误计入“需要协助”人数。
    if action.get("action_type") == "BIRTHDAY" and action.get("reason") == "明天生日":
        return 1
    return URGENCY_RANK[action["urgency"]]


def _action_sort_key(action: dict[str, Any]) -> tuple[Any, ...]:
    due_date = _calendar_date(action.get("due_date")) or date.max
    return (
        _action_priority_rank(action),
        SOURCE_RANK[action["source"]],
        due_date,
        int(action["source_id"]),
        int(action.get("_source_order", 0)),
    )


def build_member_care_actions(
    user_id: int,
    *,
    as_of: date | datetime | None = None,
) -> dict[str, Any]:
    """Aggregate member-centered care actions without creating a new task source."""
    today = (as_of or datetime.now(UTC))
    today = today.date() if isinstance(today, datetime) else today
    context = user_context(user_id)
    permissions = set((context or {}).get("permissions", []))
    source_permissions = {"renewals:read", "followups:manage", "members:detail_view"}
    if not permissions.intersection(source_permissions):
        raise PermissionError("当前账号没有学长关爱数据查看权限")

    actions: list[dict[str, Any]] = []
    completed_today: list[dict[str, Any]] = []
    coverage = _source_coverage(permissions)
    if "renewals:read" in permissions:
        try:
            actions.extend(_renewal_actions(user_id, today.year, today))
            completed_today.extend(_completed_renewal_actions(user_id, today))
            coverage["renewal"]["available"] = True
        except Exception as error:
            _mark_source_unavailable(coverage, "renewal", error)
    if "followups:manage" in permissions:
        try:
            actions.extend(_followup_actions(user_id, today))
            completed_today.extend(_completed_followup_actions(user_id, today))
            coverage["followup"]["available"] = True
        except Exception as error:
            _mark_source_unavailable(coverage, "followup", error)
    if "members:detail_view" in permissions:
        try:
            actions.extend(_birthday_items(user_id, today))
            completed_today.extend(_completed_birthday_actions(user_id, today))
            coverage["birthday"]["available"] = True
        except Exception as error:
            _mark_source_unavailable(coverage, "birthday", error)

    member_ids = {int(action["member_id"]) for action in actions}
    contexts = _member_contexts(member_ids, accessible_org_ids(user_id))
    people: dict[int, dict[str, Any]] = {}
    for action in actions:
        member_id = int(action["member_id"])
        member = contexts.get(member_id)
        if not member:
            continue
        source = (
            "RENEWAL"
            if action["action_type"] == "RENEWAL"
            else "BIRTHDAY"
            if action["action_type"] == "BIRTHDAY"
            else "FOLLOWUP"
        )
        clean_action = {
            key: value
            for key, value in action.items()
            if not key.startswith("_") and key != "member_id" and key != "source_org_unit_id"
        }
        clean_action["source"] = source
        people.setdefault(
            member_id,
            {
                **member,
                "actions": [],
            },
        )["actions"].append(clean_action)

    output_people = []
    for person in people.values():
        person["actions"].sort(key=_action_sort_key)
        primary = person["actions"][0]
        person["primary_action"] = primary
        person["action_count"] = len(person["actions"])
        person["has_overdue"] = any(
            action["urgency"] == "OVERDUE" for action in person["actions"]
        )
        output_people.append(person)
    output_people.sort(
        key=lambda person: (
            _action_priority_rank(person["primary_action"]),
            SOURCE_RANK[person["primary_action"]["source"]],
            _calendar_date(person["primary_action"].get("due_date")) or date.max,
            str(person["member_name"] or ""),
            int(person["member_id"]),
        )
    )
    completed_today.sort(key=_completed_sort_key)
    # The completion list is a read-only projection of source records.  Keep
    # the same minimal member context used by pending actions and never expose
    # service notes or enterprise-sensitive details here.
    completed_fields = {
        "member_id",
        "member_name",
        "org_unit_id",
        "org_name",
        "class_name",
        "group_name",
        "source",
        "source_id",
        "action_type",
        "label",
        "reason",
        "completed_at",
        "channel",
        "due_date",
        "navigation_type",
        "navigation_id",
        "operation_item_id",
        "task_id",
        "renewal_cycle_id",
    }
    completed_today = [
        {key: value for key, value in item.items() if key in completed_fields}
        for item in completed_today
    ]

    def count_people(predicate) -> int:
        return sum(1 for person in output_people if predicate(person["actions"]))

    summary = {
        "people_total": len(output_people),
        "action_total": sum(person["action_count"] for person in output_people),
        "overdue_people_count": count_people(
            lambda items: any(item["urgency"] == "OVERDUE" for item in items)
        ),
        "today_people_count": count_people(
            lambda items: any(item["urgency"] == "TODAY" for item in items)
        ),
        "attention_people_count": count_people(
            lambda items: any(item["urgency"] == "ATTENTION" for item in items)
        ),
        "renewal_people_count": count_people(
            lambda items: any(item["source"] == "RENEWAL" for item in items)
        ),
        "birthday_people_count": count_people(
            lambda items: any(item["source"] == "BIRTHDAY" for item in items)
        ),
        "followup_people_count": count_people(
            lambda items: any(item["source"] == "FOLLOWUP" for item in items)
        ),
        "enterprise_visit_people_count": count_people(
            lambda items: any(item["action_type"] == "ENTERPRISE_VISIT" for item in items)
        ),
        "completed_people_count": len(
            {int(item["member_id"]) for item in completed_today}
        ),
        "completed_action_count": len(completed_today),
    }
    return {
        "as_of": today.isoformat(),
        "summary": summary,
        "source_coverage": coverage,
        "people": output_people,
        "completed_today": completed_today,
    }
