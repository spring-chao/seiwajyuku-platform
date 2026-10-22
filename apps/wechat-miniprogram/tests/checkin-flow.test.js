const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const checkin = require("../utils/checkin");
const scan = require("../utils/scan");
const root = path.resolve(__dirname, "..");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness(name = "checkin/index", handler = async () => ({ data: {} }), token = "account-a") {
  const app = { globalData: { personSessionToken: token, memberSessionToken: token, apiBaseUrl: "https://stage.example.test/platform" },
    clearMemberSession() { this.globalData.personSessionToken = ""; this.globalData.memberSessionToken = ""; },
    setPersonSession(next) { this.globalData.personSessionToken = next; this.globalData.memberSessionToken = next; } };
  const guard = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/page-session.js"), "utf8"), { module: guard, getApp: () => app });
  let page;
  const calls = [], navigations = [], copied = [], modals = [];
  const wx = { navigateTo: value => navigations.push(value.url), redirectTo: value => navigations.push(value.url),
    navigateBack: () => navigations.push("back"), login: value => value.success({ code: "synthetic-login" }),
    showModal: value => modals.push(value), showToast() {}, setClipboardData: value => copied.push(value.data) };
  const checkinModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/checkin.js"), "utf8"), { module: checkinModule, wx });
  vm.runInNewContext(fs.readFileSync(path.join(root, "pages", name + ".js"), "utf8"), {
    Page(value) { page = value; }, getApp: () => app,
    require(module) {
      if (module.endsWith("page-session")) return guard.exports;
      if (module.endsWith("checkin")) return checkinModule.exports;
      if (module.endsWith("checkin-success")) return require("../utils/checkin-success");
      if (module.endsWith("scan")) return scan;
      if (module.endsWith("identity-session")) return { identityChangePending: () => false };
      return { request(url, options = {}) { calls.push({ url, options }); return handler(url, options); } };
    },
    wx
  });
  page.setData = values => Object.assign(page.data, values);
  return { page, app, wx, calls, navigations, copied, modals };
}

function context(eventId = 10, extra = {}) {
  return { data: { event: { event_id: eventId, title: "测试班级学习会", event_date: "2026-10-07", session_code: "MORNING" },
    member: { name: "合成学员", class_name: "测试班级" }, registration: { registration_id: 2, registered_name: "合成学员" },
    can_checkin: true, already_checked_in: false, ...extra } };
}

test("activity scene and scanned route resolve opaque token; generic QR data and invalid IDs fail closed", () => {
  assert.deepEqual(checkin.checkinTarget({ scene: "short_token_1234567890" }), { token: "short_token_1234567890" });
  assert.deepEqual(checkin.checkinTarget({ scene: "ci_short_123456789012" }), { token: "short_123456789012" });
  assert.deepEqual(checkin.checkinTarget({ path: "pages/checkin/index?scene=opaque_123456789012" }), { token: "opaque_123456789012" });
  assert.deepEqual(checkin.checkinTarget({ path: "pages/checkin/index", scene: "opaque_123456789012" }), { token: "opaque_123456789012" });
  assert.deepEqual(checkin.checkinTarget({ result: "https://example.test/checkin?event_id=12" }), { event_id: "12" });
  assert.deepEqual(checkin.checkinTarget({ result: JSON.stringify({ type: "activity", event_id: 15 }) }), { event_id: "15" });
  assert.deepEqual(checkin.checkinTarget({ result: { type: "activity", event_id: 15 } }), { event_id: "15" });
  assert.deepEqual(checkin.checkinTarget({ event_id: "event_abcdef-123" }), { event_id: "event_abcdef-123" });
  assert.equal(checkin.checkinTarget({ result: "https://example.test/unknown?token=opaque" }), null);
  for (const options of [{ scene: "home" }, { token: "../../secret" }, { event_id: "0" }, { event_id: "-1" }, { event_id: "1&member_id=2" }]) {
    assert.equal(checkin.checkinTarget(options), null);
  }
  assert.equal(scan.classifyScanResult({ path: "pages/checkin/index?scene=opaque" }), "activity");
  assert.equal(scan.classifyScanResult({ scene: "ci_opaque" }), "activity");
});

