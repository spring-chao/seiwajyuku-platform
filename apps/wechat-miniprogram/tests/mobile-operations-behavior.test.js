const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function page(name, request, wx = {}) {
  let instance;
  const app = { globalData: { personSessionToken: "synthetic-session" } };
  const guardModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/page-session.js"), "utf8"), {
    module: guardModule, getApp: () => app
  });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages/operations", `${name}.js`), "utf8"), {
    Page: value => { instance = value; }, require: name => name.endsWith("page-session") ? guardModule.exports : ({ request }),
    wx: { showToast() {}, navigateBack() {}, ...wx }
  });
  instance.setData = value => Object.assign(instance.data, value);
  instance.app = app;
  return instance;
}

test("double taps submit once; uncertain retries reuse the receipt key", async () => {
  let reject, resolve;
  const calls = [];
  const p = page("care-record", (_, options) => {
    calls.push(options.data);
    return new Promise((yes, no) => { resolve = yes; reject = no; });
  });
  p.data.memberId = 7;
  p.data.situation = "synthetic care";
  const first = p.submit();
  await p.submit();
  assert.equal(calls.length, 1);
  reject(new Error("network timeout"));
  await first;
  const retry = p.submit();
  assert.equal(calls[0].idempotency_key, calls[1].idempotency_key);
  resolve({ data: {} });
  await retry;
  await p.submit();
  assert.equal(calls.length, 2);
});

test("late contact responses cannot restore contact after hide, even after return", async () => {
  let finish, modal;
  const p = page("member-detail", () => new Promise(resolve => { finish = resolve; }), {
    showModal: options => { modal = options.success({ confirm: true }); }
  });
  p.data.memberId = 7;
  p.data.detail = { actions: { can_reveal_contact: true } };
  p.revealContact();
  p.onHide();
  p._visible = true;
  finish({ data: { phone: "SYNTHETIC-CONTACT" } });
  await modal;
  assert.equal(p.data.contactPhone, "");
});

test("contact confirmation after unload never starts a request", async () => {
  let confirmation, calls = 0;
  const p = page("member-detail", async () => { calls++; return { data: {} }; }, {
    showModal: options => { confirmation = options.success; }
  });
  p.data.detail = { actions: { can_reveal_contact: true } };
  p.revealContact(); p.onUnload();
  await confirmation({ confirm: true });
  assert.equal(calls, 0);
});

test("AI regenerate makes fresh POST requests, ignores double taps and retains edited text on failure", async () => {
  let finish; const calls = [];
  const p = page("member-detail", (path, options) => { calls.push([path, options]); return new Promise(resolve => { finish = resolve; }); });
  Object.assign(p.data, { loading: false, memberId: 7, renewalCycleId: 8, wechatCopy: "原文" });
  const first = p.regenerateCopy(); await p.regenerateCopy();
  assert.equal(calls.length, 1); assert.equal(calls[0][1].method, "POST");
  assert.match(calls[0][0], /renewals\/8\/care-draft$/);
  finish({ data: { status: "GENERATED", body: "新文案" } }); await first;
  assert.equal(p.data.wechatCopy, "新文案");
  const second = p.regenerateCopy();
  p.editCopy({ detail: { value: "手动编辑" } });
  finish({ data: { status: "GENERATED", body: "迟到文案" } }); await second;
  assert.equal(p.data.wechatCopy, "手动编辑"); assert.match(p.data.copyError, /已编辑/);
  const third = p.regenerateCopy(); finish({ data: { status: "PROVIDER_UNAVAILABLE" } }); await third;
  assert.equal(p.data.wechatCopy, "手动编辑"); assert.equal(p.data.copyGenerating, false);
});

test("birthday regenerate uses AI endpoint and cannot restore text after leaving the page", async () => {
  let finish; const paths = [];
  const p = page("member-detail", (path) => { paths.push(path); return new Promise(resolve => { finish = resolve; }); });
  Object.assign(p.data, { loading: false, memberId: 7, birthdayDueDate: "2026-11-06", wechatCopy: "原文" });
  const first = p.regenerateCopy();
  finish({ data: { generation_status: "GENERATED", draft: "生日新文案" } }); await first;
  assert.equal(p.data.wechatCopy, "生日新文案"); assert.match(paths[0], /members\/7\/birthday-copy$/);
  const second = p.regenerateCopy(); p.onHide();
  finish({ data: { generation_status: "GENERATED", draft: "迟到文案" } }); await second;
  assert.equal(p.data.wechatCopy, ""); assert.equal(p.data.copyGenerating, false);
});

test("member renewal entry selects the matching stage and filters to that member", async () => {
  const p = page("renewal-watch", async () => ({ data: { groups: [
    { key: "OBSERVE_3", items: [{ member_id: 8 }] },
    { key: "FOLLOW_1", items: [{ member_id: 8 }, { member_id: 7 }] }
  ] } }));
  p.onLoad({ member_id: "7" }); await p.load();
  assert.equal(p.data.activeStage, "FOLLOW_1");
  assert.deepEqual(Array.from(p.data.items, x => x.member_id), [7]);
  p.showAll();
  assert.equal(p.data.items.length, 2);
});
