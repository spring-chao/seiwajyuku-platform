const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { applicationSections } = require("../utils/application-material");
function page(name, requestModule, wx = {}) {
  let p;
  const app = { globalData: { personSessionToken: "synthetic-session" } };
  const guard = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/page-session.js"), "utf8"), { module: guard, getApp: () => app });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, `../pages/operations/${name}.js`), "utf8"), {
    Page: value => { p = value; }, wx, require: name => name.endsWith("page-session") ? guard.exports : requestModule
  });
  p.setData = patch => Object.assign(p.data, patch);
  return { p, app };
}
test("renewal watch defaults to current-month chase and orders all three tabs", async () => {
  const { p } = page("renewal-watch", { request: async () => ({ data: { groups: [
    { key: "OBSERVE_3", items: [{ member_id: 3 }] }, { key: "RENEW_2", items: [{ member_id: 2 }] }, { key: "FOLLOW_1", items: [{ member_id: 1 }] }
  ] } }) });
  await p.load();
  assert.equal(p.data.activeStage, "FOLLOW_1");
  assert.equal(p.data.items[0].member_id, 1);
  assert.deepEqual(Array.from(p.data.groups, item => item.key), ["FOLLOW_1", "RENEW_2", "OBSERVE_3"]);
});
test("operations photo survives native image viewer hide and is cleaned on return", async () => {
  let preview;
  const removed = [];
  const { p } = page("study-meeting-detail", { downloadProtected: async () => "synthetic-private.jpg", request: async () => ({ data: {} }) }, {
    previewImage: options => { preview = options; p.onHide(); },
    getFileSystemManager: () => ({ unlink: options => removed.push(options.filePath) })
  });
  await p.viewPhoto();
  assert.equal(preview.current, "synthetic-private.jpg");
  assert.equal(removed.length, 0, "viewer must still be able to read its local file");
  assert.equal(p.data.detail, null, "private page content is cleared while hidden");
  p.onShow();
  assert.deepEqual(removed, ["synthetic-private.jpg"]);
});
test("photo finishing after leaving page is discarded without opening viewer", async () => {
  let finish, previews = 0;
  const removed = [];
  const { p } = page("study-meeting-detail", { downloadProtected: () => new Promise(resolve => { finish = resolve; }) }, {
    previewImage: () => previews++, getFileSystemManager: () => ({ unlink: options => removed.push(options.filePath) })
  });
  const pending = p.viewPhoto(); p.onUnload(); finish("synthetic-private.jpg"); await pending;
  assert.equal(previews, 0); assert.equal(removed.length, 1);
});
test("legacy invoice is shown as Chinese named fields with no raw JSON or empty old questions", () => {
  const fields = applicationSections({ invoice_info: JSON.stringify({ invoice_type: "SPECIAL", invoice_title: "合成企业", invoice_tax_id: "SYNTHETIC-TAX", internal_code: "NEVER_DISPLAY" }), goal_years: 3 }).flatMap(s => s.fields);
  assert.equal(fields.find(f => f.key === "invoice_type").value, "专票");
  assert.equal(fields.find(f => f.key === "invoice_title").value, "合成企业");
  assert(!fields.some(f => ["invoice_info", "books_read", "enrollment_reason_philosophy", "learning_participation_goal", "business_goal"].includes(f.key)));
  assert(!JSON.stringify(fields).includes("NEVER_DISPLAY"));
});
test("current submitted fields and actually filled older answers remain visible in saved images too", () => {
  const fields = applicationSections({ invoice_type: "NORMAL", invoice_title: "当前抬头", invoice_info: '{"invoice_title":"旧抬头"}', books_read: ["合成书名甲", "合成书名乙"], enrollment_reason_change: "合成学习目标", employee_count: 0, notes: "合成补充" }, true).flatMap(s => s.fields);
  assert.equal(fields.find(f => f.key === "invoice_title").value, "当前抬头");
  assert.equal(fields.find(f => f.key === "books_read").value, "合成书名甲、合成书名乙");
  assert.equal(fields.find(f => f.key === "employee_count").value, "0");
  assert.equal(fields.find(f => f.key === "enrollment_reason_change").value, "合成学习目标");
});
