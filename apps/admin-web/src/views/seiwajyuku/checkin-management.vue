<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import dayjs from "dayjs";
import { hasPerms } from "@/utils/auth";
import {
  manageAttendance,
  generateAttendanceCode,
  type ManagedEvent,
  type EngineResult,
  type AttendanceManagementOperation,
  type AttendanceCode
} from "@/api/attendance-management";
import {
  parseAttendanceRows,
  buildRosterQuality
} from "@/utils/attendanceImportParser.mjs";
import {
  activityTypes,
  lifecycleLabels,
  sessionLabels,
  checkinLabels,
  rosterSelection,
  chinaLocalDateTime,
  canMaintainRegistration,
  canConfirmManually,
  filterAttendanceRows,
  attendanceExportRows
} from "@/utils/attendanceManagement.mjs";

defineOptions({ name: "CheckinManagement" });
const permission = (action: string) => hasPerms(`attendance:${action}`);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const events = ref<ManagedEvent[]>([]);
const selected = ref<ManagedEvent | null>(null);
const workspaceVisible = ref(false);
const activeEventId = ref("");
const page = ref(1);
const hasMore = ref(false);
const filters = reactive({
  keyword: "",
  activity_type: "",
  lifecycle_status: "",
  date_from: dayjs().subtract(30, "day").format("YYYY-MM-DD"),
  date_to: dayjs().add(30, "day").format("YYYY-MM-DD")
});
const stats = ref<EngineResult | null>(null);
const people = ref<any[]>([]);
const detailLoading = ref(false);
const refreshedAt = ref("");
const personFilter = ref("all");
const personKeyword = ref("");
const autoRefresh = ref(true);
const sessions = computed(() =>
  selected.value?.sessions?.length
    ? selected.value.sessions
    : selected.value
      ? [selected.value]
      : []
);
const currentEvent = computed(
  () =>
    sessions.value.find(item => item.event_id === activeEventId.value) || null
);
const visiblePeople = computed(() =>
  filterAttendanceRows(people.value, personFilter.value, personKeyword.value)
);
const groupRows = computed(() =>
  Object.entries(stats.value?.groups || {}).map(
    ([name, counts]: [string, any]) => ({ name, ...counts })
  )
);
const allowRoster = computed(
  () => permission("import") && canMaintainRegistration(currentEvent.value)
);
const allowExcel = computed(
  () =>
    permission("import") &&
    currentEvent.value?.checkin_status === "upcoming" &&
    !["class_meeting", "group_meeting"].includes(
      currentEvent.value?.activity_type
    )
);
let timer: ReturnType<typeof setInterval> | undefined;
let detailSequence = 0;

const editorVisible = ref(false);
const editingId = ref("");
const editorActivityTypes = computed(() =>
  activityTypes.filter(
    ([value]) => value !== "group_meeting" || Boolean(editingId.value)
  )
);
const creationAttendees = ref<any[]>([]);
const creationFilename = ref("");
const creationGroupField = ref("");
const creationQuality = ref<any>(null);
const creationPreview = ref<EngineResult | null>(null);
const creationPreviewFingerprint = ref("");
const editor = reactive<Record<string, any>>({
  event_name: "",
  event_date: "",
  activity_type: "course",
  checkin_start_at: "",
  checkin_end_at: "",
  scheduled_start_at: "",
  scheduled_end_at: ""
});
const classOptions = ref<any[]>([]);
const groupOptions = ref<any[]>([]);
const orgOptions = ref<any[]>([]);
const rosterOptionId = ref("");
const regionId = ref("");
const rosterOptions = computed(() =>
  editor.activity_type === "group_meeting"
    ? groupOptions.value
    : classOptions.value
);
const selectedRosterOption = computed(() =>
  rosterOptions.value.find(item => item.id === rosterOptionId.value)
);
const rosterMembers = ref<any[]>([]);
const rosterVersion = ref<any>(null);
const rosterSource = ref("");
const importTarget = ref("");
const importVisible = ref(false);
const importAttendees = ref<any[]>([]);
const importGroupField = ref("");
const importHeaderRow = ref(0);
const importFilename = ref("");
const importQuality = ref<any>(null);
const preview = ref<EngineResult | null>(null);
const previewFingerprint = ref("");
const sessionTimes = reactive<Record<string, string>>({});
const sessionPrefixes = [
  { code: "MORNING", prefix: "morning", label: "上午" },
  { code: "AFTERNOON", prefix: "afternoon", label: "下午" },
  { code: "KONPA", prefix: "konpa", label: "晚上空巴" }
];
const manualVisible = ref(false);
const rosterReconciliation = ref<EngineResult | null>(null);
const rosterReconciliationVisible = ref(false);
const manual = reactive({
  name: "",
  phone: "",
  company: "",
  center: "",
  class_name: "",
  group_name: ""
});
const codeVisible = ref(false);
const code = ref<AttendanceCode | null>(null);
const codeVersion = ref<"develop" | "trial" | "release">("develop");
const codeEvent = ref<ManagedEvent | null>(null);
const codeError = ref("");
const codeImage = computed(() =>
  code.value?.image_base64
    ? `data:${code.value.mime_type || "image/png"};base64,${code.value.image_base64}`
    : ""
);

function messageOf(e: any) {
  return (
    e?.response?.data?.detail?.message ||
    e?.response?.data?.detail ||
    e?.message ||
    "操作失败，请稍后重试"
  );
}
function timeLabel(value: string) {
  return chinaLocalDateTime(value).replace("T", " ") || "未配置";
}
function roleLabel(row: any) {
  return (
    row.attendance_role_label ||
    {
      HOME_CLASS_MEMBER: "本班",
      CROSS_CLASS_MEMBER: "外班学长",
      GUEST: "来宾",
      NON_MEMBER: "非塾生"
    }[row.attendance_role] ||
    "参加人"
  );
}
function checked(row: any) {
  return row.checked === true || Boolean(row.checked_at);
}
function rowStatus(row: any) {
  return checked(row)
    ? "已签到"
    : { late: "预计迟到", leave: "请假", pending: "未签到" }[
        row.attendance_status
      ] || "未签到";
}
async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (e) {
    error.value = messageOf(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = false;
  }
}

