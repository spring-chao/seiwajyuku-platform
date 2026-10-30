"""Audited centre/month snapshot. Missing evidence stays blank; no business writes."""
import json
import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.db import connect, execute
from app.services import credit_source_imports as sources
from app.services import learning_activity_credits as activity
from app.services import learning_credits as credits
from app.services import reading_weekly_export as weekly
from app.services.audit import write_audit
from app.services.credit_source_exports import LABELS, _safe_text
from app.services.learning_org_catalog import class_catalog
from app.services.learning_requirement_policy import exemption_reason
from app.services.study_meeting_sharing import _region, sharing_dataset
from app.services.monthly_core_layout import build_workbook


def _selection(c, actor):
    user = credits.user_context(actor) or {}
    if not set(weekly.PERMISSIONS) <= set(user.get('permissions', [])):
        raise PermissionError('需要普通导出、学员查看和学分预览权限')
    scope = sources.effective_scope(actor, weekly.PERMISSIONS)
    units = {r['id']: dict(r) for r in execute(c,
        'SELECT id,name,parent_id,unit_type,is_active FROM org_units').fetchall()}
    centers = {}
    for item in class_catalog(c, scope):
        center_id, name = _region(item['id'], units)
        # Direct classes without a centre are not relabelled as a centre.
        if units[center_id]['unit_type'] not in {'REGIONAL_CENTER', 'OPERATING_UNIT'}:
            continue
        item = {**item, 'related_class_ids': [cid for cid in item['related_class_ids']
                  if scope is None or cid in scope]}
        centers.setdefault(center_id, {'id': center_id, 'name': name, 'classes': []})['classes'].append(item)
    return centers, scope


def export_options(actor):
    c = connect()
    try:
        centers, _ = _selection(c, actor)
        return {'centers': [{'id': r['id'], 'name': r['name']} for r in
                            sorted(centers.values(), key=lambda r: (r['name'], r['id']))]}
    finally:
        c.close()


def period(month):
    today = datetime.now(activity.SHANGHAI).date()
    try:
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError
        first = date.fromisoformat(month + '-01')
        last = first.replace(day=monthrange(first.year, first.month)[1])
    except (ValueError, TypeError):
        raise ValueError('请选择有效统计月份') from None
    if first > today:
        raise ValueError('统计月份不能晚于本月')
    return first, min(last, today - timedelta(days=1))


def _source_records(c, ids, first, cutoff):
    states, shares, ignored = defaultdict(set), set(), set()
    if cutoff < first or not sources.available(c):
        return states, shares, ignored
    marks = ','.join('?' for _ in ids)
    rows = execute(c,
        'SELECT i.id,i.class_org_unit_id,i.source_kind,r.member_id,r.occurred_on,r.record_json '
        'FROM learning_credit_source_rows r JOIN learning_credit_source_imports i ON i.id=r.import_id '
        f'WHERE i.class_org_unit_id IN ({marks}) AND r.occurred_on>=? AND r.occurred_on<=? '
        "AND i.source_kind IN ('DAILY_READING','EXCELLENT_SHARE') ORDER BY i.id,r.id LIMIT 100001",
        (*sorted(ids), first.isoformat(), cutoff.isoformat())).fetchall()
    if len(rows) > 100000:
        raise ValueError('月度学习来源超过100000条，请按班级导出')
    ignored.update(source['class_org_unit_id'] for source in weekly.source_metadata(c,(r['id'] for r in rows))
                   if source['source_kind']=='DAILY_READING' and source['ignored_count'])
    for row in rows:
        cid, mid = row['class_org_unit_id'], int(row['member_id'])
        record = json.loads(row['record_json'])
        if row['source_kind'] == 'DAILY_READING':
            states[cid, mid, str(row['occurred_on'])[:10]].add(record.get('已完成录音'))
        elif record.get('已评定优秀') == '是':
            shares.add((cid, mid))
    return states, shares, ignored


