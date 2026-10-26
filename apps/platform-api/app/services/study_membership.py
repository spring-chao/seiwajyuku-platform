"""Current formal learning attribution from class/group management only.

No legacy member text, development region or participation snapshot is a
membership source. All readers use the same effective relation and precedence.
Historical participation is deliberately not rewritten by this module.
"""
from __future__ import annotations

import re

from app.db import execute


def effective_relation_sql(alias: str) -> str:
    # MySQL uses DATE; SQLite fixtures/imports may use an ISO UTC timestamp.
    # A DATE end boundary remains effective for the whole day, not midnight.
    def boundary(field, operator):
        value = f'{alias}.{field}'
        return (f"({value} IS NULL OR CASE WHEN LENGTH(CAST({value} AS CHAR))=10 "
                f"THEN {value}{operator}CURRENT_DATE ELSE "
                f"REPLACE(SUBSTR(CAST({value} AS CHAR),1,19),'T',' '){operator}CURRENT_TIMESTAMP END)")
    return boundary('valid_from', '<=') + ' AND ' + boundary('valid_until', '>=')


def current_study_relation_sql(member_id: str, kind: str, column='org_unit_id') -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9.]*', member_id):
        raise ValueError('member expression must be an internal SQL identifier')
    if kind not in {'class', 'group'} or column not in {'id', 'org_unit_id', 'name'}:
        raise ValueError('invalid study relation selection')
    prefix = 'cg' if kind == 'group' else 'cc'
    relation, unit = prefix + 'r', prefix + 'o'
    types = "'STUDY_GROUP'" if kind == 'group' else "'STUDY_CLASS','SPECIAL_COHORT'"
    unit_types = "'GROUP'" if kind == 'group' else "'CLASS','SPECIAL_COHORT'"
    selected = f'{unit}.name' if column == 'name' else f'{relation}.{column}'
    parent = ''
    if kind == 'group':
        parent = f" AND {unit}.parent_id={current_study_relation_sql(member_id, 'class')}"
    # One explicit primary relation can supersede legacy secondary rows. Two
    # equally ranked active organizations are a conflict, never an ID lottery.
    other, other_unit = prefix + 'x', prefix + 'xo'
    other_parent = f' AND {other_unit}.parent_id={unit}.parent_id' if kind == 'group' else ''
    unique = (f' AND NOT EXISTS (SELECT 1 FROM member_org_relations {other} '
              f'JOIN org_units {other_unit} ON {other_unit}.id={other}.org_unit_id '
              f'WHERE {other}.member_id={member_id} AND {other}.relation_type IN ({types}) '
              f'AND {other_unit}.unit_type IN ({unit_types}) AND {other_unit}.is_active=1 '
              f'AND {effective_relation_sql(other)} AND {other}.is_primary>={relation}.is_primary '
              f'AND {other}.org_unit_id<>{relation}.org_unit_id{other_parent})')
    return (f'(SELECT {selected} FROM member_org_relations {relation} '
            f'JOIN org_units {unit} ON {unit}.id={relation}.org_unit_id '
            f'WHERE {relation}.member_id={member_id} AND {relation}.relation_type IN ({types}) '
            f'AND {unit}.unit_type IN ({unit_types}) AND {unit}.is_active=1 '
            f'AND {effective_relation_sql(relation)}{parent}{unique} '
            f'ORDER BY {relation}.is_primary DESC, {relation}.id DESC LIMIT 1)')


CURRENT_CLASS_ID_SQL = current_study_relation_sql('m.id', 'class')
CURRENT_GROUP_ID_SQL = current_study_relation_sql('m.id', 'group')
CURRENT_CLASS_NAME_SQL = current_study_relation_sql('m.id', 'class', 'name')
CURRENT_GROUP_NAME_SQL = current_study_relation_sql('m.id', 'group', 'name')


def group_roster(connection, *, class_id=None, group_id=None, member_ids=None, include_ungrouped=False):
    conditions, params = ["m.status='ACTIVE'"], []
    if class_id is not None:
        conditions.append('c.id=?'); params.append(class_id)
    if group_id is not None:
        conditions.append('g.id=?'); params.append(group_id)
    if member_ids is not None:
        if not member_ids:
            return []
        conditions.append('m.id IN (' + ','.join('?' for _ in member_ids) + ')')
        params.extend(member_ids)
    return [dict(row) for row in execute(connection,
        'SELECT m.id AS member_id, m.name, m.phone_masked, m.member_code, '
        'g.id AS group_org_unit_id, g.name AS group_name, '
        'c.id AS class_org_unit_id, c.name AS class_name FROM members m '
        f'JOIN org_units c ON c.id={CURRENT_CLASS_ID_SQL} '
        f"{'LEFT JOIN' if include_ungrouped else 'JOIN'} org_units g ON g.id={CURRENT_GROUP_ID_SQL} "
        'WHERE ' + ' AND '.join(conditions) + ' ORDER BY m.name,m.id', tuple(params)).fetchall()]


