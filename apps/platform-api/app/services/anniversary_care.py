from __future__ import annotations

import json
import re
import sqlite3
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta

from app.core.business_time import business_today, calendar_date
from app.db import execute, fetch_all, fetch_one, transaction
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
            body = body.replace(f"【片段{index}】", title)
        draft = f"{member_salutation(member['name'])}，入塾{years}周年快乐！\n\n{body}\n\n——苏州盛和塾"
    with transaction() as connection:
        write_audit(
            connection, actor_user_id=actor["id"], action="members.anniversary_draft.generate",
            resource_type="member", resource_id=str(member_id), org_unit_id=org_id,
            after={"memory_count": len(memories), "generation_status": ai["status"],
                   "model": ai.get("model"), "total_tokens": ai.get("total_tokens", 0)},
        )
    return {"draft": draft, "memories": memories, "generation_status": ai["status"]}


CARE_WINDOW_DAYS = 7
CARE_ACTION = "member_care.anniversary"
CARE_RESOURCE = "anniversary_care"
ANNIVERSARY_LINES = (
    "一载同行，感恩相遇", "两载相伴，感恩同行", "三载同行，初心如故", "四载相伴，一路同行",
    "五载同行，感恩有您", "六载相知，温暖同行", "七载同行，感恩相遇", "八载相伴，情谊绵长",
    "九载同行，初心不改", "十载同行，感恩有您",
)
PHONE_LIKE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LEADING_ACTIVITY_DATE = re.compile(
    r"^\s*[（(【\[]?\s*(?:\d{4}\s*[年./-]\s*)?"
    r"(?:\d{1,2}\s*月\s*\d{1,2}\s*日?|\d{1,2}[./-]\d{1,2}(?!\d))"
    r"(?:\s*[-—–~～至]\s*(?:\d{1,2}\s*月\s*)?\d{1,2}\s*日?)?"
    r"[\s）)】\]·:：、-]*"
)
ACTIVITY_SESSION_SUFFIXES = ("空巴晚宴", "晚上空巴", "空巴", "上午场", "下午场")
CLASS_MEETING_SESSION = re.compile(
    r"[\s·:：、_—–/（(【\[-]*(?:上午(?:场|签到)?|下午(?:场|签到)?|(?:晚上|晚间)?空巴(?:晚宴|场|签到)?)"
    r"[\s）)】\]]*$"
)
GREETING_VARIANTS = (
    (
        "回想您与学长们在{activities}一起学习、交流的时光，每一次相聚都让这份同行的缘分更深一分。",
        "💐 感恩一路有您，期待更多美好相聚！",
    ),
    (
        "您与学长们在{activities}一起学习、交流，那些相聚的时光值得珍藏。",
        "💐 祝您周年快乐，期待您与学长们再次相聚！",
    ),
    (
        "从{activities}的共学时光里，可以看到您与学长们一路相伴的足迹。",
        "💐 感恩这一路的相伴，祝您与学长们同行常乐！",
    ),
)


def _care_due_dates(joined: date, today: date):
    for year in (today.year - 1, today.year, today.year + 1):
        if year <= joined.year:
            continue
        due = _anniversary_date(joined, year)
        if abs((due - today).days) <= CARE_WINDOW_DAYS:
            yield due


def _care_key(member_id, due):
    return f"{member_id}:{due.isoformat()}"


def _care_state(record):
    if not record:
        return {"review_status": "PENDING", "send_status": "PENDING"}
    return json.loads(record["after_json"])