def _learning(c, center, first, cutoff):
    ids = {cid for item in center['classes'] for cid in item['related_class_ids']}
    states, shares, ignored = _source_records(c, ids, first, cutoff)
    calendar = []
    for index in range(max(0, (cutoff-first).days+1)):
        day = first + timedelta(days=index)
        workday = activity.is_workday(c, day)
        if workday is not False:
            calendar.append((day, workday))
    detail, analyses, abnormal = [], [], []
    for item in center['classes']:
        class_ids = set(item['related_class_ids'])
        members = weekly._roster(c, class_ids, first, max(first, cutoff))
        grouped = defaultdict(list)
        for member in members:
            values = []
            exempt = exemption_reason(member['group_name'], item['name'], metric='reading')
            required = complete = missing = 0
            for day, workday in calendar:
                if day not in member['included_days']:
                    values.append('不在本班')
                elif member['status'] == 'SUSPENDED' or exempt:
                    values.append('停学' if member['status'] == 'SUSPENDED' else '免要求')
                elif workday is None:
                    values.append(None); missing += 1
                else:
                    required += 1
                    seen = set().union(*(states.get((cid, member['id'], day.isoformat()), set()) for cid in class_ids))
                    value = '√' if seen == {'是'} else '×' if seen == {'否'} else '待核对' if seen else None
                    values.append(value); complete += value == '√'; missing += value in (None, '待核对')
            rate = complete/required if required and not missing else None
            failures = values.count('×')
            shared = any((cid, member['id']) in shares for cid in class_ids)
            note = '来源或日历不全' if missing else '来源含待核对记录' if class_ids & ignored else '免要求' if exempt else ''
            row = [item['name'], member['group_name'], str(member['member_code']), _safe_text(member['name']),
                   *values, required if not any(flag is None for _, flag in calendar) else None,
                   complete, failures, rate, 1 if shared else None, note]
            detail.append(row)
            if failures or missing or class_ids & ignored:
                abnormal.append([item['name'], member['group_name'], str(member['member_code']), _safe_text(member['name']),
                                 failures, missing, rate, note or '来源明确未完成'])
            grouped[member['group_id']].append((member, complete, required, missing, shared))
        for members_in_group in grouped.values():
            required = sum(r[2] for r in members_in_group)
            completed = sum(r[1] for r in members_in_group)
            missing = sum(r[3] for r in members_in_group)
            rate = completed/required if required and not missing else None
            analyses.append([center['name'], item['name'], members_in_group[0][0]['group_name'], len(members_in_group),
                             required if not any(flag is None for _, flag in calendar) else None, completed, rate,
                             sum(r[4] for r in members_in_group) or None, None,
                             '分享率缺少普通分享完整来源；优秀分享人数仅统计已保存的优秀分享。' + ('读书来源或日历待补全。' if rate is None else '')])
    if len(detail) > 5000:
        raise ValueError('导出名册超过5000人，请缩小组织范围')
    return calendar, detail, analyses, abnormal


def _activities(c, center, scope, first, cutoff):
    ids = {cid for item in center['classes'] for cid in item['related_class_ids']}
    # A class-only grant never exposes centre-wide events or another class's facts.
    include_center = scope is None or center['id'] in scope
    marks = ','.join('?' for _ in ids)
    rows = execute(c,
        'SELECT eg.id,eg.title,eg.event_date,o.name AS class_name,s.id AS session_id,s.session_name,s.session_code,'
        'COUNT(DISTINCT CASE WHEN ar.attendance_status IN (\'PRESENT\',\'MANUAL_PRESENT\') THEN ar.member_id END) AS present '
        'FROM attendance_event_groups eg JOIN attendance_sessions s ON s.event_group_id=eg.id '
        'LEFT JOIN org_units o ON o.id=eg.study_org_unit_id '
        'LEFT JOIN attendance_records ar ON ar.attendance_session_id=s.id '
        f"WHERE eg.status<>'DELETED' AND eg.event_date>=? AND eg.event_date<=? AND "
        f'(eg.study_org_unit_id IN ({marks}) OR (eg.study_org_unit_id IS NULL AND eg.org_unit_id=?)) '
        'GROUP BY eg.id,eg.title,eg.event_date,o.name,s.id,s.session_name,s.session_code ORDER BY eg.event_date,eg.id,s.id',
        (first.isoformat(), cutoff.isoformat(), *sorted(ids), center['id'] if include_center else '')).fetchall()
    result = []
    for r in rows:
        attendees = execute(c,
            'SELECT m.id,m.name,ar.attendance_status FROM attendance_records ar JOIN members m ON m.id=ar.member_id '
            'WHERE ar.attendance_session_id=? ORDER BY m.name,ar.id', (r['session_id'],)).fetchall()
        by_member = {}
        for attendee in attendees:
            previous = by_member.get(attendee['id'])
            mark = '√' if attendee['attendance_status'] in ('PRESENT','MANUAL_PRESENT') else '×' if attendee['attendance_status']=='ABSENT' else None
            if not previous or previous[1] != '√': by_member[attendee['id']] = (_safe_text(attendee['name']), mark)
        result.append([str(r['id']), date.fromisoformat(str(r['event_date'])[:10]), _safe_text(r['title']), r['class_name'],
                       _safe_text(r['session_name'] or r['session_code']), None, int(r['present']), None,
                       '实到按场次去重；应到及参会率待补全。', list(by_member.values())])
    return result