test("checkin dates show Beijing calendar days while preserving actual time limits", () => {
  const source = { event_date: "2026-10-11", checkin_start_at: "2026-10-10T16:00:00.000Z", checkin_end_at: "2026-10-11T15:59:00.000Z" };
  const shown = checkin.displayEvent(source);
  assert.equal(shown.event_date_display, "2026-10-11");
  assert.equal(shown.checkin_start_date, "2026-10-11");
  assert.equal(shown.checkin_end_date, "2026-10-11");
  assert.equal(shown.checkin_start_at, source.checkin_start_at);
  assert.equal(shown.checkin_end_at, source.checkin_end_at);
  const local = checkin.displayEvent({ checkin_open_at: "2026-10-11 00:00:00", checkin_close_at: "2026-10-11T23:59:00+08:00" });
  assert.equal(local.checkin_start_date, "2026-10-11");
  assert.equal(local.checkin_end_date, "2026-10-11");
  assert.equal(checkin.displayEvent({ checkin_start_at: "invalid" }).checkin_start_date, "");
  assert.equal(checkin.displayEvent({}).checkin_end_date, "");
});

test("bound member confirms only server resolved identity, double taps write once and pending sync remains successful", async () => {
  const pending = deferred();
  const h = harness("checkin/index", async (_, options) => options.method === "POST" ? pending.promise : context());
  h.page.onLoad({ scene: "short_token_1234567890" }); await h.page.onShow();
  assert.equal(h.page.data.member.name, "合成学员");
  assert.equal(h.page.data.event.session_name, "上午");
  const first = h.page.confirmCheckin(); await h.page.confirmCheckin();
  assert.equal(h.calls.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[1].options.data)), { event_id: "10", token: "short_token_1234567890" });
  pending.resolve({ data: { status: "CHECKED_IN", checked_at: "2026-10-07T09:00:00+08:00", sync_status: "PENDING", history_kind: "learning" } });
  await first;
  assert.equal(h.page.data.alreadyChecked, true);
  assert.equal(h.page.data.result.sync_status, "PENDING");
  h.page.openHistory(); assert.equal(h.navigations[0], "/pages/history/index?kind=learning");
  await h.page.confirmCheckin(); assert.equal(h.calls.length, 2);
});

test("duplicate acknowledgement shows already checked without generating a client attendance record", async () => {
  const h = harness("checkin/index", async (_, options) => options.method === "POST"
    ? { data: { status: "ALREADY_CHECKED_IN", sync_status: "SYNCED", event: { event_id: 10 } } } : context());
  h.page.onLoad({ event_id: "10" }); await h.page.onShow(); await h.page.confirmCheckin();
  assert.equal(h.page.data.alreadyChecked, true);
  assert.equal(h.page.data.notice, "本场次已签到");
  assert.equal(h.app.globalData.attendanceRecords, undefined);
});

test("WeChat activity scene offers guest name entry and optional member binding returns to the same scene", async () => {
  const h = harness("checkin/index", async () => context(10, { member: null, can_checkin: false, guest_allowed: true }), "");
  h.page.onLoad({ scene: "scene-token-123456789" }); await h.page.onShow();
  assert.equal(h.calls[0].url, "/api/v1/wechat/checkin/entry");
  assert.equal(h.page.data.guestAllowed, true); h.page.openBinding();
  assert.equal(h.navigations[0], "/pages/identity/bind?return_checkin=%2Fpages%2Fcheckin%2Findex%3Ftoken%3Dscene-token-123456789");
  const b = harness("identity/bind", async () => ({ data: { access_token: "bound", member: { name_masked: "测*" }, identities: { identity_kinds: ["MEMBER"] } } }), "");
  b.page.onLoad({ return_checkin: "/pages/checkin/index?token=scene-token-123456789" });
  b.page.onShow(); Object.assign(b.page.data, { name: "synthetic", phone: "13800000000" });
  await b.page.bindIdentity(); b.modals[0].success();
  assert.equal(b.navigations[0], "/pages/checkin/index?token=scene-token-123456789");
  b.page.onLoad({ return_checkin: "https://untrusted.test/" }); assert.equal(b.page._returnCheckin, "");
});

test("separate morning and afternoon event IDs remain independent", async () => {
  const h = harness("checkin/index", async url => context(url.includes("=11") ? 11 : 10,
    url.includes("=11") ? { event: { event_id: 11, title: "同一活动下午", session_code: "AFTERNOON" } } : { already_checked_in: true, can_checkin: false }));
  h.page.onLoad({ event_id: 10 }); await h.page.onShow(); assert.equal(h.page.data.alreadyChecked, true);
  h.page.onHide(); h.page.onLoad({ event_id: 11 }); await h.page.onShow();
  assert.equal(h.page.data.event.session_name, "下午"); assert.equal(h.page.data.alreadyChecked, false); assert.equal(h.page.data.canCheckin, true);
});

