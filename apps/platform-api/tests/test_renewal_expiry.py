from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from app.db import execute,fetch_one,transaction
from app.services import renewal_expiry as expiry
from app.services.renewals import update_cycle
from test_renewals import _action_card_fixture


@pytest.mark.parametrize('status,member_status,expected',[
    ('IN_COMMUNICATION','ACTIVE',True),('RENEWED','ACTIVE',False),('DEFERRED','ACTIVE',False),
    ('IN_COMMUNICATION','SUSPENDED',False),('IN_COMMUNICATION','INACTIVE',False)])
def test_lapse_policy(status,member_status,expected):
    row=dict(renewal_month='2026-10',renewal_year=2026,due_month=10,member_status=member_status,cycle_status=status)
    assert expiry.eligible(row,date(2026,11,1))==expected
    assert not expiry.eligible(row,date(2026,10,31))


def test_lapse_is_audited_idempotent_and_explicit_renewal_reactivates():
    cycle,actor,_,member=_action_card_fixture(with_memory=False)
    with transaction() as c:
        execute(c,"UPDATE members SET renewal_month='2026-10' WHERE id=?",(member,))
        execute(c,"UPDATE renewal_cycles SET renewal_year=2026,due_month=10 WHERE id=?",(cycle,))
    with patch.object(expiry,'get_settings',return_value=SimpleNamespace(deployment_read_only=False,allow_production_mutations=True)),patch.object(expiry,'business_today',return_value=date(2026,11,1)):
        assert expiry.sync_expired_members(dry_run=True)['updated']==0
        expiry.sync_expired_members()
        assert fetch_one('SELECT status FROM members WHERE id=?',(member,))['status']=='INACTIVE'
        assert expiry.sync_expired_members()['updated']==0
    assert fetch_one("SELECT COUNT(*) n FROM member_change_history WHERE member_id=? AND change_type='RENEWAL_EXPIRY'",(member,))['n']==1
    update_cycle(cycle,actor,status='RENEWED')
    assert fetch_one('SELECT status FROM members WHERE id=?',(member,))['status']=='ACTIVE'
