from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from app.db import fetch_one, transaction
from app.services.audit import write_audit
from app.services.iam import accessible_org_ids, user_context
from app.services.member_memories import verified_member_memories
from app.services.members import can_access_member


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _format_month_day(value: Any) -> str | None:
    parsed = _as_date(value)
    return parsed.strftime("%m-%d") if parsed else None


def _completed_years(join_date: Any, stored: Any, overridden: Any) -> int | None:
    joined = _as_date(join_date)
    if overridden and stored is not None:
        try:
            return max(0, int(float(stored)))
        except (TypeError, ValueError):
            return None
    if not joined:
        return None
    today = datetime.now(UTC).date()
    years = today.year - joined.year
    if (today.month, today.day) < (joined.month, joined.day):
        years -= 1
    return max(0, years)


def _member_context(member_id: int) -> dict[str, Any] | None:
    member = fetch_one(
        "SELECT m.id, m.name, m.birthday, m.join_date, m.study_start_date, "
        "m.membership_years, m.membership_years_overridden, m.org_unit_id, "
        "o.name AS org_name FROM members m JOIN org_units o ON o.id=m.org_unit_id "
        "WHERE m.id=?",
        (member_id,),
    )
    if not member:
        return None
    now = datetime.now(UTC).isoformat()
    region = fetch_one(
        "SELECT r.org_unit_id, o.name AS org_name FROM member_org_relations r "
        "JOIN org_units o ON o.id=r.org_unit_id WHERE r.member_id=? "
        "AND r.relation_type='PRIMARY_REGION' AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "ORDER BY r.is_primary DESC, r.id LIMIT 1",
        (member_id, now, now),
    )
    if region:
        member["org_unit_id"] = region["org_unit_id"]
        member["org_name"] = region["org_name"]
    class_row = fetch_one(
        "SELECT o.id, o.name FROM member_org_relations r JOIN org_units o ON o.id=r.org_unit_id "
        "WHERE r.member_id=? AND r.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT') "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "AND o.is_active=1 ORDER BY r.is_primary DESC, r.id DESC LIMIT 1",
        (member_id, now, now),
    )
    group_row = fetch_one(
        "SELECT o.id, o.name FROM member_org_relations r JOIN org_units o ON o.id=r.org_unit_id "
        "WHERE r.member_id=? AND r.relation_type='STUDY_GROUP' "
        "AND (r.valid_from IS NULL OR r.valid_from<=?) AND (r.valid_until IS NULL OR r.valid_until>=?) "
        "AND o.is_active=1 ORDER BY r.is_primary DESC, r.id DESC LIMIT 1",
        (member_id, now, now),
    )
    member["class_org_unit_id"] = class_row["id"] if class_row else None
    member["class_name"] = class_row["name"] if class_row else None
    member["group_org_unit_id"] = group_row["id"] if group_row else None
    member["group_name"] = group_row["name"] if group_row else None
    return member


def get_birthday_greeting_context(member_id: int, actor_user_id: int) -> dict[str, Any]:
    user = user_context(actor_user_id)
    if not user or "members:detail_view" not in user["permissions"]:
        raise PermissionError("当前角色不能查看生日关怀资料")
    member = _member_context(member_id)
    if not member:
        raise ValueError("学长不存在")
    allowed = accessible_org_ids(actor_user_id)
    if not can_access_member(member_id, member["org_unit_id"], allowed):
        raise PermissionError("学长不在组织授权范围内")

    join_date = _as_date(member.get("join_date"))
    membership_years = _completed_years(
        member.get("join_date"),
        member.get("membership_years"),
        member.get("membership_years_overridden"),
    )
    memories = verified_member_memories(member_id) if "members:read" in user["permissions"] else []
    notes: list[str] = []
    if not join_date:
        notes.append("该学长缺少入塾日期，当前祝福不会包含同行年限。")
    if not memories:
        notes.append("当前没有可核验的本人学习或活动记录，祝福将退化为基础祝福。")
    birthday_month_day = _format_month_day(member.get("birthday"))
    member_view = {
        "id": member["id"],
        "name": member["name"],
        "birthday_month_day": birthday_month_day,
        "org_unit_id": member["org_unit_id"],
        "org_name": member["org_name"],
        "class_org_unit_id": member["class_org_unit_id"],
        "class_name": member["class_name"],
        "group_org_unit_id": member["group_org_unit_id"],
        "group_name": member["group_name"],
        "join_date": join_date.isoformat() if join_date else None,
        "study_start_date": _as_date(member.get("study_start_date")),
        "membership_years": membership_years,
        "membership_years_source": (
            "OVERRIDE"
            if member.get("membership_years_overridden") and member.get("membership_years") is not None
            else "JOIN_DATE"
            if join_date
            else "MISSING"
        ),
    }
    member_view["study_start_date"] = (
        member_view["study_start_date"].isoformat()
        if member_view["study_start_date"]
        else None
    )
    result = {
        "member": member_view,
        "memories": memories,
        "selected_memory_ids": [item["id"] for item in memories if item["selected_by_default"]],
        "data_quality": {
            "facts_only": True,
            "memory_count": len(memories),
            "join_date_available": bool(join_date),
            "attendance_source_readable": "members:read" in user["permissions"],
            "notes": notes,
        },
        "policy": "仅使用已验证的本人出席/完成记录；不包含手机号、企业敏感资料、续费或关怀备注。",
    }
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.birthday_greeting_context.view",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=member["org_unit_id"],
            after={"memory_count": len(memories), "facts_only": True},
        )
    return result