test("closed or unregistered activity cannot send confirm; fallback preserves the engine route", async () => {
  for (const requires_fallback of [true, false]) {
    const h = harness("checkin/index", async () => context(10, { can_checkin: false, requires_fallback, fallback_url: "https://signin.example.test/index.html?event_id=10", notice: "当前不可签到" }));
    h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
    assert.equal(h.calls.length, 1); assert.equal(h.page.data.fallbackAvailable, true);
    h.page.openFallback(); assert.equal(h.navigations[0], "/pages/checkin/legacy?event_id=10");
  }
});

test("confirmation failures and timeout remain retryable and never report success", async () => {
  for (const statusCode of [403, 409, 503, undefined]) {
    const h = harness("checkin/index", async (_, options) => {
      if (options.method === "POST") throw Object.assign(new Error("测试拒绝或超时"), { statusCode });
      return context();
    });
    h.page.onLoad({ event_id: 10 }); await h.page.onShow();
    await h.page.confirmCheckin(); await h.page.confirmCheckin();
    assert.equal(h.page.data.result, null); assert.equal(h.page.data.alreadyChecked, false);
    assert.equal(h.page.data.confirming, false); assert.equal(h.calls.length, 3);
    assert.deepEqual(JSON.parse(JSON.stringify(h.calls[1].options.data)), JSON.parse(JSON.stringify(h.calls[2].options.data)));
  }
});

for (const lifecycle of ["hide", "switch"]) {
  test(`late confirmation after ${lifecycle} cannot restore member data or claim success`, async () => {
    const pending = deferred();
    const h = harness("checkin/index", async (_, options) => options.method === "POST" ? pending.promise : context());
    h.page.onLoad({ event_id: 10 }); await h.page.onShow(); const promise = h.page.confirmCheckin();
    if (lifecycle === "hide") h.page.onHide(); else h.app.setPersonSession("account-b");
    pending.resolve({ data: { status: "CHECKED_IN", sync_status: "SYNCED" } }); await promise;
    assert.equal(h.page.data.result, null); assert.equal(h.page.data.member, null); assert.equal(h.page.data.confirming, false);
  });
}

test("current expired session attempts WeChat resume once; stale expired session cannot revoke new identity", async () => {
  const h = harness("checkin/index", async () => { throw Object.assign(new Error("expired"), { statusCode: 401 }); });
  h.page.onLoad({ event_id: 10 }); await h.page.onShow();
  assert.equal(h.app.globalData.personSessionToken, ""); assert.equal(h.calls.length, 2); assert.equal(h.page.data.loading, false);
  assert.equal(h.page.data.guestAllowed, false); assert.equal(h.page.data.errorMessage, "expired");
  assert.equal(h.page.data.rescueNotice, "");
  const pending = deferred();
  const old = harness("checkin/index", () => pending.promise);
  old.page.onLoad({ event_id: 10 }); const load = old.page.onShow(); old.app.setPersonSession("account-b");
  pending.reject(Object.assign(new Error("expired"), { statusCode: 401 })); await load;
  assert.equal(old.app.globalData.personSessionToken, "account-b"); assert.equal(old.page.data.member, null);
});

