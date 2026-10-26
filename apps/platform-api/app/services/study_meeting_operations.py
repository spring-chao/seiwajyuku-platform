"""Operator credit display and audited, ordinary CSV exports."""
from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import contextmanager

from app.db import transaction, connect, execute
from app.services.audit import write_audit
from app.services.iam import (accessible_org_ids, user_context, current_request_permission,
                              set_request_permission, clear_request_permission)
from app.services.learning_credits import dry_run_study_meeting_settlement
from app.services.study_membership import CURRENT_CLASS_ID_SQL, effective_relation_sql
from app.services.study_meetings import (
    StudyMeetingError, StudyMeetingPermissionError, get_study_meeting_record_for_operations,
    list_study_meeting_records, _session_payload,
)


@contextmanager
def permission_context(permission: str):
    previous = current_request_permission()
    set_request_permission(permission)
    try:
        yield
    finally:
        if previous is None:
            clear_request_permission()
        else:
            set_request_permission(previous)


def meeting_credit_preview(*, actor_user_id: int, session_id: int) -> dict:
    # Use the settlement calculator, including frozen rule versions, individual
    # completion facts and ledger deduplication. No second scoring algorithm.
    with permission_context("plans:credit_settlement_preview"):
        preview = dry_run_study_meeting_settlement(actor_user_id=actor_user_id, session_id=session_id)
        detail = get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)
    return _preview_members(preview, detail)


def _preview_members(preview: dict, detail: dict) -> dict:
    members = []
    for attendee in detail["attendees"]:
        entries = [item for item in preview["entries"] if item["member_id"] == attendee["member_id"]]
        group = sum(float(item["points"]) for item in entries if item["postable"] and item["entry_kind"] == "GROUP_MEETING_ATTENDANCE")
        course = sum(float(item["points"]) for item in entries if item["postable"] and item["entry_kind"] == "COURSE_COMPLETION")
        reasons = []
        for entry in entries:
            course_fact = next((course for course in detail["courses"] if course["course_key"] == entry.get("course_key")), {})
            fact = next((fact for fact in course_fact.get("completions", []) if fact["member_id"] == attendee["member_id"]), {})
            for reason in entry["reasons"]:
                reason = reason.removeprefix("RULE_MAPPING_MISSING:")
                if reason == "课程实际完成尚未确认" and fact.get("completion_status") == "NOT_COMPLETED":
                    reason = "未观看，不计课程学分"
                reasons.append(f"{entry['course_name']}：{reason}" if entry.get("course_name") else reason)
        members.append({
            "member_id": attendee["member_id"], "name": attendee["name"],
            "attendance_type": attendee["attendance_type"], "home_group_name": attendee["home_group_name"],
            "group_points": group, "course_points": course, "expected_points": group + course,
            "credited_points": sum(float(item["existing_entry"]["points"]) for item in entries
                if item.get("existing_entry") and item["existing_entry"].get("status")=="POSTED"),
            "reasons": list(dict.fromkeys(reasons)),
        })
    return {"members": members, "totals": preview["totals"], "persisted": False}


def _csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _export_records(actor_user_id, session_id, filters):
    user = user_context(actor_user_id) or {}
    if not {"exports:normal", "plans:credit_settlement_preview"}.issubset(user.get("permissions", [])):
        raise StudyMeetingPermissionError("需要普通导出和学分预览权限")
    export_scope = accessible_org_ids(actor_user_id, "exports:normal")
    with permission_context("plans:credit_settlement_preview"):
        if session_id is not None:
            records = [get_study_meeting_record_for_operations(actor_user_id=actor_user_id, session_id=session_id)]
            if export_scope is not None and records[0]["class_org_unit_id"] not in export_scope:
                raise StudyMeetingPermissionError("学习会不在导出授权范围内")
        else:
            records = list_study_meeting_records(actor_user_id=actor_user_id, limit=501,
                                                scope_permissions=('exports:normal',), **filters)
            records = [item for item in records if export_scope is None or item["class_org_unit_id"] in export_scope]
            if len(records) > 500:
                raise StudyMeetingError('导出超过500场，请缩小日期范围')
    return records


def _export_dataset(records):
    """Read one export snapshot after BOTH permission scopes were checked.

    Export does not need edit options, source locks, or settlement approvals.
    Loading the full operator screen twice for every meeting used to repeatedly
    resolve IAM and open hundreds of connections. Keep the canonical calculator
    but reuse the connection, facts and batch metadata for this request only.
    """
    from app.services.learning_credits import _build_study_meeting_preview
    from app.services.study_meeting_review import add_operation_metadata

    details, previews = {}, {}
    if not records:
        return details, previews
    connection = connect()
    try:
        if isinstance(connection, sqlite3.Connection):
            execute(connection, 'BEGIN')
        for record in records:
            sid = record['id']
            detail = _session_payload(connection, sid)
            if not detail or detail['class_org_unit_id'] != record['class_org_unit_id']:
                raise StudyMeetingError('学习会归属已变化，请刷新列表后重新导出')
            details[sid] = detail
        add_operation_metadata(connection, list(details.values()))
        for sid, detail in details.items():
            previews[sid] = _preview_members(_build_study_meeting_preview(connection, sid), detail)
    finally:
        connection.close()
    return details, previews