def list_anniversary_workflow(actor_user_id: int) -> dict:
    today = business_today()
    result = {"as_of": today.isoformat(), "items": []}
    due_by_member = {}
    for row in fetch_all("SELECT id, join_date FROM members WHERE status='ACTIVE' AND join_date IS NOT NULL"):
        joined = calendar_date(row["join_date"])
        dates = list(_care_due_dates(joined, today)) if joined else []
        if dates:
            due_by_member[int(row["id"])] = (joined, dates[0])
    contexts = _member_contexts(set(due_by_member), accessible_org_ids(actor_user_id))
    keys = [_care_key(member_id, due_by_member[member_id][1]) for member_id in contexts]
    if not keys:
        return result
    placeholders = ",".join("?" for _ in keys)
    latest = {}
    for row in fetch_all(
        "SELECT resource_id, after_json FROM audit_logs WHERE action=? AND resource_type=? "
        f"AND resource_id IN ({placeholders}) ORDER BY id DESC",
        (CARE_ACTION, CARE_RESOURCE, *keys),
    ):
        if row["resource_id"] not in latest:
            latest[row["resource_id"]] = _care_state(row)
    items = []
    for member_id, context in contexts.items():
        joined, due = due_by_member[member_id]
        state = latest.get(_care_key(member_id, due), {})
        items.append({**context, "join_date": joined.isoformat(), "due_date": due.isoformat(),
                      "years": due.year - joined.year, "review_status": state.get("review_status", "PENDING"),
                      "send_status": state.get("send_status", "PENDING"), "sent_date": state.get("sent_date")})
    result["items"] = sorted(items, key=lambda row: (row["due_date"] > today.isoformat(), row["due_date"], row["member_id"]))
    return result


def _care_memories(member_id, joined, due, today):
    start = _anniversary_date(joined, due.year - 1)
    eligible = verified_member_memories(member_id, limit=20, since=start, as_of=min(today, due))
    # Display only the verified event title and date. Free-text contact data
    # from a legacy title does not belong in a group greeting.
    memories = {}
    for item in eligible:
        title = _care_activity_title(item)
        if title:
            key = (item["occurred_on"], title)
            memories.setdefault(key, {"id": item["id"], "title": title, "occurred_on": item["occurred_on"]})
    return list(memories.values())[:12]


def _care_activity_title(item: dict) -> str:
    title = PHONE_LIKE.sub("", item["title"]).strip()
    if "学习会" in title and (item.get("activity_type") == "CLASS_MEETING" or re.search(r"班(?:级)?\s*学习会", title)):
        title = _greeting_activity_title({**item, "title": title})
        title = CLASS_MEETING_SESSION.sub("", title).strip()
    return title


def _greeting_activity_title(item: dict) -> str:
    day = date.fromisoformat(item["occurred_on"])
    title = LEADING_ACTIVITY_DATE.sub("", item["title"].strip(), count=1)
    # Older activity names also put a compact date code before the class name.
    compact = "|".join(dict.fromkeys((day.strftime("%Y%m%d"), day.strftime("%m%d"), f"{day.month}{day.day:02}")))
    title = re.sub(rf"^(?:{compact})(?!\d)[\s·:：、-]*", "", title, count=1)
    return title.strip()


def _care_greeting(name, years, memories, *, variant=0):
    opening = ANNIVERSARY_LINES[years - 1] if 1 <= years <= len(ANNIVERSARY_LINES) else "多年相伴，感恩有您"
    lines = [f"🎉 {member_salutation(name)}，入塾{years}周年快乐！", f"🌷 {opening}！"]
    titles_by_day = {}
    for item in sorted(memories, key=lambda row: row["occurred_on"]):
        title = _greeting_activity_title({**item, "title": _care_activity_title(item)})
        if title:
            titles_by_day.setdefault(item["occurred_on"], {})[title] = None
    experiences = {}
    for titles in titles_by_day.values():
        for title in titles:
            if not any(title.endswith(suffix) and title.removesuffix(suffix).rstrip(" ·-—") in titles
                       for suffix in ACTIVITY_SESSION_SUFFIXES):
                experiences[title] = None
    memory_line, closing = GREETING_VARIANTS[variant]
    if experiences:
        titles = list(experiences)
        text = "、".join(titles[:-1]) + "和" + titles[-1] if len(titles) > 1 else titles[0]
        lines.append(memory_line.format(activities=text))
    lines.extend(("🌱 愿您与学长们继续携手同行，在学习中收获成长，在经营中收获幸福！",
                  closing, "——苏州盛和塾"))
    return "\n\n".join(lines)


