"""Display one real activity while retaining its underlying scan sessions."""
import re


def activity_base_title(title: str | None) -> str:
    value = str(title or "").strip()
    base = re.sub(
        r"(?:\s*(?:[-－—·:：|/]\s*|[（(]\s*)?"
        r"(?:上午|下午|(?:晚上?|晚间)?空巴(?:晚宴)?)|"
        r"(?:\s*[-－—·:：|/（(]\s*|\s+)(?:MORNING|AFTERNOON|EVENING|KONPAI?|AM|PM))"
        r"\s*[）)]?\s*$", "", value, flags=re.IGNORECASE,
    ).strip()
    return base or value
