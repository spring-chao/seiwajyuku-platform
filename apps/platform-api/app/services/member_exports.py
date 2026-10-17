"""Configurable ordinary member exports using the same filters as the list."""
from datetime import UTC, date, datetime

from app.core.settings import get_settings
from app.db import fetch_all, transaction
from app.services.audit import write_audit
from app.services.iam import user_context
from app.services.members import effective_membership_years, list_members_page
from app.services.study_meeting_operations import permission_context
from app.services.tabular_exports import workbook_bytes


FIELDS = {
    'member_code': '学员编号', 'name': '姓名', 'shuku_name': '塾',
    'management_org_name': '分中心/指导团', 'class_name': '班级',
    'group_name': '小组', 'phone_masked': '手机号（脱敏）', 'status': '状态',
    'gender': '性别', 'birthday': '生日', 'district': '隶属区',
    'org_name': '归属组织', 'development_org_name': '发展归属组织',
    'class_committee_name': '班组委（历史资料）', 'join_date': '入塾日期',
    'study_start_date': '开始学习时间', 'membership_years': '塾龄（年）',
    'renewal_month': '续费月份', 'position': '职务', 'referrer': '推荐人',
    'referrer_center': '推荐人所属分中心', 'company_name': '公司名称',
    'company_address': '公司地址', 'industry_category': '行业大类', 'industry': '所属行业',
    'company_products': '公司产品/服务', 'employee_count': '员工人数',
    'volunteer_positions': '当前志工任职', 'volunteer_scopes': '志工服务组织',
}
GROUP_FIELDS = {'name', 'shuku_name', 'management_org_name', 'class_name', 'group_name'}
DETAIL_FIELDS = {'gender', 'birthday', 'district', 'class_committee_name', 'join_date',
                 'study_start_date', 'membership_years', 'renewal_month', 'position',
                 'referrer', 'referrer_center', 'development_org_name',
                 'volunteer_positions', 'volunteer_scopes'}
ENTERPRISE_FIELDS = {'company_address', 'industry_category', 'industry', 'company_products', 'employee_count'}
VOLUNTEER_FIELDS = {'volunteer_positions', 'volunteer_scopes'}
PROTECTED_FIELDS = {
    'phone': ('完整手机号', '请使用现有敏感导出流程'),
    'annual_sales': ('公司销售额（万元）', '企业敏感财务资料，不在普通导出中提供'),
    'profit_margin': ('利润率', '企业敏感财务资料，不在普通导出中提供'),
    'company_size': ('企业规模（历史资料）', '可能含敏感财务资料，不在普通导出中提供'),
    'notes': ('资料备注', '可能含关怀或个人敏感信息，不在普通导出中提供'),
}


def _permissions(actor_user_id):
    permissions = set((user_context(actor_user_id) or {}).get('permissions', []))
    if not {'exports:normal', 'members:read'} <= permissions:
        raise PermissionError('需要学员查看和普通导出权限')
    return permissions


def member_export_fields(actor_user_id):
    permissions = _permissions(actor_user_id)
    fields = []
    for key, label in FIELDS.items():
        permission = ('members:detail_view' if key in DETAIL_FIELDS else
                      'members:enterprise_view' if key in ENTERPRISE_FIELDS else None)
        reason = '需要学员资料查看权限' if permission == 'members:detail_view' else '需要企业资料查看权限'
        if key in VOLUNTEER_FIELDS and not get_settings().identity_authorization_enabled:
            reason = '身份与任职功能尚未启用'
            available = False
        else:
            available = permission is None or permission in permissions
        category = ('志工任职' if key in VOLUNTEER_FIELDS else
                    '企业资料' if key in ENTERPRISE_FIELDS or key == 'company_name' else
                    '学习与组织' if key in GROUP_FIELDS - {'name'} or key in {
                        'org_name', 'development_org_name', 'class_committee_name', 'join_date',
                        'study_start_date', 'membership_years', 'renewal_month', 'referrer', 'referrer_center'} else '基本资料')
        fields.append({'key': key, 'label': label, 'category': category, 'available': available,
                       'reason': '' if available else reason, 'personal': key not in GROUP_FIELDS,
                       'requires_purpose': key in ENTERPRISE_FIELDS})
    fields.extend({'key': key, 'label': label, 'category': '受保护字段', 'available': False,
                   'reason': reason, 'personal': True, 'requires_purpose': False}
                  for key, (label, reason) in PROTECTED_FIELDS.items())
    return fields


