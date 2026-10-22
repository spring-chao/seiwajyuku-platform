"""Monthly basic target: published workdays + fixed 5/18/4 points.

This reporting denominator is independent of a class's cycle scheduling and
never changes actual credit rules or writes a ledger entry.
"""
import calendar
from datetime import timedelta
from decimal import Decimal

from app.db import execute
from app.services import learning_activity_credits as activity

BASIC_CREDIT_TYPES = frozenset({'DAILY_READING', 'EXCELLENT_SHARE', 'CLASS_MEETING_SCORE', 'GROUP_MEETING_ATTENDANCE'})


def monthly_target(c, class_id, month):
    start=month.replace(day=1)
    count=calendar.monthrange(month.year,month.month)[1]
    version=execute(c,"SELECT id,version_label,timezone FROM learning_business_calendar_versions "
        "WHERE calendar_key=? AND calendar_year=? AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",
        (activity.BUSINESS_CALENDAR_KEY,month.year)).fetchone()
    if not version or version['timezone']!=activity.BUSINESS_CALENDAR_TIMEZONE:
        return {'points':None,'reason':'当月工作日日历未完整发布','plan':''}
    days={str(row['business_date'])[:10]:dict(row) for row in execute(c,
        'SELECT business_date,day_type FROM learning_business_calendar_days WHERE calendar_version_id=? AND business_date>=? AND business_date<=?',
        (version['id'],start.isoformat(),start.replace(day=count).isoformat())).fetchall()}
    workdays=0
    for offset in range(count):
        day=(start+timedelta(days=offset)).isoformat()
        row=days.get(day)
        resolved=activity._is_workday_row({**row,'timezone':version['timezone']}) if row else None
        if resolved is None:
            return {'points':None,'reason':'当月工作日日历未完整发布：'+day,'plan':''}
        workdays+=int(resolved)
    return {'points':Decimal(workdays+27),
        'reason':f'读书{workdays}个工作日×1分＋优秀检视分享5分＋班会和空巴18分＋小组学习会4分',
        'plan':f'当月基础满分固定口径；工作日日历 {month.year} / {version["version_label"]}（含调休）'}
