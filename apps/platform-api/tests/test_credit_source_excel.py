"""The same source/import/export acceptance runs on SQLite and isolated MySQL."""
import io
import json
from pathlib import Path
from datetime import date
from unittest.mock import patch
import pytest
from openpyxl import Workbook,load_workbook
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect,execute,transaction,fetch_one
from app.services import credit_source_imports as sources
from app.services import credit_source_exports as exports
from app.services import learning_credits as credits
from app.services import credit_settlement_batches as batches
from app.services import attendance_scoring
from app.services import learning_activity_credits as activity
from credit_batch_test_support import create_credit_batch_reviewer
from test_learning_activity_credits import _fixture,_calendar,_admin_id

DAY='2026-10-09'
NOTE='学习条线已核对本次原始记录，按现有规则计分'

@pytest.fixture
def source_fixture(monkeypatch):
    for key in ('LEARNING_CREDIT_BATCH_DRY_RUN_ENABLED','LEARNING_CREDIT_BATCH_APPROVAL_ENABLED','LEARNING_CREDIT_BATCH_POST_ENABLED','LEARNING_CREDIT_SETTLEMENT_ENABLED'):monkeypatch.setenv(key,'true')
    monkeypatch.setenv('LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED','false')
    fixture=_fixture();fixture['actor']=_admin_id();fixture['reviewer']=create_credit_batch_reviewer()
    _calendar(fixture,year=2026,day_types={DAY:'NORMAL_WORKDAY','2026-10-10':'WEEKEND'})
    c=connect()
    try:member=dict(execute(c,'SELECT member_code,name FROM members WHERE id=?',(fixture['member_id'],)).fetchone());fixture.update(member)
    finally:c.close()
    with transaction() as c:
        now=credits._db_timestamp(c)
        for index in range(1,37):
            plan=execute(c,"INSERT INTO learning_plan_cycles(plan_version_id,cohort_month,cycle_index,year_index,cycle_label,created_at,updated_at) VALUES (?,1,?,?,?, ?,?)",(fixture['plan_id'],index,(index-1)//12+1,'测试学习日'+str(index),now,now)).lastrowid
            if index==1:
                fixture['cycle_id']=int(execute(c,"INSERT INTO class_learning_cycles(binding_id,class_org_unit_id,learning_cycle_index,plan_cycle_id,opened_at,class_meeting_status,group_meeting_policy,cycle_status,created_at,updated_at) VALUES (?,?,1,?,'2026-10-01','PLANNED','REQUIRED','OPEN',?,?)",(fixture['binding_id'],fixture['class_id'],plan,now,now)).lastrowid)
    user=credits.user_context(fixture['actor']);permissions=set(user['permissions'])|{'plans:period_write','attendance:adjudicate','plans:credit_activity_fact_manage','plans:credit_settlement_manage','plans:credit_settlement_preview','plans:credit_settlement_post','plans:credit_settlement_approve','exports:normal','members:read'}
    def principal(actor):return {'id':actor,'roles':['system_admin'],'permissions':sorted(permissions)}
    with patch.object(credits,'user_context',side_effect=principal),patch.object(credits,'accessible_org_ids',return_value=None):yield fixture


def workbook(f,kind,values=None):
    wb=Workbook();s=wb.active;s.title=sources.SHEETS[kind];s.append(sources.HEADERS[kind]);identity=[f['member_code'],f['name']]
    if kind=='CLASS_MEETING':s.append(identity+(values or ['迟到','正常到场','早退']))
    elif kind=='DAILY_READING':s.append(identity+(values or [date.fromisoformat(DAY),'是']))
    else:s.append(identity+(values or [date.fromisoformat(DAY),'share-test-1','测试优秀分享','是']))
    out=io.BytesIO();wb.save(out);return out.getvalue()


def options(f,kind):return json.dumps({'cycle_id':f['cycle_id'],'actual_start':DAY+'T09:00:00+08:00'}) if kind=='CLASS_MEETING' else '{}'


def registered(f,kind,data=None):
    data=data or workbook(f,kind)
    p=sources.preview(data,kind,f['class_id'],options(f,kind),NOTE,f['actor'])
    assert p['error_count']==0,p['rows']
    receipt=sources.register(data,kind,f['class_id'],options(f,kind),NOTE,'测试原文件.xlsx',p['fingerprint'],f['actor'])
    return receipt['id'],p,data


@pytest.mark.parametrize('kind',['CLASS_MEETING','DAILY_READING','EXCELLENT_SHARE'])
def test_sources_use_existing_rules_post_once_and_trace_file_rows(source_fixture,kind):
    f=source_fixture;iid,p,data=registered(f,kind)
    before=fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']
    state=sources.create_batches(iid,f['actor'])
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before
    assert len(state['batches'])==1 and DecimalText(state['batches'][0]['proposed_points']) in ('16.00','1.00','2.00')
    sources.process(iid,f['actor'],'submit')
    with pytest.raises(PermissionError,match='审批'):sources.process(iid,f['actor'],'approve')
    sources.process(iid,f['reviewer'],'approve');posted=sources.process(iid,f['actor'],'post')
    assert posted['batches'][0]['status']=='POSTED'
    assert sources.process(iid,f['actor'],'post')['batches'][0]['status']=='POSTED'
    assert sources.register(data,kind,f['class_id'],options(f,kind),NOTE,'重命名.xlsx',p['fingerprint'],f['actor'])['idempotent']
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries')['n']==before+1
    entries=credits.list_credit_entries(actor_user_id=f['actor'],member_id=f['member_id'])
    assert entries[0]['import_sources'][0]['original_filename']=='测试原文件.xlsx'
    assert entries[0]['import_sources'][0]['excel_row']==2
    if kind=='CLASS_MEETING':
        cycle=fetch_one('SELECT class_meeting_status,source_event_group_id FROM class_learning_cycles WHERE id=?',(f['cycle_id'],))
        assert cycle['class_meeting_status']=='HELD' and cycle['source_event_group_id']==posted['event_group_id']
        c=connect();records=execute(c,'SELECT r.* FROM attendance_records r JOIN attendance_sessions s ON s.id=r.attendance_session_id WHERE s.event_group_id=?',(posted['event_group_id'],)).fetchall();c.close()
        assert all(r['checked_at'] is None for r in records)
        attendance_scoring.recalculate_event_group(posted['event_group_id'])
        scores=attendance_scoring.member_scores(f['member_id'])
        assert sum(float(r['final_points']) for r in scores)==16
    result=exports.export_credits(f['actor'],f['class_id'],DAY,DAY)
    book=load_workbook(io.BytesIO(result),data_only=False)
    assert book.sheetnames==['学分汇总','新增学分明细','已导入历史年度']
    assert book['新增学分明细']['J2'].data_type=='s' and '测试原文件.xlsx' in book['新增学分明细']['J2'].value
    assert book['新增学分明细']['F2'].data_type=='n'
    assert book['新增学分明细']['E2'].is_date


def DecimalText(value):
    from decimal import Decimal
    return format(Decimal(str(value)),'.2f')


def test_reading_weekend_incomplete_and_invalid_rows_have_actionable_results(source_fixture):
    f=source_fixture
    p=sources.preview(workbook(f,'DAILY_READING',[date(2026,10,10),'是']),'DAILY_READING',f['class_id'],'{}',NOTE,f['actor'])
    assert p['error_count']==0 and p['proposed_points']=='0.00' and p['rows'][0]['credit_status']=='NO_CREDIT'
    p=sources.preview(workbook(f,'DAILY_READING',[date.fromisoformat(DAY),'否']),'DAILY_READING',f['class_id'],'{}',NOTE,f['actor'])
    assert p['proposed_points']=='0.00'
    p=sources.preview(workbook(f,'EXCELLENT_SHARE',[date.fromisoformat(DAY),'ref','普通分享','否']),'EXCELLENT_SHARE',f['class_id'],'{}',NOTE,f['actor'])
    assert p['error_count']==1 and p['fingerprint'] is None
    bad=workbook(f,'CLASS_MEETING',['正常到场',None,'缺席'])
    assert sources.preview(bad,'CLASS_MEETING',f['class_id'],options(f,'CLASS_MEETING'),NOTE,f['actor'])['error_count']==1
    assert TestClient(app).get('/api/v1/learning-credits/excel').status_code==401


def test_source_scope_permission_and_frozen_row_tampering_fail_before_credits(source_fixture):
    f=source_fixture;iid,p,data=registered(f,'DAILY_READING')
    with patch.object(credits,'_scope_allows',return_value=False):
        with pytest.raises(PermissionError):sources.get_import(iid,f['actor'])
        with pytest.raises(PermissionError):exports.export_credits(f['actor'],f['class_id'],DAY,DAY)
    with transaction() as c:execute(c,"UPDATE learning_credit_source_rows SET source_reference='tampered' WHERE import_id=?",(iid,))
    with pytest.raises(ValueError,match='来源'):sources.create_batches(iid,f['actor'])
    assert not fetch_one('SELECT batch_id FROM learning_credit_source_batches WHERE import_id=?',(iid,))


def test_monthly_share_cap_and_stable_reference_duplicates_are_preserved(source_fixture):
    f=source_fixture;wb=Workbook();s=wb.active;s.title=sources.SHEETS['EXCELLENT_SHARE'];s.append(sources.HEADERS['EXCELLENT_SHARE'])
    for index in range(7):s.append([f['member_code'],f['name'],date.fromisoformat(DAY),'reference-'+str(index),'测试分享','是'])
    raw=io.BytesIO();wb.save(raw)
    iid,p,data=registered(f,'EXCELLENT_SHARE',raw.getvalue())
    assert sum(r['credit_status']=='READY' for r in p['rows'])==5
    state=sources.create_batches(iid,f['actor'])
    assert state['batches'][0]['proposed_entry_count']==5
    sources.process(iid,f['actor'],'submit');sources.process(iid,f['reviewer'],'approve');sources.process(iid,f['actor'],'post')
    assert fetch_one("SELECT SUM(points) AS points FROM learning_credit_entries WHERE member_id=? AND credit_type='EXCELLENT_SHARE'",(f['member_id'],))['points']==5


def test_templates_prefill_exact_identity_and_preserve_validation(source_fixture):
    f=source_fixture
    for kind in sources.KINDS:
        book=load_workbook(io.BytesIO(sources.template(kind,f['class_id'],f['actor'])))
        s=book[sources.SHEETS[kind]]
        assert s['A2'].value==f['member_code'] and s['B2'].value==f['name']
        assert len(s.data_validations.dataValidation)>0 and s.freeze_panes=='C2'


def test_permission_specific_scope_cannot_be_widened_by_workbench_permission(source_fixture):
    f=source_fixture;raw=workbook(f,'DAILY_READING')
    p=sources.preview(raw,'DAILY_READING',f['class_id'],'{}',NOTE,f['actor'])
    def scopes(actor,permission=None):return [] if permission in {'plans:credit_activity_fact_manage','exports:normal'} else None
    with patch.object(credits,'accessible_org_ids',side_effect=scopes):
        with pytest.raises(PermissionError):sources.register(raw,'DAILY_READING',f['class_id'],'{}',NOTE,'scope.xlsx',p['fingerprint'],f['actor'])
        assert exports.export_options(f['actor'])['classes']==[]
        with pytest.raises(PermissionError):exports.export_credits(f['actor'],f['class_id'],DAY,DAY)
    assert not fetch_one('SELECT id FROM learning_credit_source_imports WHERE fingerprint=?',(p['fingerprint'],))


def test_filtered_reading_batches_exclude_unrelated_facts_and_reject_swapped_link(source_fixture):
    f=source_fixture;iid,_,_=registered(f,'DAILY_READING')
    unrelated=activity.record_learning_activity_fact(actor_user_id=f['actor'],activity_type='DAILY_READING',member_id=f['member_id'],class_org_unit_id=f['class_id'],occurred_on=DAY,source_type='OTHER_TEST',source_id='unrelated-source',participation_status='CONFIRMED',binding_id=f['binding_id'])
    result=sources.create_batches(iid,f['actor']);bid=result['batches'][0]['id']
    snapshot=json.loads(fetch_one('SELECT source_snapshot_json FROM learning_credit_settlement_batches WHERE id=?',(bid,))['source_snapshot_json'])
    assert unrelated['id'] not in snapshot['fact_ids_filter']
    broader=batches.dry_run_daily_reading_batch(actor_user_id=f['actor'],class_org_unit_id=f['class_id'],occurred_from=DAY,occurred_to=DAY)
    with transaction() as c:
        execute(c,'DELETE FROM learning_credit_source_batches WHERE import_id=?',(iid,))
        execute(c,'INSERT INTO learning_credit_source_batches(import_id,batch_id) VALUES (?,?)',(iid,broader['id']))
    with pytest.raises(ValueError,match='原文件'):sources.process(iid,f['actor'],'submit')


def test_source_date_and_formula_input_are_rejected_without_writing(source_fixture):
    f=source_fixture
    for values in ([date(2026,9,30),'是'],[date(2099,10,9),'是'],[date.fromisoformat(DAY),'=1+1']):
        p=sources.preview(workbook(f,'DAILY_READING',values),'DAILY_READING',f['class_id'],'{}',NOTE,f['actor'])
        assert p['error_count']==1 and p['fingerprint'] is None


def test_posted_meeting_reupload_skips_and_refuses_mutating_absent_slot(source_fixture):
    f=source_fixture;iid,_,_=registered(f,'CLASS_MEETING',workbook(f,'CLASS_MEETING',['正常到场','缺席','缺席']))
    sources.create_batches(iid,f['actor']);sources.process(iid,f['actor'],'submit');sources.process(iid,f['reviewer'],'approve');sources.process(iid,f['actor'],'post')
    p=sources.preview(workbook(f,'CLASS_MEETING',['正常到场','缺席','缺席']),'CLASS_MEETING',f['class_id'],options(f,'CLASS_MEETING'),NOTE,f['actor'])
    assert p['error_count']==0 and p['proposed_points']=='0.00'
    p=sources.preview(workbook(f,'CLASS_MEETING',['正常到场','正常到场','缺席']),'CLASS_MEETING',f['class_id'],options(f,'CLASS_MEETING'),NOTE,f['actor'])
    assert p['error_count']==1 and '已经入账' in ' '.join(p['rows'][0]['errors'])


def test_closed_month_class_confirmation_preserves_current_month(source_fixture,monkeypatch):
    f=source_fixture
    monkeypatch.setenv('LEARNING_CYCLE_MONTHLY_REFRESH_ENABLED','true')
    from app.services import learning_cycles,learning_cycle_monthly
    with transaction() as c:
        execute(c,"UPDATE class_learning_cycles SET cycle_status='CLOSED',closed_at='2026-10-10' WHERE id=?",(f['cycle_id'],))
    iid,_,_=registered(f,'CLASS_MEETING')
    # Exercise historical confirmation itself; calendar refresh is covered by
    # its own suite and must not advance a current month during this operation.
    with patch.object(learning_cycle_monthly,'refresh_in_connection',return_value=None),patch.object(learning_cycles,'_cycle_at',return_value=None):
        sources.create_batches(iid,f['actor'])
    cycle=fetch_one('SELECT cycle_status,closed_at,class_meeting_status FROM class_learning_cycles WHERE id=?',(f['cycle_id'],))
    assert cycle['cycle_status']=='CLOSED' and str(cycle['closed_at'])[:10]=='2026-10-10' and cycle['class_meeting_status']=='HELD'


def test_offline_early_leave_cancellation_keeps_source_and_updates_proposal(source_fixture):
    from app.api.attendance import create_adjudication,AdjudicationPayload
    f=source_fixture;iid,_,_=registered(f,'CLASS_MEETING')
    receipt=sources.get_import(iid,f['actor']);slot=next(r for r in receipt['rows'] if r['source_reference']=='KONPA')
    create_adjudication(slot['attendance_record_id'],AdjudicationPayload(adjudication_type='CANCEL_EARLY_LEAVE',reason='专职人员复核原表后撤销早退'),{'id':f['actor']})
    assert json.loads(sources.get_import(iid,f['actor'])['rows'][-1]['record_json'])['state']=='早退'
    score=fetch_one('SELECT is_early_leave,final_points FROM attendance_score_records WHERE attendance_record_id=?',(slot['attendance_record_id'],))
    assert not score['is_early_leave'] and DecimalText(score['final_points'])=='4.00'
    assert DecimalText(sources.create_batches(iid,f['actor'])['batches'][0]['proposed_points'])=='17.00'


def test_calendar_preparation_preserves_existing_published_policy(source_fixture):
    f=source_fixture;before=activity.get_business_calendar(calendar_year=2026)
    result=sources.prepare_reading_calendar(f['actor'])
    assert result['idempotent'] and result['reading_calendar']['status']=='PUBLISHED'
    assert activity.get_business_calendar(calendar_year=2026)==before
    canonical=Path(__file__).resolve().parents[3]/'data/learning-calendars/china-mainland-2026.json'
    asset=Path(sources.__file__).resolve().parents[1]/'assets/credit-reading-calendar-2026.json'
    assert canonical.read_bytes()==asset.read_bytes()
