from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import fetch_all, fetch_one


RULE_VERSION = "member-service-signals/1.1"
# Renewal statuses that represent a completed decision and should no longer
# surface as an overdue operational prompt.  Keep this aligned with the
# terminal statuses used by the renewal cycle update service.
_CLOSED_RENEWAL_STATUSES = {
    "COMPLETED",
    "PAID",
    "CANCELLED",
    "CANCELED",
    "RENEWED",
    "NOT_RENEWING",
    "EXITED",
}


def _as_utc(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _signal(
    code: str,
    title: str,
    message: str,
    attention_level: str,
    action_hint: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "message": message,
        "attention_level": attention_level,
        "action_hint": action_hint,
        "rule_version": RULE_VERSION,
        "evidence": evidence,
    }


def build_member_service_signals(
    member: dict[str, Any],
    actor_user_id: int,
    permissions: set[str],
    allowed_org_ids: set[str] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build privacy-safe operational prompts without scoring or ranking.

    Evidence contains only aggregate counts, dates and boolean data-quality
    facts. Notes, phone numbers, company details and task narratives are never
    copied into a signal.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    signals: list[dict[str, Any]] = []
    active = str(member.get("status") or "").strip().upper() in {"ACTIVE", "在册"}

    if active and not member.get("phone_masked"):
        signals.append(
            _signal(
                "CONTACT_INFO_REVIEW",
                "联系方式待核对",
                "当前档案没有可用的脱敏手机号，需要由有权限的专职人员核对。",
                "REVIEW",
                "进入学员编辑页补充或确认联系方式。",
                {"masked_phone_present": False},
            )
        )

    if active:
        relation = fetch_one(
            "SELECT COUNT(*) AS total FROM member_org_relations "
            "WHERE member_id=? AND relation_type='STUDY_CLASS' "
            "AND (valid_from IS NULL OR valid_from<=?) "
            "AND (valid_until IS NULL OR valid_until>=?)",
            (member["id"], current.isoformat(), current.isoformat()),
        )
        relation_count = int(relation["total"] if relation else 0)
        if relation_count == 0:
            signals.append(
                _signal(
                    "STUDY_CLASS_RELATION_REVIEW",
                    "学习班关系待核对",
                    "当前没有生效的正式学习班组织关系，请核对实际学习安排。",
                    "REVIEW",
                    "普通班应选择正式班级；先锋班、神仙班或暂不参加班级学习按既定规则备注。",
                    {
                        "active_study_class_relations": 0,
                        "class_text_present": bool(member.get("class_name")),
                    },
                )
            )

    if "followups:manage" in permissions:
        from app.services.followup_visibility import can_view_followup_task_metadata

        task_rows = fetch_all(
            "SELECT id, org_unit_id, due_at, confidentiality_level FROM followup_tasks "
            "WHERE member_id=? AND status IN ('OPEN', 'IN_PROGRESS')",
            (member["id"],),
        )
        overdue_dates: list[datetime] = []
        for row in task_rows:
            if not can_view_followup_task_metadata(
                row, actor_user_id, allowed_org_ids
            ):
                continue
            due_at = _as_utc(row["due_at"])
            if due_at and due_at <= current:
                overdue_dates.append(due_at)
        if overdue_dates:
            signals.append(
                _signal(
                    "FOLLOWUP_DUE",
                    "关怀事项待跟进",
                    f"有 {len(overdue_dates)} 个服务事项已到建议处理时间。",
                    "ACTION_REQUIRED",
                    "进入关怀跟进页查看本人有权处理的事项。",
                    {
                        "due_task_count": len(overdue_dates),
                        "earliest_due_at": min(overdue_dates).isoformat(),
                    },
                )
            )

    if "renewals:read" in permissions:
        current_renewal_org_id = (
            member.get("development_org_unit_id") or member.get("org_unit_id")
        )
        cycle_rows = fetch_all(
            "SELECT renewal_year, due_month, status FROM renewal_cycles "
            "WHERE member_id=?",
            (member["id"],),
        )
        due_cycles: list[str] = []
        for row in cycle_rows:
            if (
                allowed_org_ids is not None
                and current_renewal_org_id not in allowed_org_ids
            ):
                continue
            if str(row["status"] or "").strip().upper() in _CLOSED_RENEWAL_STATUSES:
                continue
            try:
                year, month = int(row["renewal_year"]), int(row["due_month"])
            except (TypeError, ValueError):
                continue
            if (year, month) <= (current.year, current.month):
                due_cycles.append(f"{year:04d}-{month:02d}")
        if due_cycles:
            signals.append(
                _signal(
                    "RENEWAL_DUE",
                    "续费节点待处理",
                    f"有 {len(due_cycles)} 个续费周期已进入到期月份。",
                    "ACTION_REQUIRED",
                    "进入续费运营页核对责任人、阶段和下一步行动。",
                    {
                        "due_cycle_count": len(due_cycles),
                        "earliest_due_month": min(due_cycles),
                    },
                )
            )

    priority = {"ACTION_REQUIRED": 0, "REVIEW": 1}
    signals.sort(key=lambda item: (priority.get(item["attention_level"], 9), item["code"]))
    return signals


def attach_latest_feedback(
    member_id: int, signals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach only the newest append-only feedback for each active rule version."""
    if not signals:
        return signals
    rows = fetch_all(
        "SELECT id, signal_code, rule_version, feedback_status, created_at "
        "FROM member_service_signal_feedback WHERE member_id=? "
        "ORDER BY created_at DESC, id DESC",
        (member_id,),
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["signal_code"], row["rule_version"])
        if key not in latest:
            latest[key] = {
                "id": row["id"],
                "status": row["feedback_status"],
                "created_at": (
                    row["created_at"].isoformat()
                    if isinstance(row["created_at"], datetime)
                    else str(row["created_at"])
                ),
            }
    for signal in signals:
        signal["latest_feedback"] = latest.get(
            (signal["code"], signal["rule_version"])
        )
    return signals
