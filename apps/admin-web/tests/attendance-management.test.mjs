import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import dayjs from "dayjs";
import { computed, reactive, ref, watch } from "vue";
import * as parser from "../src/utils/attendanceImportParser.mjs";
import * as management from "../src/utils/attendanceManagement.mjs";

test("reused Excel parser finds the richest header and preserves independent team slots", () => {
  const cells = Array.from({ length: 15 }, () => []);
  cells[1] = ["姓名", "手机号"];
  cells[12] = [
    "备注",
    "工作单位",
    "报名人姓名",
    "联系电话",
    "班级名称",
    "小组名称"
  ];
  cells[13] = ["", "示例公司", "示例团队联系人", "", "示例班", "第一组"];
  cells[14] = cells[13].slice();
  const parsed = parser.parseAttendanceRows(cells);
  assert.equal(parsed.headerRow, 13);
  assert.equal(parsed.attendees.length, 2);
  assert.equal(parsed.quality.repeated_slots, 1);
  assert.equal(parsed.quality.missing_phone_count, 2);
  assert.equal(parsed.attendees[0].name, "示例团队联系人");
  assert.equal(parsed.attendees[0].class_name, "示例班");
  assert.equal(parsed.groupField, "class_name");
  assert.equal(parser.buildRosterQuality(parsed.attendees).passed, true);
});

test("Excel without a name header fails closed; blank rows do not become registrations", () => {
  assert.throws(() => parser.parseAttendanceRows([["手机", "公司"]]), /姓名/);
  const parsed = parser.parseAttendanceRows([
    ["姓名", "公司"],
    ["", "空白"],
    ["undefined"],
    ["示例来宾"]
  ]);
  assert.equal(parsed.attendees.length, 1);
});

test("roster selection uses exact IDs and rejects an orphan group instead of guessing a class", () => {
  assert.throws(
    () => management.rosterSelection({ name: "示例班" }),
    /可信组织/
  );
  assert.throws(
    () =>
      management.rosterSelection({ id: "group-1", name: "示例组" }, "group"),
    /班级组织 ID/
  );
  assert.deepEqual(
    management.rosterSelection(
      {
        id: "group-1",
        parent_id: "class-2",
        org_unit_id: "center-3",
        name: "示例组"
      },
      "group"
    ),
    {
      scope: "group",
      org_unit_id: "group-1",
      class_org_unit_id: "class-2",
      group_org_unit_id: "group-1",
      center: "",
      center_name: "",
      class_name: "",
      group_name: "示例组"
    }
  );
});

test("attendance views separate present, follow-up, cross-class and actual team attendees", () => {
  const rows = [
    {
      registration_id: "1",
      registered_name: "报名联系人",
      actual_attendee_name: "实际来宾",
      checked: true,
      checked_at: "2026-10-07T01:00:00Z",
      is_team: true
    },
    {
      registration_id: "2",
      name: "外班学长",
      attendance_role: "CROSS_CLASS_MEMBER",
      checked: true
    },
    { registration_id: "3", name: "请假学长", attendance_status: "leave" },
    { registration_id: "4", name: "预计迟到学长", attendance_status: "late" },
    { registration_id: "5", name: "待签到学长", attendance_status: "pending" }
  ];
  assert.equal(management.filterAttendanceRows(rows, "checked").length, 2);
  assert.equal(
    management.filterAttendanceRows(rows, "pending")[0].registration_id,
    "5"
  );
  assert.equal(
    management.filterAttendanceRows(rows, "cross")[0].registration_id,
    "2"
  );
  assert.equal(
    management.filterAttendanceRows(rows, "team", "实际来宾")[0]
      .registration_id,
    "1"
  );
  assert.equal(management.filterAttendanceRows(rows, "leave").length, 1);
  const exportRows = management.attendanceExportRows(rows);
  assert.equal(exportRows[1][0], "实际来宾");
  assert.equal(exportRows[1][1], "报名联系人");
  assert.equal(exportRows[0].includes("跟进备注"), false);
});

test("closed/cancelled sessions cannot accept manual check-in and display dates in China time", () => {
  assert.equal(
    management.canConfirmManually({
      lifecycle_status: "CONFIRMED",
      checkin_status: "closed"
    }),
    false
  );
  assert.equal(
    management.canConfirmManually({
      lifecycle_status: "DRAFT",
      checkin_status: "open"
    }),
    false
  );
  assert.equal(
    management.canMaintainRegistration({
      lifecycle_status: "CANCELLED",
      checkin_status: "upcoming"
    }),
    false
  );
  assert.equal(
    management.canConfirmManually({
      lifecycle_status: "CONFIRMED",
      checkin_status: "open"
    }),
    true
  );
  assert.equal(
    management.chinaLocalDateTime("2026-10-07T01:30:00Z"),
    "2026-10-07T09:30"
  );
});

