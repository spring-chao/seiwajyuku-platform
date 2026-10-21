"""One scoped class selector for organization management and learning operations.

Names are only a compatibility grouping within the SAME parent organization ID.
The underlying IDs and historical facts are never rewritten or merged.
"""
from datetime import UTC, datetime

from app.db import execute


def canonical_classes(units, allowed=None, *, selectable_ids=None):
    groups = {}
    for raw in units:
        row = dict(raw)
        if row['unit_type'] not in {'CLASS', 'SPECIAL_COHORT'}:
            continue
        if allowed is not None and row['id'] not in allowed:
            continue
        key = (row.get('parent_id'), row['unit_type'], row['name'].strip())
        groups.setdefault(key, []).append(row)
    result = []
    for rows in groups.values():
        active = [r for r in rows if r['is_active'] and
                  (selectable_ids is None or r['id'] in selectable_ids)]
        if not active:
            continue
        def priority(row):
            counts = row.get('reference_counts', {})
            score = (counts.get('active_member_relations', 0) * 1_000_000 +
                     counts.get('active_children', 0) * 1_000 + counts.get('active_events', 0))
            return (-score, str(row.get('created_at') or '9999'), row['id'])
        chosen = dict(min(active, key=priority))
        chosen['name'] = chosen['name'].strip()
        chosen['related_class_ids'] = sorted(r['id'] for r in rows)
        chosen['duplicate_count'] = len(rows) - 1
        result.append(chosen)
    return sorted(result, key=lambda r: (r.get('parent_name') or '', r['name'], r['id']))


def class_catalog(connection, allowed=None, *, selectable_ids=None):
    rows = [dict(r) for r in execute(connection,
        "SELECT o.id,o.name,o.unit_type,o.parent_id,o.is_active,o.created_at,p.name AS parent_name "
        "FROM org_units o LEFT JOIN org_units p ON p.id=o.parent_id "
        "WHERE o.unit_type IN ('CLASS','SPECIAL_COHORT')").fetchall()]
    now = datetime.now(UTC).isoformat()
    members = {r['org_unit_id']: int(r['n']) for r in execute(connection,
        "SELECT r.org_unit_id,COUNT(*) AS n FROM member_org_relations r JOIN members m ON m.id=r.member_id "
        "WHERE m.status='ACTIVE' AND (r.valid_from IS NULL OR r.valid_from<=?) "
        "AND (r.valid_until IS NULL OR r.valid_until>=?) GROUP BY r.org_unit_id", (now, now)).fetchall()}
    children = {r['parent_id']: int(r['n']) for r in execute(connection,
        "SELECT parent_id,COUNT(*) AS n FROM org_units WHERE is_active=1 GROUP BY parent_id").fetchall()}
    events = {r['study_org_unit_id']: int(r['n']) for r in execute(connection,
        "SELECT study_org_unit_id,COUNT(*) AS n FROM attendance_event_groups WHERE status='ACTIVE' "
        "GROUP BY study_org_unit_id").fetchall()}
    for row in rows:
        row['reference_counts'] = {'active_member_relations': members.get(row['id'], 0),
            'active_children': children.get(row['id'], 0), 'active_events': events.get(row['id'], 0)}
    return canonical_classes(rows, allowed, selectable_ids=selectable_ids)


def selected_class_ids(catalog, selected_id):
    for row in catalog:
        if selected_id in row['related_class_ids']:
            return set(row['related_class_ids'])
    return set()
