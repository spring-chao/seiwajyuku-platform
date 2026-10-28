"""Weekly export ingestion; reuse source identity and idempotency boundaries."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.hq_reading_import import import_hq_reading_workbook, parse_hq_reading_workbook
from app.services.learning_credits import LearningCreditError


def previous_week(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def eligible_meeting(title: str, *, configured_titles: set[str]) -> bool:
    # Never infer class identity from a fuzzy search match.
    return title in configured_titles and not any(word in title for word in ("补课", "补卡"))


def import_weekly_export(*, actor_user_id: int, target_class_org_unit_id: str,
                         week_start: date, original_filename: str, content: bytes) -> dict:
    if week_start.weekday() != 0:
        raise LearningCreditError("统计周必须从周一开始")
    week_end = week_start + timedelta(days=6)
    if week_end >= datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise LearningCreditError("只能同步已经结束的完整周")
    parsed = parse_hq_reading_workbook(content, target_class_org_unit_id=target_class_org_unit_id)
    if str(parsed["date_from"])[:10] < week_start.isoformat() or str(parsed["date_to"])[:10] > week_end.isoformat():
        raise LearningCreditError("导出文件包含所选周以外的日期，本次未导入")
    result = import_hq_reading_workbook(actor_user_id=actor_user_id,
        target_class_org_unit_id=target_class_org_unit_id, original_filename=original_filename, content=content)
    return {"week_start": week_start.isoformat(), "week_end": week_end.isoformat(),
            "class_org_unit_id": target_class_org_unit_id, "result": result}