function harness(api = async () => ({ ok: true })) {
  const source = readFileSync(
    new URL("../src/views/seiwajyuku/checkin-management.vue", import.meta.url),
    "utf8"
  );
  const script = source.match(
    /<script setup lang="ts">([\s\S]*?)<\/script>/
  )[1];
  const output = ts
    .transpileModule(script, {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022
      }
    })
    .outputText.replace(/^import [\s\S]*?;\r?\n/gm, "")
    .replace(/export \{\};?/, "");
  const calls = [];
  const context = vm.createContext({
    ...parser,
    ...management,
    computed,
    reactive,
    ref,
    watch,
    dayjs,
    defineOptions() {},
    onMounted() {},
    onUnmounted() {},
    hasPerms: () => true,
    ElMessage: { success() {}, error() {} },
    ElMessageBox: { confirm: async () => true },
    manageAttendance: async (operation, payload) => {
      calls.push({ operation, payload });
      return api(operation, payload);
    },
    generateAttendanceCode: async (eventId, env) => {
      calls.push({ operation: "code", payload: { eventId, env } });
      return {
        image_base64: "cG5n",
        mime_type: "image/png",
        event_id: eventId,
        env_version: env
      };
    },
    document: { hidden: false },
    setInterval,
    clearInterval,
    console
  });
  vm.runInContext(
    output +
      "\nglobalThis.state = { selected, workspaceVisible, activeEventId, stats, people, detailLoading, importTarget, importAttendees, importGroupField, preview, previewFingerprint, error, editor, regionId, editingId, classOptions, rosterOptionId, code, codeEvent, codeVersion }; globalThis.actions = { selectEvent, confirmEvent, previewImport, applyImport, saveEditor, createCode };",
    context
  );
  return { state: context.state, actions: context.actions, calls };
}

test("opening activity management is visible before slow details finish and survives a read error", async () => {
  let reject;
  const pending = new Promise((_, fail) => { reject = fail; });
  const h = harness(() => pending);
  const opening = h.actions.selectEvent({ event_id: "draft-1", name: "待确认活动", lifecycle_status: "DRAFT" });
  assert.equal(h.state.workspaceVisible.value, true);
  assert.equal(h.state.detailLoading.value, true);
  assert.equal(h.state.activeEventId.value, "draft-1");
  reject(new Error("活动详情暂时不可用"));
  await opening;
  assert.equal(h.state.workspaceVisible.value, true);
  assert.equal(h.state.detailLoading.value, false);
  assert.match(h.state.error.value, /暂时不可用/);
  assert.equal(h.calls.some(call => call.operation === "event_lifecycle_update"), false);
});

test("the draft row confirmation uses the selected activity and preserves the existing lifecycle operation", async () => {
  const h = harness(async operation => operation === "admin_events" ? { items: [] } : {});
  await h.actions.confirmEvent({ event_id: "draft-2", name: "待确认活动", lifecycle_status: "DRAFT" });
  assert.equal(h.state.workspaceVisible.value, true);
  const writes = h.calls.filter(call => call.operation === "event_lifecycle_update");
  assert.equal(writes.length, 1);
  assert.equal(writes[0].payload.event_id, "draft-2");
  assert.equal(writes[0].payload.lifecycle_status, "CONFIRMED");
});

test("a name-only edit preserves legacy organization ownership and does not submit protected time fields", async () => {
  const h = harness();
  const event = {
    event_id: "event-edit",
    name: "原活动",
    event_date: "2026-10-07",
    activity_type: "class_meeting",
    lifecycle_status: "CONFIRMED",
    org_unit_id: "center-1",
    class_org_unit_id: "class-1",
    checkin_start_at: "2026-10-07T00:00:00Z",
    checkin_end_at: "2026-10-07T02:00:00Z"
  };
  h.state.selected.value = event;
  h.state.activeEventId.value = event.event_id;
  h.state.editingId.value = event.event_id;
  h.state.classOptions.value = [
    { id: "class-1", parent_id: "center-1", name: "示例班" }
  ];
  h.state.rosterOptionId.value = "class-1";
  Object.assign(h.state.editor, {
    event_name: "新活动名称",
    event_date: event.event_date,
    activity_type: "class_meeting",
    checkin_start_at: "2026-10-07T08:00",
    checkin_end_at: "2026-10-07T10:00",
    scheduled_start_at: "",
    scheduled_end_at: ""
  });
  await h.actions.saveEditor();
  const write = h.calls.find(call => call.operation === "event_update");
  assert.ok(write);
  assert.equal(write.payload.name, "新活动名称");
  assert.equal("org_unit_id" in write.payload, false);
  assert.equal("class_org_unit_id" in write.payload, false);
  assert.equal("checkin_start_at" in write.payload, false);
  assert.equal("checkin_end_at" in write.payload, false);
});

