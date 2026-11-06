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


test("dashboard birthdays filter by class and month without changing the overview month", async () => {
  const paths = []; let navigation;
  const p = page("dashboard", async url => {
    paths.push(url);
    const month = Number(url.split("birthday_month=")[1]);
    return { data: { summary: { birthday_member_count: 7 }, classes: [
      { class_org_unit_id: "a", class_name: "合成甲班" }, { class_org_unit_id: "b", class_name: "合成乙班" }
    ], birthday_members: month === 2 ? [] : [
      { member_id: 1, name: "合成甲", class_org_unit_id: "a", birthday: "01-15" },
      { member_id: 2, name: "合成乙", class_org_unit_id: "b", birthday: "01-20" }
    ] } };
  }, { navigateTo: options => { navigation = options.url; } });
  p.data.month = "2026-11"; p.data.birthdayMonth = 1;
  await p.load();
  assert.equal(p.data.birthdayMembers.length, 2);
  p.chooseBirthdayClass({ detail: { value: "1" } });
  assert.equal(p.data.birthdayMembers.length, 1);
  assert.equal(p.data.birthdayMembers[0].member_id, 1);
  await p.chooseBirthdayMonth({ detail: { value: "2" } });
  assert.match(paths.at(-1), /year=2026&month=11&birthday_month=2$/);
  assert.equal(p.data.birthdayMembers.length, 0);
  assert.equal(p.data.birthdayClassId, "a");
  assert.equal(p.data.metrics.find(m => m.key === "birthday_member_count").value, 7);
  await p.chooseBirthdayMonth({ detail: { value: "0" } });
  assert.match(paths.at(-1), /birthday_month=0$/);
  assert.equal(p.data.birthdayMembers.length, 1);
  p.openBirthday({ currentTarget: { dataset: { item: p.data.birthdayMembers[0] } } });
  assert.match(navigation, /birthday_due_date=2026-01-15$/);
  p.onHide();
  assert.equal(p.data.birthdayMembers.length, 0);
  assert.equal(p.data.birthdayClasses.length, 1);
});

test("dashboard ignores outdated month results and clears the list after a failed request", async () => {
  const pending = [];
  const p = page("dashboard", () => new Promise((resolve, reject) => pending.push({ resolve, reject })));
  p.data.month = "2026-11";
  const first = p.load(); const second = p.chooseBirthdayMonth({ detail: { value: "2" } });
  pending[1].resolve({ data: { summary: {}, birthday_members: [{ member_id: 2, birthday: "02-01" }] } });
  await second;
  pending[0].resolve({ data: { summary: {}, birthday_members: [{ member_id: 1, birthday: "01-01" }] } });
  await first;
  assert.equal(p.data.birthdayMembers[0].member_id, 2);
  const third = p.load(); pending[2].reject(new Error("合成失败")); await third;
  assert.equal(p.data.birthdayMembers.length, 0);
  assert.equal(p.data.errorMessage, "合成失败");
});