for (const pageName of ["index", "events", "legacy"]) {
  for (const failure of ["network", "503"]) {
    test(`${pageName} initial ${failure} failure offers independent onsite rescue without guessing a URL or checkin success`, async () => {
      let recovered = false;
      const fallback = "https://signin.example.test/index.html?event_id=10";
      const h = harness("checkin/" + pageName, async () => {
        if (!recovered) throw Object.assign(new Error("synthetic platform outage"), { statusCode: failure === "503" ? 503 : undefined });
        return pageName === "events" ? { data: { events: [{ event_id: 10, session_code: "MORNING" }] } }
          : context(10, { fallback_url: fallback });
      });
      if (h.page.onLoad) h.page.onLoad({ event_id: 10, url: "https://untrusted.test/" });
      await h.page.onShow();
      assert.equal(h.page.data.rescueNotice, "平台暂时无法连接，签到系统仍可使用。请扫描现场备用签到码，或请工作人员帮助签到。");
      assert.equal(h.page.data.loading, false);
      if (pageName === "index") {
        assert.equal(h.page.data.canCheckin, false); assert.equal(h.page.data.result, null);
        assert.equal(h.page._checkinTicket, ""); await h.page.confirmCheckin();
      } else if (pageName === "legacy") {
        assert.equal(h.page.data.url, ""); h.page.copyLink(); assert.equal(h.copied.length, 0);
      } else assert.equal(h.page.data.events.length, 0);
      assert.equal(h.calls.length, 1);
      recovered = true;
      await h.page.onShow();
      assert.equal(h.page.data.rescueNotice, "");
      if (pageName === "legacy") { h.page.copyLink(); assert.equal(h.copied[0], fallback); }
      h.page.onHide(); assert.equal(h.page.data.rescueNotice, "");
    });
  }
}

test("common entry allows unbound visitors and uses server event IDs rather than guessed names", async () => {
  const items = [{ event_id: 10, session_code: "MORNING", lifecycle_status: "CONFIRMED", checkin_status: "open" }, { event_id: "event_afternoon-11", session_code: "AFTERNOON", lifecycle_status: "CONFIRMED", checkin_status: "open" }];
  const unbound = harness("checkin/events", async () => ({data:{events:items}}), "");
  await unbound.page.onShow(); assert.equal(unbound.calls.length, 1); assert.equal(unbound.calls[0].url, "/api/v1/wechat/checkin/open-events");
  assert.equal(unbound.calls[0].options.auth, undefined); assert.equal(unbound.navigations.length, 0);
  const h = harness("checkin/events", async () => ({ data: { events: items } }));
  await h.page.onShow(); assert.equal(h.page.data.events.length, 2);
  h.page.openEvent({ currentTarget: { dataset: { eventId: "event_afternoon-11" } } }); assert.equal(h.navigations[0], "/pages/checkin/index?event_id=event_afternoon-11");
});

test("common entry auto-opens the single open session once and blocks upcoming or unknown selections", async () => {
  const h = harness("checkin/events", async () => ({ data: { events: [
    {event_id:"one-open",lifecycle_status:"CONFIRMED",checkin_status:"open"},
    {event_id:"upcoming",lifecycle_status:"CONFIRMED",checkin_status:"upcoming"},
    {event_id:"draft",lifecycle_status:"DRAFT",checkin_status:"open"},
    {event_id:"closed",lifecycle_status:"CONFIRMED",checkin_status:"closed"}
  ] } }), "");
  await h.page.onShow(); assert.equal(h.navigations[0], "/pages/checkin/index?event_id=one-open");
  assert.equal(h.page.data.events.length,3); h.page.onHide(); await h.page.onShow();
  assert.equal(h.navigations.length,1);
  for (const eventId of ["upcoming","draft","closed","forged"]) h.page.openEvent({currentTarget:{dataset:{eventId}}});
  assert.equal(h.navigations.length,1);
});

test("open check-in sessions sort before upcoming and ended sessions",async()=>{
  const h=harness('checkin/events',async()=>({data:{events:[
    {event_id:'ended',lifecycle_status:'CONFIRMED',checkin_status:'ended'},
    {event_id:'upcoming',lifecycle_status:'CONFIRMED',checkin_status:'upcoming'},
    {event_id:'open-afternoon',lifecycle_status:'CONFIRMED',checkin_status:'open'},
    {event_id:'closed',lifecycle_status:'CONFIRMED',checkin_status:'closed'},
    {event_id:'open-morning',lifecycle_status:'CONFIRMED',checkin_status:'open'}
  ]}}));
  await h.page.onShow();
  assert.deepEqual(Array.from(h.page.data.events,row=>row.event_id),['open-afternoon','open-morning','upcoming','ended','closed']);
  for(const eventId of ['upcoming','ended','closed'])h.page.openEvent({currentTarget:{dataset:{eventId}}});
  assert.equal(h.navigations.length,0);
});

