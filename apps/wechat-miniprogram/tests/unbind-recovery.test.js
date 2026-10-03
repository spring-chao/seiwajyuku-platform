const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(request, session = "account-a", login) {
  const calls = [];
  const app = { globalData: { personSessionToken: session, memberSessionToken: session },
    setPersonSession(value) { this.globalData.personSessionToken = this.globalData.memberSessionToken = value; },
    clearPersonSession() { this.setPersonSession(""); calls.push("cleared"); } };
  const wx = { login: login || (options => options.success({ code: "fresh-login" })),
    showModal: value => calls.push(value), showToast: value => calls.push(value), navigateBack() {},
    redirectTo: value => calls.push(value) };
  const utility = { exports: {} }, guard = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/identity-session.js"), "utf8"), {
    module: utility, getApp: () => app, require: () => ({ request }), wx
  });
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/page-session.js"), "utf8"), {
    module: guard, getApp: () => app
  });
  function page(name) {
    let page;
    vm.runInNewContext(fs.readFileSync(path.join(root, "pages", name + ".js"), "utf8"), {
      Page: value => { page = value; }, getApp: () => app, wx,
      require: name => name.endsWith("identity-session") ? utility.exports : name.endsWith("page-session")
        ? guard.exports : ({ request, resolveVolunteerServices: () => ({ roles: [] }) })
    });
    page.setData = value => Object.assign(page.data, value);
    return page;
  }
  return { app, calls, wx, page, ...utility.exports };
}

test("binding conflict recovers a server binding even with no cached session, then binds another person", async () => {
  let serverBound = true;
  const requests = [];
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    if (url.endsWith("revoke")) { serverBound = false; return { success: true, data: { revoked: true } }; }
    if (serverBound) throw Object.assign(new Error("当前微信已绑定其他人员，请先解绑后再绑定"), { statusCode: 400 });
    return { data: { access_token: "account-b", identities: { identity_kinds: ["MEMBER"] } } };
  }, "");
  const page = h.page("identity/bind");
  Object.assign(page.data, { name: "synthetic-b", phone: "13800000000" });
  await page.bindIdentity();
  assert.equal(h.calls[0].confirmText, "解除绑定");
  h.calls[0].success({ confirm: true }); await tick();
  assert.equal(serverBound, false);
  assert.equal(requests[1].options.data.wx_login_code, "fresh-login");
  assert.equal(page.data.phone, "13800000000");
  assert.equal(page.data.unbinding, false);
  await page.bindIdentity();
  assert.equal(h.app.globalData.personSessionToken, "account-b");
});

for (const response of [{ success: false, data: { revoked: true } }, { data: { revoked: false } }, { data: {} }]) {
  test("unbinding never clears local identity without an explicit successful server acknowledgement " + JSON.stringify(response), async () => {
    const h = harness(async () => response);
    await assert.rejects(h.revokeCurrentBinding(), /服务器未确认/);
    assert.equal(h.app.globalData.personSessionToken, "account-a");
    assert.equal(h.identityChangePending(), false);
  });
}

test("network failure keeps the binding and permits an explicit retry", async () => {
  const h = harness(async () => { throw new Error("offline"); });
  await assert.rejects(h.revokeCurrentBinding(), /offline/);
  assert.equal(h.app.globalData.personSessionToken, "account-a");
  assert.equal(h.identityChangePending(), false);
});

test("legacy server expiry provides a recovery instruction without claiming unbind succeeded", async () => {
  const h = harness(async () => { throw Object.assign(new Error("expired"), { statusCode: 401 }); }, "");
  await assert.rejects(h.revokeCurrentBinding(), /原绑定人员.*恢复登录/);
  assert.equal(h.calls.length, 0);
});

test("the old server can still revoke with the captured token and an explicit acknowledgement", async () => {
  const h = harness(async (url, options) => {
    assert.equal(url, "/api/v1/wechat/member-bindings/revoke");
    assert.equal(options.header.Authorization, "Bearer account-a");
    assert.equal(options.data.wx_login_code, "fresh-login");
    return { data: { revoked: true } };
  });
  assert.equal(await h.revokeCurrentBinding(), true);
  assert.equal(h.app.globalData.personSessionToken, "");
});

test("an account replacement during revoke cannot clear the new session", async () => {
  let finish;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  const pending = h.revokeCurrentBinding(); await tick();
  h.app.setPersonSession("account-b");
  finish({ data: { revoked: true } });
  assert.equal(await pending, false);
  assert.equal(h.app.globalData.personSessionToken, "account-b");
});

test("leaving during WeChat login cancels revoke before the server request", async () => {
  let login, sent = 0, visible = true;
  const h = harness(async () => { sent++; }, "account-a", options => { login = options; });
  const pending = h.revokeCurrentBinding(() => visible);
  visible = false; login.success({ code: "fresh-login" });
  assert.equal(await pending, false); assert.equal(sent, 0);
});

test("pending revoke blocks repeated revoke and either binding page", async () => {
  let finish, sent = 0;
  const h = harness(() => { sent++; return new Promise(resolve => { finish = resolve; }); });
  const pending = h.revokeCurrentBinding(); await tick();
  await assert.rejects(h.revokeCurrentBinding(), /正在解除/);
  for (const name of ["identity/bind", "identity/staff-bind"]) {
    const page = h.page(name);
    Object.assign(page.data, { name: "synthetic", phone: "13800000000", username: "synthetic", password: "synthetic" });
    await page[name.endsWith("staff-bind") ? "bindStaffIdentity" : "bindIdentity"]();
  }
  assert.equal(sent, 1); finish({ data: { revoked: true } }); await pending;
});

test("an in-flight bind keeps unbinding locked until its server response settles", async () => {
  let finish;
  const h = harness(() => new Promise(resolve => { finish = resolve; }));
  const page = h.page("identity/bind");
  Object.assign(page.data, { name: "synthetic", phone: "13800000000" });
  const binding = page.bindIdentity(); await tick(); page.onHide();
  assert.equal(h.identityChangePending(), true);
  await assert.rejects(h.revokeCurrentBinding(), /正在解除/);
  finish({ data: { access_token: "obsolete" } }); await binding;
  assert.equal(h.identityChangePending(), false);
  assert.equal(h.app.globalData.personSessionToken, "account-a");
});

test("home confirmation cannot revoke an identity replaced while its dialog was open", async () => {
  let sent = 0;
  const h = harness(async () => { sent++; }); const page = h.page("home/index");
  page.unbind(); h.app.setPersonSession("account-b");
  await h.calls[0].success({ confirm: true });
  assert.equal(sent, 0); assert.equal(h.app.globalData.personSessionToken, "account-b");
});

test("a home unbind confirmation from an earlier visit stays cancelled after returning", async () => {
  let sent = 0;
  const h = harness(async () => { sent++; }); const page = h.page("home/index");
  page.unbind(); page.onHide(); page._homeVisible = true;
  await h.calls[0].success({ confirm: true }); assert.equal(sent, 0);
});

test("staff-only identity management remains on the page with an unbind action", async () => {
  const h = harness(async url => ({ data: url.endsWith("/me")
    ? { identities: { operations: { is_employee: true } } }
    : { entries: [{ key: "today_actions" }] } }));
  const page = h.page("home/index"); page.onLoad({ manage_identity: "1" });
  await page.loadHome();
  assert.equal(page.data.identityState, "bound");
  assert.equal(page.data.isEmployee, true);
  assert.equal(h.calls.length, 0);
});
