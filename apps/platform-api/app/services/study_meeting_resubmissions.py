"""Immutable meeting versions and append-only, reviewed credit differences."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from app.db import execute


def family(connection, session):
    return [dict(r) for r in execute(connection,
        "SELECT * FROM study_meeting_sessions WHERE study_group_org_unit_id=? AND learning_cycle_id=? "
        "AND (status IN ('SUBMITTED','CANCELLED') OR id=?) ORDER BY id",
        (session['study_group_org_unit_id'],session['learning_cycle_id'],session['id'])).fetchall()]


def _other_entitlement(connection, base, session):
    """A different current meeting may still support the same once-only credit."""
    from app.services.learning_credits import GROUP_MEETING_ATTENDANCE
    common = ("s.status='SUBMITTED' AND NOT (s.study_group_org_unit_id=? AND s.learning_cycle_id=?) "
              "AND s.class_org_unit_id=?")
    params=[session['study_group_org_unit_id'],session['learning_cycle_id'],session['class_org_unit_id']]
    if base['credit_type']==GROUP_MEETING_ATTENDANCE:
        sql=("SELECT 1 FROM study_meeting_sessions s JOIN study_meeting_attendances a "
             "ON a.study_meeting_session_id=s.id JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id "
             "WHERE "+common+" AND lc.group_meeting_policy='REQUIRED' AND s.learning_cycle_id=? AND a.member_id=? LIMIT 1")
        params += [session['learning_cycle_id'],base['member_id']]
    else:
        sql=("SELECT 1 FROM study_meeting_sessions s JOIN class_learning_cycles lc ON lc.id=s.learning_cycle_id "
             "JOIN study_meeting_courses c ON c.study_meeting_session_id=s.id "
             "JOIN study_meeting_course_completions r ON r.study_meeting_course_id=c.id "
             "WHERE "+common+" AND lc.binding_id=? AND c.course_key=? AND r.member_id=? "
             "AND r.completion_status='CONFIRMED' LIMIT 1")
        params += [session['binding_id'],base['rule_key'],base['member_id']]
    return bool(execute(connection,sql,tuple(params)).fetchone())


def proposals(connection, session, entries):
    """Reconcile prior posted entitlement keys touched by this meeting family.

    First-time credits retain their original member/cycle or member/course keys.
    Differences get a separate key for this immutable submitted version. Never
    mutate or delete a prior ledger row, even when a member is removed.
    """
    from app.services import learning_credits as credits
    versions=family(connection,session)
    if session['status']!='SUBMITTED':
        return entries
    ids=tuple(int(r['id']) for r in versions)
    marks=','.join('?' for _ in ids)
    keys={item['idempotency_key'] for item in entries}
    for r in execute(connection,f"SELECT DISTINCT member_id FROM study_meeting_attendances WHERE study_meeting_session_id IN ({marks})",ids).fetchall():
        keys.add(f"GROUP_MEETING:{r['member_id']}:{session['learning_cycle_id']}")
    for r in execute(connection,"SELECT DISTINCT c.course_key,r.member_id FROM study_meeting_courses c "
        "JOIN study_meeting_course_completions r ON r.study_meeting_course_id=c.id "
        f"WHERE c.study_meeting_session_id IN ({marks})",ids).fetchall():
        keys.add(f"COURSE:{r['member_id']}:{r['course_key']}:{session['binding_id']}")
    bases=[]
    ordered=sorted(keys)
    for start in range(0,len(ordered),200):
        chunk=ordered[start:start+200];placeholders=','.join('?' for _ in chunk)
        bases.extend(execute(connection,
            "SELECT e.*,m.name AS member_name FROM learning_credit_entries e JOIN members m ON m.id=e.member_id "
            f"WHERE e.idempotency_key IN ({placeholders}) AND e.reversal_of_entry_id IS NULL ORDER BY e.id",tuple(chunk)).fetchall())
    by_key={r['idempotency_key']:dict(r) for r in bases}
    current={item['idempotency_key']:item for item in entries}
    # Reconcile by the original global credit key, including a credit first
    # awarded at another group. The last remaining valid participation owns
    # entitlement; changing groups never creates a second award or strands one.
    result=[item for item in entries if item['idempotency_key'] not in by_key]
    completion={(r['course_key'],int(r['member_id'])):r['completion_status']
                for r in credits._course_rows(connection,session)}
    for base_key,base in by_key.items():
        if (base['class_org_unit_id']!=session['class_org_unit_id']
                or base['credit_category']!=credits.STANDARD_LEARNING
                or base['source_type'] not in {'STUDY_MEETING_ATTENDANCE','STUDY_MEETING_COURSE'}
                or base['credit_type'] not in {credits.GROUP_MEETING_ATTENDANCE,credits.COURSE_COMPLETION}
                or Decimal(str(base['points']))<0):
            raise credits.LearningCreditError('原学分事实与学习会不一致，请在结算工作台核对')
        item=current.get(base_key)
        if (item and str(base['source_id'])==str(session['id'])
                and base['source_type']=='STUDY_MEETING_ATTENDANCE'):
            result.append(item)
            continue
        # A first course credit written by this very version must preserve the
        # exact proposal during partial posting/retries.
        if item and base['source_type']=='STUDY_MEETING_COURSE' and str(base['source_id'])==str(item['source_id']):
            result.append(item)
            continue
        explicit_no = bool(item and item.get('course_key') and
                           completion.get((item['course_key'],int(base['member_id'])))=='NOT_COMPLETED')
        if item and not item.get('eligible') and not explicit_no:
            result.append({**item,'status':'BLOCKED','postable':False})
            continue
        desired=Decimal(str(base['points'])) if item and item.get('eligible') else Decimal('0')
        if not desired and _other_entitlement(connection,base,session):
            desired=Decimal(str(base['points']))
        digest=hashlib.sha256(base_key.encode()).hexdigest()
        adjustment_key=f"SMR:{session['id']}:{digest}"
        done=credits._existing_entry(connection,adjustment_key)
        ledger=execute(connection,
            "SELECT e.id,e.points FROM learning_credit_entries e WHERE e.status IN ('POSTED','REVERSED') AND "
            "(e.idempotency_key=? OR e.idempotency_key LIKE ? OR e.reversal_of_entry_id IN "
            "(SELECT original.id FROM learning_credit_entries original WHERE original.idempotency_key=? OR original.idempotency_key LIKE ?))",
            (base_key,f'SMR:%:{digest}',base_key,f'SMR:%:{digest}')).fetchall()
        net=sum((Decimal(str(r['points'])) for r in ledger),Decimal('0'))
        amount=desired-net
        if not amount and not done:
            if item:result.append(item)
            continue
        template=item or {
            'entry_kind':'GROUP_MEETING_ATTENDANCE' if base['credit_type']==credits.GROUP_MEETING_ATTENDANCE else 'COURSE_COMPLETION',
            'member_id':int(base['member_id']),'member_name':base['member_name'],
            'source_type':base['source_type'],'source_id':str(base['source_id']),
            'learning_cycle_id':int(session['learning_cycle_id']),'class_org_unit_id':session['class_org_unit_id'],
            'rule_key':base['rule_key'],'rule_version':base['rule_version'],
            'rule_version_id':base['rule_version_id'],'credit_category':base['credit_category'],
            'credit_type':base['credit_type'],
            **({'course_key':base['rule_key']} if base['credit_type']==credits.COURSE_COMPLETION else {})}
        snapshot=json.loads(base['rule_snapshot_json'])
        basis={'base_key':base_key,'base_entry_id':int(base['id']),'session_id':int(session['id']),
               'previous_net':str(net),'desired_points':str(desired),'difference_points':str(amount)}
        snapshot={**snapshot,'resubmission_adjustment':basis}
        if done:
            snapshot=json.loads(done['rule_snapshot_json'])
            basis=snapshot['resubmission_adjustment']
            amount=Decimal(str(done['points']))
        result.append({**template,'source_id':str(done['source_id']) if done else str(template['source_id']),
            'points':float(amount),'rule_snapshot':snapshot,'idempotency_key':adjustment_key,
            'adjustment_basis':basis,'existing_entry':done,
            'eligible':True,'postable':not bool(done),
            'status':'SKIPPED_DUPLICATE' if done else 'READY',
            'reasons':['本次重新提交差额已入账' if done else '重新提交核实后按差额调整，保留原账本']})
    return result
