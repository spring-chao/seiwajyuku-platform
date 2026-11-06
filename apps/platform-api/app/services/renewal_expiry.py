"""Idempotent end-of-month lapse of explicitly unrenewed member cycles."""
import json
import sqlite3
from datetime import date
from datetime import UTC, datetime
from app.core.business_time import business_today
from app.core.settings import get_settings
from app.db import fetch_all, execute, transaction
from app.services.audit import write_audit
from app.services.renewals import _member_renewal_schedule


def eligible(row, today):
    schedule = _member_renewal_schedule(row['renewal_month'])
    if not schedule or row['member_status'] != 'ACTIVE': return False
    year, month = int(row['renewal_year']), int(row['due_month'])
    target_year = today.year if month < today.month else today.year - 1
    return (year == target_year and year >= schedule[0] and month == schedule[1]
            and (year,month) >= (2026,10)
            and row['cycle_status'] in {'PENDING_FIRST_CONTACT','CONTACTED_WAITING_REPLY','IN_COMMUNICATION','NOT_RENEWING','EXITED'})


def sync_expired_members(*, dry_run=False):
    settings=get_settings()
    result={'enabled':not settings.deployment_read_only and settings.allow_production_mutations,
            'candidates':0,'updated':0,'failed':0,'dry_run':dry_run}
    if not result['enabled']:return result
    today=business_today(datetime.now(UTC));now=datetime.now(UTC).isoformat()
    query=("SELECT m.id AS member_id,m.status AS member_status,m.org_unit_id,m.renewal_month,"
           "c.id AS cycle_id,c.renewal_year,c.due_month,c.status AS cycle_status "
           "FROM members m JOIN renewal_cycles c ON c.member_id=m.id WHERE m.status='ACTIVE' "
           "AND c.renewal_year>=2026 AND c.renewal_year<=? "
           "AND NOT EXISTS (SELECT 1 FROM renewal_cycles paid WHERE paid.member_id=m.id "
           "AND paid.status='RENEWED' AND paid.renewal_year>=c.renewal_year)")
    rows=[r for r in fetch_all(query,(today.year,)) if eligible(r,today)]
    result['candidates']=len(rows)
    if dry_run:return result
    for candidate in rows[:100]:
        try:
            with transaction() as c:
                # Recheck the cycle and master under row locks against concurrent
                # payment confirmation; no decision is made from names or notes.
                locked=execute(c,query+' AND c.id=?'+('' if isinstance(c,sqlite3.Connection) else ' FOR UPDATE'),(today.year,candidate['cycle_id'])).fetchone()
                if not locked or not eligible(dict(locked),today):continue
                row=dict(locked)
                changed=execute(c,"UPDATE members SET status='INACTIVE',updated_at=? WHERE id=? AND status='ACTIVE'",(now,row['member_id'])).rowcount
                if not changed:continue
                year,month=int(row['renewal_year']),int(row['due_month'])
                effective=date(year+1,1,1) if month==12 else date(year,month+1,1)
                before={'status':'ACTIVE'};after={'status':'INACTIVE','source':'RENEWAL_MONTH_EXPIRED','renewal_cycle_id':row['cycle_id'],'effective_on':effective.isoformat()}
                execute(c,"INSERT INTO member_change_history(member_id,change_type,before_json,after_json,changed_by,changed_at) VALUES(?,'RENEWAL_EXPIRY',?,?,NULL,?)",(row['member_id'],json.dumps(before),json.dumps(after),now))
                write_audit(c,actor_user_id=None,action='members.status.expired_renewal',resource_type='member',resource_id=str(row['member_id']),org_unit_id=row['org_unit_id'],before=before,after=after)
            result['updated']+=1
        except Exception:
            result['failed']+=1
    return result
