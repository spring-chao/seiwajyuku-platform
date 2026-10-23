from datetime import UTC, datetime
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.db import execute, fetch_one, transaction
from app.api.attendance import event_group_detail, list_activity_sessions, list_event_groups, list_records
from app.services.volunteer_management import create_appointment, create_service_unit
from test_v12_mvp import _seed_group_leader_fixture


def fixture():
    f = _seed_group_leader_fixture()
    now = datetime.now(UTC).isoformat()
    token = uuid4().hex
    with transaction() as c:
        third = execute(c, "INSERT INTO members(member_code,name,org_unit_id,status,created_at,updated_at) "
            "VALUES (?,'仅空巴学长',?,'ACTIVE',?,?)", ('OVERALL-'+token, f['class_id'], now, now)).lastrowid
        execute(c, "INSERT INTO member_org_relations(member_id,org_unit_id,relation_type,is_primary,created_at,updated_at) "
                   "VALUES (?,?,'STUDY_CLASS',1,?,?)", (third, f['class_id'], now, now))
        event = execute(c, "INSERT INTO attendance_event_groups(source_key,external_group_id,org_unit_id,study_org_unit_id,title,activity_type,event_date,status,created_at,updated_at) "
            "VALUES ('overall-test',?,?,?,'整体参会测试','CLASS_MEETING','2026-10-22','ACTIVE',?,?)",
            (token,f['class_id'],f['class_id'],now,now)).lastrowid
        for order, (code, attendees) in enumerate([
            ('MORNING', [(f['member_id'],'MEMBER','PRESENT'), (f['member_id'],'MEMBER','PRESENT'),
                    (f['foreign_member_id'],'MEMBER','PRESENT'), (None,'GUEST','PRESENT'),
                    (third,'GUEST','PRESENT'), (None,'MEMBER','UNMATCHED')]),
            ('AFTERNOON', [(f['member_id'],'MEMBER','PRESENT'), (f['other_member_id'],'MEMBER','MANUAL_PRESENT'),
                           (third,'MEMBER','LEAVE')]),
            ('KONPA', [(third,'MEMBER','PRESENT')]),
        ]):
            sid = execute(c, "INSERT INTO attendance_sessions(event_group_id,external_session_id,session_code,session_name,session_order,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'ACTIVE',?,?)", (event,token+code,code,code,order,now,now)).lastrowid
            for index,(mid,kind,status) in enumerate(attendees):
                record=execute(c, "INSERT INTO attendance_records(attendance_session_id,external_record_id,member_id,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
                    "VALUES (?,?,?,'合成参会记录',?,0,?,?,?,?)", (sid,token+code+str(index),mid,kind,status,now,now,now))
                if kind=='GUEST':
                    execute(c,'UPDATE attendance_records SET external_registration_id=? WHERE id=?',(token+'-guest',record.lastrowid))
    return f, int(event)


