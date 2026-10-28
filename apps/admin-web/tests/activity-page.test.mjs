import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import dayjs from "dayjs";
import { computed, reactive, ref, watch } from "vue";
import * as labels from "../src/utils/business-labels.mjs";
import * as activities from "../src/utils/activity-list.mjs";

function harness(overrides = {}) {
  const source = readFileSync(new URL("../src/views/seiwajyuku/activities.vue", import.meta.url), "utf8");
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const output = ts.transpileModule(script, { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022
  }}).outputText.replace(/^import [\s\S]*?;\r?\n/gm, "").replace(/export \{\};?/, "");
  const calls = [], errors = [];
  const context = vm.createContext({ ...labels, ...activities, computed, reactive, ref, watch, dayjs,
    defineOptions() {}, onMounted() {},
    useUserStoreHook: () => ({ permissions: [] }),
    hasPerms: () => true,
    ElMessage: { error: text => errors.push(text) },
    getAttendanceActivityRows: async filters => { calls.push(filters); return { data: [] }; },
    getAttendanceSyncStatus: async () => ({ data: null }),
    getMemberOrgCatalog: async () => ({ data: { units: [], shukus: [], management_units: [], classes: [], groups: [] } }),
    ...overrides
  });
  vm.runInContext(output + "\nglobalThis.testApi={load,dateRange,rows,loading,filters,visibleRows,openActivityDetail,detail,detailLoading,detailSessionId,visibleDetailSessions,visibleDetailRecords,canMakeup,confirmationLabel,finalizeAttendance};", context);
  return { ...context.testApi, calls, errors };
}

test("query sends the inclusive date range, and clearing it queries all dates", async () => {
  const ui = harness();
  ui.dateRange.value = ["2026-07-01", "2026-07-31"];
  await ui.load();
  assert.deepEqual(JSON.parse(JSON.stringify(ui.calls[0])), { date_from: "2026-07-01", date_to: "2026-07-31" });
  ui.dateRange.value = null;
  await ui.load();
  assert.equal(ui.calls[1].date_from, undefined);
  assert.equal(ui.calls[1].date_to, undefined);
});

test("makeup is only offered for an unmatched scan in this month before final confirmation", () => {
  const ui = harness();
  const record = { member_id: 7, attendance_status: "ABSENT", event_date: dayjs().format("YYYY-MM-DD") };
  assert.equal(Boolean(ui.canMakeup(record)), true);
  assert.equal(Boolean(ui.canMakeup({ ...record, attendance_status: "PRESENT" })), false);
  assert.equal(Boolean(ui.canMakeup({ ...record, member_id: null })), false);
  assert.equal(Boolean(ui.canMakeup({ ...record, event_date: "2000-01-01" })), false);
  ui.detail.value = { confirmation: { confirmed: true } };
  assert.equal(Boolean(ui.canMakeup(record)), false);
});

test("confirmation sends the reviewed fingerprint and reports a pending gate without claiming posted", async () => {
  const calls = [];
  const detail = { group: { id: 7 }, sessions: [], confirmation: { confirmed: false, review_fingerprint: "a".repeat(64) } };
  const ui = harness({
    ElMessageBox: { prompt: async () => ({ value: "全部名单核实完成" }) },
    ElMessage: { success() {} },
    confirmAttendance: async (...args) => { calls.push(args); return { data: { settlement_status: "PENDING_ENABLEMENT" } }; },
    getAttendanceEventGroupDetail: async () => ({ data: detail }),
    getAttendanceRecords: async () => ({ data: [] })
  });
  ui.detail.value = detail;
  await ui.finalizeAttendance();
  assert.deepEqual(Array.from(calls[0]), [7, "全部名单核实完成", "a".repeat(64)]);
  ui.detail.value.confirmation = { confirmed: true, settlement_status: "PENDING_ENABLEMENT" };
  assert.match(ui.confirmationLabel.value, /等待/);
});

test("an older list response cannot replace the newer range or clear its loading state", async () => {
  const pending = [];
  const ui = harness({ getAttendanceActivityRows: () => new Promise(resolve => pending.push(resolve)) });
  const older = ui.load(), newer = ui.load();
  pending[0]({ data: [{ id: 1 }] });
  await older;
  assert.equal(ui.loading.value, true);
  assert.equal(ui.rows.value.length, 0);
  pending[1]({ data: [{ id: 2 }] });
  await newer;
  assert.equal(ui.rows.value[0].id, 2);
  assert.equal(ui.loading.value, false);
});

test("detail defaults to all scans, switches one scan and restores all without losing records", async () => {
  const sessions = [1, 2, 3].map(id => ({ id }));
  const records = sessions.map(({ id }) => ({ id, attendance_session_id: id }));
  const ui = harness({ getAttendanceEventGroupDetail: async () => ({ data: { group: { id: 9 }, sessions } }),
    getAttendanceRecords: async () => ({ data: records }) });
  await ui.openActivityDetail({ id: 9 });
  assert.equal(ui.visibleDetailSessions.value.length, 3);
  ui.detailSessionId.value = 2;
  assert.equal(ui.visibleDetailSessions.value[0].id, 2);
  assert.equal(ui.visibleDetailRecords.value.length, 1);
  ui.detailSessionId.value = 0;
  assert.equal(ui.visibleDetailSessions.value.length, 3);
  assert.equal(ui.visibleDetailRecords.value.length, 3);
});

test("an old detail response cannot replace another selected activity", async () => {
  const pending = [];
  const ui = harness({ getAttendanceEventGroupDetail: () => new Promise(resolve => pending.push(resolve)),
    getAttendanceRecords: async () => ({ data: [] }) });
  const older = ui.openActivityDetail({ id: 1 }), newer = ui.openActivityDetail({ id: 2 });
  pending[1]({ data: { group: { id: 2 }, sessions: [] } });
  await newer;
  pending[0]({ data: { group: { id: 1 }, sessions: [] } });
  await older;
  assert.equal(ui.detail.value.group.id, 2);
  assert.equal(ui.detailLoading.value, false);
});
