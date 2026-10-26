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
    Page: value => { instance = value; }, require: name => name.endsWith("page-session") ? guardModule.exports : name.endsWith("application-material") ? require("../utils/application-material") : name.endsWith("mobile-activity") ? require("../utils/mobile-activity") : ({ request }),
    wx: { showToast() {}, showModal() {}, ...wx }
  });
  instance.setData = (value, callback) => { Object.assign(instance.data, value); if (callback) callback(); };
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


test("application images retain all material fields and split long text below canvas limits", async () => {
  const { applicationSections, drawApplicationImages } = require("../utils/application-material");
  const a = { name: "合成申请人", phone: "SYNTHETIC-PRIVATE", phone_masked: "合成脱敏", financial_fields_visible: true, annual_sales: "合成区间", company_products: "第一行\n" + "长内容".repeat(2500) + "最后一行", goal_years: 3, invoice_bank: "合成银行", notes: "合成备注", employee_count: 0 };
  const sections = applicationSections(a, true);
  const values = sections.flatMap(s => s.fields);
  assert(values.some(f => f.key === "employee_count" && f.value === "0"));
  assert(values.some(f => f.key === "annual_sales" && f.value === "合成区间"));
  assert.equal(applicationSections(a).flatMap(s => s.fields).find(f => f.key === "phone").value, "合成脱敏");
  const drawn = [], heights = [], contexts = [];
  let serial = 0;
  const context = { setFontSize() {}, measureText: s => ({ width: s.length * 22 }), setFillStyle() {}, fillRect() {}, fillText: text => drawn.push(text), draw: (_, done) => done() };
  const p = { setData: (value, done) => { heights.push(value.canvasHeight); done(); } };
  const wx = { createCanvasContext: () => context, canvasToTempFilePath: (args, owner) => { contexts.push(owner); assert(args.destHeight <= 4096); args.success({ tempFilePath: "synthetic-" + ++serial }); } };
  const files = await drawApplicationImages(wx, p, sections, () => true);
  assert(files.length > 3);
  assert(heights.every(h => h < 2048));
  assert(contexts.every(owner => owner === p));
  assert(drawn.join("").includes("最后一行"));
});

test("application save opens settings for denied album permission without requesting private export", async () => {
  let requests = 0, modal, settings = 0;
  const p = page("application-detail", async () => { requests++; }, { getSetting: args => args.success({ authSetting: { "scope.writePhotosAlbum": false } }), showModal: args => { modal = args; }, openSetting: () => settings++ });
  p.data.detail = { actions: { visit_export: false } };
  await p.saveImage();
  assert.equal(requests, 0);
  assert.equal(modal.confirmText, "去设置");
  modal.success({ confirm: true });
  assert.equal(settings, 1);
  assert.equal(p.data.exporting, false);
});

test("image failures distinguish canvas generation from album and privacy errors", () => {
  const { imageError } = require("../utils/application-material");
  assert.match(imageError({ errMsg: "canvasToTempFilePath:fail" }, "draw"), /生成失败/);
  assert.match(imageError({ errMsg: "saveImageToPhotosAlbum:fail auth deny" }, "album"), /相册权限/);
  assert.match(imageError({ errMsg: "privacy permission not granted" }, "authorization"), /隐私/);
});


test("complete application images save every generated page and record the declared recipient", async () => {
  let requested, count = 0, saved = 0, toast;
  const drawn = [];
  const context = { setFontSize() {}, measureText: s => ({ width: s.length * 22 }), setFillStyle() {}, fillRect() {}, fillText: text => drawn.push(text), draw: (_, done) => done() };
  const p = page("application-detail", async (url, options) => { requested = { url, options }; return { data: { application: { name: "合成申请", phone: "SYNTHETIC-COMPLETE", notes: "补充说明".repeat(1600) + "结尾不得截断", financial_fields_visible: true, annual_sales: "合成区间" }, purpose: "班主任走访面聊", exported_at: "synthetic-time" } }; }, {
    showActionSheet: args => args.success({ tapIndex: 0 }), requirePrivacyAuthorize: args => args.success({}), getSetting: args => args.success({ authSetting: { "scope.writePhotosAlbum": true } }),
    createCanvasContext: () => context, canvasToTempFilePath: (args, owner) => { assert.equal(owner, p); args.success({ tempFilePath: "synthetic-file-" + ++count }); },
    saveImageToPhotosAlbum: args => { saved++; args.success({}); }, getFileSystemManager: () => ({ unlink() {} }), showToast: value => { toast = value.title; }
  });
  p.onLoad({ application_id: "9" }); p.data.detail = { actions: { visit_export: true } };
  await p.saveImage();
  assert.match(requested.url, /visit-image-data$/);
  assert.equal(requested.options.method, "POST");
  assert.equal(requested.options.data.recipient, "CLASS_TEACHER");
  assert.equal(requested.options.data.confirmed, true);
  assert(count > 3); assert.equal(saved, count);
  assert.match(toast, new RegExp(String(count)));
  assert(drawn.join("").includes("结尾不得截断"));
  assert.equal(p.data.exporting, false);
  assert.equal(p._imagePaths.length, 0);
});

