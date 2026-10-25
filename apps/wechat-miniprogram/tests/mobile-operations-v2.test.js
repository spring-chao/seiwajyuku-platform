const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function page(name, request, wx = {}) {
  let instance;
  const app = { globalData: { personSessionToken: "synthetic-session" } };
  const guardModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/page-session.js"), "utf8"), { module: guardModule, getApp: () => app });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages/operations", `${name}.js`), "utf8"), {
    Page: value => { instance = value; }, require: name => name.endsWith("page-session") ? guardModule.exports : ({ request }),
    wx: { showToast() {}, showModal() {}, ...wx }
  });
  instance.setData = value => Object.assign(instance.data, value);
  instance.app = app;
  return instance;
}

test("birthday action loads editable copy and copies only after a tap", async () => {
  let copied = null;
  const p = page("member-detail", async url => url.endsWith("birthday-copy") ? { data: { draft: "合成生日祝福" } } : { data: { member: { name: "合成学长" } } }, { setClipboardData: value => { copied = value.data; } });
  p.onLoad({ member_id: "7", birthday_due_date: "2026-10-25" });
  await p.load();
  assert.equal(p.data.wechatCopy, "合成生日祝福");
  assert.equal(copied, null);
  p.editCopy({ detail: { value: "编辑后的祝福" } }); p.copyWechat();
  assert.equal(copied, "编辑后的祝福");
});

test("renewal detail uses the selected cycle instead of a different current cycle", () => {
  let url;
  const p = page("member-detail", async () => ({}), { navigateTo: value => { url = value.url; } });
  p.onLoad({ member_id: "7", renewal_cycle_id: "41" });
  p.data.detail = { actions: { can_view_renewal: true }, renewal: { cycle_id: 42 } };
  p.viewRenewal();
  assert.match(url, /cycle_id=41/);
});

test("activity double taps send once and a timeout retry keeps the request id", async () => {
  let finish, reject;
  const calls = [];
  const p = page("activity-detail", (url, options) => {
    calls.push({ url, options });
    return new Promise((resolve, no) => { finish = resolve; reject = no; });
  });
  p.onLoad({});
  p.data.title = "合成活动"; p.data.eventDate = "2026-10-25"; p.data.organizations = [{ id: "org-test" }];
  const first = p.save(); await p.save();
  assert.equal(calls.length, 1);
  reject(new Error("timeout")); await first;
  p.load = async () => {};
  const retry = p.save();
  assert.equal(calls[0].options.data.request_id, calls[1].options.data.request_id);
  finish({ data: { id: 5 } }); await retry;
  assert.equal(p.data.eventId, 5);
});

test("meeting confirmation posts the reviewed revision and holds a submit lock", async () => {
  let modal, finish;
  const calls = [];
  const p = page("study-meeting-detail", (url, options) => {
    calls.push({ url, options }); return new Promise(resolve => { finish = resolve; });
  }, { showModal: options => { modal = options.success; } });
  p.onLoad({ session_id: "5" }); p.data.detail = { can_settle: true, revision: "reviewed-revision" };
  p.verifySettle(); const pending = modal({ confirm: true });
  p.verifySettle();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.data.expected_revision, "reviewed-revision");
  p.load = async () => {};
  finish({ data: { credit_settlement: { status: "POSTED", posted_points: "4" } } }); await pending;
  assert.equal(p.data.saving, false);
});

test("application approval sends the displayed revision and saved form values", async () => {
  let modal, sent;
  const p = page("application-detail", async (_, options) => { sent = options.data; }, { showModal: options => { modal = options.success; } });
  p.onLoad({ application_id: "9" });
  p.data.detail = { application: { updated_at: "synthetic-revision" } };
  p.data.name = "合成学长"; p.data.note = "合成审核说明"; p.data.joinDate = "2026-10-25";
  p.load = async () => {};
  p.review({ currentTarget: { dataset: { decision: "APPROVE" } } });
  assert.equal(sent, undefined);
  modal({ confirm: true }); await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.expected_updated_at, "synthetic-revision");
  assert.equal(sent.decision, "APPROVE");
  assert.equal(sent.join_date, "2026-10-25");
});

test("dashboard birthday navigation carries the actual month and day", () => {
  let url;
  const p = page("dashboard", async () => ({}), { navigateTo: value => { url = value.url; } });
  p.data.month = "2026-10";
  p.openBirthday({ currentTarget: { dataset: { item: { member_id: 7, birthday: "10-25" } } } });
  assert.match(url, /birthday_due_date=2026-10-25/);
});

for (const [name, field, response] of [
  ["renewal-detail", "detail", { cycle: {}, wechat_copy: "合成文案" }],
  ["study-meeting-detail", "detail", { record: { group_name: "合成小组" } }],
  ["study-meetings", "records", [{ id: 1 }]],
  ["activities", "records", { records: [{ id: "event-a" }], has_more: false }],
  ["activity-detail", "detail", { group: { title: "合成活动", event_date: "2026-10-25" } }],
  ["applications", "records", [{ id: 1, application_status: "SUBMITTED" }]],
  ["application-detail", "detail", { application: { name: "合成申请" } }],
  ["dashboard", "snapshot", { summary: {}, birthday_members: [] }]
]) {
  for (const action of ["hide", "switch"]) test(`${name}: late response after ${action} cannot restore private data`, async () => {
    let finish;
    const p = page(name, () => new Promise(resolve => { finish = resolve; }));
    if (p.onLoad) p.onLoad({ event_id: "event-a", application_id: "1", cycle_id: "1", session_id: "1" });
    if (!p.data.month) p.data.month = "2026-10";
    const pending = p.load();
    if (action === "hide") p.onHide(); else p.app.globalData.personSessionToken = "different-account";
    finish({ data: response }); await pending;
    assert.ok(p.data[field] === null || Array.isArray(p.data[field]) && !p.data[field].length);
  });
}

test("renewal modal from a previous identity cannot write using the new account", async () => {
  let modal, writes = 0;
  const p = page("renewal-detail", async () => { writes++; }, { showModal: options => { modal = options.success; } });
  p.data.detail = { can_manage: true, cycle: { status: "PENDING_FIRST_CONTACT" } };
  p.saveStatus(); p.app.globalData.personSessionToken = "different-account";
  await modal({ confirm: true });
  assert.equal(writes, 0);
});
