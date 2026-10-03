const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(pageName) {
  const calls = [];
  const app = { globalData: { memberSessionToken: "account-a" },
    clearMemberSession() { this.globalData.memberSessionToken = ""; } };
  let page;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages", pageName, "index.js"), "utf8"), {
    Page(value) { page = value; }, getApp: () => app, wx: {},
    require(name) {
      if (name.includes("identity-session")) return { identityChangePending: () => false };
      if (name.includes("volunteer-services")) return { resolveVolunteerServices: () => ({ serviceAssignments: [] }) };
      return { request(url) { const pending = deferred(); calls.push({ url, ...pending }); return pending.promise; } };
    }
  });
  page.setData = values => Object.assign(page.data, values);
  return { page, app, calls, load: () => pageName === "learning" ? page.loadLearning() :
    pageName === "services" ? page.loadServices() : page.loadProfile() };
}

for (const pageName of ["learning", "profile", "services"]) {
  test(`${pageName}: hide/unload discards delayed member response`, async () => {
    for (const lifecycle of ["onHide", "onUnload"]) {
      const h = harness(pageName);
      const pending = h.load();
      h.page[lifecycle]();
      h.calls[0].resolve({ data: { member: { member_id: 1, name_masked: "测试*" } } });
      await pending;
      assert.equal(h.page.data.member, null);
      assert.equal(h.page.data.loading, false);
      assert.equal(h.calls.length, 1);
    }
  });
  test(`${pageName}: account switch discards delayed member response`, async () => {
    const h = harness(pageName);
    const pending = h.load();
    h.app.globalData.memberSessionToken = "account-b";
    h.calls[0].resolve({ data: { member: { member_id: 1 } } });
    await pending;
    assert.equal(h.page.data.member, null);
    assert.equal(h.page.data.loading, false);
    assert.equal(h.calls.length, 1);
  });
  test(`${pageName}: an old 401 cannot revoke a new account`, async () => {
    const h = harness(pageName);
    const pending = h.load();
    h.app.globalData.memberSessionToken = "account-b";
    h.calls[0].reject(Object.assign(new Error("expired"), { statusCode: 401 }));
    await pending;
    assert.equal(h.app.globalData.memberSessionToken, "account-b");
    assert.equal(h.page.data.loading, false);
  });
  test(`${pageName}: current 401 clears identity and finishes loading`, async () => {
    const h = harness(pageName);
    const pending = h.load();
    h.calls[0].reject(Object.assign(new Error("expired"), { statusCode: 401 }));
    await pending;
    assert.equal(h.app.globalData.memberSessionToken, "");
    assert.equal(h.page.data.member, null);
    assert.equal(h.page.data.loading, false);
  });
}

test("home: a late volunteer 401 after hide cannot revoke a new account", async () => {
  const h = harness("home");
  const pending = h.page.loadHome();
  h.calls[0].resolve({ data: { member: { member_id: 1 } } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 2);
  h.page.onHide();
  h.app.globalData.memberSessionToken = "account-b";
  h.calls[1].reject(Object.assign(new Error("expired"), { statusCode: 401 }));
  await pending;
  assert.equal(h.app.globalData.memberSessionToken, "account-b");
  assert.equal(h.page.data.member, null);
  assert.equal(h.page.data.isVolunteer, false);
});

test("home: a hidden page cannot restore member or staff capabilities", async () => {
  const h = harness("home");
  const pending = h.page.loadHome();
  h.page.onUnload();
  h.calls[0].resolve({ data: { member: { member_id: 1 },
    identities: { operations: { is_employee: true } } } });
  await pending;
  assert.equal(h.calls.length, 1);
  assert.equal(h.page.data.member, null);
  assert.equal(h.page.data.isEmployee, false);
});