test("common entry does not navigate when none are open or a response arrives after hide", async () => {
  const none = harness("checkin/events", async () => ({data:{events:[{event_id:10,lifecycle_status:"CONFIRMED",checkin_status:"upcoming"}]}}), "");
  await none.page.onShow(); assert.equal(none.navigations.length,0);
  const pending = deferred(); const hidden = harness("checkin/events", () => pending.promise, "");
  const load = hidden.page.onShow(); hidden.page.onHide();
  pending.resolve({data:{events:[{event_id:10,lifecycle_status:"CONFIRMED",checkin_status:"open"}]}}); await load;
  assert.equal(hidden.navigations.length,0); assert.equal(hidden.page.data.events.length,0);
});

test("legacy fallback re-fetches a trusted context URL, rejects injection and preserves link on web-view failure", async () => {
  for (const unsafe of ["javascript:alert(1)", "http://example.test/", "https://safe.test@evil.test/", "https://example.test/\n"]) assert.equal(checkin.fallbackUrl(unsafe), "");
  const url = "https://signin.example.test/index.html?event_id=10";
  const h = harness("checkin/legacy", async () => context(10, { fallback_url: url }));
  h.page.onLoad({ event_id: 10, url: "https://evil.test/" }); await h.page.onShow();
  h.page.openWebview(); assert.equal(h.page.data.opening, true); h.page.webviewFailed();
  assert.equal(h.page.data.opening, false); h.page.copyLink(); assert.equal(h.copied[0], url);
});

test("scanner opens an activity scene without discarding the binding return context", async () => {
  const h = harness("scan/index", async () => { throw new Error("scanner does not resolve identity"); }, "");
  h.page.onShow(); await h.page.handleScanResult({ path: "pages/checkin/index?scene=short_token_1234567890" });
  assert.equal(h.calls.length, 0); assert.equal(h.navigations[0], "/pages/checkin/index?token=short_token_1234567890");
  await h.page.handleScanResult({ result: "https://example.test/checkin" });
  assert.match(h.page.data.statusMessage, /缺少有效场次/);
});

function ticketContext(extra = {}) {
  return context(10, { checkin_ticket: "synthetic.signature-ticket", engine_confirm_url: "https://stage.example.test/native/v1/checkin/confirm", history_kind: "learning", ...extra });
}

test("preloaded signed ticket signs in directly while platform is unavailable, retaining pending participation sync", async () => {
  const h = harness("checkin/index", async (_, options) => {
    if (options.method === "POST") throw new Error("platform unavailable");
    return ticketContext();
  });
  const direct = [];
  h.wx.request = options => {
    direct.push(options); options.success({ statusCode: 200, data: { ok: true, already: false, msg: "签到成功", data: { checked_at: "synthetic-time" }, sync_status: "PENDING" } });
  };
  h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
  assert.equal(h.calls.length, 1); assert.equal(direct.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(direct[0].data)), { ticket: "synthetic.signature-ticket" });
  assert.equal(direct[0].header.Authorization, undefined);
  assert.equal(h.page.data.result.status, "CHECKED_IN"); assert.equal(h.page.data.result.sync_status, "PENDING");
  assert.equal(h.page.data.result.history_kind, "learning");
  assert.equal(h.page.data.checkin_ticket, undefined); assert.equal(h.app.globalData.checkin_ticket, undefined);
  assert.equal(h.page._checkinTicket, "");
});

test("expired engine ticket prompts context refresh without revoking the platform identity", async () => {
  const h = harness("checkin/index", async () => ticketContext());
  h.wx.request = options => options.success({ statusCode: 401, data: { ok: false, msg: "CHECKIN_TICKET_EXPIRED" } });
  h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
  assert.equal(h.app.globalData.personSessionToken, "account-a"); assert.equal(h.page.data.canCheckin, false);
  assert.equal(h.page._checkinTicket, ""); assert.equal(h.page.data.result, null); assert.match(h.page.data.errorMessage, /凭证已过期/);
  await h.page.loadContext(); assert.equal(h.page.data.canCheckin, true); assert.equal(h.calls.length, 2);
});

test("invalid engine response and network timeout never report successful checkin", async () => {
  for (const network of [false, true]) {
    const h = harness("checkin/index", async () => ticketContext());
    h.wx.request = options => network ? options.fail({ errMsg: "timeout" }) : options.success({ statusCode: 200, data: { ok: false, msg: "活动已关闭" } });
    h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
    assert.equal(h.page.data.result, null); assert.equal(h.page.data.alreadyChecked, false); assert.equal(h.page.data.confirming, false);
    assert.equal(h.calls.length, 1);
  }
});

