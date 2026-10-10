const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const historyUtils = require("../utils/participation-history");
const { resolveVolunteerServices } = require("../utils/volunteer-services");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function historyData(records = [], overrides = {}) {
  return { records, total: records.length, learning_count: 8, activity_count: 2,
    category_counts: { 学习会: 2, 班级学习日: 1, 小组学习会: 3, 开班: 1, 课程: 1, 年会: 2 },
    has_more: false, available_years: [2026, 2025], history_version: "version-a", ...overrides };
}

function record(title, type = "学习会") {
  return { occurred_at: "2025-09-06T12:30:00", title, learning_type: type,
    class_name: "测试班", group_name: "测试组", status_name: "已参加", source_type: "HISTORICAL" };
}

function harness(pageName, respond) {
  const calls = [], navigations = [];
  const app = { globalData: { personSessionToken: "account-a", memberSessionToken: "account-a" }, clearCount: 0,
    clearMemberSession() { this.clearCount += 1; this.globalData.personSessionToken = ""; this.globalData.memberSessionToken = ""; } };
  let page;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../pages", pageName, "index.js"), "utf8"), {
    Page(value) { page = value; }, getApp: () => app,
    wx: { navigateTo(value) { navigations.push(value.url); }, reLaunch() {} },
    require(name) {
      if (name.includes("participation-history")) return historyUtils;
      if (name.includes("volunteer-services")) return { resolveVolunteerServices };
      if (name.includes("credit-display")) return require('../utils/credit-display');
      return { request(url, options) { calls.push({ url, options }); return Promise.resolve().then(() => respond(url, calls.length)); } };
    }
  });
  page.setData = values => Object.assign(page.data, values);
  const switchAccount = token => { app.globalData.personSessionToken = token; app.globalData.memberSessionToken = token; };
  return { page, app, calls, navigations, switchAccount };
}

function servicesResponse(url) {
  if (url.endsWith("/me")) return { data: { member: { member_id: 1, name_masked: "测试*" } } };
  if (url.endsWith("/volunteer-services")) return { data: { is_volunteer: false, roles: [] } };
  if (url.endsWith("/volunteer-history")) return { data: { appointments: [] } };
  if (url.endsWith("/learning-summary")) return { data: {
    current_learning: [{ class_name: "当前测试班", group_name: "当前测试组", status_name: "学习中" }],
    recent_learning: [record("旧来源不应出现在个人明细")]
  } };
  if (url.endsWith("/credit-summary")) return { data: { total_points: "3.00", recent_entries: [] } };
  if (url.endsWith("/context")) throw Object.assign(new Error("Forbidden"), { statusCode: 403 });
  throw new Error(`Unexpected request ${url}`);
}

test("history loads a person's selected activity year and keeps learning out of activity categories", async () => {
  const h = harness("history", () => ({ data: historyData([record("测试年会", "年会")], { total: 2 }) }));
  h.page.onLoad({ kind: "activity", year: "2025" });
  await h.page.onShow();
  assert.match(h.calls[0].url, /kind=activity/);
  assert.match(h.calls[0].url, /year=2025/);
  assert.equal(h.calls[0].options.auth, true);
  assert.equal(h.page.data.records[0].title, "测试年会");
  assert.equal(h.page.data.records[0].occurredAtLabel, "2025年9月6日");
  assert.equal(h.page.data.records[0].scopeLabel, "测试班 · 测试组");
  assert.equal(h.page.data.total, 2);
  assert.equal(h.page.data.learningCount, 8);
  assert.equal(h.page.data.activityCount, 2);
  assert.deepEqual(h.page.data.categories, [{ name: "年会", count: 2 }]);
});

test("learning category counts include all five types, including zero counts", async () => {
  const h = harness("history", () => ({ data: historyData([], { category_counts: { 学习会: 4 } }) }));
  await h.page.onShow();
  assert.equal(h.calls[0].url.includes("year="), false);
  assert.deepEqual(h.page.data.categories, [
    { name: "学习会", count: 4 }, { name: "班级学习日", count: 0 }, { name: "小组学习会", count: 0 },
    { name: "开班", count: 0 }, { name: "课程", count: 0 }
  ]);
});

