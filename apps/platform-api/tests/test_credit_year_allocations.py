"""Exercise classification and rollover unchanged on SQLite and CI MySQL."""
import io
import json
import zipfile
from datetime import datetime,UTC
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect,execute,transaction,fetch_one
from app.services import credit_year_allocations as service
from app.services import credit_opening_balances as opening
from app.services.wechat_credit_summary import get_member_credit_summary
from test_credit_opening_balances import source,submitted


@pytest.fixture
def annual(source):
    import_id,fingerprint,_=submitted(source)
    opening.action(import_id,source['actor'],'approve',fingerprint)
    opening.action(import_id,source['actor'],'post',fingerprint)
    with transaction() as c:
        now=opening._db_timestamp(c)
        plan=execute(c,"INSERT INTO learning_plan_versions(plan_key,plan_name,version_label,duration_cycles,status,created_at,updated_at) VALUES (?,?,'annual-test',36,'PUBLISHED',?,?)",('annual-test-'+uuid4().hex[:12],'年度测试计划',now,now)).lastrowid
        binding=execute(c,"INSERT INTO class_learning_bindings(class_org_unit_id,plan_version_id,started_at,status,created_at,updated_at) VALUES (?,?,'2001-01-01','ACTIVE',?,?)",(source['org'],plan,now,now)).lastrowid
        for member,_,_ in source['ids']:
            execute(c,"INSERT INTO member_org_relations(member_id,org_unit_id,relation_type,created_at,updated_at) VALUES (?,?,'STUDY_CLASS',?,?)",(member,source['org'],now,now))
    return {**source,'binding_id':int(binding),'opening_import_id':import_id}


def content(annual,points=('12.25','20'),name=None):
    book=Workbook();s=book.active;s.title='第2学年学分统计'
    s['A1']='期初测试班第二学年统计';s['B2']='姓名';s['D3']='总分值'
    for i,((_,_,member),value) in enumerate(zip(annual['ids'],points),5):
        s.cell(i,1,i-4);s.cell(i,2,name or member);s.cell(i,3,'测试组');s.cell(i,4,value)
    s['B7']='合计';s['A7']=3;s['D7']='参考平均分'
    stream=io.BytesIO();book.save(stream);return stream.getvalue()


def inspect(annual,data=None,note='第二学年12次学习日已完成，原8月实际为9月'):
    return service.preview(data or content(annual),annual['binding_id'],2,'2001-08-31',note,annual['actor'])


def registered(annual):
    data=content(annual);state=inspect(annual,data)
    assert state['error_count']==0 and state['total_points']=='32.25'
    created=service.register(data,annual['binding_id'],2,'2001-08-31',state['source_note'],'第二学年.xlsx',state['fingerprint'],annual['actor'])
    return created['id'],state['fingerprint'],data


def test_annual_roundtrip_preserves_balances_and_starts_year_three_zero(annual):
    import_id,fingerprint,data=registered(annual)
    with pytest.raises(ValueError,match='先核对'):
        service.action(import_id,annual['actor'],'post',fingerprint)
    assert service.action(import_id,annual['actor'],'approve',fingerprint)['status']=='APPROVED'
    assert service.action(import_id,annual['actor'],'post',fingerprint)['status']=='POSTED'
    assert service.action(import_id,annual['actor'],'post',fingerprint)['idempotent']
    assert service.register(data,annual['binding_id'],2,'2001-08-31','第二学年12次学习日已完成，原8月实际为9月','改名.xlsx',fingerprint,annual['actor'])['idempotent']
    assert inspect(annual)['error_count']==2
    member=annual['ids'][0][0]
    result=get_member_credit_summary(member)
    assert result['total_points']=='12.25'
    assert result['current_learning_year']==3 and result['current_learning_year_completed_days']==0
    assert result['current_learning_year_points']=='0.00'
    assert {r['year_index']:r['points'] for r in result['learning_years']}=={1:None,2:'12.25',3:'0.00'}
    assert not result['has_unallocated_learning_year_credits']
    assert fetch_one('SELECT points FROM learning_credit_opening_balances WHERE member_id=?',(member,))['points']==Decimal('12.25')
    assert fetch_one('SELECT COUNT(*) AS n FROM learning_credit_entries WHERE member_id=?',(member,))['n']==0


def test_annual_rechecks_mutation_scope_and_progress_without_partial_assignment(annual):
    import_id,fingerprint,_=registered(annual)
    with transaction() as c:
        execute(c,'UPDATE learning_credit_year_rows SET points=100 WHERE import_id=?',(import_id,))
    with pytest.raises(ValueError,match='超过余额'):
        service.action(import_id,annual['actor'],'approve',fingerprint)
    assert not fetch_one('SELECT member_id FROM learning_credit_year_allocations WHERE import_id=?',(import_id,))
    with patch.object(opening,'accessible_org_ids',return_value=set()):
        with pytest.raises(PermissionError):service.action(import_id,annual['actor'],'approve',fingerprint)
    assert TestClient(app).get('/api/v1/learning-credits/year-allocations').status_code==401


