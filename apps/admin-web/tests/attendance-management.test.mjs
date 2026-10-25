import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import dayjs from "dayjs";
import { computed, nextTick, reactive, ref, watch } from "vue";
import * as parser from "../src/utils/attendanceImportParser.mjs";
import * as management from "../src/utils/attendanceManagement.mjs";
import * as XLSXModule from "xlsx";

test("renamed registrants can be searched and exported by their current master name", () => {
  const rows = [
    {
      registration_id: "rename-slot",
      registered_name: "合成学长（旧）",
      name: "合成学长（旧）",
      registered_display_name: "合成学长",
      actual_attendee_name: "合成实际来宾",
      checked: true
    }
  ];
  assert.equal(
    management.filterAttendanceRows(rows, "all", "合成学长").length,
    1
  );
  const cells = management.attendanceExportRows(rows);
  assert.equal(cells[1][1], "合成学长");
  assert.equal(cells[1][0], "合成实际来宾");
  assert.equal(rows[0].registered_name, "合成学长（旧）");
});

test("class exports include canonical groups, numeric group rates and all six onsite totals", () => {
  const detail = management.attendanceExportRows(
    [
      {
        name: "合成学长",
        current_membership_resolved: true,
        current_group_name: "笃行组",
        group_name: "",
        class_group_participation_rate: 0.75
      }
    ],
    "CLASS_MEETING"
  );
  assert.equal(detail[1][detail[0].indexOf("小组")], "笃行组");
  assert.equal(detail[1][detail[0].indexOf("班会小组参会率")], 0.75);
  const summary = management.attendanceSummaryRows({
    home_class_total: 35,
    home_class_checked: 23,
    cross_class_checked: 5,
    guest_checked: 0,
    onsite_total: 28,
    rate: 65
  });
  assert.deepEqual(
    summary.slice(1).map(row => row[1]),
    [35, 23, 5, 0, 28, 0.65]
  );
  const groups = management.attendanceGroupSummaryRows({
    group_statistics: [
      {
        group_name: "笃行组",
        roster_count: 4,
        present_count: 3,
        participation_rate: 0.75
      }
    ]
  });
  assert.deepEqual(groups[1], ["笃行组", 4, 3, 0.75]);
  assert.equal(
    parser.attendanceGroupLabel({ group_name: "精进组" }, "CLASS_MEETING"),
    "精进组"
  );
  assert.equal(
    parser.attendanceGroupLabel(
      { current_membership_resolved: true, group_name: "历史组" },
      "class_meeting"
    ),
    "待分组"
  );
});

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

test("contact header aliases match phones without matching phone-device metadata", () => {
  for (const header of [
    "手机",
    "手机号",
    "手机号码（选填）",
    "联系人手机",
    "报名手机号",
    "联系电话",
    "phone_number"
  ]) {
    const columns = parser.detectColumns(["姓名", header]);
    assert.equal(columns.phone, 1, header);
  }
  assert.equal(
    parser.detectColumns(["姓名", "手机型号", "电话备注"]).phone,
    -1
  );
});

test("onsite group numbers use the explicit column and fall back to numeric group labels", () => {
  const parsed = parser.parseAttendanceRows([
    ["姓名", "组号", "小组", "桌号"],
    ["示例甲", "", "1组", 3],
    ["示例乙", 4, "第2组", 5],
    ["示例丙", "", "精进组", 6],
    ["示例丁", "", "第 7 小组", 8]
  ]);
  assert.deepEqual(
    parsed.attendees.map(row => row.group_num),
    [1, 4, null, 7]
  );
  assert.equal(parsed.attendees[0].dinner_table_num, 3);
  assert.equal(
    parser.attendanceGroupLabel(parsed.attendees[1], "course"),
    "第4组"
  );
  assert.equal(
    parser.attendanceGroupLabel(parsed.attendees[2], "class_meeting"),
    "精进组"
  );
  assert.equal(parser.attendanceGroupLabel(parsed.attendees[2], "course"), "—");
  const courseExport = management.attendanceExportRows(
    parsed.attendees,
    "course"
  );
  assert.equal(courseExport[0].includes("小组"), false);
  assert.equal(courseExport[0].includes("组号"), false);
  assert.equal(courseExport[2][courseExport[0].indexOf("现场分组")], "第4组");
  const classExport = management.attendanceExportRows(
    parsed.attendees,
    "class_meeting"
  );
  assert.equal(classExport[3][classExport[0].indexOf("小组")], "精进组");
  for (const invalid of [0, -1, "3abc", "1.5", "精进组"])
    assert.equal(parser.attendanceGroupNumber(invalid), null);
});