for (const result of [{}, { status: "NOT_REGISTERED", checked_at: "synthetic-time" },
  { status: "TEAM_FALLBACK", checked_at: "synthetic-time" }, { requires_fallback: true, checked_at: "synthetic-time" }]) {
  test(`direct 200 ok without an attendance receipt or with ${result.status || (result.requires_fallback ? "fallback" : "no fact")} cannot claim checkin success`, async () => {
    const h = harness("checkin/index", async () => ticketContext());
    h.wx.request = options => options.success({ statusCode: 200, data: { ok: true, msg: "签到成功", sync_status: "SYNCED", ...result } });
    h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
    assert.equal(h.page.data.result, null); assert.equal(h.page.data.alreadyChecked, false);
    assert.equal(h.page.data.confirming, false); assert.match(h.page.data.errorMessage, /结果暂未确认|备用签到入口/);
    assert.doesNotMatch(h.page.data.errorMessage, /签到成功/);
    assert.equal(h.calls.length, 1);
  });
}

test("direct duplicate acknowledgement is an attendance receipt without inventing a checkin timestamp", async () => {
  const h = harness("checkin/index", async () => ticketContext());
  h.wx.request = options => options.success({ statusCode: 200, data: { ok: true, already: true, sync_status: "SYNCED" } });
  h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
  assert.equal(h.page.data.result.status, "ALREADY_CHECKED_IN");
  assert.equal(h.page.data.result.checked_at, undefined); assert.equal(h.page.data.alreadyChecked, true);
});

for (const lifecycle of ["hide", "switch"]) {
  test(`signed ticket is cleared and delayed direct response ignored after ${lifecycle}`, async () => {
    const h = harness("checkin/index", async () => ticketContext());
    let request;
    h.wx.request = options => { request = options; };
    h.page.onLoad({ event_id: 10 }); await h.page.onShow(); const pending = h.page.confirmCheckin();
    if (lifecycle === "hide") h.page.onHide(); else h.app.setPersonSession("account-b");
    request.success({ statusCode: 200, data: { ok: true, sync_status: "SYNCED" } }); await pending;
    assert.equal(h.page._checkinTicket, ""); assert.equal(h.page._engineConfirmUrl, "");
    assert.equal(h.page.data.result, null); assert.equal(h.page.data.member, null);
  });
}

test("engine URL restricts HTTPS path and host before transmitting a ticket", async () => {
  for (const url of ["http://stage.example.test/native/v1/checkin/confirm", "https://evil.example.test/native/v1/checkin/confirm",
    "https://stage.example.test@evil.example.test/native/v1/checkin/confirm", "https://stage.example.test/native/v1/checkin/confirm?redirect=x",
    "https://stage.example.test/other", "https://app.tcloudbase.com.evil.test/native/v1/checkin/confirm"]) {
    const h = harness("checkin/index", async () => ticketContext({ engine_confirm_url: url }));
    h.wx.request = () => { throw new Error("invalid URL must not receive ticket"); };
    h.page.onLoad({ event_id: 10 }); await h.page.onShow(); await h.page.confirmCheckin();
    assert.equal(h.page._checkinTicket, ""); assert.equal(h.page.data.canCheckin, false); assert.match(h.page.data.errorMessage, /地址未确认/);
  }
  for (const url of ["https://env.ap-shanghai.app.tcloudbase.com/native/v1/checkin/confirm", "https://env.service.tcloudbase.com/native/v1/checkin/confirm"])
    assert.equal(checkin.engineConfirmUrl(url, "https://stage.example.test/platform"), url);
});


test("external code restores bound identity without a binding form", async () => {
  const h = harness("checkin/index", async () => context(10, { access_token: "restored-session" }), "");
  h.page.onLoad({ scene: "external_scene_123456789" }); await h.page.onShow();
  assert.equal(h.app.globalData.personSessionToken, "restored-session");
  assert.equal(h.page.data.member.name, "合成学员");
  assert.equal(h.page.data.canCheckin, true);
  assert.equal(h.page.data.guestAllowed, false);
  assert.equal(h.navigations.length, 0);
});

