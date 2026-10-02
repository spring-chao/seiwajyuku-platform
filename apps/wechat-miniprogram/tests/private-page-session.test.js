const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");

function harness(name, request, uploadPhoto = async () => {}) {
  const app = { globalData: { personSessionToken: "account-a", memberSessionToken: "account-a",
    studyMeetingDraft: { group_org_unit_id: "synthetic-group", member_ids: [1] } } };
  const guard = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/page-session.js"), "utf8"), { module: guard, getApp: () => app });
  let page;
  const calls = [];
  app.setPersonSession = token => { app.globalData.personSessionToken = token; calls.push("bound"); };
  app.clearMemberSession = () => { app.globalData.personSessionToken = ""; app.globalData.memberSessionToken = ""; calls.push("revoked"); };
  vm.runInNewContext(fs.readFileSync(path.join(root, "pages", name + ".js"), "utf8"), {
    Page: value => { page = value; }, getApp: () => app,
    require: name => name.endsWith("page-session") ? guard.exports : name.endsWith("study-meeting")
      ? require("../utils/study-meeting") : name.endsWith("scan") ? require("../utils/scan") : ({ request, uploadPhoto }),
    wx: { login: options => options.success({ code: "synthetic-login" }), showModal: value => calls.push(value),
      showToast: value => calls.push(value), redirectTo: value => calls.push(value), navigateBack: () => calls.push("back") }
  });
  page.setData = value => Object.assign(page.data, value);
  return { page, app, calls };
}

const readers = [
  ["operations/index", "load", "entries"], ["operations/today-actions", "load", "items"],
  ["operations/renewal-watch", "load", "groups"], ["operations/member-search", "search", "results"],
  ["operations/followups", "load", "tasks"], ["operations/study-meetings", "load", "records"],
  ["operations/member-detail", "load", "detail"], ["study-meeting/index", "loadContext", "context"],
  ["study-meeting/members", "loadMembers", "members"], ["study-meeting/submit", "loadContext", "assignment"]
];

for (const [name, method, field] of readers) {
  for (const action of ["hide", "switch"]) {
    test(`${name}: ${action} prevents late private response restoration`, async () => {
      let resolve;
      const { page, app } = harness(name, () => new Promise(yes => { resolve = yes; }));
      page.data.keyword = "synthetic";
      page.data.memberId = 1;
      const pending = page[method]();
      assert.equal(typeof resolve, "function");
      if (action === "hide") page.onHide();
      else app.globalData.personSessionToken = "account-b";
      resolve({ data: { entries: [{ private: true }], worker_name: "synthetic", pending: [{ private: true }],
        groups: [{ key: "OBSERVE_3", items: [{ private: true }] }], assignment: { members: [{ private: true }] } } });
      await pending;
      assert.ok(page.data[field] == null || page.data[field].length === 0);
      assert.equal(page.data.loading, false);
      assert.equal(page.data.contactPhone || "", "");
    });
  }
}

test("a newer member search wins over an older response", async () => {
  const resolves = [];
  const { page } = harness("operations/member-search", () => new Promise(yes => resolves.push(yes)));
  page.data.keyword = "old"; const old = page.search();
  page.data.keyword = "new"; const latest = page.search();
  resolves[1]({ data: [{ member_id: 2 }] }); await latest;
  resolves[0]({ data: [{ member_id: 1 }] }); await old;
  assert.equal(page.data.results[0].member_id, 2);
});

test("old care submission cannot navigate or claim success after account switch", async () => {
  let finish;
  const { page, app, calls } = harness("operations/care-record", () => new Promise(yes => { finish = yes; }));
  page.data.memberId = 1; page.data.situation = "synthetic care";
  const pending = page.submit();
  app.globalData.personSessionToken = "account-b";
  finish({ data: {} }); await pending;
  assert.equal(calls.length, 0);
  assert.equal(page.data.situation, "");
});

for (const boundary of ["create", "upload"]) {
  test(`study meeting: account switch during ${boundary} prevents subsequent writes`, async () => {
    let finish;
    const sent = [];
    const { page, app } = harness("study-meeting/submit", async (url) => {
      sent.push(url);
      if (boundary === "create") return new Promise(yes => { finish = yes; });
      return { data: { id: 42 } };
    }, () => new Promise(yes => { finish = yes; }));
    Object.assign(page.data, { meetingPlanReady: true, allRequiredContentConfirmed: true,
      allRequiredContentCompleted: true, evidenceEnabled: true, photoPath: "synthetic.jpg" });
    const pending = page.submit();
    await new Promise(resolve => setImmediate(resolve));
    app.globalData.personSessionToken = "account-b";
    app.globalData.studyMeetingDraft = null;
    finish({ data: { id: 42 } }); await pending;
    assert.equal(sent.length, 1);
    assert.equal(app.globalData.studyMeetingDraft, null);
    assert.equal(app.globalData.studyMeetingResult, undefined);
    assert.equal(page.data.photoPath, "");
  });
}

test("person identity replacement clears learning draft and result", () => {
  let app;
  vm.runInNewContext(fs.readFileSync(path.join(root, "app.js"), "utf8"), {
    App: value => { app = value; }, require: () => ({ sessionStorageKey: "synthetic" }),
    wx: { setStorageSync() {}, removeStorageSync() {} }
  });
  Object.assign(app.globalData, { personSessionToken: "account-a", studyMeetingDraft: { private: true }, studyMeetingResult: { private: true } });
  app.setPersonSession("account-b");
  assert.equal(app.globalData.studyMeetingDraft, null);
  assert.equal(app.globalData.studyMeetingResult, null);
});

for (const [name, method] of [["identity/bind", "bindIdentity"], ["identity/staff-bind", "bindStaffIdentity"]]) {
  for (const action of ["hide", "switch"]) {
    test(`${name}: ${action} prevents an old binding from replacing identity`, async () => {
      let finish;
      const { page, app, calls } = harness(name, () => new Promise(yes => { finish = yes; }));
      Object.assign(page.data, name === "identity/bind" ? { name: "synthetic", phone: "13800000000" }
        : { username: "synthetic", password: "synthetic" });
      const pending = page[method]();
      await new Promise(resolve => setImmediate(resolve));
      if (action === "hide") page.onHide();
      else app.globalData.personSessionToken = "account-b";
      finish({ data: { access_token: "old-response", member: { name_masked: "S*" } } });
      await pending;
      assert.equal(app.globalData.personSessionToken, action === "hide" ? "account-a" : "account-b");
      assert.equal(calls.length, 0);
      assert.equal(page.data.password || "", "");
      assert.equal(page.data.phone || "", "");
    });
  }
}

test("old scanner 401 cannot revoke a new account", async () => {
  let fail;
  const { page, app, calls } = harness("scan/index", () => new Promise((_, no) => { fail = no; }));
  const pending = page.handleScanResult({ path: "pages/study-meeting/index" });
  assert.equal(typeof fail, "function");
  app.globalData.personSessionToken = "account-b";
  app.globalData.memberSessionToken = "account-b";
  fail(Object.assign(new Error("expired old account"), { statusCode: 401 }));
  await pending;
  assert.equal(app.globalData.personSessionToken, "account-b");
  assert.equal(calls.length, 0);
  assert.equal(page.data.statusMessage, "");
});
