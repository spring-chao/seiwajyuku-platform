from unittest.mock import patch
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app.services.learning_resources import resource_catalog, month_resources, committee_resources, COMMITTEE_ROLES
from app.api.wechat import learning_resources

def test_month_mapping_and_operations_separation():
    assert len(resource_catalog()['years']) == 3
    for m in (11,12):
        month = month_resources(3,m)
        assert all(not month[k] for k in ('classQrs','groupQrs','extraQrs'))
    for role in COMMITTEE_ROLES:
        operations = committee_resources({'roles':[{'position_name':role}]})
        assert len(operations['videos']) == 9
        assert operations['videos'][0]['collageOriginalData'].startswith('data:image/')
    assert 'committeeVideos' not in resource_catalog()
    assert sum(m['qrCount'] for y in resource_catalog()['years'] for m in y['months']) == 68

@pytest.mark.parametrize('services', [{}, {'roles':[{'position_name':'理事志工'}]}, {'roles':[{'position_name':'班主任'}],'needs_manual_review':True}])
def test_committee_denies_non_members_of_role_allowlist(services):
    with pytest.raises(HTTPException) as error:
        committee_resources(services)
    assert error.value.status_code == 403

def test_endpoint_uses_resolved_member_and_rechecks_effective_roles():
    credentials=HTTPAuthorizationCredentials(scheme='Bearer',credentials='synthetic-session')
    with patch('app.api.wechat._session_token',return_value='synthetic-session'), patch('app.api.wechat.resolve_member_session',return_value={'member_id':42}), patch('app.api.wechat.get_member_volunteer_services',return_value={'roles':[]}) as roles:
        with pytest.raises(HTTPException) as error:
            learning_resources(year=None,month=None,committee=True,credentials=credentials)
        roles.assert_called_once_with(42)
        assert error.value.status_code==403
        assert learning_resources(year=3,month=11,committee=False,credentials=credentials)['data']['groupQrs']==[]

def test_endpoint_requires_session():
    with patch('app.api.wechat._ensure_enabled'):
        with pytest.raises(HTTPException) as error:
            learning_resources(year=None,month=None,committee=False,credentials=None)
        assert error.value.status_code==401
