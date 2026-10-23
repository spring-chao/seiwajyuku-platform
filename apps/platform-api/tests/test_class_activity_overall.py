from datetime import UTC, datetime
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.db import execute, fetch_one, transaction
from app.api.attendance import event_group_detail, list_activity_sessions, list_event_groups
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
                    (f['foreign_member_id'],'MEMBER','PRESENT'), (None,'GUEST','PRESENT'), (None,'MEMBER','UNMATCHED')]),
            ('AFTERNOON', [(f['member_id'],'MEMBER','PRESENT'), (f['other_member_id'],'MEMBER','MANUAL_PRESENT'),
                           (third,'MEMBER','LEAVE')]),
            ('KONPA', [(third,'MEMBER','PRESENT')]),
        ]):
            sid = execute(c, "INSERT INTO attendance_sessions(event_group_id,external_session_id,session_code,session_name,session_order,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'ACTIVE',?,?)", (event,token+code,code,code,order,now,now)).lastrowid
            for index,(mid,kind,status) in enumerate(attendees):
                execute(c, "INSERT INTO attendance_records(attendance_session_id,external_record_id,member_id,name_snapshot,participant_type,score_eligible,attendance_status,received_at,created_at,updated_at) "
                    "VALUES (?,?,?,'合成参会记录',?,0,?,?,?,?)", (sid,token+code+str(index),mid,kind,status,now,now,now))
    return f, int(event)


def test_one_activity_deduplicates_daytime_members_and_excludes_konpa_guests_and_other_classes():
    f, event = fixture()
    actor = {'id': fetch_one("SELECT id FROM app_users WHERE username='admin'")['id']}
    before = fetch_one('SELECT COUNT(*) n FROM attendance_records')['n']
    detail = event_group_detail(event,user=actor)['data']
    overall = detail['group']
    assert overall['overall_registration_count'] == 3
    assert overall['overall_present_count'] == 2
    assert overall['overall_participation_rate'] == 66.7
    assert len(detail['sessions']) == 3 and sum(s['present_count'] for s in detail['sessions']) == 7
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
