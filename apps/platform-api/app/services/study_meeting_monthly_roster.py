"""Monthly report attribution. Source attendance, IAM and credit facts are immutable here."""
from __future__ import annotations

import sqlite3
from calendar import monthrange
from datetime import UTC, datetime, timedelta, timezone

from app.db import execute, transaction

BJT = timezone(timedelta(hours=8))

def clock(at=None):
    value = at or datetime.now(UTC)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BJT)

def due_at(period):
    first = datetime.fromisoformat(period + "-01").replace(tzinfo=BJT)
    return first.replace(day=min(30, monthrange(first.year, first.month)[1]))

def stamp(at):
    return clock(at).astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")

def available(connection):
    if isinstance(connection, sqlite3.Connection):
        return execute(connection, "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name IN ('study_meeting_roster_months','study_meeting_roster_members','study_meeting_roster_groups')").fetchone()["n"] == 3
    return execute(connection, "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ('study_meeting_roster_months','study_meeting_roster_members','study_meeting_roster_groups')").fetchone()["n"] == 3

def _month(connection, at, *, create=False, lock=False):
    period = clock(at).strftime("%Y-%m")
    if create:
        verb = "INSERT OR IGNORE" if isinstance(connection, sqlite3.Connection) else "INSERT IGNORE"
        execute(connection, verb + " INTO study_meeting_roster_months(period,freeze_due_at,state,created_at,updated_at) VALUES (?,?,'OPEN',?,?)", (period, stamp(due_at(period)), stamp(at), stamp(at)))
    suffix = " FOR UPDATE" if lock and not isinstance(connection, sqlite3.Connection) else ""
    row = execute(connection, "SELECT * FROM study_meeting_roster_months WHERE period=?" + suffix, (period,)).fetchone()
    return dict(row) if row else None

def _current(connection, ids=None):
    from app.services.study_membership import group_roster
    return {int(r['member_id']): r for r in group_roster(connection, member_ids=ids, include_ungrouped=True)}

def _existing(connection, period, ids=None):
    if ids is not None and not ids:return {}
    clause = " AND r.member_id IN (" + ",".join("?" for _ in ids) + ")" if ids is not None else ""
    rows=execute(connection,
        "SELECT r.*,m.name,m.member_code,m.phone_masked,c.name AS current_class_name,g.name AS current_group_name FROM study_meeting_roster_members r JOIN members m ON m.id=r.member_id LEFT JOIN org_units c ON c.id=r.class_org_unit_id LEFT JOIN org_units g ON g.id=r.group_org_unit_id WHERE r.period=?" + clause,(period,*(ids or []))).fetchall()
    return {int(r['member_id']): {**dict(r),'class_name':r['current_class_name'] or r['class_name'],'group_name':r['current_group_name'] or r['group_name']} for r in rows}

def _source_origins(connection, period, ids=None):
    """An existing monthly fact proves the original class when bootstrapping.

    Only a canonical home group under that fact's class is admissible; a name
    or a current new class never manufactures an old membership.
    """
    if ids is not None and not ids:return {}
    clause = " AND a.member_id IN (" + ",".join("?" for _ in ids) + ")" if ids is not None else ""
    rows = execute(connection,
        "SELECT a.member_id,s.class_org_unit_id,a.home_study_group_org_unit_id AS group_org_unit_id,c.name AS class_name,g.name AS group_name "
        "FROM study_meeting_attendances a JOIN study_meeting_sessions s ON s.id=a.study_meeting_session_id "
        "JOIN org_units c ON c.id=s.class_org_unit_id JOIN org_units g ON g.id=a.home_study_group_org_unit_id AND g.parent_id=c.id "
        "WHERE substr(s.meeting_date,1,7)=? AND s.status='SUBMITTED'" + clause + " ORDER BY s.meeting_date,s.id,a.id", (period,*(ids or []))).fetchall()
    result = {}
    for row in rows:
        result.setdefault(int(row['member_id']), dict(row))
    return result

def _projection(connection, period, current, previous, ids=None):
    origins = _source_origins(connection, period, ids=ids)
    result = {}
    for member_id in sorted(set(current) | set(previous)):
        before, now = previous.get(member_id), current.get(member_id)
        if not before:
            origin = origins.get(member_id)
            before = origin if origin and now and origin['class_org_unit_id'] != now['class_org_unit_id'] else None
        if before and now and before['class_org_unit_id'] != now['class_org_unit_id']:
            result[member_id] = {**now, **before, 'member_id':member_id, 'included':1, 'cross_class_preserved':1}
        elif now:
            result[member_id] = {**now, 'included':1, 'cross_class_preserved':0}
        elif before:
            result[member_id] = {**before, 'included':0, 'cross_class_preserved':before.get('cross_class_preserved',0)}
    return result

