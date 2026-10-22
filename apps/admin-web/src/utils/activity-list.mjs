import { activityDisplayTitle, registrationCount } from "./business-labels.mjs";

// The stored event ID defines an activity; scan sessions remain separate facts.
export function groupActivitySessions(rows) {
  const groups = new Map();
  for (const row of rows) {
    let group = groups.get(row.id);
    if (!group) {
      group = { ...row, display_title: activityDisplayTitle(row.title, "", ""),
        session_id: undefined, session_count: 0, registration_count: 0,
        record_count: 0, present_count: 0, sessions: [] };
      groups.set(row.id, group);
    }
    if (group.sessions.some(item => item.session_id === row.session_id)) continue;
    group.sessions.push(row);
    group.session_count += 1;
    const count = registrationCount(row);
    group.registration_count = group.registration_count === null || count === null
      ? null : group.registration_count + count;
    group.record_count += row.record_count;
    group.present_count += row.present_count;
  }
  return [...groups.values()];
}

export function activityCenter(row, organizations) {
  const byId = new Map(organizations.map(item => [item.id, item]));
  let current = byId.get(row.study_org_unit_id) || byId.get(row.org_unit_id);
  const seen = new Set();
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    if (["REGIONAL_CENTER", "ROOT", "SHUKU", "REGIONAL_SHUKU"].includes(current.unit_type)) return current;
    current = byId.get(current.parent_id);
  }
  return { id: row.org_unit_id, name: row.org_name };
}

export function filterActivitySessions(rows, filters, organizations) {
  return rows.filter(row =>
    (!filters.center_id || activityCenter(row, organizations).id === filters.center_id) &&
    (!filters.class_id || row.study_org_unit_id === filters.class_id || row.org_unit_id === filters.class_id) &&
    (!filters.activity_type || row.activity_type === filters.activity_type)
  );
}
