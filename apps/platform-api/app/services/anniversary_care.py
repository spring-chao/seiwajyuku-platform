from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta

from app.core.business_time import business_today, calendar_date
from app.db import fetch_all, fetch_one, transaction
from app.services.audit import write_audit
from app.services.birthday_ai import generate_body
from app.services.birthday_greetings import _participation_phrase
from app.services.care_copy import member_salutation
from app.services.iam import accessible_org_ids
from app.services.member_care_actions import _member_contexts
from app.services.member_memories import verified_member_memories
from app.services.members import resolve_member_scope


def _anniversary_date(joined: date, year: int) -> date:
    day = min(joined.day, monthrange(year, joined.month)[1])
    return joined.replace(year=year, day=day)


def _anniversary(joined: date | None, today: date) -> int | None:
    if not joined or joined.year >= today.year:
        return None
    return today.year - joined.year if _anniversary_date(joined, today.year) == today else None


def _memories_by_period(member_id: int, joined: date, today: date) -> list[dict]:
    start = _anniversary_date(joined, today.year - 1)
    days = (today - start).days
    boundaries = [start + timedelta(days=days * step // 3) for step in range(4)]
    eligible = verified_member_memories(member_id, limit=None, since=start, as_of=today - timedelta(days=1))
    selected = []
    for label, lower, upper in zip(("上期", "中期", "下期"), boundaries, boundaries[1:]):
        memory = next(
            (entry for entry in eligible if lower.isoformat() <= entry["occurred_on"] < upper.isoformat()),
            None,
        )
        if memory:
            selected.append({**memory, "period": label})
    return selected


def list_anniversary_care(actor_user_id: int) -> dict:
    today = business_today()
    rows = fetch_all("SELECT id, join_date FROM members WHERE status='ACTIVE' AND join_date IS NOT NULL")
    due = {
        int(row["id"]): years for row in rows
        if (years := _anniversary(calendar_date(row["join_date"]), today))
    }
    contexts = _member_contexts(set(due), accessible_org_ids(actor_user_id))
    return {
        "as_of": today.isoformat(),
        "items": [{**context, "years": due[member_id]} for member_id, context in contexts.items()],
    }


def generate_anniversary_draft(member_id: int, actor: dict) -> dict:
    member = fetch_one(
        "SELECT name, org_unit_id, join_date FROM members WHERE id=? AND status='ACTIVE'",
        (member_id,),
    )
    if not member:
        raise ValueError("在册学长不存在")
    org_id = resolve_member_scope(member_id, member["org_unit_id"], accessible_org_ids(actor["id"]))
    today = business_today()
    joined = calendar_date(member["join_date"])
    years = _anniversary(joined, today)
    if not years:
        raise ValueError("今日没有该学长的入塾周年关怀")
    memories = _memories_by_period(member_id, joined, today) if "members:read" in actor["permissions"] else []
    ai = generate_body(
        years=years,
        participation=_participation_phrase(memories) if memories else None,
        tone="warm",
        anniversary_memories=len(memories),
    )
    draft = ""
    if ai["status"] == "GENERATED":
        body = ai["body"]
        for index, memory in enumerate(memories, 1):
            title = memory["title"]
            if re.search("[我你]", title):
                title = _participation_phrase([memory])
            body = body.replace(f"【片段{index}】", f"{memory['occurred_on']}的{title}")
        draft = f"{member_salutation(member['name'])}，入塾{years}周年快乐！\n\n{body}\n\n——苏州盛和塾"
    with transaction() as connection:
        write_audit(
            connection, actor_user_id=actor["id"], action="members.anniversary_draft.generate",
            resource_type="member", resource_id=str(member_id), org_unit_id=org_id,
            after={"memory_count": len(memories), "generation_status": ai["status"],
                   "model": ai.get("model"), "total_tokens": ai.get("total_tokens", 0)},
        )
    return {"draft": draft, "memories": memories, "generation_status": ai["status"]}