def test_one_activity_counts_only_current_class_members_and_keeps_all_session_facts():
    f, event = fixture()
    actor = {'id': fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    before = fetch_one('SELECT COUNT(*) n FROM attendance_records')['n']
    detail = event_group_detail(event,user=actor)['data']
    overall = detail['group']
    assert overall['overall_registration_count'] == 3
    assert overall['overall_present_count'] == 2
    assert overall['overall_participation_rate'] == 66.7
    assert len(detail['sessions']) == 3 and sum(s['present_count'] for s in detail['sessions']) == 8
    records=list_records(event_group_id=event,user=actor)['data']
    assert any(r['member_id']==f['foreign_member_id'] for r in records)
    assert any(r['participant_type']=='GUEST' for r in records)
    rows = [row for row in list_activity_sessions(month='2026-10',user=actor)['data'] if row['id']==event]
    assert len(rows)==3 and all(row['overall_present_count']==2 for row in rows)
    listed = next(row for row in list_event_groups(month='2026-10',user=actor)['data'] if row['id']==event)
    assert listed['overall_registration_count']==3
    assert fetch_one('SELECT COUNT(*) n FROM attendance_records')['n']==before


def test_overall_counts_never_bypass_event_scope_or_guess_a_missing_class():
    f,event=fixture()
    actor={'id':fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    with patch('app.api.attendance.accessible_org_ids',return_value={f['other_class_id']}):
        with pytest.raises(HTTPException) as denied:
            event_group_detail(event,user=actor)
        assert denied.value.status_code==403
        assert all(row['id']!=event for row in list_activity_sessions(month='2026-10',user=actor)['data'])
    with transaction() as c:
        execute(c,'UPDATE attendance_event_groups SET study_org_unit_id=NULL WHERE id=?',(event,))
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_registration_count'] is None and group['overall_participation_rate'] is None


def test_source_identity_aliases_deduplicate_and_missing_identity_keeps_total_pending():
    f,event=fixture()
    actor={'id':fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    now=datetime.now(UTC).isoformat()
    with transaction() as c:
        execute(c,"UPDATE attendance_records SET external_registration_id='same-person' WHERE member_id=? AND attendance_session_id IN (SELECT id FROM attendance_sessions WHERE event_group_id=?)",(f['member_id'],event))
        sid=execute(c,"SELECT id FROM attendance_sessions WHERE event_group_id=? AND session_code='AFTERNOON'",(event,)).fetchone()['id']
        execute(c,"INSERT INTO attendance_records(attendance_session_id,external_record_id,external_registration_id,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
            "VALUES (?,'source-alias','same-person','合成别名','MEMBER',0,'PRESENT',?,?,?)",(sid,now,now,now))
        code=execute(c,"SELECT member_code FROM members WHERE name='仅空巴学长' AND org_unit_id=?",(f['class_id'],)).fetchone()['member_code']
        execute(c,"INSERT INTO attendance_records(attendance_session_id,external_record_id,member_code_snapshot,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
            "VALUES (?,'roster-code',?,'按正式编号识别','MEMBER',0,'PRESENT',?,?,?)",(sid,code,now,now,now))
        execute(c,"INSERT INTO attendance_records(attendance_session_id,external_record_id,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
            "VALUES (?,'unknown-guest','无身份嘉宾不影响本班统计','GUEST',0,'PRESENT',?,?,?)",(sid,now,now,now))
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_present_count']==3 and group['overall_participation_rate']==100.0
    with transaction() as c:
        execute(c,"INSERT INTO attendance_records(attendance_session_id,external_record_id,external_registration_id,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
            "VALUES (?,'missing-identity','source-only-id','不能按姓名合并','MEMBER',0,'PRESENT',?,?,?)",(sid,now,now,now))
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_identified_count']==3 and group['overall_unresolved_record_count']==1
    assert group['overall_present_count'] is None and group['overall_participation_rate'] is None


def test_current_formal_class_and_active_status_override_legacy_member_fields():
    f,event=fixture()
    actor={'id':fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    with transaction() as c:
        execute(c,"UPDATE members SET status='INACTIVE' WHERE id=?",(f['member_id'],))
        execute(c,"UPDATE member_org_relations SET org_unit_id=? WHERE member_id=? AND relation_type='STUDY_CLASS'",
                (f['other_class_id'],f['other_member_id']))
    assert fetch_one('SELECT org_unit_id FROM members WHERE id=?',(f['other_member_id'],))['org_unit_id']==f['class_id']
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_registration_count']==1
    assert group['overall_present_count']==0 and group['overall_participation_rate']==0.0


def staff_appointment(f, role, target, member_id=None):
    actor=fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']
    unit=create_service_unit(actor,unit_code='OVERALL-STAFF-'+uuid4().hex,name='合成班级师资团队',
        system_type='CLASS_TEAM',line_type='GENERAL',parent_id=None,
        home_shuku_org_unit_id='org-suzhou',service_target_org_unit_id=target)
    appointment=create_appointment(actor,member_id=member_id or f['foreign_member_id'],
        service_unit_id=unit['id'],position_key=role,confirmation_note='隔离测试正式任职')
    return appointment['id'],unit['id']


@pytest.mark.parametrize('role,target_key',[
    ('volunteer_class_counselor','class_id'),
    ('volunteer_deputy_class_teacher','class_id'),
    ('volunteer_group_counselor','group_id'),
])
def test_only_host_class_formal_teachers_and_group_counselors_are_cross_class_exceptions(role,target_key):
    f,event=fixture()
    actor={'id':fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    appointment,_=staff_appointment(f,role,f[target_key])
    now=datetime.now(UTC).isoformat()
    with transaction() as c:
        # V2 service target overrides the legacy org field; dates are archival.
        execute(c,"UPDATE volunteer_appointments SET org_unit_id=?,starts_at='2099-01-01',ends_at='2000-01-01' WHERE id=?",
                (f['other_class_id'],appointment))
        sid=execute(c,"SELECT id FROM attendance_sessions WHERE event_group_id=? AND session_code='AFTERNOON'",(event,)).fetchone()['id']
        execute(c,"INSERT INTO attendance_records(attendance_session_id,external_record_id,member_id,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
            "VALUES (?,?,?,'跨班本班师资重复签到','MEMBER',0,'PRESENT',?,?,?)",(sid,uuid4().hex,f['foreign_member_id'],now,now,now))
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_registration_count']==3
    assert group['overall_present_count']==3 and group['overall_participation_rate']==100.0


@pytest.mark.parametrize('reason',['other_class','other_role','ended','inactive_member','inactive_team','moved_group','guest','only_konpa'])
def test_cross_class_staff_exception_does_not_expand_to_unrelated_or_inactive_attendees(reason):
    f,event=fixture()
    actor={'id':fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    role='volunteer_class_monitor' if reason=='other_role' else 'volunteer_group_counselor' if reason=='moved_group' else 'volunteer_class_counselor'
    target=f['other_class_id'] if reason=='other_class' else f['group_id'] if reason=='moved_group' else f['class_id']
    appointment,unit=staff_appointment(f,role,target)
    with transaction() as c:
        if reason=='ended':
            execute(c,"UPDATE volunteer_appointments SET status='ENDED' WHERE id=?",(appointment,))
        elif reason=='inactive_member':
            execute(c,"UPDATE members SET status='INACTIVE' WHERE id=?",(f['foreign_member_id'],))
        elif reason=='inactive_team':
            execute(c,'UPDATE volunteer_service_units SET is_active=0 WHERE id=?',(unit,))
        elif reason=='moved_group':
            execute(c,'UPDATE org_units SET parent_id=? WHERE id=?',(f['other_class_id'],f['group_id']))
        elif reason=='guest':
            execute(c,"UPDATE attendance_records SET participant_type='GUEST' WHERE member_id=?",(f['foreign_member_id'],))
        elif reason=='only_konpa':
            sid=execute(c,"SELECT id FROM attendance_sessions WHERE event_group_id=? AND session_code='KONPA'",(event,)).fetchone()['id']
            execute(c,'UPDATE attendance_records SET attendance_session_id=? WHERE member_id=?',(sid,f['foreign_member_id']))
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_registration_count']==3
    assert group['overall_present_count']==2 and group['overall_participation_rate']==66.7
    assert any(r['member_id']==f['foreign_member_id'] for r in list_records(event_group_id=event,user=actor)['data'])


def test_legacy_formal_staff_and_own_member_with_a_post_count_once():
    f,event=fixture()
    actor={'id':fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    appointment,_=staff_appointment(f,'volunteer_class_counselor',f['class_id'])
    staff_appointment(f,'volunteer_deputy_class_teacher',f['class_id'],f['member_id'])
    with transaction() as c:
        execute(c,'UPDATE volunteer_appointments SET volunteer_service_unit_id=NULL WHERE id=?',(appointment,))
    group=event_group_detail(event,user=actor)['data']['group']
    assert group['overall_registration_count']==3 and group['overall_present_count']==3