def _store_member(connection, period, row, at):
    values = (row['class_org_unit_id'],row.get('group_org_unit_id'),row['class_name'],row.get('group_name'),row.get('included',1),row.get('cross_class_preserved',0),stamp(at),period,int(row['member_id']))
    exists = execute(connection,"SELECT 1 FROM study_meeting_roster_members WHERE period=? AND member_id=?", (period,int(row['member_id']))).fetchone()
    if exists:
        execute(connection,"UPDATE study_meeting_roster_members SET class_org_unit_id=?,group_org_unit_id=?,class_name=?,group_name=?,included=?,cross_class_preserved=?,updated_at=? WHERE period=? AND member_id=?", values)
    else:
        execute(connection,"INSERT INTO study_meeting_roster_members(class_org_unit_id,group_org_unit_id,class_name,group_name,included,cross_class_preserved,updated_at,period,member_id) VALUES (?,?,?,?,?,?,?,?,?)", values)

def _refresh(connection, month, at):
    period = month['period']
    rows = _projection(connection, period, _current(connection), _existing(connection, period))
    for row in rows.values():
        _store_member(connection, period, row, at)
    groups = execute(connection,
        "SELECT g.id AS group_org_unit_id,g.parent_id AS class_org_unit_id,g.name AS group_name,c.name AS class_name FROM org_units g "
        "JOIN org_units c ON c.id=g.parent_id WHERE g.unit_type='GROUP' AND g.is_active=1 AND c.is_active=1 AND c.unit_type IN ('CLASS','SPECIAL_COHORT')").fetchall()
    # OPEN group catalog follows the organization master; the final catalog is
    # frozen together with membership. It includes zero-member required groups.
    execute(connection,"DELETE FROM study_meeting_roster_groups WHERE period=?", (period,))
    for row in groups:
        execute(connection,"INSERT INTO study_meeting_roster_groups(period,group_org_unit_id,class_org_unit_id,class_name,group_name) VALUES (?,?,?,?,?)", (period,row['group_org_unit_id'],row['class_org_unit_id'],row['class_name'],row['group_name']))
    frozen = clock(at) >= due_at(period)
    execute(connection,"UPDATE study_meeting_roster_months SET state=?,frozen_at=?,updated_at=? WHERE period=?", ('FROZEN' if frozen else 'OPEN',stamp(at) if frozen else None,stamp(at),period))
    return {'period':period,'state':'FROZEN' if frozen else 'OPEN','members':len(rows),'preserved':sum(r.get('cross_class_preserved',0) for r in rows.values()),'groups':len(groups)}

def capture_before_change(connection, member_id, *, at=None):
    """Called inside an already-authorized learner edit, before relation changes."""
    from app.core.settings import get_settings
    settings=get_settings()
    if not settings.study_meeting_monthly_roster_enabled or settings.deployment_read_only or (settings.is_production and not settings.allow_production_mutations) or not available(connection):
        return
    at = clock(at)
    month = _month(connection, at, create=True, lock=True)
    if month['state'] == 'FROZEN':
        return
    if at >= due_at(month['period']):
        _refresh(connection, month, at)
        return
    current = _current(connection, [int(member_id)])
    if not current:
        return
    old = execute(connection,"SELECT * FROM study_meeting_roster_members WHERE period=? AND member_id=?",(month['period'],int(member_id))).fetchone()
    row = current[int(member_id)]
    if old and old['class_org_unit_id'] != row['class_org_unit_id']:
        return
    _store_member(connection, month['period'], row, at)

def roster_for_period(connection, period, *, class_ids=None):
    """Read-only report snapshot; frozen attribution never follows later edits."""
    scope = None if class_ids is None else sorted(set(class_ids))
    if scope is not None and not scope:return [], {'source':'EMPTY','state':'OPEN'}
    ids = None
    if scope:
        from app.services.study_membership import CURRENT_CLASS_ID_SQL
        marks=','.join('?' for _ in scope)
        selected={int(r['id']) for r in execute(connection,f"SELECT m.id FROM members m WHERE m.status='ACTIVE' AND {CURRENT_CLASS_ID_SQL} IN ({marks})",tuple(scope)).fetchall()}
        if available(connection):
            selected.update(int(r['member_id']) for r in execute(connection,f"SELECT member_id FROM study_meeting_roster_members WHERE period=? AND class_org_unit_id IN ({marks})",(period,*scope)).fetchall())
        selected.update(int(r['member_id']) for r in execute(connection,"SELECT DISTINCT a.member_id FROM study_meeting_attendances a JOIN study_meeting_sessions s ON s.id=a.study_meeting_session_id " + f"WHERE substr(s.meeting_date,1,7)=? AND s.class_org_unit_id IN ({marks})",(period,*scope)).fetchall())
        ids=sorted(selected)
    if not available(connection):
        return [r for r in _current(connection,ids).values() if scope is None or r['class_org_unit_id'] in scope], {'source':'LIVE_NO_SCHEMA','state':'UNAVAILABLE'}
    raw = execute(connection,"SELECT * FROM study_meeting_roster_months WHERE period=?", (period,)).fetchone()
    previous = _existing(connection, period, ids=ids)
    frozen = raw and raw['state']=='FROZEN'
    rows = previous if frozen else _projection(connection, period, _current(connection,ids), previous,ids=ids)
    return [r for r in rows.values() if r.get('included',1) and (scope is None or r['class_org_unit_id'] in scope)], {'source':'FROZEN' if frozen else 'LIVE_WITH_BASELINE','state':'FROZEN' if frozen else 'OPEN','frozen_at':str(raw['frozen_at']) if frozen else None,'freeze_due_at':stamp(due_at(period))}

