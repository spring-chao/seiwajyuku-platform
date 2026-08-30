import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";

const nodeRequire = createRequire(import.meta.url);
const { resolveVolunteerServices } = nodeRequire(
  "../apps/wechat-miniprogram/utils/volunteer-services.js"
);

function read(relativePath) {
  return readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");
}

function harness({ member, services, history }) {
  const calls = [];
  const app = {
    globalData: { memberSessionToken: "profile-session" },
    clearMemberSession() {
      this.globalData.memberSessionToken = "";
    }
  };
  const request = async path => {
    calls.push(path);
    if (path.endsWith("/me")) return { data: { member } };
    if (path.endsWith("/volunteer-services")) return { data: services };
    if (path.endsWith("/volunteer-history")) return { data: { appointments: history } };
    throw new Error(`unexpected request: ${path}`);
  };
  let page;
  vm.runInNewContext(read("apps/wechat-miniprogram/pages/profile/index.js"), {
    Page: definition => {
      page = definition;
    },
    getApp: () => app,
    require: modulePath => modulePath.includes("volunteer-services")
      ? { resolveVolunteerServices }
      : { request },
    wx: { navigateTo() {}, reLaunch() {} }
  });
  page.setData = values => Object.assign(page.data, values);
  return { page, calls };
}

test("identity inputs define readable text, caret and placeholder colors", () => {
  const template = read("apps/wechat-miniprogram/pages/identity/bind.wxml");
  const styles = read("apps/wechat-miniprogram/pages/identity/bind.wxss");
  assert.equal((template.match(/placeholder-class="field-input-placeholder"/g) || []).length, 2);
  assert.equal((template.match(/always-embed="true"/g) || []).length, 2);
  assert.equal((template.match(/placeholder-style="color:#b7aaaa;"/g) || []).length, 2);
  assert.equal((template.match(/style="color:#3d2a2a;caret-color:#741f2b;background-color:#ffffff;"/g) || []).length, 2);
  assert.match(styles, /\.field-input\s*\{[^}]*color:\s*#3d2a2a/i);
  assert.match(styles, /\.field-input\s*\{[^}]*caret-color:\s*#741f2b/i);
  assert.match(styles, /\.field-input-placeholder\s*\{[^}]*color:\s*#b7aaaa/i);
});

test("profile uses member dates and formal volunteer endpoints", async () => {
  const source = read("apps/wechat-miniprogram/pages/profile/index.js");
  assert.match(source, /\/api\/v1\/wechat\/volunteer-services/);
  assert.match(source, /\/api\/v1\/wechat\/volunteer-history/);
  assert.doesNotMatch(source, /\/api\/v1\/study-meetings\/context/);

  const { page, calls } = harness({
    member: {
      member_id: 42,
      name_masked: "郑*昌",
      class_name: "吴越二班",
      study_group_name: "卓越组",
      join_date: "2022-05-06",
      study_start_date: "2022-04-01"
    },
    services: {
      is_volunteer: true,
      roles: [{
        position_name: "辅导员",
        scope_name: "卓越组",
        capabilities: ["STUDY_MEETING_MANAGE"]
      }]
    },
    history: [{
      position_name: "辅导员",
      scope_name: "卓越组",
      status_name: "服务中",
      starts_at: "2026-08-01T00:00:00+00:00",
      ends_at: null
    }]
  });

  await page.loadProfile();

  assert.equal(page.data.joinDateLabel, "2022年5月入塾");
  assert.notEqual(page.data.joinDateLabel, "暂未记录");
  assert.equal(page.data.currentVolunteerServices[0].positionName, "辅导员");
  assert.equal(page.data.currentVolunteerServices[0].scopeName, "卓越组");
  assert.equal(page.data.volunteerAppointments[0].rangeLabel, "2026年8月 ～ 至今");
  assert.equal(page.data.volunteerAppointments[0].statusName, "服务中");
  assert.ok(calls.includes("/api/v1/wechat/volunteer-services"));
  assert.ok(calls.includes("/api/v1/wechat/volunteer-history"));
});

test("profile falls back from join date to study start date", async () => {
  const { page } = harness({
    member: {
      member_id: 43,
      name_masked: "测*员",
      join_date: null,
      study_start_date: "2021-09-15"
    },
    services: { is_volunteer: false, roles: [] },
    history: []
  });

  await page.loadProfile();

  assert.equal(page.data.joinDateLabel, "2021年9月入塾");
  assert.deepEqual(page.data.currentVolunteerServices, []);
  assert.deepEqual(page.data.volunteerAppointments, []);
});