test("history pagination appends rows and retries the failed page without losing existing records", async () => {
  let failed = true;
  const h = harness("history", url => {
    if (url.includes("page=1&")) return { data: historyData([record("第一条")], { total: 2, has_more: true }) };
    if (failed) { failed = false; throw new Error("Network failure"); }
    return { data: historyData([record("第二条")], { total: 2 }) };
  });
  await h.page.onShow();
  assert.equal(h.page.data.nextPage, 2);
  await h.page.loadMore();
  assert.equal(h.page.data.records.length, 1);
  assert.equal(h.page.data.total, 2);
  assert.equal(h.page.data.hasMore, true);
  assert.equal(h.page.data.nextPage, 2);
  assert.match(h.page.data.errorMessage, /暂时无法加载/);
  await h.page.loadMore();
  assert.equal(h.calls[1].url, h.calls[2].url);
  assert.deepEqual(Array.from(h.page.data.records, item => item.title), ["第一条", "第二条"]);
  assert.equal(h.page.data.hasMore, false);
  assert.equal(h.page.data.errorMessage, "");
  const count = h.calls.length;
  await h.page.loadMore();
  assert.equal(h.calls.length, count);
});

for (const scenario of ["total changed", "same total with a changed version"]) {
  test(`history restarts from page one when ${scenario} and discards mixed pages`, async () => {
    let index = 0;
    const responses = [
      historyData([record("旧第一页")], { total: 27, has_more: true }),
      historyData([record("不可拼接的第二页")], { total: scenario === "total changed" ? 28 : 27,
        history_version: scenario === "total changed" ? "version-a" : "version-b" }),
      historyData([record("更新后的第一页")], { total: 28, has_more: true, history_version: "version-b" })
    ];
    const h = harness("history", () => ({ data: responses[index++] }));
    await h.page.onShow();
    await h.page.loadMore();
    assert.equal(h.calls.length, 3);
    assert.match(h.calls[0].url, /page=1&/);
    assert.match(h.calls[1].url, /page=2&/);
    assert.match(h.calls[2].url, /page=1&/);
    assert.deepEqual(Array.from(h.page.data.records, item => item.title), ["更新后的第一页"]);
    assert.equal(h.page.data.nextPage, 2);
    assert.equal(h.page.data.total, 28);
    assert.equal(h.page.data.historyVersion, "version-b");
    assert.equal(h.page.data.noticeMessage, "记录已更新，已重新加载");
    assert.equal(h.page.data.errorMessage, "");
    assert.equal(h.page.data.refreshRequired, false);
  });
}

test("history refresh failure keeps earlier rows and shows a retry instead of reporting no records", async () => {
  let index = 0;
  const h = harness("history", () => {
    index += 1;
    if (index === 1) return { data: historyData([record("已加载的旧记录")], { total: 27, has_more: true }) };
    if (index === 2) return { data: historyData([record("混合记录不可显示")], { total: 28 }) };
    throw new Error("refresh network failure");
  });
  await h.page.onShow();
  await h.page.loadMore();
  assert.deepEqual(Array.from(h.page.data.records, item => item.title), ["已加载的旧记录"]);
  assert.equal(h.page.data.total, 27);
  assert.equal(h.page.data.refreshRequired, true);
  assert.equal(h.page.data.hasMore, false);
  assert.match(h.page.data.errorMessage, /暂时无法加载/);
  assert.equal(h.page.data.noticeMessage, "记录已更新，请重新加载。");
  assert.equal(h.page.data.loading, false);
});

test("history allows only one automatic restart until the person explicitly reloads", async () => {
  let index = 0;
  const responses = [
    historyData([record("版本A")], { total: 27, has_more: true }),
    historyData([record("版本B的第二页")], { total: 28, history_version: "version-b" }),
    historyData([record("版本B第一页")], { total: 28, has_more: true, history_version: "version-b" }),
    historyData([record("版本C的第二页")], { total: 29, history_version: "version-c" }),
    historyData([record("主动刷新后的版本C")], { total: 29, has_more: true, history_version: "version-c" })
  ];
  const h = harness("history", () => ({ data: responses[index++] }));
  await h.page.onShow();
  await h.page.loadMore();
  await h.page.loadMore();
  assert.equal(h.calls.length, 4);
  assert.equal(h.page.data.records[0].title, "版本B第一页");
  assert.equal(h.page.data.refreshRequired, true);
  assert.match(h.page.data.errorMessage, /记录再次更新/);
  await h.page.loadMore();
  assert.equal(h.calls.length, 4);
  await h.page.reloadHistory();
  assert.equal(h.calls.length, 5);
  assert.equal(h.page.data.records[0].title, "主动刷新后的版本C");
  assert.equal(h.page.data.refreshRequired, false);
  assert.equal(h.page.data.errorMessage, "");
  assert.equal(h.page._autoRefreshUsed, false);
});