def _meetings(actor, center, month, cutoff):
    rows = []
    data = sharing_dataset(actor_user_id=actor, report_month=month, report_as_of=cutoff.isoformat(),
                           management_org_unit_id=center['id'],scope_permissions=('members:read',))
    for summary in data['centers']:
        grouped=defaultdict(list)
        for r in summary['meetings']:grouped[r['class_name']].append(r)
        for members in grouped.values():
            pending=any('待核对' in r['status'] for r in members)
            planned=sum(r['status'] in ('已完成','待完成') for r in members) if not pending else None
            completed=sum(r['status'] in ('已完成','已完成（政策待核对）') for r in members)
            for r in members:
                rows.append([r['class_name'], r['group_name'], r['status'], r['date'],
                             r['roster'], r['present'], r['participation_rate'],
                             _safe_text(r['names']), planned, completed,
                             completed/planned if planned else None, data['attribution']['state']])
    return rows


def _ledger(c, ids, first, cutoff):
    marks = ','.join('?' for _ in ids)
    rows = execute(c,
        'SELECT e.id,e.occurred_at,e.credit_type,e.points,e.source_type,e.source_id,e.status,e.reversal_of_entry_id,'
        'm.member_code,m.name,o.name AS class_name FROM learning_credit_entries e '
        'JOIN members m ON m.id=e.member_id JOIN org_units o ON o.id=e.class_org_unit_id '
        f'WHERE e.class_org_unit_id IN ({marks}) AND e.occurred_at>=? AND e.occurred_at<? '
        "AND e.occurred_precision='EXACT_DATE' AND e.status IN ('POSTED','REVERSED') ORDER BY e.occurred_at,e.id LIMIT 50001",
        (*sorted(ids), first.isoformat(), (cutoff+timedelta(days=1)).isoformat())).fetchall()
    if len(rows) > 50000:
        raise ValueError('月度学分明细超过50000条，请缩小组织范围')
    return [[str(r['id']), r['class_name'], str(r['member_code']), _safe_text(r['name']),
             date.fromisoformat(str(r['occurred_at'])[:10]), LABELS.get(r['credit_type'],r['credit_type']),
             Decimal(str(r['points'])), _safe_text(r['source_type']), _safe_text(r['source_id']), r['status'],
             str(r['reversal_of_entry_id']) if r['reversal_of_entry_id'] else None] for r in rows]


def export_monthly(actor, center_id, month):
    first, cutoff = period(month)
    c = connect()
    try:
        centers, scope = _selection(c, actor)
        center = centers.get(center_id)
        if not center:
            raise PermissionError('分中心不在核心数据导出授权范围')
        calendar, learning, analyses, abnormal = _learning(c, center, first, cutoff)
        activities = _activities(c, center, scope, first, cutoff)
        meetings = _meetings(actor, center, month, cutoff) if cutoff >= first else []
        ids = {cid for item in center['classes'] for cid in item['related_class_ids']}
        ledger = _ledger(c, ids, first, cutoff)
        book = build_workbook(center['name'], month, cutoff, calendar, learning, abnormal, activities, meetings, ledger)
        content = weekly._workbook_bytes(book)
        write_audit(c, actor_user_id=actor, action='learning.monthly_core.export', resource_type='monthly_core_export',
                    org_unit_id=center_id, after={'month':month, 'cutoff':str(cutoff), 'class_ids':sorted(ids),
                    'member_count':len(learning), 'ledger_count':len(ledger), 'contact_fields':False, 'ledger_write':False})
        c.commit()
        return content
    finally:
        c.close()