test("one tap verifies a unique enrollment and confirms its original slot exactly once", async () => {
  const pending = deferred();
  const h = harness("checkin/index", async url => url.endsWith("guest-confirm") ? pending.promise : url.endsWith("guest-lookup") ? {data:{status:"REGISTERED",candidates:[{registration_id:"slot-a",name:"合成报名人",company:"合成企业",candidate_token:"private-selection"}]}} : context(10, { member: null, can_checkin: false, guest_allowed: true }), "");
  h.page.onLoad({ scene: "external_scene_123456789" }); await h.page.onShow();
  await h.page.submitGuest(); assert.equal(h.calls.length, 1);
  h.page.inputGuestName({ detail: { value: "合成报名人" } });
  const first = h.page.submitGuest();
  while (h.calls.length < 3) await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.page.data.alreadyChecked,false);
  assert.equal(h.page.data.nameCandidates[0].company,"合成企业");
  assert(!JSON.stringify(h.page.data).includes("private-selection"));
  await h.page.submitGuest();
  assert.equal(h.calls.length, 3);
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[2].options.data)), { token: "external_scene_123456789", name: "合成报名人",candidate_token:"private-selection", wx_login_code: "synthetic-login" });
  pending.resolve({ data: { status: "CHECKED_IN", participant_type: "GUEST", checked_at: "2026-10-10T09:00:00+08:00", sync_status: "PENDING" } });
  await first;
  assert.equal(h.page.data.alreadyChecked, true);
  assert.equal(h.page.data.guestName, "");
  assert.equal(h.page.data.successCard.welcome, "欢迎 合成报名人学长！");
});

test("unregistered names show the requested message and never send confirmation", async () => {
  const h = harness("checkin/index", async url => url.endsWith("guest-lookup") ? {data:{status:"NOT_REGISTERED",candidates:[],message:"没有报名"}} : context(10,{member:null,guest_allowed:true}), "");
  h.page.onLoad({event_id:"test-event"}); await h.page.onShow();
  h.page.inputGuestName({detail:{value:"未报名"}}); await h.page.submitGuest();
  assert.equal(h.calls.length,2); assert.equal(h.page.data.result,null);
  assert.equal(h.page.data.errorMessage,"未查询到您的报名信息，请联系工作人员，谢谢");
});

function guestCandidate(slot, extra = {}) {
  return {registration_id:slot,name:"同名报名人",candidate_token:`selection-${slot}`,class_name:`合成班级${slot}`,...extra};
}
function guestReceipt(extra = {}) {
  return {data:{status:"CHECKED_IN",participant_type:"GUEST",checked_at:"2026-10-11T09:00:00+08:00",...extra}};
}
async function duplicateGuest(handler, candidates = [guestCandidate("a"),guestCandidate("b")]) {
  const h=harness("checkin/index", async (url, options) => url.endsWith("guest-lookup") ? {data:{candidates}} : url.endsWith("guest-confirm") ? handler(url, options) : context(10,{member:null,can_checkin:false,guest_allowed:true}), "");
  h.page.onLoad({event_id:"test-event"});await h.page.onShow();
  h.page.inputGuestName({detail:{value:"同名报名人"}});await h.page.submitGuest();
  return h;
}

test("duplicate names require an explicit checkbox selection and can confirm only a chosen subset", async () => {
  const h=await duplicateGuest(async()=>guestReceipt());
  assert.equal(h.calls.length,2);assert.equal(h.page.data.nameCandidates.length,2);
  assert.equal(h.page.data.selectedCandidateIndexes.length,0);
  await h.page.submitGuest();assert.equal(h.calls.length,2);
  assert.match(h.page.data.errorMessage,/勾选/);
  h.page.selectGuestCandidates({detail:{value:["1","1","99","-1","0.5"]}});
  await h.page.submitGuest();
  assert.equal(h.calls.length,3);assert.equal(h.calls[2].options.data.candidate_token,"selection-b");
  assert.equal(h.page.data.guestResults.length,1);assert.equal(h.page.data.alreadyChecked,true);
  assert(!JSON.stringify(h.page.data).includes("selection-"));
});

test("multiple chosen registrations use fresh login codes and retain each original success receipt", async () => {
  const h=await duplicateGuest(async (_,options)=>guestReceipt({status:options.data.candidate_token==="selection-b"?"ALREADY_CHECKED_IN":"CHECKED_IN",
    receipt:{name:"同名报名人",group_num:options.data.candidate_token==="selection-a"?"3":"4",dinner_table_num:"8"}}));
  let loginCount=0;h.wx.login=options=>options.success({code:`new-code-${++loginCount}`});
  h.page.selectGuestCandidates({detail:{value:["0","1"]}});await h.page.submitGuest();
  assert.equal(h.calls.length,4);
  assert.deepEqual(h.calls.slice(2).map(call=>call.options.data.wx_login_code),["new-code-1","new-code-2"]);
  assert.equal(h.page.data.guestResults.length,2);
  assert.deepEqual(Array.from(h.page.data.guestResults[0].card.seats),["大会现场：第 3 组","空巴晚宴：第 8 桌"]);
  assert.match(h.page.data.guestResults[1].card.lines.join(" "),/已签到/);
});

