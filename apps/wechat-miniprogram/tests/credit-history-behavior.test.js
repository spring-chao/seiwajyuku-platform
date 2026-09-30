const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function page(name, request) {
  let instance;
  const navigation = [];
  const app = { globalData: { memberSessionToken: "synthetic-session" },
    clearMemberSession() { this.globalData.memberSessionToken = ""; this.cleared = true; } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages/credits", `${name}.js`), "utf8"), {
    Page: value => { instance = value; }, getApp: () => app,
    require: () => ({ request }), wx: { navigateTo: value => navigation.push(value.url) }
  });
  instance.setData = value => Object.assign(instance.data, value);
  return { p: instance, app, navigation };
}
const row = ref => ({ entry_ref: ref, points: "2.00" });

test("history carries snapshot anchor, retries the same page, and deduplicates", async () => {
  const calls = [];
  let fail = true;
  const { p } = page("index", async (url, options) => {
    calls.push(url); assert.equal(options.auth, true);
    if (calls.length === 1) return { data: { entries: [row("9007199254740993")], snapshot_id: "91", next_offset: 20, has_more: true } };
    if (fail) { fail = false; throw new Error("synthetic timeout"); }
    return { data: { entries: [row("9007199254740993"), row("2")], snapshot_id: "91", next_offset: 21, has_more: false } };
  });
  await p.onShow(); await p.loadMore();
  assert.equal(p.data.nextOffset, 20);
  await p.loadMore();
  assert.equal(calls[1], calls[2]);
  assert.match(calls[2], /offset=20&snapshot_id=91/);
  assert.deepEqual(Array.from(p.data.entries, item => item.entry_ref), ["9007199254740993", "2"]);
  assert.equal(p.data.hasMore, false);
});

test("double load cannot duplicate request; hide rejects late private data", async () => {
  let finish, count = 0;
  const { p } = page("index", () => { count++; return new Promise(resolve => { finish = resolve; }); });
  const pending = p.onShow(); await p.loadMore();
  assert.equal(count, 1); p.onHide();
  finish({ data: { entries: [row("1")], next_offset: 1, has_more: false } });
  await pending; assert.equal(p.data.entries.length, 0);
});

test("expired session clears existing history instead of retaining private rows", async () => {
  let count = 0;
  const { p, app } = page("index", async () => {
    if (++count === 1) return { data: { entries: [row("1")], next_offset: 20, snapshot_id: "1", has_more: true } };
    throw { statusCode: 401 };
  });
  await p.onShow(); await p.loadMore();
  assert.equal(app.cleared, true); assert.equal(p.data.entries.length, 0);
  assert.equal(p.data.hasMore, false);
});

test("account change during later-page request clears the previously displayed account", async () => {
  let finish, count = 0;
  const { p, app } = page("index", () => {
    if (++count === 1) return Promise.resolve({ data: { entries: [row("1")], next_offset: 20, snapshot_id: "1", has_more: true } });
    return new Promise(resolve => { finish = resolve; });
  });
  await p.onShow(); const pending = p.loadMore();
  app.globalData.memberSessionToken = "new-account";
  finish({ data: { entries: [row("2")], next_offset: 21, has_more: false } }); await pending;
  assert.equal(p.data.entries.length, 0); assert.equal(p.data.snapshotId, null);
});

test("detail responses after account change or hide never restore data", async () => {
  for (const action of ["logout", "hide"]) {
    let finish;
    const { p, app } = page("detail", () => new Promise(resolve => { finish = resolve; }));
    p.onLoad({ entry_ref: "12" }); const pending = p.onShow();
    if (action === "hide") p.onHide(); else app.globalData.memberSessionToken = "different-session";
    finish({ data: row("12") }); await pending;
    assert.equal(p.data.entry, null);
  }
});

test("navigation preserves bigint reference; unknown and malformed references are rejected", async () => {
  const { p, navigation } = page("index", async () => ({ data: {
    entries: [row("9007199254740993")], next_offset: 1, snapshot_id: "9007199254740993", has_more: false
  } }));
  await p.onShow();
  for (const ref of ["unknown", "123", "../private"]) p.openEntry({ currentTarget: { dataset: { ref } } });
  assert.equal(navigation.length, 0);
  p.openEntry({ currentTarget: { dataset: { ref: "9007199254740993" } } });
  assert.equal(navigation[0], "/pages/credits/detail?entry_ref=9007199254740993");
});

test("detail refuses malformed input and shows a privacy-neutral 404", async () => {
  let count = 0;
  const { p } = page("detail", async () => { count++; throw { statusCode: 404 }; });
  p.onLoad({ entry_ref: "../1" }); await p.onShow(); assert.equal(count, 0);
  p.onLoad({ entry_ref: "1" }); await p.onShow();
  assert.equal(count, 1); assert.equal(p.data.errorMessage, "学分记录不存在。");
  assert.equal(p.data.entry, null);
});

test("credit pages are registered and summary contract remains separate", () => {
  const root = path.join(__dirname, "..");
  const config = JSON.parse(fs.readFileSync(path.join(root, "app.json"), "utf8"));
  for (const name of ["index", "detail"]) {
    assert.ok(config.pages.includes(`pages/credits/${name}`));
    for (const extension of ["js", "json", "wxml", "wxss"]) assert.ok(fs.existsSync(path.join(root, "pages/credits", `${name}.${extension}`)));
  }
});
