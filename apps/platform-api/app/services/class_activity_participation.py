"""One class activity: current roster, distinct morning/afternoon attendees."""
from app.db import connect, execute
from app.services.study_membership import CURRENT_CLASS_ID_SQL


def attach_class_activity_overall(rows: list[dict]) -> None:
    groups = {int(row['id']): row for row in rows
              if str(row.get('activity_type', '')).upper() == 'CLASS_MEETING'}
    if not groups:
        return
    classes = sorted({row['study_org_unit_id'] for row in groups.values() if row.get('study_org_unit_id')})
    roster = {class_id: set() for class_id in classes}
    present = {group_id: set() for group_id in groups}
    unresolved = {group_id: 0 for group_id in groups}
    facts = []
    c = connect()
    try:
        for start in range(0, len(classes), 200):
            batch = classes[start:start + 200]
            marks = ','.join('?' for _ in batch)
            for row in execute(c, f'SELECT m.id,{CURRENT_CLASS_ID_SQL} AS class_id FROM members m '
                               f"WHERE m.status='ACTIVE' AND {CURRENT_CLASS_ID_SQL} IN ({marks})", tuple(batch)).fetchall():
                roster[row['class_id']].add(int(row['id']))
        group_ids = list(groups)
        for start in range(0, len(group_ids), 300):
            batch = group_ids[start:start + 300]
            marks = ','.join('?' for _ in batch)
            facts.extend(dict(row) for row in execute(c,
                'SELECT s.event_group_id,r.member_id,r.member_code_snapshot,r.external_registration_id FROM attendance_records r '
                'JOIN attendance_sessions s ON s.id=r.attendance_session_id '
                f'WHERE s.event_group_id IN ({marks}) '
                "AND UPPER(s.session_code) IN ('MORNING','AM','AFTERNOON','PM') "
                "AND s.status NOT IN ('DELETED','CANCELLED') "
                "AND r.attendance_status IN ('PRESENT','MANUAL_PRESENT')", tuple(batch)).fetchall())
    finally:
        c.close()
    # Merge verified member IDs with stable source registration IDs/codes.
    # Names and masked phones are never used to guess who is the same person.
    aliases = {}
    for fact in facts:
        if fact['member_id'] is None:
            continue
        for field in ('member_code_snapshot', 'external_registration_id'):
            value = str(fact[field] or '').strip()
            if value:
                aliases.setdefault((fact['event_group_id'], field, value), set()).add(int(fact['member_id']))
    for fact in facts:
        group_id = int(fact['event_group_id'])
        member_id = fact['member_id']
        if member_id is not None:
            key = ('MEMBER', int(member_id))
        else:
            candidates, fallback = set(), None
            for field in ('member_code_snapshot', 'external_registration_id'):
                value = str(fact[field] or '').strip()
                if value:
                    candidates.update(aliases.get((group_id, field, value), set()))
                    if fallback is None:
                        fallback = (field, value)
            key = ('MEMBER', next(iter(candidates))) if len(candidates) == 1 else fallback if not candidates else None
        if key is None:
            unresolved[group_id] += 1
        else:
            present[group_id].add(key)
    for row in rows:
        if int(row['id']) not in groups:
            continue
        size = len(roster.get(row.get('study_org_unit_id'), set()))
        count = len(present[int(row['id'])])
        missing = unresolved[int(row['id'])]
        configured = bool(row.get('study_org_unit_id'))
        row.update(overall_registration_count=size if configured else None,
                   overall_present_count=count if configured and not missing else None,
                   overall_identified_count=count,
                   overall_unresolved_record_count=missing,
                   overall_participation_rate=round(count * 100 / size, 1) if size and not missing else None)