test("a delayed automatic refresh cannot restore an earlier person's records", async () => {
  const delayed = deferred();
  let index = 0;
  const h = harness("history", () => {
    index += 1;
    if (index === 1) return { data: historyData([record("身份A第一页")], { total: 27, has_more: true }) };
    if (index === 2) return { data: historyData([record("身份A有新记录")], { total: 28 }) };
    return delayed.promise;
  });
  await h.page.onShow();
  const more = h.page.loadMore();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.calls.length, 3);
  h.switchAccount("account-b");
  delayed.resolve({ data: historyData([record("身份A刷新结果不可残留")]) });
  await more;
  assert.equal(h.page.data.records.length, 0);
  assert.equal(h.page.data.total, null);
  assert.equal(h.page.data.historyVersion, null);
  assert.equal(h.page.data.loading, false);
  assert.equal(h.app.globalData.personSessionToken, "account-b");
});

test("a changed kind and year cannot be overwritten by a delayed earlier response", async () => {
  const pending = [deferred(), deferred(), deferred()];
  let index = 0;
  const h = harness("history", () => pending[index++].promise);
  const original = h.page.onShow();
  await Promise.resolve();
  const changedKind = h.page.changeKind({ currentTarget: { dataset: { kind: "activity" } } });
  await Promise.resolve();
  const changedYear = h.page.changeYear({ currentTarget: { dataset: { year: "2025" } } });
  await Promise.resolve();
  pending[2].resolve({ data: historyData([record("新的活动", "年会")]) });
  await changedYear;
  pending[1].resolve({ data: historyData([record("旧年份活动", "年会")]) });
  pending[0].resolve({ data: historyData([record("旧学习")]) });
  await Promise.all([original, changedKind]);
  assert.equal(h.page.data.kind, "activity");
  assert.equal(h.page.data.year, "2025");
  assert.equal(h.page.data.records[0].title, "新的活动");
  assert.equal(h.page.data.loading, false);
});

for (const lifecycle of ["onHide", "onUnload"]) {
  test(`history ${lifecycle} clears all personal history and ignores a delayed result`, async () => {
    const delayed = deferred();
    const h = harness("history", () => delayed.promise);
    const loading = h.page.onShow();
    h.page[lifecycle]();
    delayed.resolve({ data: historyData([record("旧记录")], { available_years: [2024] }) });
    await loading;
    assert.equal(h.page.data.records.length, 0);
    assert.equal(h.page.data.learningCount, null);
    assert.equal(h.page.data.activityCount, null);
    assert.equal(h.page.data.total, null);
    assert.equal(h.page.data.categories.length, 0);
    assert.equal(h.page.data.yearOptions.some(item => item.value === "2024"), false);
    assert.equal(h.page.data.loading, false);
  });
}

test("an account switch during pagination clears old records and an old 401 cannot revoke the new account", async () => {
  const delayed = deferred();
  const h = harness("history", url => url.includes("page=1&")
    ? { data: historyData([record("身份A记录")], { has_more: true, total: 2, available_years: [2024] }) }
    : delayed.promise);
  await h.page.onShow();
  const more = h.page.loadMore();
  h.switchAccount("account-b");
  delayed.reject(Object.assign(new Error("expired A"), { statusCode: 401 }));
  await more;
  assert.equal(h.app.globalData.personSessionToken, "account-b");
  assert.equal(h.app.clearCount, 0);
  assert.equal(h.page.data.records.length, 0);
  assert.equal(h.page.data.total, null);
  assert.equal(h.page.data.categories.length, 0);
  assert.equal(h.page.data.yearOptions.some(item => item.value === "2024"), false);
  assert.match(h.page.data.errorMessage, /身份已变更/);
});

test("history 401 clears cached counts and prompts rebinding, while network failures remain errors", async () => {
  const h = harness("history", () => { throw Object.assign(new Error("expired"), { statusCode: 401 }); });
  await h.page.onShow();
  assert.equal(h.app.clearCount, 1);
  assert.equal(h.app.globalData.personSessionToken, "");
  assert.equal(h.page.data.records.length, 0);
  assert.equal(h.page.data.total, null);
  assert.equal(h.page.data.hasMore, false);
  assert.equal(h.page.data.sessionMissing, true);
  assert.match(h.page.data.errorMessage, /绑定已失效/);

  const broken = harness("history", () => ({ data: {} }));
  await broken.page.onShow();
  assert.match(broken.page.data.errorMessage, /暂时无法加载/);
  assert.equal(broken.page.data.total, null);
  assert.equal(broken.page.data.hasMore, true);
});