def _enrich(items, fields):
    # Only IDs returned by the intersected list query may enter these bounded reads.
    extra = set(fields) - {'member_code', 'name', 'shuku_name', 'management_org_name',
                           'class_name', 'group_name', 'phone_masked', 'status', 'org_name'}
    columns = extra - VOLUNTEER_FIELDS - {'development_org_name'}
    if 'membership_years' in columns:
        columns |= {'join_date', 'membership_years_overridden'}
    for start in range(0, len(items), 500):
        batch = items[start:start + 500]
        ids = [item['id'] for item in batch]
        placeholders = ','.join('?' for _ in ids)
        if columns or 'development_org_name' in extra:
            projection = ['m.id'] + [f'm.{column}' for column in sorted(columns)]
            if 'development_org_name' in extra:
                projection.append('d.name AS development_org_name')
            rows = fetch_all('SELECT ' + ','.join(projection) +
                             ' FROM members m LEFT JOIN org_units d ON d.id=m.development_org_unit_id'
                             f' WHERE m.id IN ({placeholders})', tuple(ids))
            by_id = {row['id']: row for row in rows}
            for item in batch:
                item.update(by_id.get(item['id'], {}))
                if 'membership_years' in extra:
                    item['membership_years'] = effective_membership_years(
                        item.get('join_date'), item.get('membership_years'), item.get('membership_years_overridden'))
        if extra & VOLUNTEER_FIELDS:
            now = datetime.now(UTC).isoformat()
            appointments = fetch_all(
                'SELECT va.member_id, c.position_name, va.appointment_key, o.name AS org_name '
                'FROM volunteer_appointments va LEFT JOIN volunteer_position_catalog c '
                'ON c.position_key=va.appointment_key JOIN org_units o ON o.id=va.org_unit_id '
                f'WHERE va.member_id IN ({placeholders}) AND va.status=\'ACTIVE\' '
                'AND (va.starts_at IS NULL OR va.starts_at<=?) AND (va.ends_at IS NULL OR va.ends_at>?) '
                'ORDER BY va.member_id, va.id', (*ids, now, now))
            for item in batch:
                own = [row for row in appointments if row['member_id'] == item['id']]
                item['volunteer_positions'] = '、'.join(row['position_name'] or row['appointment_key'] for row in own)
                item['volunteer_scopes'] = '、'.join(row['org_name'] for row in own)


def _value(item, field):
    value = item.get(field)
    if value is None:
        return ''
    if field == 'status':
        return {'ACTIVE': '在籍', 'SUSPENDED': '停学', 'INACTIVE': '已退出'}.get(value, value)
    if field == 'gender':
        return {'MALE': '男', 'FEMALE': '女', 'UNKNOWN': '未填写'}.get(value, value)
    if field in {'birthday', 'join_date', 'study_start_date'} and value:
        if isinstance(value, (date, datetime)):
            return value
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return value


def export_members_xlsx(actor_user_id, *, fields, layout='member', purpose='', **filters):
    _permissions(actor_user_id)
    if not fields or len(fields) != len(set(fields)) or any(field not in FIELDS for field in fields):
        raise ValueError('请选择有效且不重复的导出字段')
    if layout not in {'member', 'group'}:
        raise ValueError('导出格式无效')
    if layout == 'group' and any(field not in GROUP_FIELDS for field in fields):
        raise ValueError('小组汇总仅支持姓名及组织字段；个人字段请使用一人一行')
    catalog = {field['key']: field for field in member_export_fields(actor_user_id)}
    if any(not catalog[field]['available'] for field in fields):
        raise PermissionError('所选字段需要相应资料查看权限或功能尚未启用')
    purpose = purpose.strip()
    if set(fields) & ENTERPRISE_FIELDS and len(purpose) < 4:
        raise ValueError('导出企业资料请填写用途（至少4个字符）')
    scoped_permissions = ['exports:normal', 'members:read']
    if set(fields) & DETAIL_FIELDS:
        scoped_permissions.append('members:detail_view')
    if set(fields) & ENTERPRISE_FIELDS:
        scoped_permissions.append('members:enterprise_view')
    items = []
    with permission_context('members:read'):
        for page in range(1, 52):
            data = list_members_page(actor_user_id, **filters, page=page, page_size=100,
                                     permission_scopes=tuple(scoped_permissions))
            if data['pagination']['total'] > 5000:
                raise ValueError('导出超过5000人，请缩小筛选范围')
            items.extend(data['items'])
            if page >= data['pagination']['total_pages']:
                break
    _enrich(items, fields)
    if layout == 'group':
        grouped = {}
        for item in items:
            # Unassigned people remain separate, never a fictitious shared group.
            key = item.get('group_org_unit_id') or ('unassigned', item['id'])
            grouped.setdefault(key, []).append(item)
        headers = [FIELDS[field] for field in fields] + ['人数']
        rows = [[('、'.join(item['name'] for item in group) if field == 'name' else group[0].get(field) or '')
                 for field in fields] + [len(group)] for group in grouped.values()]
    else:
        headers = [FIELDS[field] for field in fields]
        rows = [[_value(item, field) for field in fields] for item in items]
    content = workbook_bytes([('学员列表', headers, rows, ())])
    with transaction() as connection:
        write_audit(connection, actor_user_id=actor_user_id, action='exports.members.normal',
                    resource_type='member_export', purpose=purpose or None,
                    after={'row_count': len(rows), 'member_count': len(items),
                    'fields': fields, 'layout': layout, 'sensitive_fields': False})
    return content