def _member_export_rows(records, details, previews):
    headers = ["学习会编号", "日期", "班级", "小组", "周期", "状态", "学员编号", "姓名", "参加类型", "原小组", "课程", "观看确认", "预计小组会学分", "预计课程学分", "预计新增学分", "说明"]
    headers.extend(['当前归组', '按当前归组参加类型', '归组核对说明'])
    rows = []
    for record in records:
        detail = details[record['id']]
        for member in previews[record['id']]['members']:
            current = next(a for a in detail['attendees'] if a['member_id'] == member['member_id'])
            confirmations = []
            for course in detail['courses']:
                fact = next((fact for fact in course.get('completions', []) if fact['member_id'] == member['member_id']), {})
                label = {'CONFIRMED': '已观看', 'NOT_COMPLETED': '未观看', 'UNCONFIRMED': '待确认'}.get(fact.get('completion_status'), '待确认')
                source = '人工确认' if fact.get('confirmed_by_user_id') else '登记信息'
                confirmations.append(f"{course['course_name_snapshot']}：{label}（{source}）")
            rows.append([
                detail['session_code'], detail['meeting_date'], detail['class_name'], detail['group_name'], detail['cycle_index'],
                {'DRAFT': '草稿', 'SUBMITTED': '已提交', 'CANCELLED': '已取消'}[detail['status']],
                member['member_id'], member['name'], '本组' if member['attendance_type'] == 'HOME_GROUP' else '跨组', member['home_group_name'],
                '；'.join(course['course_name_snapshot'] for course in detail['courses']), '；'.join(confirmations),
                member['group_points'], member['course_points'], member['expected_points'], '；'.join(member['reasons']) or '预计值，以正式结算为准',
                current['current_group_name'], {'HOME_GROUP':'本组','CROSS_GROUP':'跨组','UNRESOLVED':'待核对'}[current['current_attendance_type']],
                '统计归组与登记快照不同；同班按本月归组，跨班保留原归属，原始事实仍保留' if current['membership_review_required'] else '',
            ])
    return headers, rows


def _audit_export(actor_user_id, session_id, filters, layout, session_count, row_count):
    with transaction() as connection:
        write_audit(connection, actor_user_id=actor_user_id, action='exports.study_meetings.normal',
                    resource_type='study_meeting_export', resource_id=str(session_id) if session_id is not None else None,
                    after={'session_count': session_count, 'row_count': row_count, 'filters': filters,
                           'layout': layout, 'sensitive_fields': False})


def export_meeting_records(*, actor_user_id: int, session_id: int | None = None, **filters) -> str:
    records = _export_records(actor_user_id, session_id, filters)
    details, previews = _export_dataset(records)
    headers, rows = _member_export_rows(records, details, previews)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([_csv_cell(value) for value in row] for row in rows)
    _audit_export(actor_user_id, session_id, filters, 'member', len(records), len(rows))
    return "\ufeff" + output.getvalue()