def process_anniversary_care(member_id, actor_id, *, due_date, may_read_memories, may_manage,
                            action="view", memory_ids=()):
    due = calendar_date(due_date)
    today = business_today()
    if not due or abs((due - today).days) > CARE_WINDOW_DAYS:
        raise ValueError("周年关怀日期已超出当前处理范围")
    writing = action in {"review", "regenerate", "sent"}
    if writing and not may_manage:
        raise PermissionError("当前角色只能查看周年关怀")
    allowed = accessible_org_ids(actor_id)
    with transaction() as connection:
        sqlite = isinstance(connection, sqlite3.Connection)
        if writing and sqlite and not connection.in_transaction:
            execute(connection, "BEGIN IMMEDIATE")
        lock = " FOR UPDATE" if writing and not sqlite else ""
        member = execute(connection, "SELECT name, org_unit_id, join_date FROM members "
                         f"WHERE id=? AND status='ACTIVE'{lock}", (member_id,)).fetchone()
        if not member:
            raise ValueError("在册学长不存在")
        joined = calendar_date(member["join_date"])
        years = _anniversary(joined, due)
        if not years:
            raise ValueError("周年日期与学长入塾日期不一致")
        org_id = resolve_member_scope(member_id, member["org_unit_id"], allowed)
        context = _member_contexts({member_id}, allowed).get(member_id, {})
        key = _care_key(member_id, due)
        previous = execute(connection, "SELECT after_json FROM audit_logs WHERE action=? AND resource_type=? "
                           "AND resource_id=? ORDER BY id DESC LIMIT 1" + lock,
                           (CARE_ACTION, CARE_RESOURCE, key)).fetchone()
        state = _care_state(previous)
        memories = _care_memories(member_id, joined, due, today) if may_read_memories else []
        by_id = {item["id"]: item for item in memories}
        selected = memories[:3]
        private = not may_read_memories and bool(state.get("selected_memories"))
        regenerate = action == "regenerate"
        if regenerate:
            if state["send_status"] == "SENT":
                raise ValueError("祝福已登记发送，无法重新生成")
            if private:
                raise PermissionError("当前无权查看祝福文案中的学习经历")
        if regenerate or (action in {"preview", "review"} and state["review_status"] == "PENDING"):
            if len(memory_ids) > 3 or len(set(memory_ids)) != len(memory_ids) or any(mid not in by_id for mid in memory_ids):
                raise ValueError("请从已核实的经历中选择最多三条")
            selected = [by_id[mid] for mid in memory_ids]
            if regenerate or action == "review":
                variant = (state.get("greeting_variant", 0) + 1) % len(GREETING_VARIANTS) if regenerate else 0
                state.update(review_status="APPROVED", draft=_care_greeting(member["name"], years, selected, variant=variant),
                             selected_memories=selected, greeting_variant=variant)
                state["generated_at" if regenerate else "reviewed_at"] = datetime.now(UTC).isoformat()
        elif action == "sent" and state["send_status"] == "PENDING":
            if state["review_status"] != "APPROVED":
                raise ValueError("请先生成祝福文案")
            if private:
                raise PermissionError("当前无权查看祝福文案中的学习经历")
            if due > today:
                raise ValueError("周年当天起可登记发送")
            state.update(send_status="SENT", sent_date=today.isoformat(), sent_at=datetime.now(UTC).isoformat(),
                         sent_channel="WECHAT_GROUP", sent_class_name=context.get("class_name"))
        if writing and state != _care_state(previous):
            write_audit(connection, actor_user_id=actor_id, action=CARE_ACTION, resource_type=CARE_RESOURCE,
                        resource_id=key, org_unit_id=org_id, purpose="入塾周年关怀",
                        after={**state, "due_date": due.isoformat(), "years": years})
    selected = state.get("selected_memories", selected)
    memory_keys = {(item["occurred_on"], item["title"]): item["id"] for item in memories}
    selected_ids = []
    if not private:
        for item in selected:
            memory_id = item["id"]
            if memory_id not in by_id:
                memory_id = memory_keys.get((item["occurred_on"], _care_activity_title(item)))
            if memory_id and memory_id not in selected_ids:
                selected_ids.append(memory_id)
    return {"member_id": member_id, "member_name": member["name"], "join_date": joined.isoformat(),
            "class_name": state.get("sent_class_name") or context.get("class_name"),
            "due_date": due.isoformat(), "years": years, "memories": memories, "can_manage": may_manage,
            "review_status": state["review_status"], "send_status": state["send_status"],
            "sent_date": state.get("sent_date"), "can_mark_sent": may_manage and not private and due <= today,
            "selected_ids": selected_ids,
            "draft": "" if private else state.get("draft") or _care_greeting(member["name"], years, selected)}
