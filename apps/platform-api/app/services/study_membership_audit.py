"""Scoped, ordinary read-only attribution audit; never exposes contacts."""
from collections import defaultdict

from app.db import connect, execute
from app.services.iam import accessible_org_ids, user_context
from app.services.study_membership import CURRENT_CLASS_ID_SQL, CURRENT_GROUP_ID_SQL, effective_relation_sql


def audit_study_membership(*, actor_user_id: int, limit: int = 200):
    user = user_context(actor_user_id) or {}
    if not {'org:manage', 'members:read'}.issubset(user.get('permissions', [])):
        raise PermissionError('需要组织管理与学员查看权限')
    scopes = [accessible_org_ids(actor_user_id, permission) for permission in ('org:manage', 'members:read')]
    allowed = None
    for scope in scopes:
        if scope is not None:
            allowed = scope if allowed is None else allowed & scope
    limit = max(1, min(int(limit), 500))
    conditions, params = ["m.status='ACTIVE'"], []
    if allowed is not None:
        if not allowed:
            return {'checked_members': 0, 'membership_issue_count': 0, 'participation_difference_count': 0,
                    'membership_issues': [], 'participation_differences': [], 'truncated': False}
        marks = ','.join('?' for _ in allowed)
        conditions.append(f'(m.org_unit_id IN ({marks}) OR {CURRENT_CLASS_ID_SQL} IN ({marks}) OR {CURRENT_GROUP_ID_SQL} IN ({marks}))')
        params = [*sorted(allowed)] * 3
    c = connect()
    try:
        members = [dict(row) for row in execute(c,
            f'SELECT m.id,m.name,m.member_code,{CURRENT_CLASS_ID_SQL} AS class_id,{CURRENT_GROUP_ID_SQL} AS group_id '
            'FROM members m WHERE ' + ' AND '.join(conditions) + ' ORDER BY m.id', tuple(params)).fetchall()]
        units = {row['id']: dict(row) for row in execute(c, 'SELECT id,name,parent_id,unit_type,is_active FROM org_units').fetchall()}
        relations, differences = defaultdict(list), []
        preview_scope = accessible_org_ids(actor_user_id, 'plans:read') if 'plans:read' in user.get('permissions', []) else set()
        for start in range(0, len(members), 300):
            batch = members[start:start+300]
            ids = [row['id'] for row in batch]
            marks = ','.join('?' for _ in ids)
            for row in execute(c, 'SELECT r.id,r.member_id,r.org_unit_id,r.relation_type,r.is_primary '
                f"FROM member_org_relations r WHERE r.member_id IN ({marks}) AND r.relation_type IN ('STUDY_CLASS','SPECIAL_COHORT','STUDY_GROUP') "
                f'AND {effective_relation_sql("r")} ORDER BY r.id', tuple(ids)).fetchall():
                relations[row['member_id']].append(dict(row))
            if preview_scope == set():
                continue
            current = {row['id']: row for row in batch}
            for row in execute(c,
                'SELECT s.id AS session_id,s.session_code,s.class_org_unit_id,s.study_group_org_unit_id,s.meeting_date,s.status,'
                'a.member_id,a.home_study_group_org_unit_id,a.attended_study_group_org_unit_id,a.attendance_type '
                'FROM study_meeting_attendances a JOIN study_meeting_sessions s ON s.id=a.study_meeting_session_id '
                f'WHERE a.member_id IN ({marks}) ORDER BY s.id,a.member_id', tuple(ids)).fetchall():
                row = dict(row)
                if (allowed is not None and row['class_org_unit_id'] not in allowed) or (preview_scope is not None and row['class_org_unit_id'] not in preview_scope):
                    continue
                member = current[row['member_id']]
                target = row['study_group_org_unit_id']
                actual = member['group_id']
                current_type = ('HOME_GROUP' if actual == target else 'CROSS_GROUP') if actual and member['class_id'] == row['class_org_unit_id'] else 'UNRESOLVED'
                structural = (row['attended_study_group_org_unit_id'] != target or
                    ((row['home_study_group_org_unit_id'] == target) != (row['attendance_type'] == 'HOME_GROUP')))
                if actual != row['home_study_group_org_unit_id'] or current_type != row['attendance_type'] or structural:
                    differences.append({
                        'session_id': row['session_id'], 'session_code': row['session_code'], 'meeting_date': str(row['meeting_date'])[:10],
                        'member_id': member['id'], 'name': member['name'], 'class_name': units.get(row['class_org_unit_id'], {}).get('name', ''),
                        'meeting_group_name': units.get(target, {}).get('name', ''),
                        'registered_group_name': units.get(row['home_study_group_org_unit_id'], {}).get('name', ''),
                        'current_group_name': units.get(actual, {}).get('name', '待核对') if current_type != 'UNRESOLVED' else '待核对（离册、调班或关系冲突）',
                        'registered_type': row['attendance_type'], 'current_type': current_type,
                        'structural_conflict': structural,
                        'message': '登记事实内部不一致' if structural else '登记时归组与当前不同，需区分后来调组与原登记误报',
                    })
        issues = []
        for member in members:
            rows = relations[member['id']]
            groups = [row for row in rows if row['relation_type'] == 'STUDY_GROUP']
            classes = [row for row in rows if row['relation_type'] != 'STUDY_GROUP']
            reasons = []
            if len(groups) > 1: reasons.append('存在多条有效小组关系')
            if len(classes) > 1: reasons.append('存在多条有效班级关系')
            if groups and not member['group_id']: reasons.append('小组停用、跨班或同优先级冲突，暂不参与当前小组名单')
            if any(not units.get(row['org_unit_id'], {}).get('is_active') for row in rows): reasons.append('仍关联已停用组织')
            if reasons:
                issues.append({'member_id': member['id'], 'name': member['name'], 'member_code': member['member_code'],
                    'class_name': units.get(member['class_id'], {}).get('name', '待核对'),
                    'current_group_name': units.get(member['group_id'], {}).get('name', '待核对'),
                    'related_groups': '、'.join(units.get(row['org_unit_id'], {}).get('name', row['org_unit_id']) for row in groups),
                    'reasons': '；'.join(reasons)})
        return {'checked_members': len(members), 'membership_issue_count': len(issues),
                'participation_difference_count': len(differences), 'membership_issues': issues[:limit],
                'participation_differences': differences[:limit],
                'truncated': len(issues) > limit or len(differences) > limit}
    finally:
        c.close()
