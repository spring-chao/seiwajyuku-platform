const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const activity = require("../utils/mobile-activity");
const { resolveVolunteerServices } = require("../utils/volunteer-services");
function harness(request) {
  let page;
  const app = { globalData: { personSessionToken: "synthetic-session" } };
  const guard = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/page-session.js"), "utf8"), { module: guard, getApp: () => app });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages/services/class-attendance.js"), "utf8"), {
    Page: value => { page = value; }, require: name => name.endsWith("request") ? { request } : name.endsWith("page-session") ? guard.exports : activity
  });
  page.setData = patch => Object.assign(page.data, patch);
  return { page, app };
}
const record = { event_id: "am", event_date: "2026-10-27", name: "合成班会" };
const context = { classes: [{ id: "class-one", name: "合成一班" }], today: "2026-10-27", month: "2026-10" };
const scenes = ["overall", "morning", "afternoon", "konpa"].map((key, index) => ({ key, name: key,
  class_statistics: { present_count: 8 - index, roster_count: 10, participation_rate: (8 - index) / 10 },
  group_statistics: [{ group_org_unit_id: "group-one", group_name: "合成一组", present_count: 4 - index, roster_count: 5, participation_rate: (4 - index) / 5 }],
  group_totals: { present_count: 4 - index, roster_count: 5, participation_rate: (4 - index) / 5 }
}));
const detail = { event: record, scenes, updated_at: "2026-10-27T02:00:00Z" };
test("volunteer capability enables the entry and manual-review ambiguity keeps it closed", () => {
  assert.equal(resolveVolunteerServices({ roles: [] }).canViewClassAttendance, false);
  const roles = [{ capabilities: ["CLASS_ATTENDANCE_VIEW"] }];
  assert.equal(resolveVolunteerServices({ roles }).canViewClassAttendance, true);
  assert.equal(resolveVolunteerServices({ roles, needs_manual_review: true }).canViewClassAttendance, false);
});
test("volunteer defaults to today, reads monthly meetings and switches four independent rates", async () => {
  const calls = [];
  const { page } = harness(async url => {
    calls.push(url);
    return { data: url.endsWith("/context") ? context : url.includes("?") ? { records: [record], has_more: false } : detail };
  });
  await page.initialize();
  assert(calls[1].includes("day=2026-10-27") && calls[1].includes("class_org_unit_id=class-one"));
  assert.equal(page.data.view.overallRate, "80.0%");
  for (const [index, key] of ["overall", "morning", "afternoon", "konpa"].entries()) {
    page.chooseScene({ currentTarget: { dataset: { key } } });
    assert.equal(page.data.view.overallRate, (80 - index * 10).toFixed(1) + "%");
    assert.equal(page.data.view.groups[0].present_count, 4 - index);
  }
  await page.choosePeriod({ currentTarget: { dataset: { period: "month" } } });
  assert(!calls[calls.length - 2].includes("&day="));
});
for (const leave of ["hide", "identity-change"]) {
  test(`late volunteer statistics cannot restore private data after ${leave}`, async () => {
    let finish;
    const { page, app } = harness(() => new Promise(resolve => { finish = resolve; }));
    page.data.records = [record];
    const pending = page.loadSnapshot();
    if (leave === "hide") page.onHide(); else app.globalData.personSessionToken = "other-synthetic-session";
    finish({ data: detail }); await pending;
    assert.equal(page.data.snapshot, null); assert.equal(page.data.view, null);
  });
}
test("missing scene shows no rate and cannot inherit the overall rate", () => {
  const { page } = harness(async () => ({}));
  page.data.snapshot = { scenes: [scenes[0]] };
  page.chooseScene({ currentTarget: { dataset: { key: "konpa" } } });
  assert.equal(page.data.view, null);
});
test("operator display uses shared per-scene results without changing overall metrics", () => {
  const statistics = { class_statistics: scenes[0].class_statistics, group_totals: scenes[0].group_totals, group_statistics: scenes[0].group_statistics,
    class_session_statistics: scenes.slice(1).map(row => ({ ...row.class_statistics, event_id: row.key, group_totals: row.group_totals, group_statistics: row.group_statistics })) };
  for (const scene of scenes.slice(1)) {
    const data = { group: { id: scene.key, activity_type: "class_meeting" }, statistics };
    assert.equal(activity.decorate(data).statistics.overallRate, "80.0%");
    assert.equal(activity.decorate(data, "", "session").statistics.overallRate, (scene.class_statistics.participation_rate * 100).toFixed(1) + "%");
    assert.equal(activity.decorate(data, "", "session").groupRows[0].present_count, scene.group_statistics[0].present_count);
  }
});