test("uploaded filename supplies the activity name while preserving manually edited names", () => {
  const first = parser.attendanceNameFromFile("示例报告会.XLS");
  assert.equal(first, "示例报告会");
  assert.equal(
    parser.attendanceNameFromFile("新活动.xlsx", first, first),
    "新活动"
  );
  assert.equal(
    parser.attendanceNameFromFile("新活动.xlsx", "手工活动名称", first),
    "手工活动名称"
  );
  assert.equal(
    parser.attendanceNameFromFile("新活动.xlsx", "   ", first),
    "新活动"
  );
});

test("named group category never shadows the later numeric seat-group column", () => {
  assert.equal(parser.detectColumns(["姓名", "分组", "组号"]).group_num, 2);
  for (const headers of [
    ["姓名", "组别", "组号", "桌号"],
    ["姓名", "组号", "组别", "桌号"]
  ]) {
    const row =
      headers[1] === "组别"
        ? ["合成甲", "奋斗组", "1组", 3]
        : ["合成甲", "1组", "奋斗组", 3];
    const parsed = parser.parseAttendanceRows([headers, row]);
    assert.equal(parsed.attendees[0].group_name, "奋斗组");
    assert.equal(parsed.attendees[0].group_num, 1);
    assert.equal(parsed.attendees[0].dinner_table_num, 3);
    assert.equal(
      parser.attendanceGroupLabel(parsed.attendees[0], "course"),
      "第1组"
    );
    assert.equal(
      parser.attendanceGroupLabel(parsed.attendees[0], "class_meeting"),
      "奋斗组"
    );
  }
  assert.equal(
    parser.parseAttendanceRows([
      ["姓名", "组别"],
      ["合成乙", "2组"]
    ]).attendees[0].group_num,
    2
  );
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

function harness(api = async () => ({ ok: true }), xlsx = XLSXModule) {
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
    .replaceAll('await import("xlsx")', "XLSXModule")
    .replace(/export \{\};?/, "");
  const calls = [];
  const context = vm.createContext({
    ...parser,
    ...management,
    XLSXModule: xlsx,
    computed,
    reactive,
    ref,
    watch,
    dayjs,
    defineOptions() {},
    onMounted() {},
    onUnmounted() {},
    hasPerms: () => true,
    ElMessage: { success() {}, error() {}, warning() {} },
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
    generateCommonAttendanceCode: async env => {
      calls.push({ operation: "common-code", payload: { env } });
      return {
        image_base64: "cG5n",
        mime_type: "image/png",
        event_id: "",
        page: "pages/checkin/events",
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
      "\nglobalThis.state = { selected, workspaceVisible, activeEventId, stats, people, detailLoading, importTarget, importAttendees, importGroupField, preview, previewFingerprint, error, editor, regionId, editingId, classOptions, groupOptions, orgOptions, organizations, rosterOptions, rosterMembers, rosterSource, rosterVersion, rosterOptionId, code, codeEvent, codeVersion, codeKind, editorActivityTypes, creationAttendees, creationPreview, creationPreviewFingerprint }; globalThis.actions = { selectEvent, confirmEvent, previewImport, applyImport, saveEditor, createCode, openCommonCode, previewCreation, openEditor, confirmPerson, exportExcel };",
    context
  );
  return { state: context.state, actions: context.actions, calls };
}

test("downloaded workbook round-trips groups, six fresh totals and percentage cell formats", async () => {
  let workbook;
  const event = {
    event_id: "morning",
    name: "合成班会",
    activity_type: "CLASS_MEETING",
    session_code: "MORNING"
  };
  const h = harness(
    async () => ({
      event,
      rows: [
        {
          name: "合成学长",
          current_membership_resolved: true,
          current_group_name: "笃行组",
          class_group_participation_rate: 0.75
        }
      ],
      stats: {
        home_class_total: 35,
        home_class_checked: 23,
        cross_class_checked: 5,
        guest_checked: 0,
        onsite_total: 28,
        rate: 65
      },
      group_statistics: [
        {
          group_name: "笃行组",
          roster_count: 4,
          present_count: 3,
          participation_rate: 0.75
        }
      ],
      group_statistics_basis: "日间去重 / 本组在册"
    }),
    {
      ...XLSXModule,
      writeFile: value => {
        workbook = value;
      }
    }
  );
  h.state.selected.value = event;
  h.state.activeEventId.value = "morning";
  h.state.stats.value = { home_class_total: 999 };
  await h.actions.exportExcel();
  assert.ok(workbook);
  const bytes = XLSXModule.write(workbook, {
    type: "buffer",
    bookType: "xlsx"
  });
  const result = XLSXModule.read(bytes, { type: "buffer", cellNF: true });
  assert.deepEqual(result.SheetNames, ["签到名单", "签到汇总", "小组参会统计"]);
  assert.equal(result.Sheets["签到名单"].G2.v, "笃行组");
  assert.equal(result.Sheets["签到名单"].H2.v, 0.75);
  assert.equal(result.Sheets["签到名单"].H2.z, "0.0%");
  assert.deepEqual(
    [2, 3, 4, 5, 6, 7].map(row => result.Sheets["签到汇总"][`B${row}`].v),
    [35, 23, 5, 0, 28, 0.65]
  );
  assert.equal(result.Sheets["小组参会统计"].D2.v, 0.75);
  assert.equal(result.Sheets["小组参会统计"].D2.z, "0.0%");
});

test("opening activity management is visible before slow details finish and survives a read error", async () => {
  let reject;
  const pending = new Promise((_, fail) => {
    reject = fail;
  });
  const h = harness(() => pending);
  const opening = h.actions.selectEvent({
    event_id: "draft-1",
    name: "待确认活动",
    lifecycle_status: "DRAFT"
  });
  assert.equal(h.state.workspaceVisible.value, true);
  assert.equal(h.state.detailLoading.value, true);
  assert.equal(h.state.activeEventId.value, "draft-1");
  reject(new Error("活动详情暂时不可用"));
  await opening;
  assert.equal(h.state.workspaceVisible.value, true);
  assert.equal(h.state.detailLoading.value, false);
  assert.match(h.state.error.value, /暂时不可用/);
  assert.equal(
    h.calls.some(call => call.operation === "event_lifecycle_update"),
    false
  );
});

test("the draft row confirmation uses the selected activity and preserves the existing lifecycle operation", async () => {
  const h = harness(async operation =>
    operation === "admin_events" ? { items: [] } : {}
  );
  await h.actions.confirmEvent({
    event_id: "draft-2",
    name: "待确认活动",
    lifecycle_status: "DRAFT"
  });
  assert.equal(h.state.workspaceVisible.value, true);
  const writes = h.calls.filter(
    call => call.operation === "event_lifecycle_update"
  );
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
  h.state.regionId.value = "center-1";
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

function configureOrdinaryCreation(h) {
  Object.assign(h.state.editor, {
    event_name: "隔离报名活动",
    event_date: "2026-10-11",
    activity_type: "course",
    checkin_start_at: "2026-10-11T08:00",
    checkin_end_at: "2026-10-11T18:00"
  });
  h.state.regionId.value = "isolated-center";
}

test("common QR generation needs no selected activity and never rotates a session code", async () => {
  const h = harness();
  h.state.codeEvent.value = { event_id: "old-session" };
  h.state.codeVersion.value = "trial";
  await h.actions.openCommonCode();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].operation, "common-code");
  assert.equal(h.calls[0].payload.env, "trial");
  assert.equal(h.state.codeEvent.value, null);
  assert.equal(h.state.code.value.page, "pages/checkin/events");
});

test("ordinary creation requires a roster and uses the original signed upload preview before saving", async () => {
  const h = harness(async operation =>
    operation === "upload_preview"
      ? { ok: true, preview_token: "signed-preview", preview_issued_at: 123 }
      : { ok: true }
  );
  configureOrdinaryCreation(h);
  await h.actions.saveEditor();
  assert.match(h.state.error.value, /上传报名表格/);
  assert.equal(h.calls.length, 0);
  h.state.creationAttendees.value = [
    { name: "合成报名联系人" },
    { name: "合成报名联系人" }
  ];
  await h.actions.previewCreation();
  assert.equal(h.calls[0].operation, "upload_preview");
  assert.equal(h.calls[0].payload.attendees.length, 2);
  await h.actions.saveEditor();
  const write = h.calls.find(call => call.operation === "upload");
  assert.ok(write);
  assert.equal(write.payload.preview_token, "signed-preview");
  assert.equal(write.payload.attendees.length, 2);
  assert.equal(
    h.calls.some(call => call.operation === "create_event"),
    false
  );
});

test("creation rejects a preview after activity metadata or roster changes", async () => {
  for (const mutate of [
    h => (h.state.editor.event_name = "修改名称"),
    h => h.state.creationAttendees.value.push({ name: "新增报名" })
  ]) {
    const h = harness(async () => ({
      ok: true,
      preview_token: "old-preview",
      preview_issued_at: 123
    }));
    configureOrdinaryCreation(h);
    h.state.creationAttendees.value = [{ name: "合成报名" }];
    await h.actions.previewCreation();
    mutate(h);
    await h.actions.saveEditor();
    assert.match(h.state.error.value, /重新核对/);
    assert.equal(
      h.calls.some(call => call.operation === "upload"),
      false
    );
  }
});

test("group study meetings are absent from new activity choices and cannot be created here", async () => {
  const h = harness();
  assert.equal(
    h.state.editorActivityTypes.value.some(
      ([value]) => value === "group_meeting"
    ),
    false
  );
  configureOrdinaryCreation(h);
  h.state.editor.activity_type = "group_meeting";
  await h.actions.saveEditor();
  assert.match(h.state.error.value, /辅导员/);
  assert.equal(h.calls.length, 0);
  // Historical activity labels remain available when editing existing events.
  h.state.editingId.value = "historical";
  assert.equal(
    h.state.editorActivityTypes.value.some(
      ([value]) => value === "group_meeting"
    ),
    true
  );
});

const organizationFixture = {
  centers: [
    { id: "center-a", name: "甲分中心" },
    { id: "center-b", name: "乙分中心" }
  ],
  org_units: [
    { id: "center-a", parent_id: "shuku" },
    { id: "center-b", parent_id: "shuku" }
  ],
  classes: [
    { id: "class-a", parent_id: "center-a", name: "圆融五班" },
    { id: "class-b", parent_id: "center-b", name: "同名五班" }
  ]
};

test("class options follow center IDs; changing center clears the class and loaded roster", async () => {
  const h = harness(async () => organizationFixture);
  await h.actions.openEditor();
  h.state.editor.activity_type = "class_meeting";
  h.state.regionId.value = "center-a";
  await nextTick();
  assert.deepEqual(
    Array.from(h.state.rosterOptions.value, item => item.id),
    ["class-a"]
  );
  h.state.rosterOptionId.value = "class-a";
  await nextTick();
  assert.equal(h.state.editor.event_name, "圆融五班班级学习会");
  h.state.rosterMembers.value = [{ name: "合成学员" }];
  h.state.rosterSource.value = "ops_roster";
  h.state.rosterVersion.value = { source_version: "old" };
  h.state.regionId.value = "center-b";
  await nextTick();
  assert.equal(h.state.rosterOptionId.value, "");
  assert.equal(h.state.rosterMembers.value.length, 0);
  assert.equal(h.state.rosterSource.value, "");
  assert.equal(h.state.rosterVersion.value, null);
  assert.equal(h.state.editor.event_name, "");
  h.state.rosterOptionId.value = "class-b";
  await nextTick();
  assert.equal(h.state.editor.event_name, "同名五班班级学习会");
  h.state.regionId.value = "";
  await nextTick();
  assert.equal(h.state.rosterOptions.value.length, 0);
});

test("editing a class-ID event restores its center and preserves its historical name", async () => {
  const h = harness(async () => organizationFixture);
  await h.actions.openEditor({
    event_id: "existing",
    org_unit_id: "class-a",
    class_org_unit_id: "class-a",
    activity_type: "class_meeting",
    name: "原定专题班会"
  });
  await nextTick();
  assert.equal(h.state.regionId.value, "center-a");
  assert.equal(h.state.rosterOptionId.value, "class-a");
  assert.equal(h.state.editor.event_name, "原定专题班会");
});

test("organization filtering supports nested scopes and rejects missing or cyclic ancestry", () => {
  const options = [
    { id: "class-a", parent_id: "center-a" },
    { id: "orphan" },
    { id: "loop", parent_id: "loop" }
  ];
  assert.deepEqual(
    management.scopedRosterOptions(
      options,
      organizationFixture.org_units,
      "shuku"
    ),
    [options[0]]
  );
  assert.deepEqual(
    management.scopedRosterOptions(
      options,
      organizationFixture.org_units,
      "center-b"
    ),
    []
  );
  assert.deepEqual(
    management
      .scopedRosterOptions(
        [{ id: "group-a", parent_id: "class-a" }],
        [...organizationFixture.org_units, ...options],
        "center-a"
      )
      .map(item => item.id),
    ["group-a"]
  );
});

test("manual attendance explains team slots and event restrictions and never writes a team contact", async () => {
  const event = { lifecycle_status: "CONFIRMED", checkin_status: "open" };
  assert.equal(management.manualCheckinReason(event, {}), "");
  assert.match(
    management.manualCheckinReason(event, { is_team: true }),
    /实际参加人/
  );
  assert.match(
    management.manualCheckinReason({ ...event, lifecycle_status: "DRAFT" }, {}),
    /先确认/
  );
  assert.match(
    management.manualCheckinReason({ ...event, checkin_status: "ended" }, {}),
    /已结束/
  );
  assert.match(management.manualCheckinReason(event, {}, true), /稍候/);
  const h = harness();
  h.state.selected.value = { ...event, event_id: "event-a" };
  h.state.activeEventId.value = "event-a";
  await h.actions.confirmPerson({
    is_team: true,
    registration_id: "team-slot"
  });
  assert.equal(h.calls.length, 0);
});