def generate_birthday_greeting_draft(
    member_id: int,
    actor_user_id: int,
    *,
    selected_memory_ids: list[str],
    tone: Literal["standard", "warm", "concise"] = "warm",
) -> dict[str, Any]:
    context = get_birthday_greeting_context(member_id, actor_user_id)
    memories_by_id = {item["id"]: item for item in context["memories"]}
    if len(selected_memory_ids) > 4 or any(item not in memories_by_id for item in selected_memory_ids):
        raise ValueError("只能选择当前上下文中的已验证共同记忆，最多 4 条")
    memories = [memories_by_id[item] for item in selected_memory_ids]
    member = context["member"]
    name = member["name"]
    salutation = name if name.endswith(("学长", "学姐")) else f"{name}学长"
    birthday_month_day = member.get("birthday_month_day")
    birthday_text = None
    if birthday_month_day:
        birthday_month, birthday_day = birthday_month_day.split("-")
        birthday_text = f"{int(birthday_month)}月{int(birthday_day)}日"

    if tone == "warm":
        birthday_line = (
            f"{salutation}好，{birthday_text}是您的生日，祝您生日快乐！"
            if birthday_text
            else f"{salutation}好，祝您生日快乐！"
        )
        lines = [birthday_line]
        if member["join_date"] and member["membership_years"] is not None:
            joined = _as_date(member["join_date"])
            lines.append(
                f"从{joined.year}年{joined.month}月加入盛和塾以来，已经与盛和塾各位学长同行{member['membership_years']}年。"
            )
        lines.append("这些年的学习与相聚，记录着与各位学长共同走过的时光。")
        lines.append(
            "感谢您这些年与盛和塾各位学长同行与践行。祝您身心安康、家庭幸福、事业顺遂，在经营与人生的道路上不断精进。"
        )
    else:
        birthday_line = (
            f"{salutation}好，{birthday_text}是您的生日，祝您生日快乐！"
            if birthday_text
            else f"{salutation}好，祝您生日快乐！"
        )
        lines = [birthday_line]
        if member["join_date"] and member["membership_years"] is not None:
            joined = _as_date(member["join_date"])
            lines.append(
                f"从{joined.year}年{joined.month}月加入盛和塾以来，我们已经同行{member['membership_years']}年。"
            )
        if memories:
            phrases = [
                f"{item['year']}年{item['month']}月的{item['title']}"
                for item in memories
            ]
            if tone == "concise":
                lines.append("这些共同经历，也记录着我们一路学习与相聚的时光：" + "、".join(phrases) + "。")
            else:
                lines.append(
                    "还记得我们一起经历过"
                    + "、".join(phrases)
                    + "，一次次学习与相聚，也记录着我们共同走过的时光。"
                )
        elif tone != "concise":
            lines.append("这些年的学习与相聚，记录着我们共同走过的时光。")
    if tone == "concise":
        lines.append("祝您身心安康、家庭幸福、事业顺遂，愿您在经营与人生的道路上持续精进。")
    else:
        if tone != "warm":
            lines.append("感谢这些年的同行。祝您身心安康、家庭幸福、事业顺遂，愿您在经营与人生的道路上持续精进。")
    lines.append("——苏州盛和塾")
    draft = "\n".join(lines)
    with transaction() as connection:
        write_audit(
            connection,
            actor_user_id=actor_user_id,
            action="members.birthday_greeting_draft.generate",
            resource_type="member",
            resource_id=str(member_id),
            org_unit_id=member["org_unit_id"],
            after={"selected_memory_count": len(memories), "tone": tone, "facts_only": True},
        )
    return {
        "member_id": member_id,
        "tone": tone,
        "selected_memory_ids": selected_memory_ids,
        "draft": draft,
        "facts_only": True,
        "editable": True,
    }