test("partial failures retain confirmed receipts and retry only the failed chosen registration", async () => {
  let fail=true;
  const h=await duplicateGuest(async(_,options)=>{
    if(options.data.candidate_token==="selection-b"&&fail)throw new Error("合成临时故障");
    return guestReceipt();
  });
  h.page.selectGuestCandidates({detail:{value:["0","1"]}});await h.page.submitGuest();
  assert.equal(h.page.data.result,null);assert.equal(h.page.data.alreadyChecked,false);
  assert.equal(h.page.data.guestResults.length,1);assert.equal(h.page.data.nameCandidates[0].completed,true);
  assert.deepEqual(Array.from(h.page.data.selectedCandidateIndexes),["1"]);
  assert.match(h.page.data.errorMessage,/已完成 1 条/);
  h.page.inputGuestName({detail:{value:"改名"}});assert.equal(h.page.data.guestName,"同名报名人");
  fail=false;await h.page.submitGuest();
  assert.equal(h.calls.length,5);assert.equal(h.calls[4].options.data.candidate_token,"selection-b");
  assert.equal(h.page.data.guestResults.length,2);assert.equal(h.page.data.alreadyChecked,true);
});

test("editing a name clears earlier candidate credentials and checkbox selection", async () => {
  const h=await duplicateGuest(async()=>{throw new Error("must not confirm");});
  h.page.selectGuestCandidates({detail:{value:["0"]}});
  h.page.inputGuestName({detail:{value:"修改姓名"}});
  assert.equal(h.page._nameSelections.length,0);assert.equal(h.page.data.nameCandidates.length,0);
  assert.equal(h.page.data.selectedCandidateIndexes.length,0);assert.equal(h.calls.length,2);
});

for(const lifecycle of ["hide","switch"]) {
  test(`late guest batch after ${lifecycle} cannot restore receipts or send the next confirmation`,async()=>{
    const pending=deferred();const h=await duplicateGuest(async()=>pending.promise);
    h.page.selectGuestCandidates({detail:{value:["0","1"]}});const first=h.page.submitGuest();
    while(h.calls.length<3)await new Promise(resolve=>setImmediate(resolve));
    if(lifecycle==="hide")h.page.onHide();else h.app.setPersonSession("account-b");
    pending.resolve(guestReceipt());await first;
    assert.equal(h.calls.length,3);assert.equal(h.page.data.guestResults.length,0);
    assert.equal(h.page.data.result,null);assert.equal(h.page.data.confirming,false);
  });
}

test("a 200 response without a verified guest receipt remains a retryable failure",async()=>{
  const h=await duplicateGuest(async()=>({data:{status:"CHECKED_IN"}}));
  h.page.selectGuestCandidates({detail:{value:["0","1"]}});await h.page.submitGuest();
  assert.equal(h.page.data.guestResults.length,0);assert.equal(h.page.data.result,null);
  assert.equal(h.page.data.alreadyChecked,false);assert.equal(h.page.data.confirming,false);
});

test("failed WeChat resume does not loop, offer guest entry or confirm an identity", async () => {
  const h = harness("checkin/index", async () => { throw Object.assign(new Error("身份暂不可用"), { statusCode: 401 }); }, "");
  h.page.onLoad({ event_id: "test-event" }); await h.page.onShow();
  assert.equal(h.calls.length, 1);
  assert.equal(h.page.data.guestAllowed, false);
  assert.equal(h.page.data.canCheckin, false);
});

test("mini-program templates have no camera or scan buttons", () => {
  for (const page of ["home/index", "checkin/events", "scan/index"]) {
    const source = fs.readFileSync(path.join(root, "pages", page + ".wxml"), "utf8");
    assert.doesNotMatch(source, /bindtap="(?:openScan|startScan|clearStatus)"|打开相机扫码|再扫一次/);
  }
});
