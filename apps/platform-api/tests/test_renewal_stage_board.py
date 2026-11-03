from unittest.mock import patch
from datetime import date
from app.services import renewals as r


def test_november_includes_next_year_missing_cycles_without_writes():
    base={'member_id':1,'id':7,'stage':{'code':'CLOSED'}}
    coverage={'rows':[{'member_id':1,'member_name':'合成学员','member_status':'ACTIVE','due_month':1,'can_create_cycle':True}], 'truncated':False}
    with patch.object(r,'business_today',return_value=date(2026,11,3)),patch.object(r,'list_cycles',side_effect=[[base],[base],[]]),patch.object(r,'list_cycle_coverage',side_effect=[{'rows':[],'truncated':False},coverage]):
        rows=r.list_stage_board(1,2026)
    assert len(rows)==1 and rows[0]['stage']['code']=='RENEW_2'
    assert rows[0]['renewal_year']==2027 and rows[0]['is_projection']


def test_pending_current_cycle_is_not_replaced_by_future_projection():
    base={'member_id':1,'id':7,'stage':{'code':'RECOVERY'}}
    item={'member_id':1,'member_status':'ACTIVE','due_month':2,'can_create_cycle':True}
    with patch.object(r,'business_today',return_value=date(2026,11,3)),patch.object(r,'list_cycles',side_effect=[[base],[base],[]]),patch.object(r,'list_cycle_coverage',return_value={'rows':[item],'truncated':False}):
        assert r.list_stage_board(1,2026)==[base]
