const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function page(name, request, wx = {}) {
  let instance;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages/operations", `${name}.js`), "utf8"), {
    Page: value => { instance = value; }, require: () => ({ request }),
    wx: { showToast() {}, navigateBack() {}, ...wx }
  });
  instance.setData = value => Object.assign(instance.data, value);
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
