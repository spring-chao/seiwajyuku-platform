import io
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import execute, fetch_one, transaction
from app.services.study_membership import set_formal_relation
from app.services.study_meeting_monthly_roster import due_at, refresh_current_month, roster_for_period
from test_study_meeting_sharing import source
from test_study_meeting_operations import flags

@pytest.fixture(autouse=True)
def monthly_flag(monkeypatch):
    monkeypatch.setenv("STUDY_MEETING_MONTHLY_ROSTER_ENABLED","true")

from test_learning_credit_ledger import _admin_id


def person(period,member_id,class_ids=None):
    with transaction() as c:
        rows,meta=roster_for_period(c,period,class_ids=class_ids)
    return next((r for r in rows if int(r['member_id'])==member_id),None),meta


def test_freeze_is_beijing_midnight_with_february_fallback():
    assert due_at('2025-10').isoformat()=='2025-10-30T00:00:00+08:00'
    assert due_at('2026-02').day==28
    assert due_at('2024-02').day==29
    assert due_at('2025-10').astimezone(__import__('datetime').UTC).isoformat()=='2025-10-29T16:00:00+00:00'


def test_month_reclassifies_same_class_freezes_once_and_never_changes_source_facts():
    f,meeting=source();period='2025-10';member_id=f['member_id']
    with transaction() as c:
        execute(c,"UPDATE study_meeting_sessions SET meeting_date='2025-10-23' WHERE id=?",(meeting['id'],))
        before=[dict(r) for r in execute(c,'SELECT * FROM study_meeting_attendances WHERE study_meeting_session_id=?',(meeting['id'],)).fetchall()]
    credits=fetch_one('SELECT COUNT(*) n FROM learning_credit_entries')['n']
    refresh_current_month(at='2025-10-26T00:00:00+08:00')
    with transaction() as c:
        set_formal_relation(c,member_id=member_id,relation_type='STUDY_GROUP',org_id=f['other_group_id'],now='2025-10-26 01:00:00')
    row,meta=person(period,member_id,[f['class_id']]);assert row['group_org_unit_id']==f['other_group_id'] and meta['state']=='OPEN'
    assert refresh_current_month(at='2025-10-29T15:59:59+00:00')['state']=='OPEN'
    frozen=refresh_current_month(at='2025-10-29T16:00:00+00:00');assert frozen['state']=='FROZEN'
    with transaction() as c:
        set_formal_relation(c,member_id=member_id,relation_type='STUDY_GROUP',org_id=f['group_id'],now='2025-10-30 01:00:00')
    row,meta=person(period,member_id,[f['class_id']]);assert row['group_org_unit_id']==f['other_group_id'] and meta['state']=='FROZEN'
    assert refresh_current_month(at='2025-10-31T12:00:00+08:00')['updated']==0
    with transaction() as c:
        after=[dict(r) for r in execute(c,'SELECT * FROM study_meeting_attendances WHERE study_meeting_session_id=?',(meeting['id'],)).fetchall()]
    assert after==before and fetch_one('SELECT COUNT(*) n FROM learning_credit_entries')['n']==credits
    # New month uses current learner management again.
    november=refresh_current_month(at='2025-11-01T00:00:00+08:00');assert november['state']=='OPEN'
    row,_=person('2025-11',member_id,[f['class_id']]);assert row['group_org_unit_id']==f['group_id']


def test_cross_class_move_keeps_month_origin_and_cannot_leak_into_new_class():
    f,meeting=source();member_id=f['member_id']
    # A dedicated synthetic month avoids depending on another test's frozen month.
    period='2025-09'
    with transaction() as c:
        execute(c,"UPDATE study_meeting_sessions SET meeting_date='2025-09-12' WHERE id=?",(meeting['id'],))
    refresh_current_month(at='2025-09-20T00:00:00+08:00')
    target='monthly-target-'+uuid4().hex
    with transaction() as c:
        execute(c,"INSERT INTO org_units(id,unit_code,name,unit_type,parent_id,is_active,created_at,updated_at) VALUES (?,?,'合成转入组','GROUP',?,1,'2025-09-01','2025-09-01')",(target,target,f['other_class_id']))
        set_formal_relation(c,member_id=member_id,relation_type='STUDY_CLASS',org_id=f['other_class_id'],now='2025-09-21 00:00:00')
        set_formal_relation(c,member_id=member_id,relation_type='STUDY_GROUP',org_id=target,now='2025-09-21 00:00:00')
    original,_=person(period,member_id,[f['class_id']]);assert original['class_org_unit_id']==f['class_id'] and original['group_org_unit_id']==f['group_id'] and original['cross_class_preserved']==1
    moved,_=person(period,member_id,[f['other_class_id']]);assert moved is None
    refresh_current_month(at='2025-09-30T00:00:00+08:00')
    original,meta=person(period,member_id,[f['class_id']]);assert meta['state']=='FROZEN' and original['group_org_unit_id']==f['group_id']


def test_private_scheduler_cannot_choose_date_scope_or_members(monkeypatch):
    token='synthetic-roster-maintenance-token-2026'
    monkeypatch.setenv('STUDY_EVIDENCE_CLEANUP_TOKEN',token)
    url='/api/v1/internal/study-meeting-roster-monthly'
    with TestClient(app) as client:
        assert client.post(url,json={}).status_code==401
        assert client.post(url,json={},headers={'X-Study-Evidence-Cleanup-Token':'wrong'}).status_code==401
        headers={'X-Study-Evidence-Cleanup-Token':token}
        assert client.post(url,json={'at':'2026-01-30','class_id':'anything'},headers=headers).status_code==422
        response=client.post(url,json={},headers=headers)
        assert response.status_code==200,response.text
        assert 'members' not in response.json()['data'] or isinstance(response.json()['data']['members'],int)
        assert 'member_id' not in response.text and 'phone' not in response.text
