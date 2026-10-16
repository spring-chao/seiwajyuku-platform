"""Configurable ordinary member exports using the same filters as the list."""
from app.db import transaction
from app.services.audit import write_audit
from app.services.iam import user_context
from app.services.members import list_members_page
from app.services.study_meeting_operations import permission_context
from app.services.tabular_exports import workbook_bytes


FIELDS = {
    'member_code': '学员编号', 'name': '姓名', 'shuku_name': '塾',
    'management_org_name': '分中心/指导团', 'class_name': '班级',
    'group_name': '小组', 'phone_masked': '手机号（脱敏）', 'status': '状态',
}


def export_members_xlsx(actor_user_id, *, fields, layout='member', **filters):
    user = user_context(actor_user_id) or {}
    if not {'exports:normal', 'members:read'} <= set(user.get('permissions', [])):
        raise PermissionError('需要学员查看和普通导出权限')
    if not fields or len(fields) != len(set(fields)) or any(field not in FIELDS for field in fields):
        raise ValueError('请选择有效且不重复的导出字段')
    if layout not in {'member', 'group'}:
        raise ValueError('导出格式无效')
    if layout == 'group' and any(field not in {'name', 'shuku_name', 'management_org_name', 'class_name', 'group_name'} for field in fields):
        raise ValueError('小组汇总仅支持姓名及组织字段；个人字段请使用一人一行')
    items = []
    with permission_context('members:read'):
        for page in range(1, 52):
            data = list_members_page(actor_user_id, **filters, page=page, page_size=100,
                                     permission_scopes=('exports:normal', 'members:read'))
            if data['pagination']['total'] > 5000:
                raise ValueError('导出超过5000人，请缩小筛选范围')
            items.extend(data['items'])
            if page >= data['pagination']['total_pages']:
                break
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
        rows = [[({'ACTIVE': '在籍', 'SUSPENDED': '停学', 'INACTIVE': '已退出'}.get(item['status'], item['status'])
                  if field == 'status' else item.get(field) or '') for field in fields] for item in items]
    content = workbook_bytes([('学员列表', headers, rows, ())])
    with transaction() as connection:
        write_audit(connection, actor_user_id=actor_user_id, action='exports.members.normal',
                    resource_type='member_export', after={'row_count': len(rows), 'member_count': len(items),
                    'fields': fields, 'layout': layout, 'sensitive_fields': False})
    return content
