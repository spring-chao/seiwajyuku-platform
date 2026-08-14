<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  ElMessage,
  ElMessageBox,
  type FormInstance,
  type FormRules,
  type UploadFile,
  type UploadUserFile
} from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import {
  createMember,
  getMemberEditProfile,
  getMembers,
  getOrgUnits,
  applyDirectClassWorkbook,
  applyFullClassRosterOrganization,
  applyFullClassRosterRelations,
  getMemberChangeHistory,
  getMemberTimeline,
  submitMemberServiceSignalFeedback,
  updateMember,
  previewDirectClassWorkbook,
  previewFullClassRosterWorkbook,
  type DirectClassPreflight,
  type FullClassRosterPreflight,
  type Member,
  type MemberChangeHistory,
  type MemberServiceSignal,
  type MemberServiceSignalFeedbackStatus,
  type MemberTimeline,
  type OrgUnit
} from "@/api/seiwajyuku";

defineOptions({ name: "MemberManagement" });

const loading = ref(false);
const saving = ref(false);
const dialogVisible = ref(false);
const historyVisible = ref(false);
const historyLoading = ref(false);
const historyMember = ref<Member>();
const historyRows = ref<MemberChangeHistory[]>([]);
const timelineVisible = ref(false);
const timelineLoading = ref(false);
const timeline = ref<MemberTimeline>();
const serviceSignalFeedbackLoading = ref("");
const editProfileLoading = ref(false);
const editPhoneReady = ref(false);
const editClassOrgName = ref("");
const editGroupOrgName = ref("");
const originalClassOrgUnitId = ref("");
const originalGroupOrgUnitId = ref("");
const financialFieldsEditable = ref(false);
const editingMemberId = ref<number>();
const preflightVisible = ref(false);
const preflightLoading = ref(false);
const preflightFiles = ref<UploadUserFile[]>([]);
const preflightResult = ref<DirectClassPreflight>();
const fullPreflightVisible = ref(false);
const fullPreflightLoading = ref(false);
const fullOrgImportLoading = ref(false);
const fullRelationImportLoading = ref(false);
const fullPreflightFiles = ref<UploadUserFile[]>([]);
const fullPreflightResult = ref<FullClassRosterPreflight>();
const selectedOrg = ref("");
const keyword = ref("");
const rows = ref<Member[]>([]);
const fullOrgConfirmationText = "确认创建20个普通班和112个普通班小组";
const canApplyFullOrgImport = computed(() => {
  const result = fullPreflightResult.value;
  if (!result) return false;
  const classActions = Object.fromEntries(
    result.organization.class_action_summary.map(item => [
      item.action,
      item.count
    ])
  );
  const groupActions = Object.fromEntries(
    result.organization.group_action_summary.map(item => [
      item.action,
      item.count
    ])
  );
  const matching = Object.fromEntries(
    result.matching.summary.map(item => [item.status, item.count])
  );
  const issues = Object.fromEntries(
    result.issues.map(item => [item.code, item.count])
  );
  return (
    result.source.active_member_count === 834 &&
    result.source.ordinary_class_count === 20 &&
    result.source.ordinary_group_pair_count === 112 &&
    classActions.CREATE_OR_RESOLVE === 20 &&
    classActions.REUSE === 4 &&
    groupActions.REVIEW === 112 &&
    groupActions.REUSE === 11 &&
    matching.UNIQUE_ACTIVE_MATCH === 722 &&
    matching.NO_PRODUCTION_MATCH === 84 &&
    matching.MANUAL_REVIEW === 28 &&
    issues.DUPLICATE_SOURCE_PHONE === 8 &&
    issues.INVALID_PHONE === 9 &&
    issues.MISSING_PHONE === 11 &&
    issues.MISSING_CLASS === 18
  );
});
const canApplyFullRelations = computed(() => {
  const result = fullPreflightResult.value;
  if (!result) return false;
  const classes = Object.fromEntries(result.organization.class_action_summary.map(item => [item.action, item.count]));
  const groups = Object.fromEntries(result.organization.group_action_summary.map(item => [item.action, item.count]));
  const matching = Object.fromEntries(result.matching.summary.map(item => [item.status, item.count]));
  return classes.REUSE === 24 && groups.REUSE === 123 && matching.UNIQUE_ACTIVE_MATCH === 722 && matching.NO_PRODUCTION_MATCH === 84 && matching.MANUAL_REVIEW === 28;
});
const orgs = ref<OrgUnit[]>([]);
const formRef = ref<FormInstance>();
const canManage = computed(() =>
  useUserStoreHook().permissions.includes("members:manage")
);
const canViewHistory = computed(() =>
  useUserStoreHook().permissions.includes("members:detail_view")
);
const centerOrgs = computed(() =>
  orgs.value.filter(item => item.unit_type === "REGIONAL_CENTER")
);
const classOrgs = computed(() => {
  const available = orgs.value.filter(
    item =>
      ["CLASS", "SPECIAL_COHORT"].includes(item.unit_type) &&
      (item.parent_id === form.org_unit_id ||
        (item.parent_id === "org-suzhou" &&
          ["先锋班", "神仙班", "黄埔一班", "黄埔二班"].includes(item.name))) &&
      !item.duplicate_name
  );
  return available;
});
const classOptionLabel = (org: { name: string; parent_id?: string | null }) => {
  if (
    org.parent_id === "org-suzhou" &&
    ["先锋班", "神仙班", "黄埔一班", "黄埔二班"].includes(org.name)
  ) return `${org.name}（苏州塾直属）`;
  const owner = orgs.value.find(item => item.id === org.parent_id);
  return `${org.name}（${owner?.name || "运营归属待核"}）`;
};
const classOptions = computed(() => {
  const options = classOrgs.value.map(org => ({
    ...org,
    option_label: classOptionLabel(org)
  }));
  if (
    form.class_org_unit_id &&
    !options.some(item => item.id === form.class_org_unit_id)
  ) {
    const current = orgs.value.find(
      item => item.id === form.class_org_unit_id
    );
    const name = current?.name || editClassOrgName.value || "原班级名称缺失";
    options.push({
      id: form.class_org_unit_id,
      unit_code: current?.unit_code || "HISTORICAL_CLASS",
      name,
      unit_type: current?.unit_type || "CLASS",
      parent_id: current?.parent_id,
      parent_name: current?.parent_name,
      duplicate_name: current?.duplicate_name,
      option_label: `${name}（当前归属，需复核）`
    });
  }
  return options;
});
const groupOrgs = computed(() =>
  orgs.value.filter(
    item => item.unit_type === "GROUP" && item.parent_id === form.class_org_unit_id
  )
);
const groupOptions = computed(() => {
  const options = [...groupOrgs.value];
  if (
    form.group_org_unit_id &&
    !options.some(item => item.id === form.group_org_unit_id) &&
    editGroupOrgName.value
  ) {
    options.push({
      id: form.group_org_unit_id,
      unit_code: "HISTORICAL_GROUP",
      name: `${editGroupOrgName.value}（历史归属，需复核）`,
      unit_type: "GROUP",
      parent_id: form.class_org_unit_id
    });
  }
  return options;
});
const filteredRows = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  if (!term) return rows.value.filter(item => item.status === "ACTIVE");
  return rows.value.filter(item =>
    [item.name, item.member_code, item.phone_last4]
      .filter(Boolean)
      .some(value => String(value).toLowerCase().includes(term))
  );
});