test("management client respects raw engine results, surfaces rejections and unwraps QR results", async () => {
  const source = readFileSync(
    new URL("../src/api/attendance-management.ts", import.meta.url),
    "utf8"
  );
  const output = ts
    .transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022
      }
    })
    .outputText.replace(/^import [\s\S]*?;\r?\n/gm, "")
    .replace(/^export /gm, "");
  const calls = [];
  const context = vm.createContext({
    http: {
      request: async (method, path, options) => {
        calls.push({ method, path, options });
        if (path.includes("/code"))
          return {
            success: true,
            data: {
              event_id: "event/afternoon",
              image_base64: "cG5n",
              env_version: "develop"
            }
          };
        if (options.data.event_id === "rejected")
          return { ok: false, msg: "活动已关闭" };
        return { ok: true, checked: 2, total: 3 };
      }
    }
  });
  vm.runInContext(
    output +
      "\nglobalThis.client = { manageAttendance, generateAttendanceCode };",
    context
  );
  const stats = await context.client.manageAttendance("stats", {
    event_id: "event-1"
  });
  assert.equal(stats.checked, 2);
  assert.equal(calls[0].path, "/api/v1/attendance/manage/stats");
  assert.equal(calls[0].options.data.event_id, "event-1");
  await assert.rejects(
    context.client.manageAttendance("manual_checkin", { event_id: "rejected" }),
    /活动已关闭/
  );
  const code = await context.client.generateAttendanceCode(
    "event/afternoon",
    "develop"
  );
  assert.equal(code.event_id, "event/afternoon");
  assert.equal(
    calls.at(-1).path,
    "/api/v1/attendance/manage/events/event%2Fafternoon/code"
  );
});

test("switching activities discards a slow old response and reads the selected event ID", async () => {
  let resolveOld;
  const oldPromise = new Promise(resolve => {
    resolveOld = resolve;
  });
  const h = harness(async (operation, payload) => {
    if (payload.event_id === "morning") return oldPromise;
    if (operation === "stats")
      return { ok: true, event_name: "下午", checked: 1 };
    if (operation === "event_detail")
      return {
        ok: true,
        rows: [{ registration_id: "afternoon-person" }],
        event: { event_id: "afternoon" }
      };
    return { ok: true };
  });
  const oldRead = h.actions.selectEvent({ event_id: "morning", name: "上午" });
  await h.actions.selectEvent({ event_id: "afternoon", name: "下午" });
  resolveOld({
    ok: true,
    event_name: "旧上午",
    rows: [{ registration_id: "old-person" }]
  });
  await oldRead;
  assert.equal(h.state.activeEventId.value, "afternoon");
  assert.equal(h.state.stats.value.event_name, "下午");
  assert.equal(h.state.people.value[0].registration_id, "afternoon-person");
  assert.equal(h.calls.filter(call => call.operation === "stats").length, 2);
});

test("import confirmation cannot reuse a preview after the uploaded roster changes", async () => {
  const h = harness(async () => ({
    ok: true,
    preview_token: "test-preview",
    preview_issued_at: "test-time"
  }));
  h.state.importTarget.value = "event-1";
  h.state.importAttendees.value = [{ name: "示例学长" }];
  await h.actions.previewImport();
  h.state.importAttendees.value.push({ name: "新增学长" });
  await h.actions.applyImport();
  assert.match(h.state.error.value, /名单已变化/);
  assert.equal(
    h.calls.some(call => call.operation === "import_apply"),
    false
  );
});

test("ordinary creation without a verified organization sends no write; QR uses the selected session", async () => {
  const h = harness();
  Object.assign(h.state.editor, {
    event_name: "测试活动",
    event_date: "2026-10-07",
    activity_type: "course",
    checkin_start_at: "2026-10-07T08:00",
    checkin_end_at: "2026-10-07T09:00"
  });
  await h.actions.saveEditor();
  assert.match(h.state.error.value, /所属组织/);
  assert.equal(h.calls.length, 0);
  h.state.codeEvent.value = { event_id: "afternoon-event", name: "下午签到" };
  h.state.codeVersion.value = "trial";
  await h.actions.createCode();
  assert.equal(h.calls[0].payload.eventId, "afternoon-event");
  assert.equal(h.state.code.value.event_id, "afternoon-event");
});