def export_meeting_workbook(*, actor_user_id, session_id=None, layout='group',
                            report_month=None, report_as_of=None, **filters):
    from datetime import date
    from app.services.tabular_exports import workbook_bytes
    from app.services.study_meetings import _db_timestamp

    if layout == 'sharing':
        if session_id is not None:
            raise StudyMeetingError('群分享周报请按月份导出，不能指定单场学习会')
        from app.services.study_meeting_sharing import export_sharing_workbook
        return export_sharing_workbook(actor_user_id=actor_user_id, report_month=report_month,
            report_as_of=report_as_of, **filters)
    if layout not in {'group', 'member'}:
        raise StudyMeetingError('导出格式无效')
    records = _export_records(actor_user_id, session_id, filters)
    if layout == 'member':
        details, previews = _export_dataset(records)
        headers, rows = _member_export_rows(records, details, previews)
        for row in rows:
            row[1] = date.fromisoformat(str(row[1])[:10])
        content = workbook_bytes([('人员明细', headers, rows, ())])
        _audit_export(actor_user_id, session_id, filters, layout, len(records), len(rows))
        return content

    grouped = {}
    for record in records:
        grouped.setdefault((record['study_group_org_unit_id'], record['learning_cycle_id']), []).append(record)
    duplicate_counts = {}
    records = []
    for group in grouped.values():
        # Historic duplicate facts are retained. The group report follows the
        # latest submitted record; individual export still exposes every fact.
        selected = max(group, key=lambda r: (r['status'] == 'SUBMITTED', r['status'] != 'CANCELLED', r['id']))
        records.append(selected)
        duplicate_counts[selected['id']] = len(group)

    connection = connect()
    try:
        organizations = {r['id']: dict(r) for r in execute(connection, 'SELECT id,name,parent_id,unit_type FROM org_units').fetchall()}
        class_ids = sorted({record['class_org_unit_id'] for record in records})
        roster = {}
        from app.services.study_meeting_monthly_roster import roster_for_period
        from app.services.learning_requirement_policy import exemption_reason
        for month in sorted({str(record['meeting_date'])[:7] for record in records}):
            members,_=roster_for_period(connection,month,class_ids=class_ids)
            for member in members:
                if member.get('group_org_unit_id') and not exemption_reason(member.get('group_name'),member.get('class_name'),metric='group_meeting'):
                    roster.setdefault((member['class_org_unit_id'],month),set()).add(int(member['member_id']))
    finally:
        connection.close()

    def region_for(class_id):
        unit = organizations.get(class_id)
        visited = set()
        while unit and unit['id'] not in visited:
            visited.add(unit['id'])
            if unit['unit_type'] in {'REGIONAL_CENTER', 'OPERATING_UNIT'}:
                return unit['id'], unit['name']
            unit = organizations.get(unit['parent_id'])
        return class_id, '未设置分中心'

    details, previews = _export_dataset(records)
    records = [details[record['id']] for record in records]
    class_stats, region_stats = {}, {}
    for record in records:
        sid = record['id']
        class_id = record['class_org_unit_id']
        cycle = record['cycle_index']
        # Region rows compare the same calendar month, not unrelated class cycle numbers.
        region_id, region_name = region_for(class_id)
        month = str(record['meeting_date'])[:7]
        stat = class_stats.setdefault((class_id, record['learning_cycle_id']), {
            'label': record['class_name'], 'region': region_name, 'period': cycle,
            'roster': roster.get((class_id,month), set()), 'attendees': set()})
        region_stat = region_stats.setdefault((region_id, month), {
            'label': region_name, 'period': month, 'roster': set(), 'attendees': set()})
        region_stat['roster'].update(roster.get((class_id,month), set()))
        if record['status'] == 'SUBMITTED':
            attended = {int(member['member_id']) for member in details[sid]['attendees']}
            stat['attendees'].update(attended & stat['roster'])
            region_stat['attendees'].update(attended)

    headers = ['学习会编号', '日期', '分中心/指导团', '班级', '小组', '周期', '状态', '姓名', '其中跨组姓名',
               '本组月度参会人数', '本组应参人数', '小组月度参会率', '班级参会率（筛选范围）', '分中心参会率（筛选班级及月份）',
               '课程', '预计小组会学分合计', '预计课程学分合计', '预计新增学分合计', '说明']
    rows = []
    rate = lambda stat: len(stat['attendees'] & stat['roster']) / len(stat['roster']) if stat['roster'] else None
    for record in records:
        sid = record['id']
        detail, members = details[sid], previews[sid]['members']
        region_id, region_name = region_for(record['class_org_unit_id'])
        rows.append([record['session_code'], date.fromisoformat(str(record['meeting_date'])[:10]), region_name,
            record['class_name'], record['group_name'], record['cycle_index'],
            {'DRAFT': '草稿', 'SUBMITTED': '已提交', 'CANCELLED': '已取消'}[record['status']],
            '、'.join(m['name'] for m in detail['attendees']),
            '、'.join(m['name'] for m in detail['attendees'] if m['current_attendance_type'] == 'CROSS_GROUP'),
            detail.get('monthly_home_present_count',detail['current_home_count']), detail['home_group_member_count'],
            detail['home_group_attendance_rate'] / 100 if detail['home_group_attendance_rate'] is not None else None,
            rate(class_stats[(record['class_org_unit_id'], record['learning_cycle_id'])]),
            rate(region_stats[(region_id, str(record['meeting_date'])[:7])]),
            '；'.join(c['course_name_snapshot'] for c in detail['courses']),
            sum(m['group_points'] for m in members), sum(m['course_points'] for m in members),
            sum(m['expected_points'] for m in members), '预计值，以正式结算为准；当前归组以班级与小组管理为准；学分按登记事实预演；汇总参会率仅统计筛选结果中的已提交记录与当前有效在册学员'
            + (f"；{detail['membership_review_count']}人当前归组与登记快照不同，需核对" if detail['membership_review_count'] else '')
            + (f"；同组同周期有{duplicate_counts[sid]}条历史记录，采用最新已提交记录优先，其余见人员明细" if duplicate_counts[sid] > 1 else '')])
    stats_headers = ['统计层级', '分中心/指导团', '班级', '周期或月份', '参会人数（去重）', '当前在册人数', '参会率', '统计范围']
    stats_rows = []
    for stat in class_stats.values():
        stats_rows.append(['班级', stat['region'], stat['label'], stat['period'], len(stat['attendees']), len(stat['roster']), rate(stat), '筛选结果中的已提交记录；本班当前有效在册学员'])
    for stat in region_stats.values():
        stats_rows.append(['分中心', stat['label'], '', stat['period'], len(stat['attendees'] & stat['roster']), len(stat['roster']), rate(stat), '筛选结果涉及的班级及月份；非分中心全部学员'])
    content = workbook_bytes([('小组汇总', headers, rows, (12, 13, 14)), ('参会率汇总', stats_headers, stats_rows, (7,))])
    _audit_export(actor_user_id, session_id, filters, layout, len(records), len(rows))
    return content
