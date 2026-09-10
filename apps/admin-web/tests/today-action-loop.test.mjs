import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const dashboard = readFileSync(
  new URL("../src/views/seiwajyuku/dashboard.vue", import.meta.url),
  "utf8"
);
const detailDrawer = readFileSync(
  new URL("../src/components/seiwajyuku/MemberDetailDrawer.vue", import.meta.url),
  "utf8"
);
const api = readFileSync(
  new URL("../src/api/seiwajyuku.ts", import.meta.url),
  "utf8"
);

test("today action workbench exposes pending and completed member-centered views", () => {
  assert.match(dashboard, /careView = ref<"pending" \| "completed">/);
  assert.match(dashboard, /今日已完成/);
  assert.match(dashboard, /completed_today/);
  assert.match(dashboard, /openMemberProfile\(row\.member_id\)/);
  assert.match(dashboard, /getMemberCareActionsToday\(\)/);
  assert.match(dashboard, /onActivated\(/);
});

test("member operation detail keeps action navigation and source facts together", () => {
  assert.match(detailDrawer, /运营摘要/);
  assert.match(detailDrawer, /关键事实/);
  assert.match(detailDrawer, /完成后返回今日行动即可刷新/);
  assert.match(detailDrawer, /emit\("action", action\)/);
  assert.match(api, /export type MemberCareCompletedItem/);
  assert.match(api, /completed_today: MemberCareCompletedItem\[\]/);
});