def set_formal_relation(connection, *, member_id, relation_type, org_id, now, source_type='MEMBER_UPDATE'):
    """Existing gated member edits must retire ALL competing formal rows."""
    from datetime import UTC, datetime, timedelta
    from app.services.study_meeting_monthly_roster import capture_before_change
    capture_before_change(connection, member_id)
    rows = execute(connection, 'SELECT id,org_unit_id FROM member_org_relations '
        'WHERE member_id=? AND relation_type=? ORDER BY id', (member_id, relation_type)).fetchall()
    selected = next((row for row in rows if row['org_unit_id'] == org_id), None) if org_id else None
    retired = [row['id'] for row in rows if not selected or row['id'] != selected['id']]
    # Production boundaries are DATE. Using today's timestamp as an end date
    # would leave the old group effective all day after a transfer or clearing.
    ended = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    for relation_id in retired:
        execute(connection, "UPDATE member_org_relations SET is_primary=0,valid_until=?,updated_at=? "
                'WHERE id=? AND (valid_until IS NULL OR valid_until>?)', (ended, now, relation_id, ended))
    if org_id:
        if selected:
            execute(connection, "UPDATE member_org_relations SET is_primary=1,valid_from=NULL,valid_until=NULL,"
                "source_type=?,updated_at=? WHERE id=?", (source_type, now, selected['id']))
        else:
            execute(connection, 'INSERT INTO member_org_relations(member_id,org_unit_id,relation_type,is_primary,'
                "source_type,created_at,updated_at) VALUES (?,?,?,1,?,?,?)",
                (member_id, org_id, relation_type, source_type, now, now))


def add_current_participation(connection, records):
    """Current attribution alongside immutable registration snapshots."""
    if not records:
        return
    by_id = {int(record['id']): record for record in records}
    ids = tuple(by_id)
    placeholders = ','.join('?' for _ in ids)
    facts = [dict(row) for row in execute(connection,
        'SELECT study_meeting_session_id,member_id,home_study_group_org_unit_id,attendance_type '
        f'FROM study_meeting_attendances WHERE study_meeting_session_id IN ({placeholders})', ids).fetchall()]
    from app.services.study_meeting_monthly_roster import roster_for_period, monthly_participation
    from app.services.learning_requirement_policy import exemption_reason
    months = sorted({str(record['meeting_date'])[:7] for record in records})
    attribution = {}; monthly = {}; roster_by_month = {}
    for month in months:
        classes = {record['class_org_unit_id'] for record in records if str(record['meeting_date'])[:7]==month}
        roster, meta = roster_for_period(connection, month, class_ids=classes)
        roster_by_month[month] = {int(row['member_id']):row for row in roster}
        attribution[month] = meta
        classes = {record['class_org_unit_id'] for record in records if str(record['meeting_date'])[:7]==month}
        monthly[month] = monthly_participation(connection,month,class_ids=classes)
    counts = {sid: {'HOME_GROUP':0, 'CROSS_GROUP':0, 'UNRESOLVED':0, 'review':0} for sid in ids}
    details = {sid: {int(a['member_id']): a for a in record.get('attendees', [])} for sid, record in by_id.items()}
    for fact in facts:
        sid = int(fact['study_meeting_session_id'])
        record = by_id[sid]
        member = roster_by_month[str(record['meeting_date'])[:7]].get(int(fact['member_id']))
        if not member or member['class_org_unit_id'] != record['class_org_unit_id'] or not member.get('group_org_unit_id'):
            kind, group_name = 'UNRESOLVED', '待核对（离册、调班或关系冲突）'
        else:
            kind = 'HOME_GROUP' if member['group_org_unit_id'] == record['study_group_org_unit_id'] else 'CROSS_GROUP'
            group_name = member['group_name']
        changed = kind != fact['attendance_type'] or not member or member['group_org_unit_id'] != fact['home_study_group_org_unit_id']
        counts[sid][kind] += 1
        counts[sid]['review'] += int(changed)
        detail = details[sid].get(int(fact['member_id']))
        if detail is not None:
            detail.update(current_group_name=group_name, current_attendance_type=kind,
                          membership_review_required=changed)
    for sid, record in by_id.items():
        count = counts[sid]
        record.update(current_home_count=count['HOME_GROUP'], current_cross_group_count=count['CROSS_GROUP'],
                      current_unresolved_count=count['UNRESOLVED'], membership_review_count=count['review'])
        month = str(record['meeting_date'])[:7]
        required = [row for row in roster_by_month[month].values() if row.get('group_org_unit_id')==record['study_group_org_unit_id'] and row['class_org_unit_id']==record['class_org_unit_id'] and not exemption_reason(row.get('group_name'),row.get('class_name'),metric='group_meeting')]
        roster_count = len(required)
        participated = monthly[month].get(record['study_group_org_unit_id'],{})
        record['home_group_member_count'] = roster_count
        record['monthly_home_present_count'] = len(participated)
        record['monthly_home_participant_names'] = list(participated.values())
        record['monthly_home_participant_ids'] = list(participated)
        record['home_group_attendance_rate'] = round(len(participated) * 100 / roster_count, 1) if roster_count else None
        record['participation_basis'] = '本月同班任意应开小组会，按本人月度统计归组去重；跨班归属保留，30日锁定'
        record['monthly_attribution'] = attribution[month]
        record['requirement_exempt_reason'] = exemption_reason(record.get('group_name'),record.get('class_name'),metric='group_meeting')
