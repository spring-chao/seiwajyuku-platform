import assert from "node:assert/strict";
import test from "node:test";
import { activityCenter, filterActivitySessions, groupActivitySessions } from "../src/utils/activity-list.mjs";

const organizations = [
  { id: "root", name: "二级塾", unit_type: "ROOT" },
  { id: "center", name: "分中心", unit_type: "REGIONAL_CENTER", parent_id: "root" },
  { id: "class", name: "一班", unit_type: "CLASS", parent_id: "center" }
];
const row = { id: 11, title: "一班学习会", activity_type: "CLASS_MEETING", org_unit_id: "class",
  study_org_unit_id: "class", org_name: "一班", event_date: "2026-10-22", record_count: 5, present_count: 4, registration_count: 6 };

test("three scan sessions become one activity without discarding scan counts", () => {
  const rows = [1, 2, 3].map(session_id => ({ ...row, session_id }));
  const groups = groupActivitySessions(rows);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].display_title, "一班学习会");
  assert.equal(groups[0].session_count, 3);
  assert.equal(groups[0].registration_count, 18);
  assert.equal(groups[0].present_count, 12);
  assert.equal(groups[0].session_id, undefined);
  assert.equal(groups[0].sessions.length, 3);
});

test("separate activity IDs and organizations never merge because their names match", () => {
  const rows = [{ ...row, session_id: 1 }, { ...row, id: 12, session_id: 2, study_org_unit_id: "other-class" }];
  assert.equal(groupActivitySessions(rows).length, 2);
});

test("duplicate session rows do not double-count and unknown registration totals stay unknown", () => {
  const rows = [{ ...row, session_id: 1 }, { ...row, session_id: 1 }, { ...row, session_id: 2, registration_count: null }];
  const [group] = groupActivitySessions(rows);
  assert.equal(group.session_count, 2);
  assert.equal(group.registration_count, null);
  assert.equal(group.present_count, 8);
});

test("center filter follows organization IDs and combines with class and activity type", () => {
  const rows = [{ ...row, session_id: 1 }, { ...row, id: 12, session_id: 2, activity_type: "OTHER" }];
  assert.equal(activityCenter(row, organizations).id, "center");
  assert.equal(filterActivitySessions(rows, { center_id: "center", class_id: "class", activity_type: "CLASS_MEETING" }, organizations).length, 1);
  assert.equal(filterActivitySessions(rows, { center_id: "unknown" }, organizations).length, 0);
});