const form = reactive({
  name: "",
  org_unit_id: "",
  phone: "",
  company_name: "",
  gender: "",
  district: "",
  company_address: "",
  class_name: "",
  group_name: "",
  class_org_unit_id: "",
  group_org_unit_id: "",
  birthday: "",
  join_date: "",
  study_start_date: "",
  membership_years: undefined as number | undefined,
  membership_years_inferred: true,
  renewal_month: "",
  status: "ACTIVE",
  position: "",
  referrer: "",
  referrer_center: "",
  industry_category: "",
  industry: "",
  company_products: "",
  annual_sales: "",
  employee_count: undefined as number | undefined,
  profit_margin: "",
  notes: ""
});
const memberStatusLabel = (status: string) =>
  ({ ACTIVE: "在册", INACTIVE: "流失", SUSPENDED: "暂停" })[status] ?? status;
const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: "请输入姓名", trigger: "blur" }],
  org_unit_id: [{ required: true, message: "请选择分中心", trigger: "change" }],
  phone: [
    ...(editingMemberId.value
      ? []
      : [{ required: true, message: "请输入手机号", trigger: "blur" }]),
    {
      pattern: /^$|^1\d{10}$/,
      message: "请输入 11 位手机号",
      trigger: "blur"
    }
  ]
}));

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || "操作失败";
}