def refresh_current_month(*, at=None):
    from app.core.settings import get_settings
    settings = get_settings()
    if not settings.study_meeting_monthly_roster_enabled:
        return {"enabled":False,"updated":0,"failed":0,"state":"FEATURE_DISABLED"}
    if settings.deployment_read_only or settings.is_production and not settings.allow_production_mutations:
        return {'enabled':False,'updated':0,'failed':0,'state':'WRITE_DISABLED'}
    at = clock(at)
    with transaction() as connection:
        if not available(connection):
            return {'enabled':False,'updated':0,'failed':0,'state':'SCHEMA_REQUIRED'}
        month = _month(connection,at,create=True,lock=True)
        if month['state']=='FROZEN':
            return {'enabled':True,'updated':0,'failed':0,'state':'FROZEN','period':month['period']}
        report = _refresh(connection,month,at)
        from app.services.audit import write_audit
        write_audit(connection,actor_user_id=None,action='study_meeting.monthly_roster_refresh',resource_type='study_meeting_roster_month',resource_id=month['period'],after=report)
        return {'enabled':True,'updated':1,'failed':0,**report}

def participation_by_home(roster, meetings):
    from app.services.learning_requirement_policy import exemption_reason
    members={int(row['member_id']):row for row in roster}
    result={}
    for meeting in meetings:
        if exemption_reason(meeting.get('group_name'),meeting.get('class_name'),metric='group_meeting'):
            continue
        for member_id,name in meeting['attendees'].items():
            member=members.get(int(member_id))
            if not member or member['class_org_unit_id']!=meeting['class_org_unit_id']:
                continue
            if exemption_reason(member.get('group_name'),member.get('class_name'),metric='group_meeting'):
                continue
            group_id=member.get('group_org_unit_id')
            if group_id:result.setdefault(group_id,{})[int(member_id)]=name
    return result


def monthly_participation(connection,period,*,class_ids,cutoff=None):
    if not class_ids or period > clock().strftime("%Y-%m"):return {}
    from app.services.study_meeting_sharing import report_period
    # Reports may inspect older dates but never future attendance.
    _,first,end=report_period(period,cutoff)
    ids=sorted(set(class_ids));marks=','.join('?' for _ in ids)
    rows=execute(connection,
        "SELECT s.id,s.class_org_unit_id,s.study_group_org_unit_id,s.learning_cycle_id,c.name AS class_name,g.name AS group_name "
        "FROM study_meeting_sessions s JOIN org_units c ON c.id=s.class_org_unit_id JOIN org_units g ON g.id=s.study_group_org_unit_id "
        f"WHERE s.class_org_unit_id IN ({marks}) AND s.status='SUBMITTED' AND s.meeting_date>=? AND s.meeting_date<=? ORDER BY s.id",(*ids,first,end)).fetchall()
    latest={}
    for row in rows:latest[(row['study_group_org_unit_id'],row['learning_cycle_id'])]=dict(row)
    records={int(row['id']):{**row,'attendees':{}} for row in latest.values()}
    source_ids=sorted(records)
    for start in range(0,len(source_ids),200):
        chunk=source_ids[start:start+200];mask=','.join('?' for _ in chunk)
        for row in execute(connection,
            "SELECT a.study_meeting_session_id,a.member_id,m.name FROM study_meeting_attendances a JOIN members m ON m.id=a.member_id "
            f"WHERE a.study_meeting_session_id IN ({mask})",tuple(chunk)).fetchall():
            records[int(row['study_meeting_session_id'])]['attendees'][int(row['member_id'])]=row['name']
    roster,_=roster_for_period(connection,period,class_ids=class_ids)
    return participation_by_home(roster,records.values())