test("unbound history sends no requests", async () => {
  const h = harness("history", () => { throw new Error("must not request"); });
  h.switchAccount("");
  await h.page.onShow();
  assert.equal(h.calls.length, 0);
  assert.equal(h.page.data.sessionMissing, true);
  assert.match(h.page.data.errorMessage, /请先绑定/);
});

test("profile shows recent learning and other activities separately with full counts", async () => {
  const h = harness("profile", url => url.includes("/participation-history?")
    ? { data: historyData([record(url.includes("kind=learning") ? "我的课程" : "我的年会", url.includes("kind=learning") ? "课程" : "年会")]) }
    : servicesResponse(url));
  await h.page.loadProfile();
  assert.equal(h.page.data.recentLearning[0].title, "我的课程");
  assert.equal(h.page.data.recentActivities[0].title, "我的年会");
  assert.equal(h.page.data.learningCount, 8);
  assert.equal(h.page.data.activityCount, 2);
  assert.equal(h.calls.filter(call => call.url.includes("page_size=3")).length, 2);
  h.page.openHistory({ currentTarget: { dataset: { kind: "activity" } } });
  assert.equal(h.navigations[0], "/pages/history/index?kind=activity");
  h.page.onHide();
  assert.equal(h.page.data.recentLearning.length, 0);
  assert.equal(h.page.data.recentActivities.length, 0);
  assert.equal(h.page.data.learningCount, null);
  assert.equal(h.page.data.activityCount, null);
});

test("profile distinguishes an activity request failure from zero activity records", async () => {
  const h = harness("profile", url => {
    if (url.includes("kind=activity")) throw new Error("network");
    if (url.includes("kind=learning")) return { data: historyData([record("已加载学习")]) };
    return servicesResponse(url);
  });
  await h.page.loadProfile();
  assert.equal(h.page.data.recentLearning[0].title, "已加载学习");
  assert.equal(h.page.data.activityCount, null);
  assert.match(h.page.data.activityErrorMessage, /暂时无法加载/);
  assert.equal(h.page.data.member.member_id, 1);
});

test("learning history stays visible when current schedules fail and volunteers remain permission gated", async () => {
  const h = harness("learning", url => {
    if (url.includes("/participation-history?")) return { data: historyData([record("本人完整学习记录", "班级学习日")]) };
    if (url.endsWith("/learning-summary")) throw new Error("schedules unavailable");
    return servicesResponse(url);
  });
  await h.page.loadLearning();
  assert.equal(h.page.data.member.member_id, 1);
  assert.equal(h.page.data.recentLearning[0].title, "本人完整学习记录");
  assert.equal(h.page.data.learningCount, 8);
  assert.match(h.page.data.currentLearningErrorMessage, /暂时无法加载/);
  assert.equal(h.page.data.canManageStudyMeeting, false);
  assert.equal(h.page.data.creditSummary.totalPointsLabel, "3分");
  h.page.openStudyMeeting();
  assert.equal(h.navigations.length, 0);
  h.page.openLearningHistory();
  assert.equal(h.navigations[0], "/pages/history/index?kind=learning");
});

for (const pageName of ["profile", "learning"]) {
  test(`${pageName} history 401 clears the person's visible data and identity`, async () => {
    const h = harness(pageName, url => {
      if (url.includes("/participation-history?")) throw Object.assign(new Error("expired"), { statusCode: 401 });
      return servicesResponse(url);
    });
    await (pageName === "profile" ? h.page.loadProfile() : h.page.loadLearning());
    assert.equal(h.app.clearCount, 1);
    assert.equal(h.page.data.member, null);
    assert.equal(h.page.data.recentLearning.length, 0);
    assert.equal(h.page.data.learningCount, null);
    assert.equal(h.page.data.loading, false);
    assert.match(h.page.data.errorMessage, /绑定已失效/);
  });

  test(`${pageName} late personal history cannot appear after a session change`, async () => {
    const delayed = deferred();
    const h = harness(pageName, url => url.includes("/participation-history?") ? delayed.promise : servicesResponse(url));
    const loading = pageName === "profile" ? h.page.loadProfile() : h.page.loadLearning();
    await new Promise(resolve => setImmediate(resolve));
    h.switchAccount("account-b");
    delayed.resolve({ data: historyData([record("身份A不可残留")]) });
    await loading;
    assert.equal(h.page.data.member, null);
    assert.equal(h.page.data.recentLearning.length, 0);
    assert.equal(h.page.data.learningCount, null);
    assert.equal(h.page.data.loading, false);
    assert.equal(h.app.globalData.personSessionToken, "account-b");
  });
}
