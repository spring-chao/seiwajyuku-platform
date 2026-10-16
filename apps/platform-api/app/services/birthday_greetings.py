from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from app.core.business_time import business_today
from app.db import fetch_one, transaction
from app.services.audit import write_audit
from app.services.care_copy import member_salutation
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


def _participation_phrase(memories: list[dict[str, Any]]) -> str:
    """Summarize selected evidence without turning a blessing into a roster."""
    themes = []
    for memory in memories:
        activity_type = str(memory.get("activity_type") or "").upper()
        if activity_type in {"CLASS_MEETING", "GROUP_MEETING", "STUDY_GROUP"}:
            theme = "相聚共学"
        elif activity_type in {"READING", "READING_CHECKIN"}:
            theme = "读书学习"
        elif activity_type in {"COURSE", "STUDY_COURSE"}:
            theme = "课程学习"
        elif activity_type in {"STUDY_TOUR", "ENTERPRISE_VISIT"}:
            theme = "参访交流"
        elif activity_type in {
            "SEMINAR", "CENTER_MONTHLY_REPORT", "ANNUAL_REPORT",
            "REPORT_MEETING", "CONFERENCE",
        }:
            theme = "交流活动"
        else:
            # An unknown type does not support a more specific claim.
            return "学习活动"
        if theme not in themes:
            themes.append(theme)
    if len(themes) > 2:
        return "各类学习活动"
    return "与".join(themes)


def _completed_years(join_date: Any, stored: Any, overridden: Any) -> int | None:
    joined = _as_date(join_date)
    if overridden and stored is not None:
        try:
            return max(0, int(float(stored)))
        except (TypeError, ValueError):
            return None
    if not joined:
        return None
    today = business_today(datetime.now(UTC))
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
        notes.append("入塾日期尚未完善，不影响生成生日祝福。")
    if not memories:
        notes.append("暂无可核验的学习或活动经历，祝福将以日常问候为主。")
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
        "selected_memory_ids": [item["id"] for item in memories[:2]],
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
    if tone not in {"standard", "warm", "concise"}:
        raise ValueError("不支持的祝福语气")
    context = get_birthday_greeting_context(member_id, actor_user_id)
    memories_by_id = {item["id"]: item for item in context["memories"]}
    if len(selected_memory_ids) > 4 or any(item not in memories_by_id for item in selected_memory_ids):
        raise ValueError("只能选择当前上下文中的已验证共同记忆，最多 4 条")
    selected_memory_ids = list(dict.fromkeys(selected_memory_ids))
    memories = [memories_by_id[item] for item in selected_memory_ids]
    member = context["member"]
    salutation = member_salutation(member["name"])
    participation = _participation_phrase(memories) if memories else None
    opening = f"{salutation}，生日快乐！"
    if tone == "warm":
        lines = [
            f"🎂 {opening}",
            "在盛和塾，学长们因学习而相识。这份难得的缘分，值得珍惜。",
        ]
        if participation:
            lines.append(f"感谢您在{participation}中的参与。曾经的学习时光，也是这份缘分中温暖的一部分。")
        lines.extend([
            "愿往后有机会与学长们相聚，交流学习心得，也聊聊工作与生活。",
            "在这个特别的日子里，苏州盛和塾衷心祝愿您：\n"
            "🌷 身体安康，心中有热爱，生活有欢喜。\n"
            "🌷 工作顺遂，也有更多时间陪伴家人、享受生活。",
            "愿新的一岁，多些从容与欢喜，与家人幸福常伴！",
        ])
    elif tone == "concise":
        lines = [opening, "珍惜因盛和塾而与学长们结下的学习缘分。"]
        if participation:
            lines.append(f"感谢您在{participation}中的参与。")
        lines.append("苏州盛和塾祝您身体安康、生活有欢喜，与家人幸福常伴！")
    else:
        lines = [opening, "因学习而相识，因盛和塾而结缘。与学长们同行的这份缘分，值得珍惜。"]
        if participation:
            lines.append(f"感谢您在{participation}中的参与，也期待往后有更多相聚交流的机会。")
        lines.append("苏州盛和塾衷心祝您身体安康、工作顺遂，生活中多些从容与欢喜，与家人幸福常伴！")
    lines.append("——苏州盛和塾")
    draft = ("\n" if tone == "concise" else "\n\n").join(lines)
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
