from unittest.mock import patch
import pytest
from app.services import konpa_review as service
from app.api.wechat_checkin import ConfirmPayload,GuestConfirmPayload
from pydantic import ValidationError


def test_scopes_follow_five_formal_posts_and_never_expand_counselor_to_class():
    roles=[{'position_key':'volunteer_group_counselor','org_unit_id':'g'},
           {'position_key':'volunteer_class_monitor','org_unit_id':'c2'},
           {'position_key':'volunteer_director','org_unit_id':'center'}]
    with patch.object(service,'get_member_role_scopes',return_value=roles),patch.object(service,'fetch_one',side_effect=lambda sql,args:{'id':args[0],'parent_id':'c1','is_active':1}):
        scopes=service.scopes(1)
    assert scopes['c1']=={'manage':False,'groups':{'g'}}
    assert scopes['c2']['manage'] is True
    assert 'center' not in scopes


def test_write_in_other_class_does_not_inherit_manager_post():
    allowed={'c1':{'manage':False,'groups':{'g'}},'c2':{'manage':True,'groups':set()}}
    with patch.object(service,'scopes',return_value=allowed),patch.object(service,'event_list',return_value={'events':[{'event_id':'evening','class_org_unit_id':'c1'}]}),patch.object(service.signin_engine,'engine_request') as transport:
        with pytest.raises(PermissionError):service.request(1,'evening','confirm',registration_id='other',expected_revision='initial')
        transport.assert_not_called()


def test_group_read_uses_only_live_scoped_member_ids():
    allowed={'c1':{'manage':False,'groups':{'g'}}}
    with patch.object(service,'scopes',return_value=allowed),patch.object(service,'event_list',return_value={'events':[{'event_id':'evening','class_org_unit_id':'c1'}]}),patch.object(service,'group_roster_groups',return_value=[{'member_id':123}]),patch.object(service.signin_engine,'enabled'),patch.object(service.signin_engine,'engine_request',return_value={'rows':[]}) as transport:
        service.request(1,'evening','list')
        actor=transport.call_args.args[1]['actor']
        assert actor=={'id':'1','classes':['c1'],'members':['123'],'manage':False}


def test_choice_models_preserve_false_and_reject_string_boolean():
    assert ConfirmPayload(event_id='e',konpa_choice=False).konpa_choice is False
    for cls,extra in [(ConfirmPayload,{}),(GuestConfirmPayload,{'name':'合成学员','wx_login_code':'code'})]:
        with pytest.raises(ValidationError):cls(event_id='e',konpa_choice='false',**extra)