async function loadEvents(reset = false) {
  if (!permission("view")) return;
  if (reset) page.value = 1;
  loading.value = true;
  try {
    const result = await manageAttendance("admin_events", {
      ...filters,
      page: page.value,
      page_size: 20
    });
    events.value = result.items || result.events || [];
    hasMore.value = Boolean(result.has_more);
    if (selected.value) {
      const replacement = events.value.find(
        item =>
          item.event_id === selected.value.event_id ||
          item.sessions?.some(
            session => session.event_id === activeEventId.value
          )
      );
      if (replacement) selected.value = replacement;
    }
    error.value = "";
  } catch (e) {
    error.value = messageOf(e);
  } finally {
    loading.value = false;
  }
}
async function selectEvent(item: any) {
  selected.value = item;
  workspaceVisible.value = true;
  personFilter.value = "all";
  personKeyword.value = "";
  await switchSession(
    (
      item.sessions?.find(session => session.checkin_status === "open") ||
      item.sessions?.[0] ||
      item
    ).event_id
  );
}
async function confirmEvent(item: any) {
  await selectEvent(item);
  await changeLifecycle("CONFIRMED");
}
async function refreshDetail(quiet = false) {
  if (!activeEventId.value || detailLoading.value) return;
  const eventId = activeEventId.value;
  const sequence = ++detailSequence;
  detailLoading.value = true;
  try {
    const [summary, detail] = await Promise.all([
      manageAttendance("stats", { event_id: eventId }),
      manageAttendance("event_detail", { event_id: eventId })
    ]);
    if (eventId !== activeEventId.value || sequence !== detailSequence) return;
    stats.value = summary;
    people.value = detail.rows || [];
    if (detail.event && selected.value) {
      if (selected.value.sessions?.length) {
        selected.value = {
          ...selected.value,
          sessions: selected.value.sessions.map(item =>
            item.event_id === eventId ? { ...item, ...detail.event } : item
          )
        };
      } else selected.value = { ...selected.value, ...detail.event };
    }
    refreshedAt.value = dayjs().format("HH:mm:ss");
    error.value = "";
  } catch (e) {
    error.value = messageOf(e);
    if (!quiet) ElMessage.error(error.value);
  } finally {
    if (sequence === detailSequence) detailLoading.value = false;
  }
}
async function switchSession(eventId: string) {
  ++detailSequence;
  activeEventId.value = eventId;
  stats.value = null;
  people.value = [];
  // Finish the previous read before starting the next; stale results are discarded.
  detailLoading.value = false;
  await refreshDetail();
}
async function reloadAfterMutation() {
  await loadEvents();
  await refreshDetail();
}
async function loadRosterOptions() {
  const result = await manageAttendance("ops_roster_options");
  classOptions.value = result.classes || result.class_options || [];
  groupOptions.value = (result.groups || result.group_options || []).map(
    item => {
      const homeClass = classOptions.value.find(
        option => option.id === (item.class_org_unit_id || item.parent_id)
      );
      return {
        ...item,
        org_unit_id: item.org_unit_id || homeClass?.parent_id || "",
        class_name: item.class_name || homeClass?.name || ""
      };
    }
  );
  orgOptions.value = result.centers || result.org_units || [];
}
function setDateDefaults() {
  if (!editor.event_date) return;
  editor.checkin_start_at = `${editor.event_date}T00:00`;
  editor.checkin_end_at = `${editor.event_date}T23:59`;
  const defaults = {
    morning: ["07:30", "09:00", "10:30", "12:00"],
    afternoon: ["12:10", "13:30", "15:00", "17:00"],
    konpa: ["17:10", "18:00", "20:30", "20:30"]
  };
  for (const [prefix, times] of Object.entries(defaults)) {
    [
      "checkin_start",
      "scheduled_start",
      "checkin_end",
      "scheduled_end"
    ].forEach(
      (key, index) =>
        (sessionTimes[`${prefix}_${key}`] =
          `${editor.event_date}T${times[index]}`)
    );
  }
}
async function openEditor(item?: ManagedEvent) {
  if (busy.value) return;
  editingId.value = item?.event_id || "";
  rosterOptionId.value = "";
  regionId.value = item?.org_unit_id || "";
  rosterMembers.value = [];
  rosterVersion.value = null;
  rosterSource.value = "";
  importAttendees.value = [];
  preview.value = null;
  resetCreationImport();
  Object.assign(editor, {
    event_name: item?.name || "",
    event_date: item?.event_date || dayjs().format("YYYY-MM-DD"),
    activity_type: item?.activity_type || "course",
    checkin_start_at: chinaLocalDateTime(item?.checkin_start_at),
    checkin_end_at: chinaLocalDateTime(item?.checkin_end_at),
    scheduled_start_at: chinaLocalDateTime(item?.scheduled_start_at),
    scheduled_end_at: chinaLocalDateTime(item?.scheduled_end_at)
  });
  if (!item) setDateDefaults();
  editorVisible.value = true;
  await perform(async () => {
    await loadRosterOptions();
    rosterOptionId.value =
      item?.group_org_unit_id || item?.class_org_unit_id || "";
  });
}
function organizationFields() {
  if (selectedRosterOption.value) {
    const identity = rosterSelection(
      selectedRosterOption.value,
      editor.activity_type === "group_meeting" ? "group" : "class"
    );
    const original = currentEvent.value;
    if (
      editingId.value &&
      original &&
      identity.class_org_unit_id === (original.class_org_unit_id || "") &&
      identity.group_org_unit_id === (original.group_org_unit_id || "")
    ) {
      identity.org_unit_id = original.org_unit_id || identity.org_unit_id;
      identity.center_name ||= original.center_name || "";
    }
    return identity;
  }
  if (["class_meeting", "group_meeting"].includes(editor.activity_type))
    throw new Error("请选择已确认组织 ID 的班级或小组");
  if (!regionId.value) throw new Error("请选择活动所属组织 / 分中心");
  return {
    org_unit_id: regionId.value || "",
    class_org_unit_id: "",
    group_org_unit_id: "",
    center_name:
      orgOptions.value.find(item => item.id === regionId.value)?.name || "",
    class_name: "",
    group_name: ""
  };
}
function editorMetadata() {
  if (!editor.event_name.trim()) throw new Error("请填写活动名称");
  if (!editor.event_date || !editor.checkin_start_at || !editor.checkin_end_at)
    throw new Error("请填写活动日期和签到时间");
  if (editor.checkin_start_at >= editor.checkin_end_at)
    throw new Error("签到截止时间必须晚于开放时间");
  return { ...editor, ...organizationFields() };
}
async function readRoster() {
  await perform(async () => {
    const identity = rosterSelection(
      selectedRosterOption.value,
      editor.activity_type === "group_meeting" ? "group" : "class"
    );
    const result = await manageAttendance("ops_roster_members", identity);
    rosterMembers.value = result.attendees || [];
    rosterVersion.value = result.version || null;
    rosterSource.value = "ops_roster";
    if (!rosterMembers.value.length)
      throw new Error("当前可信组织名单为空，请核对组织关系");
    ElMessage.success(`已读取 ${rosterMembers.value.length} 名学员`);
  });
}
async function saveEditor() {
  await perform(async () => {
    if (!editor.event_name.trim()) throw new Error("请填写活动名称");
    if (!editor.event_date) throw new Error("请填写活动日期");
    if (!editingId.value && editor.activity_type === "group_meeting")
      throw new Error("小组学习会请由辅导员在学习会入口安排");
    let operation: AttendanceManagementOperation;
    let payload: Record<string, any>;
    if (!editingId.value && editor.activity_type === "class_meeting") {
      if (rosterSource.value !== "ops_roster" || !rosterMembers.value.length)
        throw new Error("请先读取当前班级的可信组织名单");
      if (!buildRosterQuality(rosterMembers.value).passed)
        throw new Error("组织名单资料异常，请修正后重新读取");
      operation = "create_class_meeting_sessions";
      payload = {
        event_name: editor.event_name,
        event_date: editor.event_date,
        ...organizationFields(),
        ...sessionTimes,
        group_field: "class_name",
        roster_source: "ops_roster",
        roster_members: rosterMembers.value,
        roster_version:
          rosterVersion.value?.file_name ||
          rosterVersion.value?.source_version ||
          ""
      };
    } else {
      payload = editorMetadata();
      if (editingId.value) {
        operation = "event_update";
        payload = {
          ...payload,
          event_id: editingId.value,
          name: editor.event_name
        };
        // Send only changed protected fields: a name-only edit stays possible after check-in begins.
        const original = currentEvent.value;
        for (const key of [
          "checkin_start_at",
          "checkin_end_at",
          "scheduled_start_at",
          "scheduled_end_at"
        ]) {
          if (payload[key] === chinaLocalDateTime(original?.[key]))
            delete payload[key];
        }
        for (const key of [
          "org_unit_id",
          "class_org_unit_id",
          "group_org_unit_id"
        ]) {
          if (String(payload[key] || "") === String(original?.[key] || ""))
            delete payload[key];
        }
      } else {
        if (!permission("import"))
          throw new Error("当前账号没有导入报名名单的权限");
        if (!creationAttendees.value.length)
          throw new Error("请上传报名表格并核对导入预览");
        if (
          !creationPreview.value ||
          creationPreviewFingerprint.value !== JSON.stringify(creationPayload())
        )
          throw new Error("活动信息或报名表已变化，请重新核对导入预览");
        operation = "upload";
        payload = {
          ...creationPayload(),
          preview_token: creationPreview.value.preview_token,
          preview_issued_at: creationPreview.value.preview_issued_at
        };
      }
    }
    const result = await manageAttendance(operation, payload);
    ElMessage.success(result.msg || "活动已保存");
    editorVisible.value = false;
    await loadEvents(true);
    if (editingId.value) await refreshDetail();
  });
}
function resetCreationImport() {
  creationAttendees.value = [];
  creationFilename.value = "";
  creationGroupField.value = "";
  creationQuality.value = null;
  creationPreview.value = null;
  creationPreviewFingerprint.value = "";
}
async function parseExcelFile(file: File) {
  const XLSX = await import("xlsx");
  const workbook = XLSX.read(await file.arrayBuffer(), { type: "array" });
  const cells = XLSX.utils.sheet_to_json(
    workbook.Sheets[workbook.SheetNames[0]],
    { header: 1, defval: "" }
  );
  const parsed = parseAttendanceRows(cells);
  if (!parsed.attendees.length) throw new Error("表格未包含可导入的报名记录");
  return parsed;
}
async function readCreationFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  resetCreationImport();
  await perform(async () => {
    const parsed = await parseExcelFile(file);
    creationAttendees.value = parsed.attendees;
    creationFilename.value = file.name;
    creationGroupField.value = parsed.groupField;
    creationQuality.value = parsed.quality;
  });
  input.value = "";
}
function creationPayload() {
  return {
    ...editorMetadata(),
    attendees: creationAttendees.value,
    group_field: creationGroupField.value
  };
}
async function previewCreation() {
  creationPreview.value = null;
  await perform(async () => {
    if (!permission("import"))
      throw new Error("当前账号没有导入报名名单的权限");
    if (!creationAttendees.value.length) throw new Error("请先上传报名表格");
    const payload = creationPayload();
    const fingerprint = JSON.stringify(payload);
    creationPreview.value = await manageAttendance("upload_preview", payload);
    creationPreviewFingerprint.value = fingerprint;
  });
}
async function changeLifecycle(next: "DRAFT" | "CONFIRMED" | "CANCELLED") {
  if (!currentEvent.value) return;
  const targetNames = sessions.value
    .map(
      item => item.session_name || sessionLabels[item.session_code] || item.name
    )
    .join("、");
  try {
    await ElMessageBox.confirm(
      `确定将“${selected.value.name}”${lifecycleLabels[next]}？本活动包含 ${targetNames}；操作将作用于全部场次。`,
      "活动状态",
      { type: next === "CANCELLED" ? "warning" : "info" }
    );
  } catch {
    return;
  }
  await perform(async () => {
    const result = await manageAttendance("event_lifecycle_update", {
      event_id: activeEventId.value,
      lifecycle_status: next,
      reason: "运营平台活动管理"
    });
    ElMessage.success(result.msg || "活动状态已更新");
    await reloadAfterMutation();
  });
}
async function toggleCheckin() {
  if (!currentEvent.value) return;
  const next =
    currentEvent.value.manual_status === "closed" ? "active" : "closed";
  try {
    await ElMessageBox.confirm(
      `${next === "closed" ? "关闭" : "开放"}当前“${currentEvent.value.session_name || currentEvent.value.name}”签到？开放仍受活动确认状态和签到时间限制。`,
      "场次签到状态"
    );
  } catch {
    return;
  }
  await perform(async () => {
    await manageAttendance("event_update", {
      event_id: activeEventId.value,
      status: next
    });
    ElMessage.success("场次签到状态已更新");
    await reloadAfterMutation();
  });
}
async function openImport() {
  importTarget.value = activeEventId.value;
  importAttendees.value = [];
  importFilename.value = "";
  importQuality.value = null;
  preview.value = null;
  importVisible.value = true;
}
async function readFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  preview.value = null;
  importAttendees.value = [];
  await perform(async () => {
    const parsed = await parseExcelFile(file);
    importAttendees.value = parsed.attendees;
    importGroupField.value = parsed.groupField;
    importHeaderRow.value = parsed.headerRow;
    importFilename.value = file.name;
    importQuality.value = parsed.quality;
    if (!parsed.attendees.length) throw new Error("表格未包含可导入的报名记录");
  });
  (e.target as HTMLInputElement).value = "";
}
function importPayload() {
  return {
    event_id: importTarget.value,
    attendees: importAttendees.value,
    group_field: importGroupField.value
  };
}
function importFingerprint() {
  return JSON.stringify(importPayload());
}
async function previewImport() {
  await perform(async () => {
    if (!importAttendees.value.length) throw new Error("请先选择报名表");
    const fingerprint = importFingerprint();
    preview.value = await manageAttendance("import_preview", importPayload());
    previewFingerprint.value = fingerprint;
  });
}
async function applyImport() {
  await perform(async () => {
    if (!preview.value || previewFingerprint.value !== importFingerprint())
      throw new Error("名单已变化，请重新预览");
    const result = await manageAttendance("import_apply", {
      ...importPayload(),
      preview_token: preview.value.preview_token,
      preview_issued_at: preview.value.preview_issued_at
    });
    ElMessage.success(result.msg || "报名名单已导入");
    importVisible.value = false;
    await reloadAfterMutation();
  });
}
async function syncRoster() {
  try {
    await ElMessageBox.confirm(
      "按当前可信组织关系，仅增补本场及后续未开始场次的名单？已有报名和签到事实保留。",
      "同步组织名单"
    );
  } catch {
    return;
  }
  await perform(async () => {
    const result = await manageAttendance("sync_class_roster", {
      event_id: activeEventId.value
    });
    ElMessage.success(result.msg || "组织名单已同步");
    await reloadAfterMutation();
  });
}
async function showRosterReconciliation() {
  await perform(async () => {
    rosterReconciliation.value = await manageAttendance(
      "class_roster_reconciliation",
      { event_id: activeEventId.value }
    );
    rosterReconciliationVisible.value = true;
  });
}
function openManual() {
  Object.assign(manual, {
    name: "",
    phone: "",
    company: "",
    center: "",
    class_name: "",
    group_name: ""
  });
  manualVisible.value = true;
}
async function saveManual() {
  await perform(async () => {
    if (!manual.name.trim()) throw new Error("请填写来宾姓名");
    await manageAttendance("registration", {
      event_id: activeEventId.value,
      ...manual
    });
    manualVisible.value = false;
    manual.phone = "";
    ElMessage.success("临时报名已新增");
    await reloadAfterMutation();
  });
}
async function updatePerson(row: any, status: string) {
  await perform(async () => {
    await manageAttendance("attendance_status", {
      event_id: activeEventId.value,
      registration_id: row.registration_id,
      status,
      note: ""
    });
    await refreshDetail();
  });
}
async function removePerson(row: any) {
  try {
    await ElMessageBox.confirm(
      `删除“${row.registered_name || row.name}”这一个报名名额？已签到记录不可删除。`,
      "删除报名",
      { type: "warning" }
    );
  } catch {
    return;
  }
  await perform(async () => {
    await manageAttendance("registration_delete", {
      event_id: activeEventId.value,
      registration_id: row.registration_id
    });
    await reloadAfterMutation();
  });
}
async function confirmPerson(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认“${row.registered_name || row.name}”本人现场到场？团队实际参加人请由小程序团队确认入口填写，不能覆盖报名联系人。`,
      "人工确认签到"
    );
  } catch {
    return;
  }
  await perform(async () => {
    const result = await manageAttendance("manual_checkin", {
      event_id: activeEventId.value,
      registration_id: row.registration_id
    });
    ElMessage.success(result.msg || "签到已确认");
    await reloadAfterMutation();
  });
}
async function exportExcel() {
  await perform(async () => {
    const result = await manageAttendance("export", {
      event_id: activeEventId.value
    });
    const XLSX = await import("xlsx");
    const workbook = XLSX.utils.book_new();
    const worksheet = XLSX.utils.aoa_to_sheet(
      attendanceExportRows(result.rows)
    );
    worksheet["!cols"] = Array.from({ length: 13 }, () => ({ wch: 18 }));
    XLSX.utils.book_append_sheet(workbook, worksheet, "签到名单");
    XLSX.writeFile(
      workbook,
      `${currentEvent.value?.name || "签到名单"}-${currentEvent.value?.session_code || ""}.xlsx`
    );
    ElMessage.success("签到明细已导出");
  });
}
async function openCode() {
  codeEvent.value = currentEvent.value;
  code.value = null;
  codeVisible.value = true;
  await createCode();
}
async function createCode() {
  codeError.value = "";
  await perform(async () => {
    if (!codeEvent.value?.event_id) throw new Error("请先选择场次");
    code.value = null;
    try {
      code.value = await generateAttendanceCode(
        codeEvent.value.event_id,
        codeVersion.value
      );
    } catch (e) {
      codeError.value = messageOf(e);
      throw e;
    }
  });
}
function downloadCode() {
  if (!codeImage.value) return;
  const anchor = document.createElement("a");
  anchor.href = codeImage.value;
  anchor.download = `${codeEvent.value?.name || "签到"}-${codeEvent.value?.session_code || ""}-${code.value.env_version}.png`;
  anchor.click();
}
watch(
  () => editor.activity_type,
  () => {
    resetCreationImport();
    rosterOptionId.value = "";
    rosterMembers.value = [];
    rosterSource.value = "";
  }
);
watch(rosterOptionId, () => {
  rosterMembers.value = [];
  rosterSource.value = "";
});
watch(codeVersion, () => {
  code.value = null;
});
onMounted(async () => {
  await loadEvents();
  timer = setInterval(() => {
    if (
      autoRefresh.value &&
      workspaceVisible.value &&
      activeEventId.value &&
      !busy.value &&
      !document.hidden
    )
      void refreshDetail(true);
  }, 15000);
});
onUnmounted(() => {
  if (timer) clearInterval(timer);
  ++detailSequence;
});
</script>

<template>
  <div class="checkin-management">
    <section class="heading">
      <div>
        <h1>签到现场管理</h1>
        <p>创建活动、读取名单并管理每个场次的真实到场。</p>
      </div>
      <div class="actions">
        <router-link to="/operations/activities"
          ><el-button>活动统计与学分记录</el-button></router-link
        ><el-button
          v-if="permission('create')"
          type="primary"
          :disabled="busy"
          @click="openEditor()"
          >创建活动</el-button
        >
      </div>
    </section>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="notice"
    />
    <el-alert
      v-if="!permission('view')"
      title="当前账号没有查看签到管理的权限"
      type="warning"
      :closable="false"
    />
    <template v-else>
      <el-card shadow="never" class="notice">
        <el-form inline @submit.prevent="loadEvents(true)">
          <el-form-item label="活动"
            ><el-input
              v-model="filters.keyword"
              placeholder="活动名称"
              clearable
          /></el-form-item>
          <el-form-item label="类型"
            ><el-select
              v-model="filters.activity_type"
              clearable
              style="width: 180px"
              ><el-option
                v-for="[value, label] in activityTypes"
                :key="value"
                :label="label"
                :value="value" /></el-select
          ></el-form-item>
          <el-form-item label="状态"
            ><el-select
              v-model="filters.lifecycle_status"
              clearable
              style="width: 130px"
              ><el-option
                v-for="(label, value) in lifecycleLabels"
                :key="value"
                :label="label"
                :value="value" /></el-select
          ></el-form-item>
          <el-form-item label="从"
            ><el-date-picker
              v-model="filters.date_from"
              value-format="YYYY-MM-DD"
              style="width: 150px"
          /></el-form-item>
          <el-form-item label="至"
            ><el-date-picker
              v-model="filters.date_to"
              value-format="YYYY-MM-DD"
              style="width: 150px"
          /></el-form-item>
          <el-form-item
            ><el-button native-type="submit" :loading="loading"
              >查询</el-button
            ></el-form-item
          >
        </el-form>
        <el-table
          :data="events"
          v-loading="loading"
          highlight-current-row
          @row-click="selectEvent"
        >
          <el-table-column prop="event_date" label="日期" width="115" />
          <el-table-column prop="name" label="活动" min-width="200"
            ><template #default="{ row }"
              ><strong>{{ row.name }}</strong>
              <div class="muted">
                {{ row.class_name || row.center_name || "" }}
              </div></template
            ></el-table-column
          >
          <el-table-column label="类型" min-width="140"
            ><template #default="{ row }">{{
              activityTypes.find(item => item[0] === row.activity_type)?.[1] ||
              row.activity_type
            }}</template></el-table-column
          >
          <el-table-column label="状态" width="100"
            ><template #default="{ row }"
              ><el-tag
                :type="
                  row.lifecycle_status === 'CONFIRMED' ? 'success' : 'info'
                "
                >{{
                  lifecycleLabels[row.lifecycle_status] || row.lifecycle_status
                }}</el-tag
              ></template
            ></el-table-column
          >
          <el-table-column label="场次" min-width="150"
            ><template #default="{ row }">{{
              (row.sessions || [row])
                .map(
                  item =>
                    item.session_name ||
                    sessionLabels[item.session_code] ||
                    "单场"
                )
                .join(" / ")
            }}</template></el-table-column
          >
          <el-table-column label="管理" width="200" fixed="right"
            ><template #default="{ row }"
              ><el-button
                v-if="permission('update') && row.lifecycle_status === 'DRAFT'"
                type="primary"
                :disabled="busy"
                @click.stop="confirmEvent(row)"
                >确认活动</el-button
              ><el-button type="primary" link @click.stop="selectEvent(row)"
                >管理活动</el-button
              ></template
            ></el-table-column
          >
        </el-table>
        <div class="pagination">
          <el-button
            :disabled="page <= 1 || loading"
            @click="
              page--;
              loadEvents();
            "
            >上一页</el-button
          ><span>第 {{ page }} 页</span
          ><el-button
            :disabled="!hasMore || loading"
            @click="
              page++;
              loadEvents();
            "
            >下一页</el-button
          >
        </div>
      </el-card>

      <el-drawer
        v-model="workspaceVisible"
        title="活动签到管理"
        size="min(1200px, 96vw)"
        :close-on-click-modal="false"
      >
        <el-card v-if="selected" shadow="never" v-loading="detailLoading">
          <template #header
            ><div class="heading">
              <div>
                <h2>{{ selected.name }}</h2>
                <p>
                  {{ selected.event_date }} ·
                  {{
                    selected.class_name || selected.center_name || "未配置组织"
                  }}
                </p>
              </div>
              <div class="actions">
                <el-switch v-model="autoRefresh" active-text="自动刷新" /><span
                  class="muted"
                  >{{ refreshedAt ? `更新于 ${refreshedAt}` : "" }}</span
                ><el-button @click="refreshDetail()">刷新</el-button>
              </div>
            </div></template
          >
          <el-alert
            v-if="currentEvent?.lifecycle_status === 'DRAFT'"
            title="活动当前为草稿。核对活动与场次信息后，点击“确认活动”，再生成签到码。"
            type="warning"
            :closable="false"
            show-icon
            class="notice"
          />
          <el-alert
            v-if="error"
            :title="error"
            type="error"
            show-icon
            :closable="false"
            class="notice"
          />
          <div class="session-picker">
            <el-button
              v-for="session in sessions"
              :key="session.event_id"
              :type="activeEventId === session.event_id ? 'primary' : 'default'"
              @click="switchSession(session.event_id)"
              >{{
                session.session_name ||
                sessionLabels[session.session_code] ||
                "当前场次"
              }}
              ·
              {{
                checkinLabels[session.checkin_status] || session.checkin_status
              }}</el-button
            >
          </div>
          <p class="muted">
            每个场次独立签到。签到时间：{{
              timeLabel(currentEvent?.checkin_start_at)
            }}
            至 {{ timeLabel(currentEvent?.checkin_end_at) }}
          </p>
          <div class="actions notice">
            <el-button
              v-if="permission('update')"
              :disabled="busy"
              @click="openEditor(currentEvent)"
              >编辑当前场次</el-button
            >
            <el-button
              v-if="
                permission('update') &&
                currentEvent?.lifecycle_status === 'DRAFT'
              "
              type="primary"
              :disabled="busy"
              @click="changeLifecycle('CONFIRMED')"
              >确认活动</el-button
            >
            <el-button
              v-if="
                permission('update') &&
                currentEvent?.lifecycle_status === 'CONFIRMED'
              "
              :disabled="busy"
              @click="changeLifecycle('DRAFT')"
              >退回草稿</el-button
            >
            <el-button
              v-if="
                permission('manage') &&
                currentEvent?.lifecycle_status !== 'CANCELLED'
              "
              :disabled="busy"
              @click="toggleCheckin()"
              >{{
                currentEvent?.manual_status === "closed"
                  ? "开放签到"
                  : "关闭签到"
              }}</el-button
            >
            <el-button
              v-if="
                permission('manage') &&
                currentEvent?.lifecycle_status !== 'CANCELLED'
              "
              type="danger"
              plain
              :disabled="busy"
              @click="changeLifecycle('CANCELLED')"
              >取消活动</el-button
            >
            <el-button
              v-if="permission('code')"
              :disabled="busy || currentEvent?.lifecycle_status === 'CANCELLED'"
              @click="openCode()"
              >生成当前场次小程序码</el-button
            >
            <el-button
              v-if="permission('export')"
              :disabled="busy"
              @click="exportExcel()"
              >导出 Excel</el-button
            >
          </div>
          <div v-if="stats" class="summary">
            <el-statistic
              title="本班应到 / 报名"
              :value="stats.home_class_total ?? stats.total ?? 0"
            />
            <el-statistic
              title="本班已到 / 已签到"
              :value="stats.home_class_checked ?? stats.checked ?? 0"
            />
            <el-statistic
              title="外班实际到场"
              :value="stats.cross_class_checked || 0"
            />
            <el-statistic
              title="来宾实际到场"
              :value="stats.guest_checked || 0"
            />
            <el-statistic
              title="现场真实人数"
              :value="stats.onsite_total ?? stats.checked ?? 0"
            />
            <el-statistic
              title="本班签到率"
              :value="stats.rate || 0"
              suffix="%"
            />
          </div>
          <el-alert
            title="报名联系人与实际参加人分别保留。外班参加保留原班级；预计迟到、请假属于跟进状态，最终到场以签到事实为准。"
            type="info"
            :closable="false"
            class="notice"
          />
          <div class="actions notice">
            <el-button
              v-if="permission('manage')"
              :disabled="busy || !canMaintainRegistration(currentEvent)"
              @click="openManual()"
              >新增临时报名</el-button
            >
            <el-button
              v-if="
                permission('import') &&
                !['class_meeting', 'group_meeting'].includes(
                  currentEvent?.activity_type
                )
              "
              :disabled="busy || !allowExcel"
              @click="openImport()"
              >Excel 追加报名名单</el-button
            >
            <el-button
              v-if="
                permission('import') &&
                currentEvent?.activity_type === 'class_meeting'
              "
              :disabled="busy || !allowRoster"
              @click="syncRoster()"
              >同步可信班级名单</el-button
            >
            <el-button
              v-if="currentEvent?.activity_type === 'class_meeting'"
              :disabled="busy"
              @click="showRosterReconciliation()"
              >班级名单对账</el-button
            >
          </div>
          <el-tabs v-model="personFilter">
            <el-tab-pane label="全部报名与实际到场" name="all" /><el-tab-pane
              label="已签到"
              name="checked"
            /><el-tab-pane
              :label="`未签到 (${stats?.pending || 0})`"
              name="pending"
            /><el-tab-pane
              :label="`预计迟到 (${stats?.late || 0})`"
              name="late"
            /><el-tab-pane
              :label="`请假 (${stats?.leave || 0})`"
              name="leave"
            /><el-tab-pane label="外班学长" name="cross" /><el-tab-pane
              label="团队实际到场"
              name="team"
            />
          </el-tabs>
          <el-input
            v-model="personKeyword"
            placeholder="搜索报名人、实际参加人、班级、公司"
            clearable
            class="people-search"
          />
          <el-table
            :data="visiblePeople"
            row-key="registration_id"
            max-height="620"
          >
            <el-table-column label="原报名人" min-width="110"
              ><template #default="{ row }">{{
                row.registered_name || row.name
              }}</template></el-table-column
            >
            <el-table-column label="实际参加人" min-width="110"
              ><template #default="{ row }">{{
                checked(row) ? row.actual_attendee_name || row.name : "—"
              }}</template></el-table-column
            >
            <el-table-column label="身份" width="95"
              ><template #default="{ row }"
                >{{ roleLabel(row) }}
                <div v-if="row.is_team" class="muted">团队名额</div></template
              ></el-table-column
            >
            <el-table-column prop="center" label="分中心" width="100" />
            <el-table-column
              prop="class_name"
              label="参加班级"
              width="115"
            /><el-table-column
              prop="home_class_name"
              label="原班级"
              width="115"
            /><el-table-column prop="group_name" label="小组" width="95" />
            <el-table-column label="状态" width="100"
              ><template #default="{ row }"
                ><el-tag
                  :type="
                    checked(row)
                      ? 'success'
                      : row.attendance_status === 'leave'
                        ? 'info'
                        : 'warning'
                  "
                  >{{ rowStatus(row) }}</el-tag
                ></template
              ></el-table-column
            >
            <el-table-column label="签到时间" width="155"
              ><template #default="{ row }">{{
                checked(row) ? timeLabel(row.checked_at) : "—"
              }}</template></el-table-column
            >
            <el-table-column
              v-if="permission('status') || permission('manage')"
              label="现场操作"
              min-width="260"
              fixed="right"
              ><template #default="{ row }"
                ><template v-if="!checked(row)">
                  <el-button
                    v-if="permission('status')"
                    size="small"
                    :disabled="
                      busy ||
                      !canMaintainRegistration(currentEvent) ||
                      currentEvent?.session_code === 'KONPA'
                    "
                    @click="updatePerson(row, 'late')"
                    >预计迟到</el-button
                  ><el-button
                    v-if="permission('status')"
                    size="small"
                    :disabled="busy || !canMaintainRegistration(currentEvent)"
                    @click="updatePerson(row, 'leave')"
                    >请假</el-button
                  ><el-button
                    v-if="
                      permission('status') &&
                      ['late', 'leave'].includes(row.attendance_status)
                    "
                    size="small"
                    :disabled="busy || !canMaintainRegistration(currentEvent)"
                    @click="updatePerson(row, 'pending')"
                    >恢复待签到</el-button
                  >
                  <el-button
                    v-if="permission('manage')"
                    size="small"
                    type="success"
                    :disabled="
                      busy || !canConfirmManually(currentEvent) || row.is_team
                    "
                    @click="confirmPerson(row)"
                    >人工签到</el-button
                  ><el-button
                    v-if="permission('manage')"
                    size="small"
                    type="danger"
                    text
                    :disabled="busy || !canMaintainRegistration(currentEvent)"
                    @click="removePerson(row)"
                    >删除报名</el-button
                  > </template
                ><span v-else class="muted">已签到事实保留</span></template
              ></el-table-column
            >
          </el-table>
          <el-collapse class="notice"
            ><el-collapse-item title="分组签到进度" name="groups"
              ><el-table :data="groupRows"
                ><el-table-column
                  prop="name"
                  :label="stats?.group_type || '分组'" /><el-table-column
                  prop="total"
                  label="报名人数" /><el-table-column
                  prop="checked"
                  label="实际签到人数" /></el-table></el-collapse-item
          ></el-collapse>
        </el-card>
      </el-drawer>
    </template>

    <el-dialog
      v-model="editorVisible"
      :title="editingId ? '编辑当前签到场次' : '创建活动'"
      width="min(920px, 96vw)"
      top="5vh"
      body-class="attendance-activity-editor-body"
      :close-on-click-modal="false"
    >
      <el-form label-position="top" class="editor-grid">
        <el-form-item label="活动名称（必填）"
          ><el-input v-model="editor.event_name" maxlength="150"
        /></el-form-item>
        <el-form-item label="活动日期（北京时间）"
          ><el-date-picker
            v-model="editor.event_date"
            value-format="YYYY-MM-DD"
            @change="setDateDefaults"
        /></el-form-item>
        <el-form-item label="活动类型"
          ><el-select
            v-model="editor.activity_type"
            :disabled="
              Boolean(editingId) && editor.activity_type === 'class_meeting'
            "
            ><el-option
              v-for="[value, label] in editorActivityTypes"
              :key="value"
              :value="value"
              :label="label" /></el-select
        ></el-form-item>
        <el-form-item
          :label="
            editor.activity_type === 'group_meeting'
              ? '所属小组（可信组织 ID）'
              : '所属班级（可信组织 ID，非班级活动可选）'
          "
          ><el-select v-model="rosterOptionId" filterable clearable
            ><el-option
              v-for="item in rosterOptions"
              :key="item.id"
              :value="item.id"
              :label="
                item.path ||
                [item.center, item.class_name, item.name]
                  .filter(Boolean)
                  .join(' / ')
              " /></el-select
        ></el-form-item>
        <el-form-item v-if="!rosterOptionId" label="所属组织 / 分中心（必填）"
          ><el-select v-model="regionId" filterable clearable
            ><el-option
              v-for="item in orgOptions"
              :key="item.id"
              :value="item.id"
              :label="item.path || item.name" /></el-select
        ></el-form-item>
      </el-form>
      <template v-if="!editingId && editor.activity_type === 'class_meeting'">
        <el-alert
          title="上午、下午、晚上空巴生成三个独立签到场次。必须从当前可信组织名单创建；不通过姓名或班级名称猜测身份。"
          type="info"
          :closable="false"
          class="notice"
        />
        <div class="actions notice">
          <el-button
            v-if="permission('import')"
            :disabled="busy || !rosterOptionId"
            @click="readRoster"
            >读取班级可信名单</el-button
          ><span>已读取 {{ rosterMembers.length }} 人</span>
        </div>
        <div
          v-for="session in sessionPrefixes"
          :key="session.code"
          class="session-editor"
        >
          <h3>{{ session.label }}</h3>
          <el-form inline label-position="top"
            ><el-form-item
              v-for="[key, label] in [
                ['checkin_start', '签到开放'],
                ['scheduled_start', '正式开始'],
                ['checkin_end', '签到截止'],
                ['scheduled_end', '正式结束']
              ]"
              :key="key"
              :label="label"
              ><el-date-picker
                v-model="sessionTimes[`${session.prefix}_${key}`]"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm"
                style="width: 195px" /></el-form-item
          ></el-form>
        </div>
      </template>
      <el-form v-else label-position="top" class="editor-grid"
        ><el-form-item label="签到开放"
          ><el-date-picker
            v-model="editor.checkin_start_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm" /></el-form-item
        ><el-form-item label="签到截止"
          ><el-date-picker
            v-model="editor.checkin_end_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm" /></el-form-item
        ><el-form-item label="正式开始（选填）"
          ><el-date-picker
            v-model="editor.scheduled_start_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm" /></el-form-item
        ><el-form-item label="正式结束（选填）"
          ><el-date-picker
            v-model="editor.scheduled_end_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm" /></el-form-item
      ></el-form>
      <section
        v-if="!editingId && editor.activity_type !== 'class_meeting'"
        class="notice"
      >
        <h3>上传报名表格（必填）</h3>
        <p class="muted">
          选择 Excel
          报名表，核对名单后保存活动。表格需包含姓名，联系方式、公司、班级等可选；重复报名名额会分别保留。
        </p>
        <input
          type="file"
          accept=".xls,.xlsx"
          aria-label="上传活动报名表格"
          :disabled="busy || !permission('import')"
          @change="readCreationFile"
        />
        <p v-if="creationFilename">
          {{ creationFilename }} · {{ creationAttendees.length }} 个报名名额
        </p>
        <el-table
          v-if="creationAttendees.length"
          :data="creationAttendees.slice(0, 20)"
          max-height="220"
        >
          <el-table-column prop="name" label="姓名" /><el-table-column
            prop="company"
            label="公司"
          /><el-table-column prop="class_name" label="班级" />
        </el-table>
        <el-alert
          v-if="creationQuality"
          :title="`报名名额 ${creationAttendees.length}；联系方式缺失 ${creationQuality.missing_phone_count}，格式异常 ${creationQuality.invalid_phone_count}，不阻断导入。`"
          type="info"
          :closable="false"
          class="notice"
        />
        <el-button
          :loading="busy"
          :disabled="!creationAttendees.length || !permission('import')"
          @click="previewCreation"
          >核对报名表预览</el-button
        >
        <el-alert
          v-if="creationPreview"
          title="报名表预览通过，可以保存活动；修改活动信息或更换表格后请重新核对。"
          type="success"
          :closable="false"
          class="notice"
        />
        <el-alert
          v-if="!permission('import')"
          title="当前账号没有导入报名名单的权限，请联系管理员。"
          type="warning"
          :closable="false"
        />
      </section>
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
        class="notice"
      />
      <el-alert
        v-if="editingId"
        title="已开始或已有签到的活动不能变更组织与时间。服务端会校验当前活动状态，保存失败时请按提示处理。"
        type="info"
        :closable="false"
      />
      <template #footer
        ><el-button @click="editorVisible = false">取消</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="
            !editingId &&
            (editor.activity_type === 'class_meeting'
              ? !rosterMembers.length
              : !creationPreview || !permission('import'))
          "
          @click="saveEditor"
          >{{
            !editingId && editor.activity_type === "class_meeting"
              ? "创建上午、下午、空巴三场"
              : "保存活动"
          }}</el-button
        ></template
      >
    </el-dialog>

    <el-dialog
      v-model="importVisible"
      title="Excel 追加报名名单"
      width="min(720px, 96vw)"
      :close-on-click-modal="false"
    >
      <el-alert
        title="追加当前场次的报名名额，保留已有报名和签到事实。班级、小组正式成员以可信组织 ID 为准；Excel 适用于课程、外部活动和临时报名。"
        type="info"
        :closable="false"
        class="notice"
      />
      <input
        type="file"
        accept=".xls,.xlsx"
        aria-label="选择 Excel 报名表"
        :disabled="busy"
        @change="readFile"
      />
      <p v-if="importFilename">
        {{ importFilename }} · 表头第 {{ importHeaderRow }} 行 ·
        {{ importAttendees.length }} 个报名名额
      </p>
      <el-alert
        v-if="importQuality"
        :title="`手机号正常 ${importQuality.valid_phone_count}，缺失 ${importQuality.missing_phone_count}，格式异常 ${importQuality.invalid_phone_count}；重复名额 ${importQuality.repeated_slots}，保留为独立报名位。手机号问题不阻断导入。`"
        type="info"
        :closable="false"
        class="notice"
      />
      <el-table
        v-if="importAttendees.length"
        :data="importAttendees.slice(0, 20)"
        max-height="290"
        ><el-table-column prop="name" label="姓名" /><el-table-column
          prop="company"
          label="公司" /><el-table-column
          prop="center"
          label="分中心" /><el-table-column prop="class_name" label="班级"
      /></el-table>
      <el-alert
        v-if="preview"
        :title="`预览通过：将追加 ${preview.added ?? preview.added_count ?? importAttendees.length} 个报名名额，追加后共 ${preview.new_total ?? importAttendees.length} 个名额。已有签到事实保留。`"
        type="success"
        :closable="false"
        class="notice"
      />
      <template #footer
        ><el-button @click="importVisible = false">取消</el-button
        ><el-button
          :loading="busy"
          :disabled="!importAttendees.length"
          @click="previewImport"
          >核对导入预览</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="!preview"
          @click="applyImport"
          >确认追加名单</el-button
        ></template
      >
    </el-dialog>

    <el-dialog
      v-model="rosterReconciliationVisible"
      title="班级名单对账"
      width="min(720px, 96vw)"
    >
      <template v-if="rosterReconciliation">
        <div class="summary">
          <el-statistic
            title="当前有效成员"
            :value="rosterReconciliation.current_effective_count || 0"
          /><el-statistic
            title="活动名单快照"
            :value="rosterReconciliation.activity_roster_count || 0"
          /><el-statistic
            title="初始名单快照"
            :value="rosterReconciliation.initial_snapshot_count || 0"
          />
        </div>
        <h3>当前组织新增、尚未进入本活动名单</h3>
        <el-table :data="rosterReconciliation.additions || []"
          ><el-table-column prop="name" label="姓名" /><el-table-column
            prop="member_code"
            label="学员编号"
        /></el-table>
        <h3 class="notice">活动快照中保留、当前已不在有效班级名册</h3>
        <el-table :data="rosterReconciliation.no_longer_current || []"
          ><el-table-column prop="name" label="姓名" /><el-table-column
            prop="member_code"
            label="学员编号"
        /></el-table>
        <el-alert
          :title="
            rosterReconciliation.can_sync
              ? `可增补至：${(rosterReconciliation.sync_target_sessions || []).map(item => item.session_name || item.name).join('、')}。已有记录保留。`
              : '当前场次已开始或结束，保留名单快照。'
          "
          type="info"
          :closable="false"
          class="notice"
        />
      </template>
      <template #footer
        ><el-button @click="rosterReconciliationVisible = false">关闭</el-button
        ><el-button
          v-if="permission('import')"
          type="primary"
          :disabled="
            busy ||
            !rosterReconciliation?.can_sync ||
            rosterReconciliation?.roster_quality?.passed === false
          "
          @click="
            syncRoster();
            rosterReconciliationVisible = false;
          "
          >仅增补未开始场次</el-button
        ></template
      >
    </el-dialog>

    <el-dialog
      v-model="manualVisible"
      title="新增临时报名"
      width="min(620px, 96vw)"
      :close-on-click-modal="false"
      ><el-form label-position="top" class="editor-grid"
        ><el-form-item label="姓名（必填）"
          ><el-input v-model="manual.name" /></el-form-item
        ><el-form-item label="联系方式（选填）"
          ><el-input v-model="manual.phone" autocomplete="off" /></el-form-item
        ><el-form-item label="公司"
          ><el-input v-model="manual.company" /></el-form-item
        ><el-form-item label="分中心（展示信息）"
          ><el-input v-model="manual.center" /></el-form-item
        ><el-form-item label="原班级（展示信息）"
          ><el-input v-model="manual.class_name" /></el-form-item
        ><el-form-item label="小组（展示信息）"
          ><el-input v-model="manual.group_name" /></el-form-item></el-form
      ><el-alert
        title="临时信息不修改学员组织关系。正式学员优先使用可信名单；特殊来宾保留兼容签到能力。"
        type="info"
        :closable="false"
      /><template #footer
        ><el-button @click="manualVisible = false">取消</el-button
        ><el-button type="primary" :loading="busy" @click="saveManual"
          >新增报名</el-button
        ></template
      ></el-dialog
    >

    <el-dialog
      v-model="codeVisible"
      title="当前场次专属小程序码"
      width="min(520px, 96vw)"
      :close-on-click-modal="false"
      ><el-alert
        v-if="codeError"
        :title="codeError"
        type="error"
        show-icon
        :closable="false"
        class="notice"
      />
      <h3>{{ codeEvent?.name }}</h3>
      <p>
        {{ codeEvent?.event_date }} ·
        {{
          codeEvent?.session_name ||
          sessionLabels[codeEvent?.session_code] ||
          "单场"
        }}
      </p>
      <el-radio-group v-model="codeVersion"
        ><el-radio-button value="develop">开发版验收</el-radio-button
        ><el-radio-button value="trial">体验版验收</el-radio-button
        ><el-radio-button value="release"
          >正式版</el-radio-button
        ></el-radio-group
      ><el-alert
        :title="
          codeVersion === 'develop'
            ? '开发版码仅供当前小程序开发成员验收；每个场次请分别生成。'
            : codeVersion === 'trial'
              ? '体验版码仅供已配置的体验成员验收；每个场次请分别生成。'
              : '正式版需要签到页面已审核发布；生成后仅下载预览，由授权人员安排现场投放。'
        "
        type="info"
        :closable="false"
        class="notice"
      />
      <div v-loading="busy" class="code-preview">
        <img
          v-if="codeImage"
          :src="codeImage"
          :alt="`${codeEvent?.name} ${codeEvent?.session_name || ''} 签到小程序码`"
        /><el-empty v-else description="点击生成当前版本签到码" />
      </div>
      <template #footer
        ><el-button :disabled="busy" @click="createCode">{{
          code ? "重新生成" : "生成签到码"
        }}</el-button
        ><el-button
          type="primary"
          :disabled="!codeImage || busy"
          @click="downloadCode"
          >下载当前场次小程序码</el-button
        ></template
      ></el-dialog
    >
  </div>
</template>

<style scoped>
.checkin-management {
  padding: 18px;
}
.heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 16px;
}
h1 {
  font-size: 24px;
  font-weight: 650;
}
h2 {
  font-size: 20px;
  font-weight: 600;
}
h3 {
  font-weight: 600;
  margin-bottom: 10px;
}
.heading p,
.muted {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.actions,
.session-picker {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.actions .el-button + .el-button,
.session-picker .el-button + .el-button {
  margin-left: 0;
}
.notice {
  margin-top: 16px;
  margin-bottom: 16px;
}
.summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(100px, 1fr));
  gap: 14px;
  padding: 20px 0;
}
.pagination {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  align-items: center;
  margin-top: 16px;
}
.editor-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 20px;
}
:global(.attendance-activity-editor-body) {
  max-height: calc(85vh - 140px);
  overflow-y: auto;
}
.editor-grid :deep(.el-select),
.editor-grid :deep(.el-date-editor) {
  width: 100%;
}
.session-editor {
  padding: 15px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}
.people-search {
  max-width: 480px;
  margin-bottom: 12px;
}
.code-preview {
  min-height: 280px;
  display: flex;
  justify-content: center;
  align-items: center;
  padding-top: 16px;
}
.code-preview img {
  width: 280px;
  height: 280px;
  object-fit: contain;
}
@media (max-width: 900px) {
  .summary {
    grid-template-columns: repeat(3, 1fr);
  }
  .heading {
    flex-direction: column;
    align-items: flex-start;
  }
}
@media (max-width: 600px) {
  .editor-grid {
    grid-template-columns: 1fr;
  }
  .checkin-management {
    padding: 10px;
  }
  .summary {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
