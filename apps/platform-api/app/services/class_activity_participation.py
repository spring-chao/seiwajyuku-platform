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
            for row in execute(c, 'SELECT DISTINCT s.event_group_id,r.member_id FROM attendance_records r '
                'JOIN attendance_sessions s ON s.id=r.attendance_session_id '
                f'WHERE s.event_group_id IN ({marks}) '
                "AND UPPER(s.session_code) IN ('MORNING','AM','AFTERNOON','PM') "
                "AND s.status NOT IN ('DELETED','CANCELLED') "
                "AND r.attendance_status IN ('PRESENT','MANUAL_PRESENT') "
                "AND r.participant_type='MEMBER' AND r.member_id IS NOT NULL", tuple(batch)).fetchall():
                group_id = int(row['event_group_id'])
                if int(row['member_id']) in roster.get(groups[group_id].get('study_org_unit_id'), set()):
                    present[group_id].add(int(row['member_id']))
    finally:
        c.close()
    for row in rows:
        if int(row['id']) not in groups:
            continue
        size = len(roster.get(row.get('study_org_unit_id'), set()))
        count = len(present[int(row['id'])])
        configured = bool(row.get('study_org_unit_id'))
        row.update(overall_registration_count=size if configured else None,
                   overall_present_count=count if configured else None,
                   overall_participation_rate=round(count * 100 / size, 1) if size else None)