test("mobile activity actions respect staff permissions, session windows and immutable checkins", () => {
  const a = require("../utils/mobile-activity");
  const detail = { group: { status: "CONFIRMED", checkin_status: "open", session_code: "MORNING" }, can_manage: true, can_status: true };
  const row = { registration_id: "row", checked: false };
  assert.equal(a.rowActions(detail, row).length, 5);
  assert.equal(a.rowActions(detail, { ...row, checked: true }).length, 0);
  assert.equal(a.rowActions({ ...detail, group: { ...detail.group, checkin_status: "ended" } }, row).length, 0);
  assert.equal(a.rowActions({ ...detail, can_manage: false, can_status: false }, row).length, 0);
  assert.ok(!a.rowActions(detail, { ...row, is_team: true }).some(r => r.value === "manual_checkin"));
  assert.ok(!a.rowActions({ ...detail, group: { ...detail.group, session_code: "KONPA" } }, row).some(r => r.value === "late"));
  const view = a.decorate({ ...detail, group: { ...detail.group, activity_type: "class_meeting" }, statistics: { class_statistics: { present_count: 28, roster_count: 35, member_present_count: 26, staff_present_count: 2, participation_rate: .8 }, group_totals: { participation_rate: 25/34 } }, attendees: [{ ...row, registered_display_name: "合成新名", name: "合成旧名" }] }, "新名");
  assert.equal(view.statistics.overallRate, "80.0%");
  assert.equal(view.statistics.staffPresent, 2);
  assert.equal(view.attendees.length, 1);
  assert.equal(a.clockFromIso("2026-10-25T23:30:00Z"), "07:30");
});

test("mobile class creation confirms three trusted sessions and keeps the same intent on timeout", async () => {
  const calls = [];
  const p = page("activity-detail", async (url, options) => { calls.push({url, options}); throw new Error("timeout"); }, { showModal: options => { if (options.success) options.success({ confirm: true }); } });
  p.onLoad({});
  p.data.classes = [{ id: "class-a", name: "合成班级" }]; p.data.activityType = "class_meeting"; p.data.title = "合成班会"; p.data.eventDate = "2026-10-30";
  await p.save(); await p.save();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.data.request_id, calls[1].options.data.request_id);
  assert.equal(calls[0].options.data.class_org_unit_id, "class-a");
  assert.equal(calls[0].options.data.activity_type, "class_meeting");
  assert.equal(calls[0].options.data.class_times.morning_scheduled_start, "09:00");
  assert.equal(calls[0].options.data.roster_members, undefined);
});

test("hiding phone activity cancels delayed confirmations and a late QR file is deleted", async () => {
  let confirm, resolveFile;
  const calls = [], deleted = [];
  const p = page("activity-detail", async (url) => { calls.push(url); return { data: { image_base64: "synthetic" } }; }, {
    env: { USER_DATA_PATH: "/synthetic" },
    getFileSystemManager: () => ({ writeFile: options => { resolveFile = options.success; }, unlink: options => deleted.push(options.filePath) }),
    showModal: options => { confirm = options.success; }
  });
  p.onLoad({ event_id: "scene" });
  p.data.detail = { can_code: true, group: { manual_status: "closed" }, revision: "synthetic" };
  p.setCheckinState();
  p.onHide(); p.onShow = () => {}; confirm({ confirm: true });
  assert.equal(calls.length, 0);
  // A new visible page may request a code; hiding during file IO still clears it.
  p._privatePageHidden = false;
  const guard = require("../utils/page-session"); guard.showPrivatePage(p);
  p.data.detail = { can_code: true };
  const pending = p.showCode(); await new Promise(resolve => setImmediate(resolve));
  p.onHide(); resolveFile(); await pending;
  assert.equal(p.data.codePath, ""); assert.equal(deleted.length, 1);
});

test("activity entry explains missing permission before requesting any event data", async () => {
  const calls=[];
  const p=page("activities",async url=>{ calls.push(url); return { data:{can_view:false,organizations:[],permission_message:"请按已有范围授权活动签到运营"} }; });
  p.onLoad();await p.load();
  assert.equal(calls.length,1);assert.match(calls[0],/activity-context$/);
  assert.equal(p.data.needsAuthorization,true);assert.equal(p.data.canCreate,false);
  assert.equal(p.data.records.length,0);assert.match(p.data.permissionMessage,/授权/);
});

test("late activity context after hiding cannot request records or restore permissions", async () => {
  let finish;const calls=[];
  const p=page("activities",url=>{ calls.push(url);return new Promise(resolve=>{finish=resolve;}); });
  p.onLoad();const pending=p.load();p.onHide();finish({data:{can_view:true,organizations:[{id:"synthetic"}]}});await pending;
  assert.equal(calls.length,1);assert.equal(p.data.records.length,0);assert.equal(p.data.canCreate,false);
});
