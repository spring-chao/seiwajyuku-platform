"""One class activity: distinct daytime attendees in the current class roster."""
from app.db import connect, execute
from app.services.study_membership import CURRENT_CLASS_ID_SQL


def _class_staff(connection, classes: list[str]) -> dict[str, dict[int, str]]:
    """Only the host class's formal teachers and its groups' counselors.

    Current volunteer posts follow ACTIVE member/post state, not legacy date
    fields (the same authority as volunteer management). Never use account
    roles, free-text titles or broad center-level scopes as a substitute.
    """
    staff = {class_id: {} for class_id in classes}
    for start in range(0, len(classes), 200):
        batch = classes[start:start + 200]
        marks = ','.join('?' for _ in batch)
        rows = execute(connection,
            'SELECT DISTINCT m.id,m.member_code,host.id AS class_id '
            'FROM volunteer_appointments va JOIN members m ON m.id=va.member_id '
            'JOIN volunteer_position_catalog pc ON pc.position_key=va.appointment_key '
            'LEFT JOIN volunteer_service_units vsu ON vsu.id=va.volunteer_service_unit_id '
            'JOIN org_units target ON target.id=COALESCE(vsu.service_target_org_unit_id,va.org_unit_id) '
            "JOIN org_units host ON host.id=CASE WHEN target.unit_type='GROUP' THEN target.parent_id ELSE target.id END "
            f'WHERE host.id IN ({marks}) '
            "AND host.unit_type IN ('CLASS','SPECIAL_COHORT') AND host.is_active=1 AND target.is_active=1 "
            "AND m.status='ACTIVE' AND va.status='ACTIVE' AND pc.is_active=1 "
            'AND (va.volunteer_service_unit_id IS NULL OR vsu.is_active=1) '
            "AND ((va.appointment_key IN ('volunteer_class_counselor','volunteer_deputy_class_teacher') AND target.id=host.id) "
            "OR (va.appointment_key='volunteer_group_counselor' AND target.unit_type='GROUP'))",
            tuple(batch)).fetchall()
        for row in rows:
            staff[row['class_id']][int(row['id'])] = str(row['member_code'] or '').strip()
    return staff


def attach_class_activity_overall(rows: list[dict]) -> None:
    groups = {int(row['id']): row for row in rows
              if str(row.get('activity_type', '')).upper() == 'CLASS_MEETING'}
    if not groups:
        return
    classes = sorted({row['study_org_unit_id'] for row in groups.values() if row.get('study_org_unit_id')})
    roster = {class_id: set() for class_id in classes}
    roster_codes = {class_id: {} for class_id in classes}
    eligible = {class_id: set() for class_id in classes}
    present = {group_id: set() for group_id in groups}
    unresolved = {group_id: 0 for group_id in groups}
    facts = []
    c = connect()
    try:
        for start in range(0, len(classes), 200):
            batch = classes[start:start + 200]
            marks = ','.join('?' for _ in batch)
            for row in execute(c, f'SELECT m.id,m.member_code,{CURRENT_CLASS_ID_SQL} AS class_id FROM members m '
                               f"WHERE m.status='ACTIVE' AND {CURRENT_CLASS_ID_SQL} IN ({marks})", tuple(batch)).fetchall():
                roster[row['class_id']].add(int(row['id']))
                code = str(row['member_code'] or '').strip()
                if code:
                    roster_codes[row['class_id']].setdefault(code, set()).add(int(row['id']))
        for class_id, staff in _class_staff(c, classes).items():
            eligible[class_id] = roster[class_id] | set(staff)
            for member_id, code in staff.items():
                if code:
                    roster_codes[class_id].setdefault(code, set()).add(member_id)
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
                "AND r.participant_type='MEMBER' "
                "AND r.attendance_status IN ('PRESENT','MANUAL_PRESENT')", tuple(batch)).fetchall())
    finally:
        c.close()
    # Resolve stable identities before intersecting with the canonical roster.
    # An unbound source registration ID alone cannot prove class membership.
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
        class_id = groups[group_id].get('study_org_unit_id')
        member_id = fact['member_id']
        if member_id is None:
            candidates = set()
            for field in ('member_code_snapshot', 'external_registration_id'):
                value = str(fact[field] or '').strip()
                if value:
                    candidates.update(aliases.get((group_id, field, value), set()))
            code = str(fact['member_code_snapshot'] or '').strip()
            candidates.update(roster_codes.get(class_id, {}).get(code, set()))
            if len(candidates) != 1:
                unresolved[group_id] += 1
                continue
            member_id = next(iter(candidates))
        if int(member_id) in eligible.get(class_id, set()):
            present[group_id].add(int(member_id))
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
