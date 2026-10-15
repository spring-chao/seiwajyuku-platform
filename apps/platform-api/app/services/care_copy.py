"""Fact-based wording for messages that an operator reviews before sending."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core.business_time import calendar_date


def member_salutation(name: str) -> str:
    name = name.strip()
    return name if name.endswith(("学长", "学姐")) else f"{name}学长"


def memory_phrase(memory: dict[str, Any]) -> str:
    return f"{memory['year']}年{memory['month']}月的{memory['title']}"


def renewal_care_references(
    *,
    name: str,
    stage_code: str,
    status: str,
    renewal_year: int,
    due_month: int,
    memories: list[dict[str, Any]],
    latest: dict[str, Any] | None,
    as_of: date,
) -> tuple[str | None, str | None]:
    if stage_code == "CLOSED":
        return None, None

    salutation = member_salutation(name)
    early = stage_code in {"PREPARE", "OBSERVE_3"}
    next_date = calendar_date(latest.get("next_followup_at")) if latest else None
    # Prefer continuity over reciting the same activity at every contact. Free
    # text notes and intentions never become recipient-facing copy.
    if latest and latest.get("needs_support"):
        body = "这次想先听听您目前最希望我们协助的事。您方便时说说，我们再一起看看可以怎么安排。"
        phone_body = "想先了解您目前需要哪些协助，再一起商量合适的安排"
    elif next_date and next_date > as_of:
        body = "给您留个问候，您先忙手头的事，不用急着回复。之后方便时，我们再找时间聊聊。"
        phone_body = "想问候一下近况，如果今天不方便，我们另找您合适的时间再聊"
    elif status == "DEFERRED":
        body = "想问候一下您的近况。您先按自己的节奏安排，等方便时，我们再聊聊接下来的学习。"
        phone_body = "想关心一下您的近况，也听听什么时间再聊接下来的学习比较合适"
    elif status == "CONTACTED_WAITING_REPLY":
        body = "给您留个言，您方便时再回就好。"
        body += (
            "最近学习上有没有想交流的话题？"
            if early else "关于接下来是否继续在塾里学习，想听听您的想法。"
        )
        phone_body = "想听听您的近况，您方便的话我们再接着聊"
    elif early:
        recent_memories = [
            memory for memory in memories
            if (occurred := calendar_date(memory.get("occurred_on")))
            and 0 <= (as_of - occurred).days <= 180
        ]
        memory = max(recent_memories, key=lambda item: item["occurred_on"], default=None)
        body = (
            f"您参加了{memory_phrase(memory)}，想听听其中有没有让您有感触的内容？"
            if memory and not latest else "最近怎么样？有空时想听您聊聊最近的学习和经营近况。"
        )
        phone_body = "想关心一下您的近况，听听最近学习和经营上有没有想交流的事"
    else:
        due = f"{due_month}月" if renewal_year == as_of.year else f"{renewal_year}年{due_month}月"
        body = {
            "RENEW_2": f"您的年度学员服务将在{due}到期，想提前听听您的想法：接下来还打算继续在塾里学习吗？有想调整的学习安排，也可以和我聊聊。",
            "FOLLOW_1": f"您的年度学员服务将在{due}到期，想和您确认一下接下来是否继续学习。有还没想清楚的地方，我们可以一起聊聊，您按实际情况考虑就好。",
            "DUE_NOW": "这个月是您的续费月份，想和您确认一下接下来是否继续在塾里学习。若还有需要了解的事项，您告诉我，我们先把情况沟通清楚。",
            "RECOVERY": "这期续费时间已经过去了，想了解一下您接下来的学习安排。是希望晚些再聊，还是暂时不继续？都可以直接告诉我，我们尊重您的安排。",
        }[stage_code]
        phone_body = "想听听您对接下来继续学习的考虑，有需要了解或协调的事项也可以一起聊聊"

    return (
        f"{salutation}好，{body}",
        f"{salutation}好，我是盛和塾的运营同仁。{phone_body}。现在方便聊两分钟吗？",
    )