async function load() {
  loading.value = true;
  try {
    const [members, organizations] = await Promise.all([
      getMembers(selectedOrg.value || undefined),
      getOrgUnits()
    ]);
    rows.value = members.data;
    orgs.value = organizations.data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  editingMemberId.value = undefined;
  editPhoneReady.value = true;
  financialFieldsEditable.value = useUserStoreHook().permissions.includes(
    "members:enterprise_view"
  );
  editClassOrgName.value = "";
  editGroupOrgName.value = "";
  originalClassOrgUnitId.value = "";
  originalGroupOrgUnitId.value = "";
  Object.assign(form, {
    name: "",
    org_unit_id: selectedOrg.value,
    phone: "",
    company_name: "",
    gender: "",
    district: "",
    company_address: "",
    class_name: "",
    group_name: "",
    class_org_unit_id: "",
    group_org_unit_id: "",
    birthday: "",
    join_date: "",
    study_start_date: "",
    membership_years: undefined,
    membership_years_inferred: true,
    renewal_month: "",
    status: "ACTIVE",
    position: "",
    referrer: "",
    referrer_center: "",
    industry_category: "",
    industry: "",
    company_products: "",
    annual_sales: "",
    employee_count: undefined,
    profit_margin: "",
    notes: ""
  });
  dialogVisible.value = true;
}

async function openEdit(row: any) {
  editingMemberId.value = row.id;
  editPhoneReady.value = false;
  editClassOrgName.value = "";
  editGroupOrgName.value = "";
  Object.assign(form, {
    name: row.name,
    org_unit_id: row.org_unit_id,
    phone: "",
    company_name: "",
    gender: "",
    district: "",
    company_address: "",
    class_name: "",
    group_name: "",
    class_org_unit_id: "",
    group_org_unit_id: "",
    birthday: "",
    join_date: "",
    study_start_date: "",
    membership_years: undefined,
    membership_years_inferred: true,
    renewal_month: "",
    status: row.status,
    position: "",
    referrer: "",
    referrer_center: "",
    industry_category: "",
    industry: "",
    company_products: "",
    annual_sales: "",
    employee_count: undefined,
    profit_margin: "",
    notes: ""
  });
  originalClassOrgUnitId.value = "";
  originalGroupOrgUnitId.value = "";
  dialogVisible.value = true;
  editProfileLoading.value = true;
  try {
    const profile = await getMemberEditProfile(row.id);
    const data = profile.data;
    financialFieldsEditable.value = data.financial_fields_editable;
    Object.assign(form, {
      name: data.name,
      org_unit_id: data.org_unit_id,
      phone: data.phone || "",
      company_name: data.company_name || "",
      gender: data.gender || "",
      district: data.district || "",
      company_address: data.company_address || "",
      class_org_unit_id: data.class_org_unit_id || "",
      group_org_unit_id: data.group_org_unit_id || "",
      birthday: data.birthday || "",
      join_date: data.join_date || "",
      study_start_date: data.study_start_date || "",
      membership_years: data.membership_years ?? undefined,
      membership_years_inferred: data.membership_years_inferred,
      renewal_month: data.renewal_month || "",
      status: data.status,
      position: data.position || "",
      referrer: data.referrer || "",
      referrer_center: data.referrer_center || "",
      industry_category: data.industry_category || "",
      industry: data.industry || "",
      company_products: data.company_products || "",
      annual_sales: normalizeAnnualSales(data.annual_sales),
      employee_count: data.employee_count ?? undefined,
      profit_margin: data.profit_margin || "",
      notes: data.notes || ""
    });
    editClassOrgName.value = data.class_org_name || "";
    editGroupOrgName.value = data.group_org_name || "";
    originalClassOrgUnitId.value = data.class_org_unit_id || "";
    originalGroupOrgUnitId.value = data.group_org_unit_id || "";
    editPhoneReady.value = true;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    editProfileLoading.value = false;
  }
}

function inferMembershipYears(joinDate: string) {
  if (!joinDate) return undefined;
  const joined = new Date(`${joinDate}T00:00:00`);
  if (Number.isNaN(joined.getTime())) return undefined;
  const elapsed = Math.max(0, Date.now() - joined.getTime());
  return Math.round((elapsed / (365.2425 * 24 * 60 * 60 * 1000)) * 10) / 10;
}

function normalizeAnnualSales(value?: string | null) {
  return (value || "").replace(/\s*(万元|万)\s*$/, "").trim();
}

function onJoinDateChange(value: string) {
  if (!form.membership_years_inferred) return;
  form.membership_years = inferMembershipYears(value);
}

function onClassOrgChange() {
  form.group_org_unit_id = "";
  editGroupOrgName.value = "";
}

function enableMembershipYearsOverride() {
  form.membership_years_inferred = false;
}

function restoreInferredMembershipYears() {
  form.membership_years_inferred = true;
  form.membership_years = inferMembershipYears(form.join_date);
}

function parseHistoryValue(value: string) {
  try {
    return JSON.parse(value || "{}") as Record<string, unknown>;
  } catch {
    return {};
  }
}

function historyLabel(key: string) {
  return ({
    name: "姓名",
    org_unit_id: "所属分中心",
    development_org_unit_id: "发展归属",
    status: "状态",
    phone_masked: "手机号（脱敏）",
    company_name: "公司名称",
    notes: "备注",
    class_name: "班级",
    group_name: "小组"
  } as Record<string, string>)[key] ?? key;
}

function historyValue(key: string, value: unknown) {
  if (value === null || value === undefined || value === "") return "无";
  if (key.endsWith("org_unit_id")) {
    return orgs.value.find(item => item.id === String(value))?.name ?? String(value);
  }
  if (key === "status") return memberStatusLabel(String(value));
  return String(value);
}

function historySummary(item: any) {
  const before = parseHistoryValue(item.before_json);
  const after = parseHistoryValue(item.after_json);
  const keys = [
    "name",
    "org_unit_id",
    "development_org_unit_id",
    "status",
    "phone_masked",
    "company_name",
    "notes",
    "class_name",
    "group_name"
  ];
  const changes = keys
    .filter(key => JSON.stringify(before[key] ?? null) !== JSON.stringify(after[key] ?? null))
    .map(key => `${historyLabel(key)}：${historyValue(key, before[key])} → ${historyValue(key, after[key])}`);
  return changes.length ? changes.join("；") : "已记录变更（字段无差异）";
}

function historyTypeLabel(type: string) {
  return ({ PROFILE_UPDATE: "档案更新", MERGE: "档案合并" } as Record<string, string>)[type] ?? type;
}

function timelineTypeLabel(type: string) {
  return ({
    PROFILE_CHANGE: "档案变更",
    ATTENDANCE: "签到记录",
    LEARNING_ACTIVITY: "学习活动",
    FOLLOWUP_TASK: "关怀事项",
    FOLLOWUP_RECORD: "关怀记录",
    ENTERPRISE_VISIT: "企业走访",
    RENEWAL_CYCLE: "续费周期",
    RENEWAL_FOLLOWUP: "续费跟进"
  } as Record<string, string>)[type] ?? type;
}

function timelineStatusLabel(status?: string) {
  if (!status) return "—";
  return ({
    PRESENT: "已签到",
    MANUAL_PRESENT: "人工确认签到",
    ABSENT: "未签到",
    COMPLETED: "已完成",
    RECORDED: "已记录",
    LEAVE: "请假",
    OPEN: "开放",
    IN_PROGRESS: "进行中",
    CLOSED: "已关闭",
    PENDING_FIRST_CONTACT: "待首次联系",
    RENEWED: "已续费",
    NOT_RENEWING: "不续费",
    EXITED: "已退出",
    PROFILE_UPDATE: "档案更新",
    已记录: "已记录"
  } as Record<string, string>)[status] ?? status;
}

function timelineSummaryLabel(type: string) {
  return ({
    PROFILE_CHANGE: "档案变更",
    ATTENDANCE: "签到记录",
    LEARNING_ACTIVITY: "学习活动",
    FOLLOWUP_TASK: "关怀事项",
    FOLLOWUP_RECORD: "关怀记录",
    ENTERPRISE_VISIT: "企业走访",
    RENEWAL_CYCLE: "续费周期",
    RENEWAL_FOLLOWUP: "续费跟进"
  } as Record<string, string>)[type] ?? type;
}

function timelineChannelLabel(channel?: string) {
  if (!channel) return "—";
  return ({
    GROUP_SESSION: "小组学习会",
    CLASS_SESSION: "班级学习会",
    COURSE: "课程",
    REPORT_MEETING: "报告会",
    STUDY_TOUR: "游学",
    READING_CHECKIN: "读书打卡",
    READING_SHARE: "读书分享"
  } as Record<string, string>)[channel] ?? channel;
}

function formatTimelineTime(value?: string) {
  if (!value) return "—";
  return value.replace("T", " ").replace("+00:00", "");
}

function serviceSignalFeedbackLabel(status?: MemberServiceSignalFeedbackStatus) {
  if (!status) return "";
  return ({
    CONFIRMED_VALID: "已确认有效",
    NOT_APPLICABLE: "已标记暂不适用",
    DATA_CORRECTED: "已反馈数据修正"
  } as Record<MemberServiceSignalFeedbackStatus, string>)[status];
}

async function submitServiceSignalFeedback(
  signal: MemberServiceSignal,
  status: MemberServiceSignalFeedbackStatus
) {
  if (!timeline.value) return;
  const label = serviceSignalFeedbackLabel(status);
  try {
    await ElMessageBox.confirm(
      `确认将“${signal.title}”反馈为“${label}”？系统会保存当前规则版本和脱敏证据快照。`,
      "提交服务提示反馈",
      { type: "warning", confirmButtonText: "确认提交", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  const loadingKey = `${signal.code}:${status}`;
  serviceSignalFeedbackLoading.value = loadingKey;
  try {
    await submitMemberServiceSignalFeedback(timeline.value.member.id, signal.code, {
      rule_version: signal.rule_version,
      status
    });
    timeline.value = (await getMemberTimeline(timeline.value.member.id)).data;
    ElMessage.success("服务提示反馈已保存并记录审计");
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    serviceSignalFeedbackLoading.value = "";
  }
}

async function openTimeline(row: any) {
  timeline.value = undefined;
  timelineVisible.value = true;
  timelineLoading.value = true;
  try {
    timeline.value = (await getMemberTimeline(row.id)).data;
  } catch (error) {
    timelineVisible.value = false;
    ElMessage.error(errorText(error));
  } finally {
    timelineLoading.value = false;
  }
}

async function openHistory(row: any) {
  historyMember.value = row;
  historyRows.value = [];
  historyVisible.value = true;
  historyLoading.value = true;
  try {
    historyRows.value = (await getMemberChangeHistory(row.id)).data;
  } catch (error) {
    historyVisible.value = false;
    ElMessage.error(errorText(error));
  } finally {
    historyLoading.value = false;
  }
}

async function submit() {
  if (editingMemberId.value && !editPhoneReady.value) {
    ElMessage.error("手机号尚未读取完成，请稍后重试")
    return;
  }
  if (!(await formRef.value?.validate())) return;
  saving.value = true;
  try {
    if (editingMemberId.value) {
      await updateMember(editingMemberId.value, {
        name: form.name.trim(),
        org_unit_id: form.org_unit_id,
        status: form.status,
        phone: form.phone.trim() || null,
        company_name: form.company_name.trim() || null,
        gender: form.gender || null,
        district: form.district.trim() || null,
        company_address: form.company_address.trim() || null,
        birthday: form.birthday || null,
        join_date: form.join_date || null,
        study_start_date: form.study_start_date || null,
        membership_years: form.membership_years_inferred
          ? null
          : (form.membership_years ?? null),
        renewal_month: form.renewal_month || null,
        position: form.position.trim() || null,
        referrer: form.referrer.trim() || null,
        referrer_center: form.referrer_center.trim() || null,
        industry_category: form.industry_category.trim() || null,
        industry: form.industry.trim() || null,
        company_products: form.company_products.trim() || null,
        employee_count: form.employee_count ?? null,
        notes: form.notes.trim() || null,
        ...(financialFieldsEditable.value
          ? {
              annual_sales: form.annual_sales.trim() || null,
              profit_margin: form.profit_margin.trim() || null
            }
          : {}),
        ...(form.class_org_unit_id !== originalClassOrgUnitId.value
          ? { class_org_unit_id: form.class_org_unit_id || null }
          : {}),
        ...(form.group_org_unit_id !== originalGroupOrgUnitId.value
          ? { group_org_unit_id: form.group_org_unit_id || null }
          : {})
      });
      ElMessage.success("学员档案已更新，变更已记录");
    } else {
      await createMember({
        name: form.name.trim(),
        org_unit_id: form.org_unit_id,
        phone: form.phone.trim(),
        company_name: form.company_name.trim() || undefined,
        gender: form.gender || undefined,
        district: form.district.trim() || undefined,
        company_address: form.company_address.trim() || undefined,
        class_org_unit_id: form.class_org_unit_id || undefined,
        group_org_unit_id: form.group_org_unit_id || undefined,
        birthday: form.birthday || undefined,
        join_date: form.join_date || undefined,
        study_start_date: form.study_start_date || undefined,
        membership_years: form.membership_years_inferred
          ? undefined
          : form.membership_years,
        renewal_month: form.renewal_month || undefined,
        status: form.status,
        position: form.position.trim() || undefined,
        referrer: form.referrer.trim() || undefined,
        referrer_center: form.referrer_center.trim() || undefined,
        industry_category: form.industry_category.trim() || undefined,
        industry: form.industry.trim() || undefined,
        company_products: form.company_products.trim() || undefined,
        annual_sales: form.annual_sales.trim() || undefined,
        employee_count: form.employee_count,
        profit_margin: form.profit_margin.trim() || undefined,
        notes: form.notes.trim() || undefined
      });
      ElMessage.success("学员已创建，手机号已加密保存");
    }
    dialogVisible.value = false;
    await load();
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

function selectPreflightFile(file: UploadFile) {
  preflightFiles.value = [file];
  preflightResult.value = undefined;
  return false;
}

function selectFullPreflightFile(file: UploadFile) {
  fullPreflightFiles.value = [file];
  fullPreflightResult.value = undefined;
  return false;
}

async function runFullClassPreflight() {
  const workbook = fullPreflightFiles.value[0]?.raw;
  if (!workbook) {
    ElMessage.warning("请先选择最新学员表 .xlsx 文件");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "文件只在服务器内存中用于受保护匹配，结果只返回班级、小组和匹配汇总；不会创建、修改或停用任何生产数据。",
      "确认进行全量班级只读预检",
      {
        confirmButtonText: "开始只读预检",
        cancelButtonText: "取消",
        type: "warning"
      }
    );
  } catch {
    return;
  }
  fullPreflightLoading.value = true;
  try {
    const result = await previewFullClassRosterWorkbook(workbook);
    fullPreflightResult.value = result.data;
    ElMessage.success("全量班级只读预检已完成，未写入生产数据");
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    fullPreflightLoading.value = false;
  }
}

async function applyFullOrgImport() {
  const workbook = fullPreflightFiles.value[0]?.raw;
  if (!workbook || !canApplyFullOrgImport.value) return;
  let confirmationText = "";
  try {
    const prompt = await ElMessageBox.prompt(
      `本阶段仅创建20个普通班和112个普通班小组，不修改任何学员或签到数据。请输入：${fullOrgConfirmationText}`,
      "第一阶段组织节点生产写入确认",
      {
        confirmButtonText: "执行第一阶段",
        cancelButtonText: "取消",
        type: "warning",
        inputValidator: value =>
          value === fullOrgConfirmationText || "确认文字不完整，已禁止写入"
      }
    );
    confirmationText = prompt.value;
  } catch {
    return;
  }
  fullOrgImportLoading.value = true;
  try {
    const result = await applyFullClassRosterOrganization(
      workbook,
      confirmationText
    );
    ElMessage.success(
      `第一阶段完成：创建班级 ${result.data.created_classes} 个、小组 ${result.data.created_groups} 个；学员变更 ${result.data.members_changed} 人`
    );
    const refreshed = await previewFullClassRosterWorkbook(workbook);
    fullPreflightResult.value = refreshed.data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    fullOrgImportLoading.value = false;
  }
}

async function applyFullRelationImport() {
  const workbook = fullPreflightFiles.value[0]?.raw;
  if (!workbook || !canApplyFullRelations.value) return;
  fullRelationImportLoading.value = true;
  try {
    const result = await applyFullClassRosterRelations(workbook);
    ElMessage.success(`第二阶段完成：唯一匹配学员 ${result.data.matched_members ?? 722} 人，新增组织关系 ${result.data.relations_added ?? 0} 条`);
    fullPreflightResult.value = (await previewFullClassRosterWorkbook(workbook)).data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    fullRelationImportLoading.value = false;
  }
}

async function applyDirectClassImport() {
  const workbook = preflightFiles.value[0]?.raw;
  if (!workbook || !preflightResult.value) return;
  try { await ElMessageBox.confirm("将按已确认工作簿写入直属四班：8 名新建、115 名更新、430 条组织关系和 4 条备注。指纹或实时预检不符将自动停止并回滚。", "执行直属四班生产导入", { confirmButtonText: "确认执行", cancelButtonText: "取消", type: "warning" }); } catch { return; }
  preflightLoading.value = true;
  try { const result = await applyDirectClassWorkbook(workbook); ElMessage.success(`导入完成：新建 ${result.data.created}，更新 ${result.data.updated}，关系 ${result.data.relations}`); await load(); }
  catch (error) { ElMessage.error(errorText(error)); }
  finally { preflightLoading.value = false; }
}

async function runDirectClassPreflight() {
  const workbook = preflightFiles.value[0]?.raw;
  if (!workbook) {
    ElMessage.warning("请先选择直属班级名单 .xlsx 文件");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "文件只在服务器内存中用于受保护匹配，结果只返回汇总数量；不会创建、修改或停用任何学员、组织或关系。",
      "确认进行直属四班只读预检",
      { confirmButtonText: "开始只读预检", cancelButtonText: "取消", type: "warning" }
    );
  } catch {
    return;
  }
  preflightLoading.value = true;
  try {
    const result = await previewDirectClassWorkbook(workbook);
    preflightResult.value = result.data;
    ElMessage.success("只读预检已完成，未写入生产数据");
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    preflightLoading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="member-page" v-loading="loading">
    <section class="page-head">
      <div>
        <p>关怀试点 · 主数据</p>
        <h1>学员管理</h1>
        <span>手机号加密保存；列表、普通查询和后续任务默认只显示脱敏号码。</span>
      </div>
      <div class="head-actions" v-if="canManage">
        <el-button size="large" @click="fullPreflightVisible = true">
          全量班级预检
        </el-button>
        <el-button size="large" @click="preflightVisible = true">
          直属四班预检
        </el-button>
        <el-button type="primary" size="large" @click="openCreate">
          新增学员
        </el-button>
      </div>
    </section>

    <el-alert
      title="先选择一个分中心开展小范围试点；正式批量导入前需提供已填写的学员基本信息表。"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <div class="toolbar">
        <el-select
          v-model="selectedOrg"
          clearable
          placeholder="全部分中心"
          @change="load"
        >
          <el-option
            v-for="org in centerOrgs"
            :key="org.id"
            :label="org.name"
            :value="org.id"
          />
        </el-select>
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索姓名、编号或手机后四位（含非在册）"
        />
        <span class="result-count">
          {{ keyword.trim() ? "搜索全部状态" : "默认显示在册" }} · 共 {{ filteredRows.length }} 人
        </span>
      </div>

      <el-table :data="filteredRows" stripe empty-text="暂无学员数据">
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column prop="org_name" label="所属分中心" min-width="140" />
        <el-table-column prop="class_name" label="班级" min-width="120">
          <template #default="{ row }">{{ row.class_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="group_name" label="组名" min-width="110">
          <template #default="{ row }">{{ row.group_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="phone_masked" label="手机号（脱敏）" min-width="150" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">
              {{ memberStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="canManage || canViewHistory" label="操作" width="270" fixed="right">
          <template #default="{ row }">
            <el-button v-if="canManage" link type="primary" @click="openEdit(row)">
              编辑
            </el-button>
            <el-button v-if="canViewHistory" link type="primary" @click="openTimeline(row)">
              档案时间线
            </el-button>
            <el-button v-if="canViewHistory" link type="primary" @click="openHistory(row)">
              变更历史
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="preflightVisible"
      title="直属四班生产前只读预检"
      width="860px"
      class="preflight-dialog"
    >
      <el-alert
        title="本操作不创建或修改任何生产数据"
        description="工作簿仅在服务器内存中解析；手机号仅用于受保护的匹配，界面只显示汇总数量。"
        type="success"
        :closable="false"
        show-icon
      />
      <el-upload
        class="preflight-upload"
        accept=".xlsx"
        :auto-upload="false"
        :limit="1"
        :file-list="preflightFiles"
        :on-change="selectPreflightFile"
      >
        <el-button>选择直属班级名单 .xlsx</el-button>
      </el-upload>
      <el-button
        type="primary"
        :loading="preflightLoading"
        @click="runDirectClassPreflight"
      >
        生成只读预检报告
      </el-button>

      <div v-if="preflightResult" class="preflight-result">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="在册直属学员">
            {{ preflightResult.source.active_direct_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="生产写入">
            已禁止
          </el-descriptions-item>
          <el-descriptions-item label="工作簿班级分布" :span="2">
            <el-tag
              v-for="item in preflightResult.source.by_class"
              :key="item.class_name"
              class="result-tag"
              type="success"
            >
              {{ item.class_name }} {{ item.count }} 人
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="生产现有直属班记录" :span="2">
            <el-tag
              v-for="item in preflightResult.production_existing_direct_class_records"
              :key="item.class_name"
              class="result-tag"
            >
              {{ item.class_name }} {{ item.count }} 人
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="匹配结果" :span="2">
            <el-tag
              v-for="item in preflightResult.matching.summary"
              :key="item.status"
              class="result-tag"
              type="info"
            >
              {{ item.status }}：{{ item.count }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="未匹配生产主档" :span="2">
            <span v-if="!preflightResult.matching.no_production_match_by_class.length">
              无
            </span>
            <template v-else>
              <el-tag
                v-for="item in preflightResult.matching.no_production_match_by_class"
                :key="item.class_name"
                class="result-tag"
                type="warning"
              >
                {{ item.class_name }}：{{ item.count }} 人
              </el-tag>
            </template>
          </el-descriptions-item>
          <el-descriptions-item label="已匹配但待校正字段" :span="2">
            <span v-if="!preflightResult.matching.matched_profile_fields_needing_reconciliation.length">
              无
            </span>
            <template v-else>
              <el-tag
                v-for="item in preflightResult.matching.matched_profile_fields_needing_reconciliation"
                :key="item.field"
                class="result-tag"
                type="info"
              >
                {{ item.field }}：{{ item.count }}
              </el-tag>
            </template>
          </el-descriptions-item>
          <el-descriptions-item label="直属班组织解析" :span="2">
            <el-tag
              v-for="item in preflightResult.organization.direct_class_status"
              :key="item.class_name"
              class="result-tag"
              :type="item.action === 'REUSE' ? 'success' : 'warning'"
            >
              {{ item.class_name }}：{{ item.action }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="preflightResult.issues.length"
          class="result-alert"
          title="存在需人工复核的汇总项；系统不会自动写入"
          type="warning"
          :closable="false"
          show-icon
        />
        <el-alert
          v-if="preflightResult.issues.length"
          class="result-alert"
          :title="`复核原因：${preflightResult.issues.map(item => `${item.code} ${item.count}`).join('；')}`"
          type="warning"
          :closable="false"
          show-icon
        />
        <p class="form-hint">{{ preflightResult.write_gates[0] }}</p>
      </div>
      <template #footer>
        <el-button v-if="preflightResult && !preflightResult.issues.length" type="danger" :loading="preflightLoading" @click="applyDirectClassImport">执行确认导入</el-button>
        <el-button @click="preflightVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="fullPreflightVisible"
      title="全量班级与小组生产前只读预检"
      width="1080px"
      class="preflight-dialog"
    >
      <el-alert
        title="本操作只生成聚合报告，不创建或修改任何生产数据"
        description="工作簿只在服务器内存中解析；手机号仅转换为受保护的匹配摘要，界面不显示姓名、手机号、成员编号或组织 ID。"
        type="success"
        :closable="false"
        show-icon
      />
      <el-upload
        class="preflight-upload"
        accept=".xlsx"
        :auto-upload="false"
        :limit="1"
        :file-list="fullPreflightFiles"
        :on-change="selectFullPreflightFile"
      >
        <el-button>选择最新学员表 .xlsx</el-button>
      </el-upload>
      <el-button
        type="primary"
        :loading="fullPreflightLoading"
        @click="runFullClassPreflight"
      >
        生成全量只读预检报告
      </el-button>

      <div v-if="fullPreflightResult" class="preflight-result">
        <el-descriptions :column="4" border>
          <el-descriptions-item label="在册学员">
            {{ fullPreflightResult.source.active_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="已有班级">
            {{ fullPreflightResult.source.with_class_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="未分班">
            {{ fullPreflightResult.source.missing_class_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="生产写入">
            已禁止
          </el-descriptions-item>
          <el-descriptions-item label="普通班">
            {{ fullPreflightResult.source.ordinary_class_count }} 个／
            {{ fullPreflightResult.source.ordinary_class_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="直属班">
            {{ fullPreflightResult.source.direct_class_count }} 个／
            {{ fullPreflightResult.source.direct_class_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="普通班小组">
            {{ fullPreflightResult.source.ordinary_group_pair_count }} 个
          </el-descriptions-item>
          <el-descriptions-item label="直属班小组">
            {{ fullPreflightResult.source.direct_group_pair_count }} 个
          </el-descriptions-item>
          <el-descriptions-item label="生产匹配" :span="4">
            <el-tag
              v-for="item in fullPreflightResult.matching.summary"
              :key="item.status"
              class="result-tag"
              type="info"
            >
              {{ item.status }}：{{ item.count }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="班级组织解析" :span="4">
            <el-tag
              v-for="item in fullPreflightResult.organization.class_status"
              :key="item.class_name"
              class="result-tag"
              :type="item.action === 'REUSE' ? 'success' : item.action === 'REVIEW' ? 'danger' : 'warning'"
            >
              {{ item.class_name }}（{{ item.expected_parent }}）：{{ item.action }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="小组组织处理" :span="4">
            <el-tag
              v-for="item in fullPreflightResult.organization.group_action_summary"
              :key="item.action"
              class="result-tag"
              :type="item.action === 'REUSE' ? 'success' : 'warning'"
            >
              {{ item.action }}：{{ item.count }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="待校正字段或关系" :span="4">
            <span
              v-if="!fullPreflightResult.matching.fields_or_relations_needing_reconciliation.length"
            >
              无
            </span>
            <template v-else>
              <el-tag
                v-for="item in fullPreflightResult.matching.fields_or_relations_needing_reconciliation"
                :key="item.field"
                class="result-tag"
                type="warning"
              >
                {{ item.field }}：{{ item.count }}
              </el-tag>
            </template>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="fullPreflightResult.issues.length"
          class="result-alert"
          :title="`需人工复核：${fullPreflightResult.issues.map(item => `${item.code} ${item.count}`).join('；')}`"
          type="warning"
          :closable="false"
          show-icon
        />
        <p class="form-hint">
          {{ fullPreflightResult.write_gates.join(" ") }}
        </p>
        <el-alert
          v-if="canApplyFullOrgImport"
          class="result-alert"
          title="第一阶段只创建组织节点：20个普通班、112个普通班小组；不修改任何学员或签到数据。"
          type="error"
          :closable="false"
          show-icon
        />
        <el-button
          v-if="canApplyFullOrgImport"
          type="danger"
          :loading="fullOrgImportLoading"
          @click="applyFullOrgImport"
        >
          执行第一阶段组织创建
        </el-button>
        <el-alert
          v-if="fullPreflightResult"
          class="result-alert"
          title="第二阶段仅补齐唯一匹配且已分班学员的班级、小组关系；不修改学员字段、发展归属或签到数据。"
          type="warning"
          :closable="false"
          show-icon
        />
        <el-button
          v-if="fullPreflightResult"
          type="danger"
          :loading="fullRelationImportLoading"
          @click="applyFullRelationImport"
        >
          执行第二阶段关系写入
        </el-button>
      </div>
      <template #footer>
        <el-button @click="fullPreflightVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="dialogVisible"
      :title="editingMemberId ? '编辑学员' : '新增学员'"
      width="1180px"
      class="member-dialog"
    >
      <p class="form-hint">
        {{ editingMemberId ? "编辑时可核对或更换手机号；历史缺失号码可先保存其他资料。" : "姓名、分中心和手机号为必填项。" }}
        年销售额与利润率按敏感信息加密保存。
      </p>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <div class="form-grid">
          <el-form-item label="姓名" prop="name">
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="分中心" prop="org_unit_id">
            <el-select v-model="form.org_unit_id" placeholder="请选择">
              <el-option
                v-for="org in centerOrgs"
                :key="org.id"
                :label="org.name"
                :value="org.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="公司名称" prop="company_name">
            <el-input v-model="form.company_name" />
          </el-form-item>
          <el-form-item label="手机号" prop="phone">
            <el-input
              v-model="form.phone"
              maxlength="11"
              :loading="editProfileLoading"
              :disabled="editProfileLoading"
              :placeholder="editingMemberId ? '可留空；填写时须为 11 位手机号' : '请输入 11 位手机号'"
            />
          </el-form-item>
          <el-form-item label="隶属区">
            <el-input v-model="form.district" />
          </el-form-item>
          <el-form-item label="公司地址">
            <el-input v-model="form.company_address" />
          </el-form-item>
          <el-form-item label="性别">
            <el-select v-model="form.gender" clearable placeholder="请选择">
              <el-option label="男" value="MALE" />
              <el-option label="女" value="FEMALE" />
              <el-option label="其他/未说明" value="UNSPECIFIED" />
            </el-select>
          </el-form-item>
          <el-form-item label="班级组织">
            <el-select
              v-model="form.class_org_unit_id"
              clearable
              filterable
              placeholder="请选择正式班级"
              @change="onClassOrgChange"
            >
            <el-option
              v-for="org in classOptions"
              :key="org.id"
              :label="org.option_label"
              :value="org.id"
            />
            </el-select>
          </el-form-item>
          <el-form-item label="行业分类">
            <el-input v-model="form.industry_category" />
          </el-form-item>
          <el-form-item label="生日">
            <el-date-picker
              v-model="form.birthday"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="小组组织">
            <el-select
              v-model="form.group_org_unit_id"
              clearable
              filterable
              :disabled="!form.class_org_unit_id"
              placeholder="请选择正式小组"
            >
              <el-option
                v-for="org in groupOptions"
                :key="org.id"
                :label="org.name"
                :value="org.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="行业">
            <el-input v-model="form.industry" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="form.status">
              <el-option label="在册" value="ACTIVE" />
              <el-option label="流失" value="INACTIVE" />
              <el-option label="暂停" value="SUSPENDED" />
            </el-select>
          </el-form-item>
          <el-form-item label="职务">
            <el-input v-model="form.position" />
          </el-form-item>
          <el-form-item label="公司产品">
            <el-input v-model="form.company_products" />
          </el-form-item>
          <el-form-item label="入塾日期">
            <el-date-picker
              v-model="form.join_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
              @change="onJoinDateChange"
            />
          </el-form-item>
          <el-form-item label="续费月份">
            <el-date-picker
              v-model="form.renewal_month"
              type="month"
              value-format="YYYY-MM"
              placeholder="YYYY-MM"
            />
          </el-form-item>
          <el-form-item label="公司销售额（万元）">
            <el-input
              v-model="form.annual_sales"
              :disabled="!financialFieldsEditable"
              :placeholder="financialFieldsEditable ? '例如 10000' : '需企业敏感资料权限'"
            >
              <template #append>万元</template>
            </el-input>
          </el-form-item>
          <el-form-item label="开始学习时间">
            <el-date-picker
              v-model="form.study_start_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="推荐人">
            <el-input v-model="form.referrer" />
          </el-form-item>
          <el-form-item label="员工人数（人）">
            <el-input-number
              v-model="form.employee_count"
              :min="0"
              :max="10000000"
              :precision="0"
              controls-position="right"
              placeholder="例如 102"
            />
          </el-form-item>
          <el-form-item label="入塾年限">
            <div class="tenure-field">
              <el-input-number
                v-model="form.membership_years"
                :min="0"
                :max="100"
                :precision="1"
                controls-position="right"
                :disabled="form.membership_years_inferred"
              />
              <el-button
                v-if="form.membership_years_inferred"
                link
                type="primary"
                @click="enableMembershipYearsOverride"
              >
                手动修改
              </el-button>
              <el-button
                v-else
                link
                type="primary"
                @click="restoreInferredMembershipYears"
              >
                恢复自动计算
              </el-button>
              <span class="tenure-hint">
                {{ form.membership_years_inferred ? "根据入塾日期自动计算" : "当前为人工覆盖值" }}
              </span>
            </div>
          </el-form-item>
          <el-form-item label="推荐人所属分中心">
            <el-input v-model="form.referrer_center" />
          </el-form-item>
          <el-form-item label="利润率">
            <el-input
              v-model="form.profit_margin"
              :disabled="!financialFieldsEditable"
              :placeholder="financialFieldsEditable ? '例如 12%' : '需企业敏感资料权限'"
            />
          </el-form-item>
          <el-form-item class="full" label="备注">
            <el-input v-model="form.notes" type="textarea" :rows="3" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="Boolean(editingMemberId) && !editPhoneReady"
          @click="submit"
        >
          {{ editingMemberId ? "保存变更" : "加密保存" }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="historyVisible"
      :title="`${historyMember?.name ?? '学员'} · 变更历史`"
      width="920px"
      class="history-dialog"
    >
      <el-alert
        title="只读审计记录"
        description="这里显示学员状态、分中心、班级、小组及档案字段的变更，不提供直接修改入口；初始导入不会生成历史，首次编辑后才会记录。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-table
        v-loading="historyLoading"
        :data="historyRows"
        stripe
        empty-text="暂无变更记录；初始导入不会生成历史，首次编辑后才会记录"
        class="history-table"
      >
        <el-table-column label="时间" width="190">
          <template #default="{ row }">{{ row.changed_at }}</template>
        </el-table-column>
        <el-table-column label="变更类型" width="130">
          <template #default="{ row }">
            <el-tag type="info">{{ historyTypeLabel(row.change_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="变更内容" min-width="520">
          <template #default="{ row }">{{ historySummary(row) }}</template>
        </el-table-column>
        <el-table-column prop="changed_by" label="操作人" width="100" />
      </el-table>
      <template #footer>
        <el-button @click="historyVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="timelineVisible"
      :title="`${timeline?.member.name ?? '学员'} · 档案与服务时间线`"
      width="1120px"
      class="timeline-dialog"
    >
      <div v-loading="timelineLoading">
        <template v-if="timeline">
          <el-descriptions :column="4" border class="timeline-profile">
            <el-descriptions-item label="姓名">{{ timeline.member.name }}</el-descriptions-item>
            <el-descriptions-item label="分中心">{{ timeline.member.org_name }}</el-descriptions-item>
            <el-descriptions-item label="班级">{{ timeline.member.class_name || "—" }}</el-descriptions-item>
            <el-descriptions-item label="小组">{{ timeline.member.group_name || "—" }}</el-descriptions-item>
            <el-descriptions-item label="手机号（脱敏）">{{ timeline.member.phone_masked || "—" }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ memberStatusLabel(timeline.member.status) }}</el-descriptions-item>
          </el-descriptions>

          <section class="service-signals">
            <div class="service-signals__head">
              <div>
                <h3>服务提示</h3>
                <p>只依据明确数据规则提示待核对事项，不评价学长，也不用于排名；人工反馈不会自动创建任务。</p>
              </div>
              <el-tag
                :type="timeline.service_signal_feedback_enabled ? 'success' : 'info'"
                effect="plain"
              >
                {{ timeline.service_signal_feedback_enabled ? "反馈试点已开启" : "规则只读" }}
              </el-tag>
            </div>
            <div v-if="timeline.service_signals.length" class="service-signals__grid">
              <article
                v-for="signal in timeline.service_signals"
                :key="signal.code"
                class="service-signal"
              >
                <el-tag
                  :type="signal.attention_level === 'ACTION_REQUIRED' ? 'warning' : 'info'"
                  effect="light"
                >
                  {{ signal.attention_level === "ACTION_REQUIRED" ? "待处理" : "待核对" }}
                </el-tag>
                <div>
                  <strong>{{ signal.title }}</strong>
                  <p>{{ signal.message }}</p>
                  <small>{{ signal.action_hint }}</small>
                  <div v-if="signal.latest_feedback" class="service-signal__feedback">
                    <el-tag size="small" type="success" effect="plain">
                      {{ serviceSignalFeedbackLabel(signal.latest_feedback.status) }}
                    </el-tag>
                    <small>{{ formatTimelineTime(signal.latest_feedback.created_at) }}</small>
                  </div>
                  <div
                    v-if="timeline.service_signal_feedback_enabled && canManage"
                    class="service-signal__actions"
                  >
                    <el-button
                      size="small"
                      plain
                      :loading="serviceSignalFeedbackLoading === `${signal.code}:CONFIRMED_VALID`"
                      @click="submitServiceSignalFeedback(signal, 'CONFIRMED_VALID')"
                    >
                      确认有效
                    </el-button>
                    <el-button
                      size="small"
                      plain
                      :loading="serviceSignalFeedbackLoading === `${signal.code}:NOT_APPLICABLE`"
                      @click="submitServiceSignalFeedback(signal, 'NOT_APPLICABLE')"
                    >
                      暂不适用
                    </el-button>
                    <el-button
                      size="small"
                      plain
                      :loading="serviceSignalFeedbackLoading === `${signal.code}:DATA_CORRECTED`"
                      @click="submitServiceSignalFeedback(signal, 'DATA_CORRECTED')"
                    >
                      数据已修正
                    </el-button>
                  </div>
                </div>
              </article>
            </div>
            <el-empty v-else description="当前没有需要提示的事项" :image-size="52" />
          </section>

          <div class="timeline-summary">
            <el-tag
              v-for="(count, type) in timeline.summary"
              :key="type"
              type="info"
            >
              {{ timelineSummaryLabel(type) }}：{{ count }}
            </el-tag>
          </div>

          <el-table
            :data="timeline.events"
            stripe
            empty-text="暂无服务记录"
            class="timeline-table"
            max-height="480"
          >
            <el-table-column label="时间" width="190">
              <template #default="{ row }">{{ formatTimelineTime(row.occurred_at) }}</template>
            </el-table-column>
            <el-table-column label="记录类型" width="130">
              <template #default="{ row }">
                <el-tag type="info">{{ timelineTypeLabel(row.event_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="事项" min-width="220" />
            <el-table-column label="状态" width="150">
              <template #default="{ row }">{{ timelineStatusLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column label="场次/渠道" width="150">
              <template #default="{ row }">{{ timelineChannelLabel(row.channel) }}</template>
            </el-table-column>
          </el-table>
          <p class="form-hint timeline-hint">
            时间线只显示受权限控制的事件摘要；服务原文、企业资料和完整联系方式仍需进入对应业务页面并按用途审计。
          </p>
        </template>
      </div>
      <template #footer>
        <el-button @click="timelineVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.member-page {
  display: grid;
  gap: 18px;
  padding: 20px;
}
.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  padding: 28px;
  color: #f6fff9;
  background: linear-gradient(125deg, #123c2e, #25704e);
  border-radius: 18px;
}
.page-head p {
  margin: 0 0 8px;
  color: #9fe0bd;
  letter-spacing: 0.14em;
}
.page-head h1 {
  margin: 0 0 10px;
  font-size: 30px;
}
.page-head span {
  color: #cbe9d8;
}
.head-actions {
  display: flex;
  gap: 12px;
}
.toolbar {
  display: grid;
  grid-template-columns: 220px minmax(260px, 1fr) auto;
  gap: 14px;
  align-items: center;
  margin-bottom: 18px;
}
.result-count {
  color: var(--el-text-color-secondary);
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 18px;
}
.form-grid .full {
  grid-column: 1 / -1;
}
.form-grid :deep(.el-select) {
  width: 100%;
}
.form-grid :deep(.el-date-editor),
.form-grid :deep(.el-input-number) {
  width: 100%;
}
.tenure-field {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 10px;
  width: 100%;
}
.tenure-hint {
  grid-column: 1 / -1;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.4;
}
.form-hint {
  margin: -4px 0 20px;
  color: var(--el-text-color-secondary);
}
.preflight-upload {
  margin: 18px 0 12px;
}
.preflight-result {
  display: grid;
  gap: 14px;
  margin-top: 20px;
}
.result-tag {
  margin: 0 8px 6px 0;
}
.result-alert {
  margin-top: 4px;
}
.timeline-profile {
  margin-bottom: 18px;
}
.service-signals {
  padding: 16px;
  margin-bottom: 18px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
}
.service-signals__head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}
.service-signals__head h3,
.service-signals__head p,
.service-signal p {
  margin: 0;
}
.service-signals__head p,
.service-signal small {
  color: var(--el-text-color-secondary);
}
.service-signals__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.service-signal {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px;
  background: var(--el-bg-color);
  border-radius: 10px;
}
.service-signal p {
  margin: 4px 0;
}
.service-signal__feedback,
.service-signal__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
}
.service-signal__actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.timeline-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 16px;
}
.timeline-table {
  width: 100%;
}
.timeline-hint {
  margin: 14px 0 0;
}
:global(.member-dialog) {
  max-width: calc(100vw - 40px);
}
@media (max-width: 760px) {
  .page-head {
    align-items: flex-start;
    gap: 20px;
  }
  .head-actions {
    flex-wrap: wrap;
  }
  .toolbar,
  .form-grid {
    grid-template-columns: 1fr;
  }
  .form-grid .full {
    grid-column: auto;
  }
  .service-signals__grid {
    grid-template-columns: 1fr;
  }
}
</style>
