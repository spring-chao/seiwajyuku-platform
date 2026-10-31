"""Formal volunteer posts determine per-class review rights; no role grants."""
from app.db import fetch_one
from app.services.wechat_identity import get_member_role_scopes
from app.services import signin_engine

MANAGERS={'volunteer_class_counselor','volunteer_deputy_class_teacher','volunteer_class_monitor'}
VIEWERS={'volunteer_group_leader','volunteer_group_counselor'}

def scopes(member_id):
    result={}
    for scope in get_member_role_scopes(member_id):
        position=scope.get('position_key');oid=scope.get('org_unit_id')
        if position not in MANAGERS|VIEWERS:continue
        org=fetch_one('SELECT id,parent_id,is_active FROM org_units WHERE id=?',(oid,))
        if not org or not org['is_active']:continue
        if position in MANAGERS:
            result.setdefault(oid,{'manage':False,'groups':set()})['manage']=True
        else:
            result.setdefault(org['parent_id'],{'manage':False,'groups':set()})['groups'].add(oid)
    if not result:raise PermissionError('当前没有空巴名单查看权限')
    return result

def event_list(member_id):
    signin_engine.enabled(member=True)
    allowed=scopes(member_id)
    return signin_engine.engine_request('/ops/v1/konpa',{'action':'events','actor':{
        'id':str(member_id),'classes':list(allowed),'members':None,'manage':False}})

def request(member_id,event_id,action,**payload):
    allowed=scopes(member_id)
    events=event_list(member_id).get('events',[])
    event=next((r for r in events if r.get('event_id')==event_id),None)
    if not event:raise PermissionError('活动不在空巴授权范围内')
    class_id=event.get('class_org_unit_id')
    if not class_id:
        # Public event metadata is deliberately minimal; ask the engine to
        # resolve one class at a time without returning any participant rows.
        for candidate in allowed:
            visible=signin_engine.engine_request('/ops/v1/konpa',{'action':'events','actor':{
                'id':str(member_id),'classes':[candidate],'members':None,'manage':False}}).get('events',[])
            if any(r.get('event_id')==event_id for r in visible):class_id=candidate;break
    scope=allowed.get(class_id)
    if not scope:raise PermissionError('活动不在空巴授权范围内')
    if action!='list' and not scope['manage']:raise PermissionError('仅班主任、副班主任、班长可修改或确认')
    members=None
    if not scope['manage']:
        from app.services.study_membership import group_roster
        members=sorted({str(row['member_id']) for row in group_roster_groups(scope['groups'],group_roster)})
    signin_engine.enabled(member=True,write=action!='list')
    return signin_engine.engine_request('/ops/v1/konpa',{'action':action,'event_id':event_id,**payload,
        'actor':{'id':str(member_id),'classes':[class_id],'members':members,'manage':scope['manage']}},timeout=20)

def group_roster_groups(groups,loader):
    from app.db import transaction
    with transaction() as c:
        rows=[r for group in groups for r in loader(c,group_id=group)]
    return rows