def test_annual_wrong_class_total_cutoff_and_duplicate_names_are_rejected(annual):
    assert inspect(annual,content(annual,('999','20')))['error_count']==1
    with pytest.raises(ValueError,match='重复'):
        inspect(annual,content(annual,name='相同姓名'))
    result=service.preview(content(annual),annual['binding_id'],2,'2001-09-30','第二学年12次学习日已经完成',annual['actor'])
    assert result['error_count']==2 and result['fingerprint'] is None
    with patch.object(opening,'accessible_org_ids',return_value=set()):
        with pytest.raises(PermissionError):inspect(annual)


def test_annual_missing_first_year_and_unallocated_remainder_are_preserved(annual):
    data=content(annual,('10','20'));state=inspect(annual,data)
    created=service.register(data,annual['binding_id'],2,'2001-08-31',state['source_note'],'第二学年.xlsx',state['fingerprint'],annual['actor'])
    service.action(created['id'],annual['actor'],'approve',state['fingerprint']);service.action(created['id'],annual['actor'],'post',state['fingerprint'])
    result=get_member_credit_summary(annual['ids'][0][0])
    assert result['total_points']=='12.25' and result['unallocated_learning_year_points']=='2.25'
    assert result['has_unallocated_learning_year_credits']


def test_original_formula_cache_is_independently_checked_without_styles(annual):
    data=content(annual)
    # Inject a SUM/ref chain and invalid irrelevant fill in a synthetic file.
    with zipfile.ZipFile(io.BytesIO(data)) as source_zip:
        files={name:source_zip.read(name) for name in source_zip.namelist()}
    xml=files['xl/worksheets/sheet1.xml'].decode()
    xml=xml.replace('<c r="D5" t="inlineStr"><is><t>12.25</t></is></c>','<c r="D5"><f>=E5</f><v>12.25</v></c><c r="E5"><f>=13-0.75</f><v>12.25</v></c>')
    assert '<f>=E5</f>' in xml
    files['xl/worksheets/sheet1.xml']=xml.encode();files['xl/styles.xml']=b'irrelevant invalid style data'
    def zipped():
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for name,value in files.items():z.writestr(name,value)
        return stream.getvalue()
    assert service.parse_source(zipped(),2)[1][0]['points']=='12.25'
    files['xl/worksheets/sheet1.xml']=xml.replace('<f>=13-0.75</f><v>12.25</v>','<f>=13-0.75</f><v>99</v>').encode()
    with pytest.raises(ValueError,match='缓存'):
        service.parse_source(zipped(),2)


def test_real_learning_days_after_history_advance_without_counting_old_days_again(annual):
    import_id,fingerprint,_=registered(annual)
    service.action(import_id,annual['actor'],'approve',fingerprint)
    service.action(import_id,annual['actor'],'post',fingerprint)
    from app.services.learning_credits import _insert_entry
    with transaction() as c:
        binding=execute(c,'SELECT * FROM class_learning_bindings WHERE id=?',(annual['binding_id'],)).fetchone()
        now=opening._db_timestamp(c)
        cycles=[]
        for index,opened,actual in [(100,'2001-08-01','2001-08-20'),(101,'2001-09-01','2001-09-10')]:
            plan=execute(c,"INSERT INTO learning_plan_cycles(plan_version_id,cohort_month,cycle_index,year_index,cycle_label,created_at,updated_at) VALUES (?,1,?,1,'synthetic',?,?)",(binding['plan_version_id'],index,now,now)).lastrowid
            cycles.append(execute(c,"INSERT INTO class_learning_cycles(binding_id,class_org_unit_id,learning_cycle_index,plan_cycle_id,opened_at,class_meeting_status,actual_class_meeting_at,group_meeting_policy,cycle_status,created_at,updated_at) VALUES (?,?,?,?,?,'HELD',?,'REQUIRED','OPEN',?,?)",(binding['id'],annual['org'],index,plan,opened,actual,now,now)).lastrowid)
        _insert_entry(c,{'member_id':annual['ids'][0][0],'credit_category':'STANDARD_LEARNING','credit_type':'CLASS_MEETING_SCORE','points':'2.75','source_type':'TEST','source_id':uuid4().hex,'class_org_unit_id':annual['org'],'learning_cycle_id':cycles[1],'rule_key':'TEST','rule_version':'TEST','rule_snapshot':{},'occurred_at':'2001-09-10','idempotency_key':uuid4().hex},status='POSTED',actor_user_id=None)
    result=get_member_credit_summary(annual['ids'][0][0])
    assert result['current_learning_year']==3 and result['current_learning_year_completed_days']==1
    assert result['current_learning_year_points']=='2.75' and result['total_points']=='15.00'
    assert {r['year_index']:r['points'] for r in result['learning_years']}[2]=='12.25'
