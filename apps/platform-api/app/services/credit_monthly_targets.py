"""Read-only monthly targets from published class plans and bound policies.

No calendar-month-to-cycle inference: only dated class cycle schedules own a
month. Missing schedules/policies yield an explicit unresolved target.
"""
import calendar
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.db import execute
from app.services import learning_activity_credits as activity
from app.services.credit_rule_mapping import bound_generic_rule, bound_course_rule_version


def _number(value):
    result=Decimal(str(value))
    if not result.is_finite() or result<0:raise ValueError('分值配置无效')
    return result


def _normalize(value):
    return re.sub(r'[\W_]+','',str(value or '')).lower()


def _rule(c,binding,key,model):
    item=bound_generic_rule(c,binding['credit_rule_version_id'],key)
    if not item or item['rule_version_status'] not in {'PUBLISHED','RETIRED'} or item['settlement_model']!=model:
        raise ValueError('缺少已发布计分规则：'+key)
    return item


def monthly_target(c,class_id,month):
    """Return a full calendar month's planned opportunities, never ledger sums."""
    start=month.replace(day=1);end=month.replace(day=calendar.monthrange(month.year,month.month)[1])
    try:
        bindings=[b for b in activity._binding_rows(c,class_id)
                  if activity._binding_interval_contains(b,start) and activity._binding_interval_contains(b,end)]
        if len(bindings)!=1:raise ValueError('当月没有唯一完整的学习计划绑定')
        binding=bindings[0]
        if binding['plan_status'] not in {'PUBLISHED','RETIRED'}:raise ValueError('学习计划尚未发布')
        cycles=[]
        for row in execute(c,'SELECT lc.*,pc.plan_version_id FROM class_learning_cycles lc JOIN learning_plan_cycles pc ON pc.id=lc.plan_cycle_id WHERE lc.binding_id=?',(binding['id'],)).fetchall():
            row=dict(row)
            day=activity._local_date_from_timestamp(row['actual_class_meeting_at'] or row['planned_class_meeting_at'])
            if day and start<=day<=end:
                if row['plan_version_id']!=binding['plan_version_id']:raise ValueError('学习周期与绑定计划不一致')
                cycles.append((row,day))
        if not cycles:raise ValueError('当月学习周期尚未设置计划或实际班会日期')
        reading=_rule(c,binding,'DAILY_READING','DAILY_ONCE')
        sharing=_rule(c,binding,'EXCELLENT_SHARE','MONTHLY_CAP')
        reading_unit=json.loads(reading['rule_snapshot_json'] or '{}').get('unit')
        if reading_unit not in {'WORKDAY','DAY','CALENDAR_DAY'}:raise ValueError('读书计分日期单位待配置')
        reading_days=0
        for offset in range((end-start).days+1):
            day=start+timedelta(days=offset)
            workday=activity.is_workday(c,day) if reading_unit=='WORKDAY' else True
            if workday is None:raise ValueError('当月工作日日历未完整发布')
            reading_days+=int(workday)
        total=_number(reading['points'])*reading_days+_number(sharing['cap_points'])
        pieces=[f'读书{reading_days}天×{reading["points"]}分',f'优秀分享月上限{sharing["cap_points"]}分']
        seen_courses=set()
        for cycle,day in cycles:
            tasks=[dict(r) for r in execute(c,'SELECT * FROM learning_plan_tasks WHERE plan_cycle_id=?',(cycle['plan_cycle_id'],)).fetchall()]
            if not tasks:raise ValueError('当月学习周期没有已发布的学习任务')
            types={t['task_type'] for t in tasks}
            if 'GROUP_MEETING' in types and cycle['group_meeting_policy']=='REQUIRED':
                points=_number(_rule(c,binding,'GROUP_MEETING_ATTENDANCE','CYCLE_ONCE')['points'])
                total+=points;pieces.append(f'周期{cycle["learning_cycle_index"]}小组出席{points}分（周期一次）')
            if 'CLASS_MEETING' in types or 'KONPA' in types:
                _rule(c,binding,'CLASS_MEETING_SCORE','EVENT_ONCE')
                codes=(['MORNING','AFTERNOON'] if 'CLASS_MEETING' in types else [])+(['KONPA'] if 'KONPA' in types else [])
                for code in codes:
                    rule=execute(c,"SELECT * FROM attendance_score_rules WHERE session_code=? AND activity_type='CLASS_MEETING' AND status='ACTIVE' AND effective_from<=? AND (effective_until IS NULL OR effective_until>=?) ORDER BY rule_version DESC LIMIT 1",(code,day.isoformat(),day.isoformat())).fetchone()
                    if not rule:raise ValueError('班会场次计分规则待配置：'+code)
                    points=_number(rule['base_points']);total+=points;pieces.append(f'周期{cycle["learning_cycle_index"]}{code}满分{points}')
            course_tasks=[t for t in tasks if t['task_type'] in {'ONLINE_COURSE','OFFLINE_COURSE'}]
            if course_tasks:
                _rule(c,binding,'COURSE_COMPLETION','COURSE_COMPLETION')
                version=bound_course_rule_version(c,binding['course_credit_rule_version_id'])
                if not version or version['status'] not in {'PUBLISHED','RETIRED'}:raise ValueError('课程计分规则未发布')
                rules=[dict(r) for r in execute(c,'SELECT * FROM learning_plan_credit_rules WHERE rule_version_id=?',(version['id'],)).fetchall()]
                for task in course_tasks:
                    meta=json.loads(task['metadata_json'] or '{}')
                    key=meta.get('course_key') or meta.get('credit_rule_key')
                    names={_normalize(task['title']),_normalize(meta.get('canonical_key'))}-{''}
                    matches=[r for r in rules if (key and r['course_key']==key) or (not key and names & {_normalize(r['course_name']),*(_normalize(v) for v in json.loads(r['aliases_json'] or '[]'))})]
                    if len(matches)!=1 or matches[0]['status']!='CONFIGURED':raise ValueError('课程学分待配置：'+str(task['title']))
                    rule=matches[0]
                    if rule['course_key'] in seen_courses:continue
                    seen_courses.add(rule['course_key']);points=_number(rule['credit_points']);total+=points;pieces.append(f'{rule["course_name"]}{points}分（同课程去重）')
        return {'points':total,'reason':'；'.join(pieces),'plan':f'{binding["plan_name"]} / {binding["version_label"]}；规则版本ID {binding["credit_rule_version_id"]}'}
    except (ValueError,TypeError,KeyError,InvalidOperation) as exc:
        return {'points':None,'reason':str(exc),'plan':''}
