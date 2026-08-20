<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import {
  generateBirthdayGreetingDraft as requestBirthdayGreetingDraft,
  generateOperationRhythm,
  getClassOperations,
  getBirthdayGreetingContext,
  getMemberCareActionsToday,
  getMemberCareManagementOverview,
  getRenewalAnnualAnalytics,
  getAnnualPlans,
  getMpDashboard,
  getOperationRhythmSnapshot,
  getOperationsSnapshot,
  getTargetVariances,
  updateClassOperations,
  updateOperationRhythmItem,
  type AnnualPlan,
  type BirthdayGreetingContext,
  type ClassOperationsDetail,
  type DashboardItem,
  type MemberCareAction,
  type MemberCareActions,
  type MemberCareManagementException,
  type MemberCareManagementExceptionType,
  type MemberCareManagementOverview,
  type MemberCarePerson,
  type RenewalAnnualAnalytics,
  type OperationRhythmItem,
  type OperationRhythmSnapshot,
  type OperationRhythmStatus,
  type OperationsSnapshot
} from "@/api/seiwajyuku";
import { useUserStoreHook } from "@/store/modules/user";

defineOptions({ name: "MpDashboard" });

const loading = ref(false);
const router = useRouter();
const plans = ref<AnnualPlan[]>([]);
const planId = ref<number>();
const year = ref(new Date().getFullYear());
const month = ref(Math.min(new Date().getMonth() + 1, 12));
const operations = ref<OperationsSnapshot>();
const rhythm = ref<OperationRhythmSnapshot>();
const memberCare = ref<MemberCareActions>();
const memberCareError = ref(false);
const memberCareDialogVisible = ref(false);
const selectedCarePerson = ref<MemberCarePerson>();
const memberCareManagement = ref<MemberCareManagementOverview>();
const memberCareManagementError = ref(false);
const renewalAnnualAnalytics = ref<RenewalAnnualAnalytics>();
const renewalAnnualAnalyticsError = ref(false);
const rhythmView = ref<"today" | "next_7_days" | "month" | "attention">(
  "next_7_days"
);
const rhythmOrganizationId = ref("");
const rhythmClassOrgUnitId = ref("");
const rhythmStatus = ref<OperationRhythmStatus | "">("");
const rhythmGenerating = ref(false);
const rhythmItemSaving = ref<number | null>(null);
const rhythmEditVisible = ref(false);
const rhythmEditSaving = ref(false);
const rhythmEditing = ref<OperationRhythmItem>();
const rhythmEditForm = ref({
  title: "",
  start_date: "",
  due_date: "",
  note: ""
});
const birthdayCenterId = ref("");
const birthdayClassOrgUnitId = ref("");
const birthdayMonth = ref(String(month.value).padStart(2, "0"));
const birthdayGreetingVisible = ref(false);
const birthdayGreetingLoading = ref(false);
const birthdayGreetingDraftLoading = ref(false);
const birthdayGreeting = ref<BirthdayGreetingContext>();
const selectedBirthdayMemoryIds = ref<string[]>([]);
const birthdayGreetingTone = ref<"standard" | "warm" | "concise">("warm");
const birthdayGreetingDraft = ref("");
const items = ref<DashboardItem[]>([]);
const selectedMetricKey = ref("active_member_count");
const classDrawerVisible = ref(false);
const classDetailLoading = ref(false);
const classSaving = ref(false);
const classDetail = ref<ClassOperationsDetail>();
const canManageClassOperations = computed(() =>
  useUserStoreHook().permissions.includes("plans:period_write")
);
const canManageRhythm = canManageClassOperations;
const classForm = ref({
  weekly_meeting_at: "",
  planned_class_meeting_at: "",
  learning_month: undefined as number | undefined,
  learning_progress: "",
  revenue_growing_member_count: undefined as number | undefined,
  revenue_comparable_member_count: undefined as number | undefined,
  groups: [] as {
    group_org_unit_id: string;
    name: string;
    planned_meeting_at: string;
  }[]
});
const variances = ref<
  { metric_key: string; difference: number; aggregation: string }[]
>([]);

const currentPlan = computed(() =>
  plans.value.find(item => item.id === planId.value)
);
const centers = computed(() => [
  ...new Set(items.value.map(item => item.org_name))
]);
const metrics = computed(() => {
  const result = new Map<string, { key: string; name: string; unit: string }>();
  items.value.forEach(item => {
    result.set(item.metric_key, {
      key: item.metric_key,
      name: item.metric_name,
      unit: item.unit
    });
  });
  return [...result.values()];
});
const selectedMetric = computed(() =>
  metrics.value.find(item => item.key === selectedMetricKey.value)
);
const selectedItems = computed(() =>
  items.value.filter(item => item.metric_key === selectedMetricKey.value)
);
const toNumber = (value: number | string | null | undefined) => {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};
const achievementValues = computed(() =>
  selectedItems.value
    .map(item => toNumber(item.forecast_achievement))
    .filter((value): value is number => value !== null)
);
const averageAchievement = computed(() => {
  const values = achievementValues.value;
  return values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;
});
const actualCount = computed(
  () =>
    selectedItems.value.filter(item => toNumber(item.actual?.value) !== null)
      .length
);
const reachedForecastCount = computed(
  () =>
    selectedItems.value.filter(item => {
      const achievement = toNumber(item.forecast_achievement);
      return achievement !== null && achievement >= 1;
    }).length
);
const yearOptions = computed(() => {
  const currentYear = new Date().getFullYear();
  return Array.from(
    new Set([
      currentYear - 2,
      currentYear - 1,
      currentYear,
      currentYear + 1,
      ...plans.value.map(plan => plan.year)
    ])
  ).sort((a, b) => b - a);
});
const selectedVariance = computed(() =>
  variances.value.find(item => item.metric_key === selectedMetricKey.value)
);
const operationsCards = computed(() => {
  const summary = operations.value?.summary;
  if (!summary) return [];
  return [
    {
      label: "本月续费",
      value: operations.value?.data_quality.renewal_source_authorized
        ? summary.renewed_member_count
        : null,
      unit: "位",
      note: operations.value?.data_quality.renewal_source_authorized
        ? "本月状态转为已续费"
        : "当前账号无续费查看权限"
    },
    {
      label: "本月新增",
      value: summary.new_member_count,
      unit: "位",
      note: "按学员主档入塾日期"
    },
    {
      label:
        operations.value?.scope_label === "苏州塾"
          ? "苏州塾总在册"
          : "当前在册",
      value: summary.active_member_count,
      unit: "位",
      note: operations.value?.scope_label || "授权范围"
    },
    {
      label: "本月生日",
      value: summary.birthday_member_count,
      unit: "位",
      note: "仅统计当前在册学长"
    },
    {
      label: "班级数量",
      value: summary.class_count,
      unit: "个",
      note: "按正式班级组织去重统计"
    },
    {
      label: "本月课程",
      value: operations.value?.data_quality.course_schedule_source_ready
        ? summary.course_count
        : null,
      unit: "次",
      note: operations.value?.data_quality.course_schedule_source_ready
        ? "按课程活动组计次"
        : "课程排期数据尚未接入"
    },
    {
      label: "本月其他活动",
      value: operations.value?.data_quality.attendance_schedule_source_ready
        ? summary.activity_count
        : null,
      unit: "次",
      note: operations.value?.data_quality.attendance_schedule_source_ready
        ? "不含班会与课程"
        : "活动排期数据尚未接入"
    }
  ];
});
const classRows = computed(() => operations.value?.classes || []);
const rhythmOrganizationOptions = computed(() => {
  const options = new Map<string, string>();
  (rhythm.value?.items || []).forEach(item => {
    if (item.organization_id && item.organization_name) {
      options.set(item.organization_id, item.organization_name);
    }
  });
  return [...options].map(([id, name]) => ({ id, name }));
});
const rhythmClassOptions = computed(() => {
  const options = new Map<string, string>();
  (rhythm.value?.items || [])
    .filter(
      item =>
        !rhythmOrganizationId.value ||
        item.organization_id === rhythmOrganizationId.value
    )
    .forEach(item => {
      const id = item.class_org_unit_id || item.org_unit_id;
      const name = item.class_name || item.org_name;
      if (id && name) options.set(id, name);
    });
  return [...options].map(([id, name]) => ({ id, name }));
});
const rhythmItems = computed(() => {
  const items = rhythm.value?.views[rhythmView.value] ?? [];
  return items.filter(
    item =>
      (!rhythmOrganizationId.value ||
        item.organization_id === rhythmOrganizationId.value) &&
      (!rhythmClassOrgUnitId.value ||
        (item.class_org_unit_id || item.org_unit_id) ===
          rhythmClassOrgUnitId.value) &&
      (!rhythmStatus.value || item.status === rhythmStatus.value)
  );
});
const classMeetingRows = computed(() => {
  if (!classDetail.value) return [];
  if (classDetail.value.class_meetings.length)
    return classDetail.value.class_meetings;
  if (classDetail.value.planned_class_meeting_at) {
    return [
      {
        id: null,
        title: "计划班会（尚未形成正式活动事实）",
        event_date: classDetail.value.planned_class_meeting_at,
        activity_type: "CLASS_MEETING",
        org_name: classDetail.value.org_name,
        class_org_unit_id: classDetail.value.class_org_unit_id,
        class_name: classDetail.value.class_name,
        status: "PLANNED" as const
      }
    ];
  }
  return [];
});
const otherScheduleRows = computed(() => [
  ...(operations.value?.courses || []).map(item => ({
    ...item,
    category: "课程"
  })),
  ...(operations.value?.activities || []).map(item => ({
    ...item,
    category: "活动"
  }))
]);
const birthdayCenterOptions = computed(() => {
  const options = new Map<string, string>();
  (operations.value?.birthday_members || []).forEach(item => {
    options.set(item.org_unit_id, item.org_name);
  });
  return [...options].map(([id, name]) => ({ id, name }));
});
const birthdayClassOptions = computed(() => {
  const options = new Map<string, string>();
  (operations.value?.birthday_members || [])
    .filter(
      item =>
        !birthdayCenterId.value || item.org_unit_id === birthdayCenterId.value
    )
    .forEach(item => {
      if (item.class_org_unit_id && item.class_name) {
        options.set(item.class_org_unit_id, item.class_name);
      }
    });
  return [...options].map(([id, name]) => ({ id, name }));
});
const birthdayMonthOptions = Array.from({ length: 12 }, (_, index) => {
  const value = String(index + 1).padStart(2, "0");
  return { id: value, name: `${index + 1}月` };
}).concat({ id: "ALL", name: "全年" });
const filteredBirthdayMembers = computed(() =>
  (operations.value?.birthday_members || []).filter(
    item =>
      (!birthdayCenterId.value ||
        item.org_unit_id === birthdayCenterId.value) &&
      (!birthdayClassOrgUnitId.value ||
        item.class_org_unit_id === birthdayClassOrgUnitId.value) &&
      (birthdayMonth.value === "ALL" ||
        !birthdayMonth.value ||
        item.birthday.slice(0, 2) === birthdayMonth.value)
  )
);

function changeBirthdayCenter() {
  birthdayClassOrgUnitId.value = "";
}

function changeOperationsMonth() {
  birthdayMonth.value = String(month.value).padStart(2, "0");
  rhythmOrganizationId.value = "";
  rhythmClassOrgUnitId.value = "";
  rhythmStatus.value = "";
  load();
}

function changeRhythmOrganization() {
  rhythmClassOrgUnitId.value = "";
}

const rhythmStatusLabel = (status: OperationRhythmStatus) =>
  ({
    PENDING: "待确认",
    PLANNED: "已计划",
    IN_PROGRESS: "推进中",
    WAITING_EXTERNAL: "等待外部反馈",
    COMPLETED: "已圆满",
    ATTENTION: "需关注",
    CANCELLED: "已取消"
  })[status];

const rhythmStatusType = (
  status: OperationRhythmStatus
): "primary" | "success" | "warning" | "info" | "danger" =>
  ({
    PENDING: "info",
    PLANNED: "primary",
    IN_PROGRESS: "warning",
    WAITING_EXTERNAL: "warning",
    COMPLETED: "success",
    ATTENTION: "danger",
    CANCELLED: "info"
  })[status] as "primary" | "success" | "warning" | "info" | "danger";

const careUrgencyType = (urgency: MemberCareAction["urgency"]) =>
  ({
    OVERDUE: "danger",
    TODAY: "primary",
    ATTENTION: "warning",
    WINDOW: "success"
  })[urgency] as "primary" | "success" | "warning" | "danger";
const careUrgencyLabel = (urgency: MemberCareAction["urgency"]) =>
  ({ OVERDUE: "逾期", TODAY: "今天", ATTENTION: "需协助", WINDOW: "窗口" })[
    urgency
  ];
const careActionTypeLabel = (action: MemberCareAction) =>
  ({
    RENEWAL: "续费关爱",
    BIRTHDAY: "生日关怀",
    ENTERPRISE_VISIT: "企业走访",
    PHONE: "电话关怀",
    WECHAT: "微信关怀",
    MEETING: "面谈关怀",
    CARE: "日常关怀",
    COURSE: "学习关怀",
    OTHER: "日常关怀"
  })[action.action_type] ?? action.action_type;
const careNavigationLabel = (
  navigationType: MemberCareAction["navigation_type"]
) =>
  ({
    RENEWAL: "去续费今日行动",
    FOLLOWUP: "去关怀跟进",
    ENTERPRISE_VISIT: "去企业走访",
    BIRTHDAY: "去生日关怀"
  })[navigationType];
const careDueDate = (value?: string | null) =>
  value ? dayjs(value).format("YYYY-MM-DD") : "待确认时间";

function openCarePerson(person: unknown) {
  selectedCarePerson.value = person as MemberCarePerson;
  memberCareDialogVisible.value = true;
}

async function navigateCareAction(action: MemberCareAction) {
  memberCareDialogVisible.value = false;
  if (action.navigation_type === "BIRTHDAY") {
    await openBirthdayGreeting({ member_id: action.navigation_id });
    return;
  }
  if (action.navigation_type === "RENEWAL") {
    await router.push({
      path: "/operations/renewals",
      query: { cycle_id: String(action.navigation_id) }
    });
    return;
  }
  await router.push({
    path: "/operations/followups",
    query: { task_id: String(action.navigation_id) }
  });
}

const managementExceptionLabels: Record<
  MemberCareManagementExceptionType,
  string
> = {
  CARE_OVERDUE: "关爱逾期",
  RENEWAL_RECOVERY_OPEN: "续费挽回未闭环",
  RENEWAL_SUPPORT_NEEDED: "续费需要协助",
  RENEWAL_STAGE_UNTOUCHED: "当前阶段尚未留下关爱记录",
  RENEWAL_UNASSIGNED: "续费责任人待分配",
  FOLLOWUP_NO_SCHEDULE: "关怀任务缺少下一时间"
};
const managementExceptionLabel = (type: MemberCareManagementExceptionType) =>
  managementExceptionLabels[type] ?? type;
const managementExceptionType = (type: MemberCareManagementExceptionType) =>
  type === "CARE_OVERDUE"
    ? "danger"
    : type === "RENEWAL_SUPPORT_NEEDED"
      ? "warning"
      : "info";
const managementCountLabel = (value: number | null | undefined) =>
  value === null || value === undefined ? "未授权" : String(value);
const managementSourceLabel = (
  source: MemberCareManagementException["source"]
) =>
  ({
    RENEWAL: "续费",
    FOLLOWUP: "日常关怀",
    ENTERPRISE_VISIT: "企业走访",
    BIRTHDAY: "生日关怀"
  })[source];

async function navigateManagementException(item: unknown) {
  const exception = item as MemberCareManagementException;
  if (exception.navigation_type === "BIRTHDAY") {
    await openBirthdayGreeting({ member_id: exception.navigation_id });
    return;
  }
  if (exception.navigation_type === "RENEWAL") {
    await router.push({
      path: "/operations/renewals",
      query: { cycle_id: String(exception.navigation_id) }
    });
    return;
  }
  await router.push({
    path: "/operations/followups",
    query: { task_id: String(exception.navigation_id) }
  });
}

async function generateRhythm() {
  rhythmGenerating.value = true;
  try {
    const response = await generateOperationRhythm({
      year: year.value,
      month: month.value
    });
    ElMessage.success(
      `已生成 ${response.data.created_item_count} 项本月运营事项，可继续由核心运营人员维护状态`
    );
    await load();
  } catch (error) {
    ElMessage.error("本月运营事项生成失败，请检查当前账号和组织范围");
  } finally {
    rhythmGenerating.value = false;
  }
}

async function saveRhythmStatus(item: any, status: OperationRhythmStatus) {
  const previous = item.status;
  item.status = status;
  rhythmItemSaving.value = item.id;
  try {
    const response = await updateOperationRhythmItem(item.id, { status });
    Object.assign(item, response.data);
    ElMessage.success("运营事项状态已记录");
  } catch (error) {
    item.status = previous;
    ElMessage.error("运营事项状态保存失败，请稍后重试");
  } finally {
    rhythmItemSaving.value = null;
  }
}

function openRhythmEdit(item: any) {
  rhythmEditing.value = item;
  rhythmEditForm.value = {
    title: item.title,
    start_date: item.start_date || "",
    due_date: item.due_date || "",
    note: item.completion_note || ""
  };
  rhythmEditVisible.value = true;
}

async function saveRhythmEdit() {
  const item = rhythmEditing.value;
  if (!item || !rhythmEditForm.value.title.trim()) {
    ElMessage.warning("请填写事项名称");
    return;
  }
  rhythmEditSaving.value = true;
  try {
    const response = await updateOperationRhythmItem(item.id, {
      title: rhythmEditForm.value.title.trim(),
      start_date: rhythmEditForm.value.start_date || null,
      due_date: rhythmEditForm.value.due_date || null,
      note: rhythmEditForm.value.note || null
    });
    Object.assign(item, response.data);
    rhythmEditVisible.value = false;
    ElMessage.success("运营事项已更新，日期和事项名称已记录");
  } catch {
    ElMessage.error("运营事项保存失败，请检查日期后重试");
  } finally {
    rhythmEditSaving.value = false;
  }
}

function openRhythmBusinessItem(item: any) {
  if (item.business_type !== "BIRTHDAY_CARE") return;
  const memberId = Number(item.business_id || 0);
  if (memberId) openBirthdayGreeting({ member_id: memberId });
}

async function openBirthdayGreeting(row: unknown) {
  const memberId = Number(
    (row as { member_id?: number } | null)?.member_id || 0
  );
  if (!memberId) return;
  birthdayGreetingVisible.value = true;
  birthdayGreetingLoading.value = true;
  birthdayGreeting.value = undefined;
  birthdayGreetingDraft.value = "";
  selectedBirthdayMemoryIds.value = [];
  try {
    const response = await getBirthdayGreetingContext(memberId);
    birthdayGreeting.value = response.data;
    selectedBirthdayMemoryIds.value = [...response.data.selected_memory_ids];
    await generateBirthdayDraft();
  } catch (error) {
    birthdayGreetingVisible.value = false;
    ElMessage.error("生日关怀资料加载失败，请稍后重试");
  } finally {
    birthdayGreetingLoading.value = false;
  }
}

async function generateBirthdayDraft(tone = birthdayGreetingTone.value) {
  if (!birthdayGreeting.value) return;
  birthdayGreetingTone.value = tone;
  birthdayGreetingDraftLoading.value = true;
  try {
    const response = await requestBirthdayGreetingDraft(
      birthdayGreeting.value.member.id,
      {
        selected_memory_ids: selectedBirthdayMemoryIds.value,
        tone: birthdayGreetingTone.value
      }
    );
    birthdayGreetingDraft.value = response.data.draft;
  } catch (error) {
    ElMessage.error("生日祝福生成失败，请检查已选记忆");
  } finally {
    birthdayGreetingDraftLoading.value = false;
  }
}

function formatBirthdayMemory(
  memory: BirthdayGreetingContext["memories"][number]
) {
  return `${memory.year}年${memory.month}月 · ${memory.title}`;
}

function changeBirthdayMemorySelection(ids: string[]) {
  if (ids.length <= 4) return;
  selectedBirthdayMemoryIds.value = ids.slice(0, 4);
  ElMessage.warning("最多选择 4 条共同记忆");
}

async function copyBirthdayGreeting() {
  if (!birthdayGreetingDraft.value) return;
  await navigator.clipboard.writeText(birthdayGreetingDraft.value);
  ElMessage.success("祝福已复制，可人工确认后使用");
}

const percentLabel = (value?: number | null) =>
  value === null || value === undefined
    ? "未接入"
    : `${(value * 100).toFixed(1)}%`;

async function openClassOperations(row: unknown) {
  const classOrgUnitId = String(
    (row as { class_org_unit_id?: string })?.class_org_unit_id || ""
  );
  if (!classOrgUnitId) return;
  classDrawerVisible.value = true;
  classDetailLoading.value = true;
  try {
    const response = await getClassOperations(classOrgUnitId, {
      year: year.value,
      month: month.value
    });
    classDetail.value = response.data;
    classForm.value = {
      weekly_meeting_at: response.data.weekly_meeting_at || "",
      planned_class_meeting_at: response.data.planned_class_meeting_at || "",
      learning_month: response.data.learning_month ?? undefined,
      learning_progress: response.data.learning_progress || "",
      revenue_growing_member_count:
        response.data.revenue_growing_member_count ?? undefined,
      revenue_comparable_member_count:
        response.data.revenue_comparable_member_count ?? undefined,
      groups: response.data.groups.map(group => ({
        group_org_unit_id: group.id,
        name: group.name,
        planned_meeting_at: group.planned_meeting_at || ""
      }))
    };
  } finally {
    classDetailLoading.value = false;
  }
}

async function saveClassOperations() {
  if (!classDetail.value) return;
  classSaving.value = true;
  try {
    const response = await updateClassOperations(
      classDetail.value.class_org_unit_id,
      { year: year.value, month: month.value },
      {
        ...classForm.value,
        weekly_meeting_at: classForm.value.weekly_meeting_at || null,
        planned_class_meeting_at:
          classForm.value.planned_class_meeting_at || null,
        learning_progress: classForm.value.learning_progress || null,
        groups: classForm.value.groups.map(group => ({
          group_org_unit_id: group.group_org_unit_id,
          planned_meeting_at: group.planned_meeting_at || null
        }))
      }
    );
    classDetail.value = response.data;
    await load();
    ElMessage.success("班级运营事项已保存，驾驶舱数据已刷新");
  } catch (error) {
    ElMessage.error("班级运营事项保存失败，请稍后重试");
  } finally {
    classSaving.value = false;
  }
}

const formatValue = (
  value: number | string | null | undefined,
  unit?: string
) => {
  const numeric = toNumber(value);
  if (numeric === null) return "—";
  if (unit === "PERCENT") return `${(numeric * 100).toFixed(1)}%`;
  if (unit === "PERSON") return `${Math.round(numeric)} 人`;
  if (unit === "SCORE") return `${numeric.toFixed(1)} 分`;
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2);
};
const formatAchievement = (value: number | string | null | undefined) => {
  const numeric = toNumber(value);
  return numeric === null ? "—" : `${(numeric * 100).toFixed(1)}%`;
};
const annualAchievement = (row: any) => {
  const actual = toNumber(row.actual?.value);
  const annualTarget = toNumber(row.annual_target);
  if (actual === null || annualTarget === null || annualTarget === 0) {
    return null;
  }
  return actual / annualTarget;
};
const unitLabel = (unit?: string) =>
  ({ PERCENT: "百分比", PERSON: "人数", SCORE: "分数" })[unit ?? ""] ?? "数值";
const renewalTimingStages = [
  "PREPARE",
  "OBSERVE_3",
  "RENEW_2",
  "FOLLOW_1",
  "DUE_NOW",
  "RECOVERY"
] as const;
const renewalTimingFallbackLabels: Record<
  (typeof renewalTimingStages)[number],
  string
> = {
  PREPARE: "观3之前",
  OBSERVE_3: "观3",
  RENEW_2: "续2",
  FOLLOW_1: "追1",
  DUE_NOW: "到期月",
  RECOVERY: "到期后"
};
const renewalEvidenceLabels: Record<string, string> = {
  LIVE_STATUS_TRANSITION: "人工状态变更（可信）",
  IMPORT_SNAPSHOT: "正式导入快照",
  HISTORICAL_AUTO_RECONCILIATION: "历史自动补录",
  UNKNOWN: "来源未知"
};
const renewalStatusLabels: Record<string, string> = {
  RENEWED: "已续费",
  NOT_RENEWING: "明确不续",
  EXITED: "已退出",
  DEFERRED: "延期",
  PENDING_FIRST_CONTACT: "待首次联系",
  CONTACTED_WAITING_REPLY: "等待回复",
  IN_COMMUNICATION: "沟通中"
};
const renewalRateLabel = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
const renewalStageLabel = (analytics: RenewalAnnualAnalytics, stage: string) =>
  analytics.timing_distribution.stage_labels[stage] ||
  renewalTimingFallbackLabels[stage as (typeof renewalTimingStages)[number]] ||
  stage;

async function load() {
  loading.value = true;
  memberCareError.value = false;
  memberCareManagementError.value = false;
  renewalAnnualAnalyticsError.value = false;
  try {
    const [
      snapshot,
      dashboard,
      variance,
      rhythmSnapshot,
      careActions,
      careManagement,
      renewalAnalytics
    ] = await Promise.allSettled([
      getOperationsSnapshot({
        year: year.value,
        month: month.value,
        birthday_month:
          birthdayMonth.value === "ALL" ? 0 : Number(birthdayMonth.value)
      }),
      planId.value
        ? getMpDashboard({ plan_id: planId.value, month: month.value })
        : Promise.resolve(null),
      planId.value ? getTargetVariances(planId.value) : Promise.resolve(null),
      getOperationRhythmSnapshot({ year: year.value, month: month.value }),
      getMemberCareActionsToday(),
      getMemberCareManagementOverview(),
      getRenewalAnnualAnalytics(year.value)
    ]);

    if (snapshot.status === "fulfilled") {
      operations.value = snapshot.value.data;
      if (
        birthdayCenterId.value &&
        !snapshot.value.data.birthday_members.some(
          item => item.org_unit_id === birthdayCenterId.value
        )
      ) {
        birthdayCenterId.value = "";
      }
      if (
        birthdayClassOrgUnitId.value &&
        !snapshot.value.data.birthday_members.some(
          item =>
            item.class_org_unit_id === birthdayClassOrgUnitId.value &&
            (!birthdayCenterId.value ||
              item.org_unit_id === birthdayCenterId.value)
        )
      ) {
        birthdayClassOrgUnitId.value = "";
      }
    } else {
      ElMessage.error("本月学员与生日数据加载失败，请稍后重试");
    }

    if (rhythmSnapshot.status === "fulfilled") {
      rhythm.value = rhythmSnapshot.value.data;
    } else {
      ElMessage.warning("运营节奏暂时加载失败，生日和学员数据仍可查看");
    }

    if (dashboard.status === "fulfilled") {
      items.value = dashboard.value?.data.items || [];
      if (
        !items.value.some(item => item.metric_key === selectedMetricKey.value)
      ) {
        selectedMetricKey.value = items.value[0]?.metric_key ?? "";
      }
    } else if (planId.value) {
      ElMessage.warning("年度 MP 数据暂时加载失败，其他运营数据仍可查看");
    }

    if (variance.status === "fulfilled") {
      variances.value = variance.value?.data || [];
    }

    if (careActions.status === "fulfilled") {
      memberCare.value = careActions.value.data;
    } else {
      memberCare.value = undefined;
      memberCareError.value = true;
    }

    if (careManagement.status === "fulfilled") {
      memberCareManagement.value = careManagement.value.data;
    } else {
      memberCareManagement.value = undefined;
      memberCareManagementError.value = true;
    }
    if (renewalAnalytics.status === "fulfilled") {
      renewalAnnualAnalytics.value = renewalAnalytics.value.data;
    } else {
      renewalAnnualAnalytics.value = undefined;
      renewalAnnualAnalyticsError.value = true;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    const response = await getAnnualPlans();
    plans.value = response.data;
    planId.value = plans.value[0]?.id;
  } catch {
    ElMessage.warning("年度方案暂时加载失败，本月运营数据仍可查看");
  }
  await load();
});

function changePlan() {
  load();
}
</script>

<template>
  <div v-loading="loading" class="page-shell">
    <section class="hero">
      <div>
        <p class="eyebrow">月度实况 · 组织盘面 · 服务节奏</p>
        <h1>运营驾驶舱</h1>
        <p class="subtitle">
          先看当月续费、新增、在册、生日与活动排期，再下钻年度 MP
          目标差距；所有数字来自统一平台数据库。
        </p>
      </div>
      <div class="filters">
        <el-select v-model="year" aria-label="运营年份" @change="load">
          <el-option
            v-for="option in yearOptions"
            :key="option"
            :label="`${option} 年`"
            :value="option"
          />
        </el-select>
        <el-select
          v-model="month"
          aria-label="月份"
          @change="changeOperationsMonth"
        >
          <el-option
            v-for="value in 12"
            :key="value"
            :label="`${value}月`"
            :value="value"
          />
        </el-select>
      </div>
    </section>

    <section class="content-card care-center-card">
      <div class="section-title care-center-heading">
        <div>
          <p class="eyebrow dark">MEMBER CARE CENTER</p>
          <h2>今日关爱</h2>
          <p>以学长为中心合并续费、日常关怀/走访和生日行动。</p>
        </div>
        <div class="care-center-heading-actions">
          <strong v-if="memberCare" class="care-center-count">
            今天有 {{ memberCare.summary.people_total }} 位学长值得关注
          </strong>
          <el-button link type="primary" @click="load"> 刷新 </el-button>
        </div>
      </div>
      <el-alert
        v-if="memberCareError"
        title="今日关爱暂时不可用"
        description="当前账号没有可见的关爱来源，或聚合接口暂时不可用；不会根据不完整数据臆造行动。"
        type="info"
        :closable="false"
        show-icon
        class="care-center-alert"
      />
      <template v-else-if="memberCare">
        <div class="care-center-summary">
          <span class="overdue"
            >🔴 逾期 <b>{{ memberCare.summary.overdue_people_count }}</b></span
          >
          <span class="today"
            >🔵 今天 <b>{{ memberCare.summary.today_people_count }}</b></span
          >
          <span class="attention"
            >🟠 需要协助
            <b>{{ memberCare.summary.attention_people_count }}</b></span
          >
          <span class="birthday"
            >🎂 生日关怀
            <b>{{ memberCare.summary.birthday_people_count }}</b></span
          >
          <span class="renewal"
            >♻️ 续费关爱
            <b>{{ memberCare.summary.renewal_people_count }}</b></span
          >
          <span class="followup"
            >🤝 日常关怀/走访
            <b>{{ memberCare.summary.followup_people_count }}</b></span
          >
        </div>
        <el-table
          :data="memberCare.people"
          stripe
          empty-text="今天暂无确定性学长关爱行动"
          class="care-center-table"
        >
          <el-table-column prop="member_name" label="学长" min-width="130" />
          <el-table-column label="归属" min-width="190">
            <template #default="{ row }">
              {{ row.org_name }}
              <span v-if="row.class_name" class="muted-inline">
                · {{ row.class_name }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="今天为什么出现" min-width="330">
            <template #default="{ row }">
              <div class="care-action-tags">
                <el-tag
                  v-for="action in row.actions"
                  :key="`${action.source}-${action.source_id}`"
                  :type="careUrgencyType(action.urgency)"
                  effect="light"
                >
                  {{ action.label }}
                </el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="先处理" min-width="150">
            <template #default="{ row }">
              <el-tag :type="careUrgencyType(row.primary_action.urgency)">
                {{ careUrgencyLabel(row.primary_action.urgency) }}
              </el-tag>
              <span class="muted-inline">
                · {{ careActionTypeLabel(row.primary_action) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="责任人" min-width="120">
            <template #default="{ row }">
              {{ row.primary_action.assigned_user_name || "按现有流程" }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openCarePerson(row)">
                查看关爱
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </section>

    <section class="content-card management-card">
      <div class="section-title management-heading">
        <div>
          <p class="eyebrow dark">CARE OPERATIONS HEALTH</p>
          <h2>关爱运营健康</h2>
          <p>
            只看逾期、未闭环、责任人和下一时间等确定性支持需求，不做员工或分中心排名。
          </p>
        </div>
        <el-button link type="primary" @click="load">刷新健康看板</el-button>
      </div>
      <el-alert
        v-if="memberCareManagementError"
        title="关爱运营健康暂时不可用"
        description="当前账号没有可见的关爱来源，或管理聚合接口暂时不可用；不会把未授权来源显示为 0。"
        type="info"
        :closable="false"
        show-icon
        class="care-center-alert"
      />
      <template v-else-if="memberCareManagement">
        <div class="management-summary-grid">
          <article>
            <span>今日关爱人数</span>
            <strong>{{
              memberCareManagement.summary.today_care_people_count
            }}</strong>
          </article>
          <article class="danger">
            <span>逾期未处理人数</span>
            <strong>{{
              memberCareManagement.summary.overdue_people_count
            }}</strong>
            <small
              >最早逾期
              {{ memberCareManagement.summary.oldest_overdue_days }} 天</small
            >
          </article>
          <article class="warning">
            <span>需要协助</span>
            <strong>{{
              managementCountLabel(
                memberCareManagement.summary.renewal_support_needed_count
              )
            }}</strong>
          </article>
          <article>
            <span>续费挽回未闭环</span>
            <strong>{{
              managementCountLabel(
                memberCareManagement.summary.renewal_recovery_open_count
              )
            }}</strong>
          </article>
          <article>
            <span>责任人待分配</span>
            <strong>{{
              managementCountLabel(
                memberCareManagement.summary.renewal_unassigned_count
              )
            }}</strong>
          </article>
          <article>
            <span>无下一时间</span>
            <strong>{{
              managementCountLabel(
                memberCareManagement.summary.followup_no_schedule_count
              )
            }}</strong>
          </article>
        </div>
        <div class="management-coverage">
          <span>当前数据覆盖：</span>
          <el-tag
            :type="
              memberCareManagement.source_coverage.renewal.accessible
                ? 'success'
                : 'info'
            "
            effect="plain"
          >
            {{
              memberCareManagement.source_coverage.renewal.accessible
                ? "✓ 续费关爱"
                : "— 续费关爱（无权限）"
            }}
          </el-tag>
          <el-tag
            :type="
              memberCareManagement.source_coverage.birthday.accessible
                ? 'success'
                : 'info'
            "
            effect="plain"
          >
            {{
              memberCareManagement.source_coverage.birthday.accessible
                ? "✓ 生日关怀"
                : "— 生日关怀（无权限）"
            }}
          </el-tag>
          <el-tag
            :type="
              memberCareManagement.source_coverage.followup.accessible
                ? 'success'
                : 'info'
            "
            effect="plain"
          >
            {{
              memberCareManagement.source_coverage.followup.accessible
                ? "✓ 普通关怀/走访"
                : "— 普通关怀/走访（无权限）"
            }}
          </el-tag>
        </div>

        <h3 class="management-subheading">各分中心当前需要支持的事项</h3>
        <el-table
          :data="memberCareManagement.organizations"
          stripe
          empty-text="当前覆盖范围内暂无管理异常"
          class="management-org-table"
        >
          <el-table-column prop="org_name" label="分中心" min-width="150" />
          <el-table-column
            prop="today_care_people_count"
            label="今日关爱"
            width="95"
          />
          <el-table-column
            prop="overdue_people_count"
            label="已逾期"
            width="85"
          />
          <el-table-column label="最早逾期" width="100">
            <template #default="{ row }">
              {{
                row.oldest_overdue_days ? `${row.oldest_overdue_days}天` : "—"
              }}
            </template>
          </el-table-column>
          <el-table-column label="需要协助" width="100">
            <template #default="{ row }">{{
              managementCountLabel(row.renewal_support_needed_count)
            }}</template>
          </el-table-column>
          <el-table-column label="阶段未触达" width="105">
            <template #default="{ row }">{{
              managementCountLabel(row.renewal_stage_untouched_count)
            }}</template>
          </el-table-column>
          <el-table-column label="续费挽回" width="100">
            <template #default="{ row }">{{
              managementCountLabel(row.renewal_recovery_open_count)
            }}</template>
          </el-table-column>
          <el-table-column label="待分责任人" width="110">
            <template #default="{ row }">{{
              managementCountLabel(row.renewal_unassigned_count)
            }}</template>
          </el-table-column>
          <el-table-column label="无下一时间" width="110">
            <template #default="{ row }">{{
              managementCountLabel(row.followup_no_schedule_count)
            }}</template>
          </el-table-column>
          <el-table-column label="逾期来源" min-width="270">
            <template #default="{ row }">
              <span class="management-breakdown">
                续费 {{ managementCountLabel(row.renewal_overdue_count) }} ·
                关怀 {{ managementCountLabel(row.followup_overdue_count) }} ·
                走访
                {{ managementCountLabel(row.enterprise_visit_overdue_count) }}
                · 生日 {{ managementCountLabel(row.birthday_overdue_count) }}
              </span>
            </template>
          </el-table-column>
        </el-table>

        <h3 class="management-subheading">需要管理支持的事项</h3>
        <el-table
          :data="memberCareManagement.exceptions"
          stripe
          empty-text="当前没有确定性管理异常"
          class="management-exception-table"
        >
          <el-table-column label="异常" min-width="170">
            <template #default="{ row }">
              <el-tag :type="managementExceptionType(row.exception_type)">
                {{ managementExceptionLabel(row.exception_type) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="org_name" label="分中心" min-width="140" />
          <el-table-column label="学长" min-width="120">
            <template #default="{ row }">{{ row.member_name || "—" }}</template>
          </el-table-column>
          <el-table-column label="来源" width="105">
            <template #default="{ row }">{{
              managementSourceLabel(row.source)
            }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="事实依据" min-width="300" />
          <el-table-column label="逾期" width="80">
            <template #default="{ row }">
              {{ row.days_overdue ? `${row.days_overdue}天` : "—" }}
            </template>
          </el-table-column>
          <el-table-column label="责任人" width="120">
            <template #default="{ row }">{{
              row.assigned_user_name || "待分配"
            }}</template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="navigateManagementException(row)"
              >
                去处理
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </section>

    <section class="content-card annual-renewal-card">
      <div class="section-title management-heading">
        <div>
          <p class="eyebrow dark">ANNUAL RENEWAL INSIGHT</p>
          <h2>年度续费洞察</h2>
          <p>
            年度结果反映当前记录状态；续费节奏只使用可证明完成时点的样本，不做阶段漏斗或分中心排名。
          </p>
        </div>
        <el-button link type="primary" @click="load">刷新年度洞察</el-button>
      </div>
      <el-alert
        v-if="renewalAnnualAnalyticsError"
        title="年度续费洞察暂时不可用"
        description="当前账号没有续费读取权限，或年度分析接口暂时不可用。"
        type="info"
        :closable="false"
        show-icon
      />
      <template v-else-if="renewalAnnualAnalytics">
        <div class="annual-renewal-summary-grid">
          <article>
            <span>年度周期</span>
            <strong>{{ renewalAnnualAnalytics.total_cycles }}</strong>
          </article>
          <article class="success">
            <span>已续费</span>
            <strong>{{ renewalAnnualAnalytics.renewed_count }}</strong>
          </article>
          <article>
            <span>明确不续</span>
            <strong>{{ renewalAnnualAnalytics.not_renewing_count }}</strong>
          </article>
          <article>
            <span>已退出</span>
            <strong>{{ renewalAnnualAnalytics.exited_count }}</strong>
          </article>
          <article class="warning">
            <span>延期</span>
            <strong>{{ renewalAnnualAnalytics.deferred_count }}</strong>
          </article>
          <article>
            <span>推进中</span>
            <strong>{{ renewalAnnualAnalytics.open_count }}</strong>
          </article>
        </div>

        <div class="annual-renewal-quality">
          <div>
            <h3>数据可信度</h3>
            <p>
              已续费
              {{ renewalAnnualAnalytics.completion_quality.renewed_count }} 人；
              有可信完成时点
              {{
                renewalAnnualAnalytics.completion_quality
                  .reliable_completion_count
              }}
              人； 历史/导入时点不可用于节奏分析
              {{
                renewalAnnualAnalytics.completion_quality
                  .unreliable_completion_count
              }}
              人。
            </p>
            <p class="muted-inline">
              续费节奏分析仅基于具有可信完成时点的已续费周期。
            </p>
          </div>
          <div class="annual-renewal-evidence-tags">
            <el-tag
              v-for="evidence in Object.keys(renewalEvidenceLabels)"
              :key="evidence"
              effect="plain"
            >
              {{ renewalEvidenceLabels[evidence] }}
              {{
                renewalAnnualAnalytics.completion_quality.evidence_counts[
                  evidence
                ] || 0
              }}
            </el-tag>
          </div>
        </div>

        <div class="annual-renewal-timing">
          <div class="annual-renewal-timing-heading">
            <div>
              <h3>可信样本的续费节奏</h3>
              <p>
                以下续费节奏仅统计具有可信完成时点的已续费周期，不代表全部已续费学长。
              </p>
            </div>
            <el-tag type="success" effect="plain">
              到期前完成比例
              {{
                renewalRateLabel(
                  renewalAnnualAnalytics.before_due_rate_among_reliable_renewals
                )
              }}
            </el-tag>
          </div>
          <div class="annual-renewal-stage-grid">
            <article v-for="stage in renewalTimingStages" :key="stage">
              <span>{{
                renewalStageLabel(renewalAnnualAnalytics, stage)
              }}</span>
              <strong>{{
                renewalAnnualAnalytics.stage_counts[stage] || 0
              }}</strong>
            </article>
          </div>
          <p class="muted-inline">
            到期前 {{ renewalAnnualAnalytics.before_due_count }} 人 · 到期月
            {{ renewalAnnualAnalytics.due_month_count }} 人 · 到期后
            {{ renewalAnnualAnalytics.after_due_count }} 人
          </p>
        </div>

        <h3 class="management-subheading">各分中心续费结果与数据覆盖</h3>
        <el-table
          :data="renewalAnnualAnalytics.organizations"
          stripe
          empty-text="当前覆盖范围内暂无年度续费周期"
          class="annual-renewal-org-table"
        >
          <el-table-column prop="org_name" label="分中心" min-width="150" />
          <el-table-column prop="total_cycles" label="年度周期" width="90" />
          <el-table-column prop="renewed_count" label="已续费" width="85" />
          <el-table-column
            prop="not_renewing_count"
            label="明确不续"
            width="100"
          />
          <el-table-column prop="exited_count" label="已退出" width="85" />
          <el-table-column prop="deferred_count" label="延期" width="75" />
          <el-table-column prop="open_count" label="推进中" width="85" />
          <el-table-column label="可信/不可用" width="120">
            <template #default="{ row }">
              {{ row.reliable_completion_count }} /
              {{ row.unreliable_completion_count }}
            </template>
          </el-table-column>
          <el-table-column label="到期前/当月/到期后" min-width="160">
            <template #default="{ row }">
              {{ row.before_due_count }} / {{ row.due_month_count }} /
              {{ row.after_due_count }}
            </template>
          </el-table-column>
          <el-table-column label="可信样本到期前比例" min-width="145">
            <template #default="{ row }">
              {{
                renewalRateLabel(row.before_due_rate_among_reliable_renewals)
              }}
            </template>
          </el-table-column>
        </el-table>
      </template>
    </section>

    <section class="section-heading">
      <div>
        <p class="eyebrow dark">MONTHLY OPERATIONS</p>
        <h2>{{ year }} 年 {{ month }} 月运营实况</h2>
      </div>
      <span>在册为当前快照；新增、续费和排期按所选月份统计</span>
    </section>

    <section class="operations-grid">
      <article
        v-for="card in operationsCards"
        :key="card.label"
        class="operations-card"
        :class="{ unavailable: card.value === null }"
      >
        <span>{{ card.label }}</span>
        <strong v-if="card.value !== null"
          >{{ card.value }}<small>{{ card.unit }}</small></strong
        >
        <strong v-else class="not-ready">未接入</strong>
        <p>{{ card.note }}</p>
      </article>
    </section>

    <el-alert
      v-if="operations?.data_quality.missing_join_date_count"
      :title="`${operations.data_quality.missing_join_date_count} 位在册学长缺少入塾日期，未计入本月新增`"
      type="warning"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <el-alert
      v-if="operations?.data_quality.unscheduled_class_count"
      :title="`${operations.data_quality.unscheduled_class_count} 个班级本月尚未接入班会排期`"
      description="驾驶舱会保留这些班级并显示“待排期”，不会把缺少排期误报为已召开 0 次。"
      type="info"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <el-alert
      v-if="operations?.data_quality.unlinked_class_meeting_count"
      :title="`${operations.data_quality.unlinked_class_meeting_count} 场班会尚未关联正式班级`"
      description="这些班会计入本月总次数并保留在日历中，但不会据活动名称自动猜测班级。"
      type="warning"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <el-alert
      v-if="operations?.data_quality.duplicate_class_node_count"
      :title="`${operations.data_quality.duplicate_class_node_count} 个历史班级重复节点已按名称合并展示`"
      description="不会重复计入班会或待排期；系统已阻止继续创建同名班级，历史节点仅在完成受控归并后才会停用。"
      type="warning"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <el-alert
      v-if="operations?.data_quality.invalid_direct_root_class_count"
      :title="`${operations.data_quality.invalid_direct_root_class_count} 个历史班级节点不符合苏州塾直属四班规则，已从驾驶舱排除`"
      description="苏州塾直属仅保留先锋班、神仙班、黄埔一班和黄埔二班；其他班级按各自分中心的正式节点运营。"
      type="warning"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <section class="content-card rhythm-card">
      <div class="section-title rhythm-heading">
        <div>
          <p class="eyebrow dark">OPERATION RHYTHM</p>
          <h2>本月运营节奏</h2>
          <p>
            由核心运营人员维护；微信群、电话和线下沟通继续保留，班主任无需登录。
          </p>
        </div>
        <el-button
          v-if="canManageRhythm"
          type="primary"
          :loading="rhythmGenerating"
          @click="generateRhythm"
        >
          生成/刷新本月事项
        </el-button>
      </div>

      <el-alert
        v-for="note in rhythm?.data_quality.notes || []"
        :key="note"
        :title="note"
        type="info"
        :closable="false"
        show-icon
        class="data-alert"
      />

      <div class="rhythm-summary">
        <article>
          <span>本月事项</span><strong>{{ rhythm?.summary.total || 0 }}</strong>
        </article>
        <article>
          <span>今日运营</span
          ><strong>{{ rhythm?.summary.today_count || 0 }}</strong>
        </article>
        <article>
          <span>未来 7 天</span
          ><strong>{{ rhythm?.summary.next_7_days_count || 0 }}</strong>
        </article>
        <article class="attention">
          <span>需关注</span
          ><strong>{{ rhythm?.summary.attention_count || 0 }}</strong>
        </article>
      </div>

      <div class="rhythm-toolbar">
        <el-radio-group v-model="rhythmView" size="small">
          <el-radio-button label="today">今日运营</el-radio-button>
          <el-radio-button label="next_7_days">未来 7 天</el-radio-button>
          <el-radio-button label="month">本月运营</el-radio-button>
          <el-radio-button label="attention">异常中心</el-radio-button>
        </el-radio-group>
        <div class="rhythm-filters">
          <el-select
            v-model="rhythmOrganizationId"
            clearable
            filterable
            size="small"
            placeholder="全部组织"
            aria-label="运营节奏组织筛选"
            @change="changeRhythmOrganization"
          >
            <el-option
              v-for="option in rhythmOrganizationOptions"
              :key="option.id"
              :label="option.name"
              :value="option.id"
            />
          </el-select>
          <el-select
            v-model="rhythmClassOrgUnitId"
            clearable
            filterable
            size="small"
            placeholder="全部班级"
            aria-label="运营节奏班级筛选"
          >
            <el-option
              v-for="option in rhythmClassOptions"
              :key="option.id"
              :label="option.name"
              :value="option.id"
            />
          </el-select>
          <el-select
            v-model="rhythmStatus"
            clearable
            size="small"
            placeholder="全部状态"
            aria-label="运营节奏状态筛选"
          >
            <el-option label="待确认" value="PENDING" />
            <el-option label="已计划" value="PLANNED" />
            <el-option label="推进中" value="IN_PROGRESS" />
            <el-option label="等待外部反馈" value="WAITING_EXTERNAL" />
            <el-option label="已圆满" value="COMPLETED" />
            <el-option label="需关注" value="ATTENTION" />
            <el-option label="已取消" value="CANCELLED" />
          </el-select>
        </div>
      </div>

      <el-table
        :data="rhythmItems"
        stripe
        size="small"
        empty-text="当前视图暂无运营事项"
      >
        <el-table-column label="日期" width="130">
          <template #default="{ row }">
            {{ row.due_date || "待确认" }}
            <small
              v-if="row.item_key === 'CLASS_MEETING'"
              class="rhythm-source-note"
            >
              来自班级服务日历
            </small>
          </template>
        </el-table-column>
        <el-table-column label="事项" min-width="230">
          <template #default="{ row }">
            <el-button
              v-if="row.business_type === 'BIRTHDAY_CARE'"
              link
              type="primary"
              @click="openRhythmBusinessItem(row)"
            >
              {{ row.title }}
            </el-button>
            <span v-else>{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="org_name" label="组织" min-width="140" />
        <el-table-column prop="category" label="类型" width="120" />
        <el-table-column label="责任角色" min-width="150">
          <template #default="{ row }">
            {{ row.responsibility_role || "待确认" }}
            <small
              v-if="row.external_responsibility_role"
              class="rhythm-external-role"
            >
              外部：{{ row.external_responsibility_role }}
            </small>
          </template>
        </el-table-column>
        <el-table-column label="维护" width="82" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="canManageRhythm && row.item_key !== 'CLASS_MEETING'"
              link
              type="primary"
              @click="openRhythmEdit(row)"
            >
              编辑
            </el-button>
            <span
              v-else-if="row.item_key === 'CLASS_MEETING'"
              class="rhythm-source-note"
            >
              日历维护
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-select
              v-if="canManageRhythm"
              :model-value="row.status"
              size="small"
              :loading="rhythmItemSaving === row.id"
              @update:model-value="
                (value: OperationRhythmStatus) => saveRhythmStatus(row, value)
              "
            >
              <el-option label="待确认" value="PENDING" />
              <el-option label="已计划" value="PLANNED" />
              <el-option label="推进中" value="IN_PROGRESS" />
              <el-option label="等待外部反馈" value="WAITING_EXTERNAL" />
              <el-option label="已圆满" value="COMPLETED" />
              <el-option label="需关注" value="ATTENTION" />
              <el-option label="已取消" value="CANCELLED" />
            </el-select>
            <el-tag v-else :type="rhythmStatusType(row.status)">
              {{ rhythmStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <el-dialog
        v-model="rhythmEditVisible"
        title="维护运营事项"
        width="520px"
        destroy-on-close
      >
        <el-form label-width="88px" @submit.prevent>
          <el-form-item label="事项名称" required>
            <el-input
              v-model="rhythmEditForm.title"
              maxlength="255"
              show-word-limit
            />
          </el-form-item>
          <el-form-item label="开始日期">
            <el-date-picker
              v-model="rhythmEditForm.start_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="可不填"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="截止日期">
            <el-date-picker
              v-model="rhythmEditForm.due_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="可不填"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="完成备注">
            <el-input
              v-model="rhythmEditForm.note"
              type="textarea"
              :rows="3"
              maxlength="2000"
              show-word-limit
              placeholder="可记录关怀方式、核对结果或后续说明"
            />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="rhythmEditVisible = false">取消</el-button>
          <el-button
            type="primary"
            :loading="rhythmEditSaving"
            @click="saveRhythmEdit"
          >
            保存维护
          </el-button>
        </template>
      </el-dialog>
    </section>

    <section class="operations-panels">
      <article class="content-card">
        <div class="section-title birthday-title">
          <h2>各分中心当前在册</h2>
          <p>
            按学员管理主档所属分中心统计；直属学习班保留独立口径，不并入六个分中心。
          </p>
        </div>
        <div class="center-list">
          <div v-for="center in operations?.centers || []" :key="center.id">
            <span>{{ center.name }}</span>
            <strong>{{ center.active_member_count }} 人</strong>
          </div>
        </div>
      </article>

      <article class="content-card">
        <div class="section-title">
          <div>
            <h2>本月生日关怀</h2>
            <p>仅展示生日月日，不展示出生年份及其他敏感资料。</p>
          </div>
          <div class="birthday-filters">
            <el-select
              v-model="birthdayCenterId"
              clearable
              aria-label="生日关怀分中心"
              placeholder="全部分中心"
              @change="changeBirthdayCenter"
            >
              <el-option
                v-for="option in birthdayCenterOptions"
                :key="option.id"
                :label="option.name"
                :value="option.id"
              />
            </el-select>
            <el-select
              v-model="birthdayMonth"
              aria-label="生日关怀月份"
              placeholder="生日月份"
              class="birthday-month-filter"
              @change="load"
            >
              <el-option
                v-for="option in birthdayMonthOptions"
                :key="option.id"
                :label="option.name"
                :value="option.id"
              />
            </el-select>
            <el-select
              v-model="birthdayClassOrgUnitId"
              clearable
              aria-label="生日关怀班级"
              placeholder="全部班级"
            >
              <el-option
                v-for="option in birthdayClassOptions"
                :key="option.id"
                :label="option.name"
                :value="option.id"
              />
            </el-select>
          </div>
        </div>
        <el-table
          :data="filteredBirthdayMembers"
          size="small"
          max-height="300"
          empty-text="本月暂无在册学长生日"
        >
          <el-table-column prop="birthday" label="日期" width="86" />
          <el-table-column label="学长" min-width="100">
            <template #default="{ row }">
              <el-button link type="primary" @click="openBirthdayGreeting(row)">
                {{ row.name }}
              </el-button>
            </template>
          </el-table-column>
          <el-table-column prop="org_name" label="分中心" min-width="130" />
          <el-table-column label="班级" min-width="120">
            <template #default="{ row }">{{
              row.class_name || "未分班"
            }}</template>
          </el-table-column>
        </el-table>
      </article>
    </section>

    <section class="content-card schedule-card">
      <div class="section-title">
        <h2>班级运营与本月服务日历</h2>
        <p>
          按班级组织自身的运营归属列出正式班级；不会根据班内学长的发展分中心改变班级归属。
        </p>
      </div>
      <el-table :data="classRows" stripe empty-text="当前授权范围暂无正式班级">
        <el-table-column label="班级" min-width="150">
          <template #default="{ row }">
            <el-button link type="primary" @click="openClassOperations(row)">
              {{ row.class_name }}
            </el-button>
          </template>
        </el-table-column>
        <el-table-column prop="org_name" label="班级运营归属" min-width="150" />
        <el-table-column label="本月班会" width="130">
          <template #default="{ row }">
            {{
              row.class_meeting_at
                ? dayjs(row.class_meeting_at).format("MM 月 DD 日")
                : "待排期"
            }}
          </template>
        </el-table-column>
        <el-table-column label="班会次序" width="130">
          <template #default="{ row }">
            {{
              row.year_sequence
                ? `本年第 ${row.year_sequence} 次`
                : row.status === "PLANNED"
                  ? "待正式记录"
                  : "待维护"
            }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag
              :type="
                row.status === 'SCHEDULED'
                  ? 'success'
                  : row.status === 'PLANNED'
                    ? 'warning'
                    : 'info'
              "
            >
              {{
                row.status === "SCHEDULED"
                  ? "已接入事实"
                  : row.status === "PLANNED"
                    ? "已维护排期"
                    : "待排期"
              }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="运营分析" width="120">
          <template #default="{ row }">
            <el-button link type="primary" @click="openClassOperations(row)"
              >查看分析</el-button
            >
          </template>
        </el-table-column>
      </el-table>

      <template v-if="otherScheduleRows.length">
        <el-divider content-position="left">本月课程与其他活动</el-divider>
        <el-table :data="otherScheduleRows" stripe>
          <el-table-column label="日期" width="120">
            <template #default="{ row }">{{
              dayjs(row.event_date).format("MM 月 DD 日")
            }}</template>
          </el-table-column>
          <el-table-column prop="category" label="类型" width="90" />
          <el-table-column prop="org_name" label="组织" min-width="150" />
          <el-table-column prop="title" label="事项" min-width="240" />
        </el-table>
      </template>
    </section>

    <el-dialog
      v-model="memberCareDialogVisible"
      :title="
        selectedCarePerson
          ? `${selectedCarePerson.member_name}学长 · 今日关爱`
          : '今日关爱'
      "
      width="760px"
    >
      <template v-if="selectedCarePerson">
        <section class="care-person-profile">
          <strong>{{ selectedCarePerson.member_name }}学长</strong>
          <span>
            {{ selectedCarePerson.org_name }} ·
            {{ selectedCarePerson.class_name || "未分班" }}
            <template v-if="selectedCarePerson.group_name">
              · {{ selectedCarePerson.group_name }}
            </template>
          </span>
          <small>今天有 {{ selectedCarePerson.action_count }} 项值得关注</small>
        </section>
        <div class="care-person-actions">
          <article
            v-for="action in selectedCarePerson.actions"
            :key="`${action.source}-${action.source_id}`"
            class="care-person-action"
          >
            <div>
              <div class="care-person-action-head">
                <el-tag :type="careUrgencyType(action.urgency)">
                  {{ careUrgencyLabel(action.urgency) }}
                </el-tag>
                <strong>{{ action.label }}</strong>
              </div>
              <p>{{ action.reason }}</p>
              <small>
                {{ action.assigned_user_name || "按现有流程" }} ·
                {{ careDueDate(action.due_date) }}
              </small>
            </div>
            <el-button type="primary" plain @click="navigateCareAction(action)">
              {{ careNavigationLabel(action.navigation_type) }}
            </el-button>
          </article>
        </div>
      </template>
    </el-dialog>

    <el-drawer
      v-model="birthdayGreetingVisible"
      :title="
        birthdayGreeting
          ? `${birthdayGreeting.member.name}学长 · 生日关怀`
          : '生日关怀助手'
      "
      size="min(680px, 96vw)"
    >
      <div v-loading="birthdayGreetingLoading" class="birthday-greeting-drawer">
        <template v-if="birthdayGreeting">
          <section class="birthday-profile-card">
            <strong>{{ birthdayGreeting.member.name }}学长</strong>
            <span v-if="birthdayGreeting.member.birthday_month_day">
              🎂 {{ birthdayGreeting.member.birthday_month_day }} 生日
            </span>
            <span>
              {{
                birthdayGreeting.member.join_date
                  ? `${birthdayGreeting.member.join_date.slice(0, 7)} 入塾`
                  : "入塾日期待维护"
              }}
              <template
                v-if="
                  birthdayGreeting.member.membership_years !== null &&
                  birthdayGreeting.member.membership_years !== undefined
                "
              >
                · 已同行 {{ birthdayGreeting.member.membership_years }} 年
              </template>
            </span>
            <span>
              {{ birthdayGreeting.member.org_name }} ·
              {{ birthdayGreeting.member.class_name || "未分班"
              }}<template v-if="birthdayGreeting.member.group_name">
                · {{ birthdayGreeting.member.group_name }}</template
              >
            </span>
          </section>

          <el-alert
            v-for="note in birthdayGreeting.data_quality.notes"
            :key="note"
            :title="note"
            type="warning"
            :closable="false"
            show-icon
            class="birthday-note"
          />

          <section>
            <div class="birthday-drawer-heading">
              <div>
                <h3>我们的共同记忆</h3>
                <p>
                  只展示已核验的本人出席或完成记录，可勾选 0～4 条写入祝福。
                </p>
              </div>
              <el-tag type="success">事实资料</el-tag>
            </div>
            <el-checkbox-group
              v-model="selectedBirthdayMemoryIds"
              class="birthday-memory-list"
              @change="changeBirthdayMemorySelection"
            >
              <el-checkbox
                v-for="memory in birthdayGreeting.memories"
                :key="memory.id"
                :label="memory.id"
                class="birthday-memory"
              >
                <span>{{ formatBirthdayMemory(memory) }}</span>
                <small
                  >{{ memory.category_label }} ·
                  {{
                    memory.source_type === "ATTENDANCE"
                      ? "正式签到"
                      : "历史学习记录"
                  }}</small
                >
              </el-checkbox>
            </el-checkbox-group>
            <el-empty
              v-if="!birthdayGreeting.memories.length"
              description="暂无可核验的共同记忆"
              :image-size="60"
            />
          </section>

          <section class="birthday-draft-section">
            <div class="birthday-drawer-heading">
              <div>
                <h3>生日祝福</h3>
                <p>文案可以直接编辑；生成器只围绕上方事实，不补造经历。</p>
              </div>
              <div class="birthday-tone-actions">
                <el-button size="small" @click="generateBirthdayDraft('warm')"
                  >更温暖</el-button
                >
                <el-button
                  size="small"
                  @click="generateBirthdayDraft('concise')"
                  >更简洁</el-button
                >
              </div>
            </div>
            <div v-loading="birthdayGreetingDraftLoading">
              <el-input
                v-model="birthdayGreetingDraft"
                type="textarea"
                :rows="9"
                resize="vertical"
                placeholder="请选择共同记忆后生成祝福"
              />
            </div>
            <div class="birthday-draft-actions">
              <el-button
                :loading="birthdayGreetingDraftLoading"
                @click="generateBirthdayDraft()"
                >重新生成</el-button
              >
              <el-button
                type="primary"
                :disabled="!birthdayGreetingDraft"
                @click="copyBirthdayGreeting"
                >复制祝福</el-button
              >
            </div>
          </section>
        </template>
      </div>
    </el-drawer>

    <el-drawer
      v-model="classDrawerVisible"
      :title="
        classDetail
          ? `${classDetail.class_name} · 班级运营分析`
          : '班级运营分析'
      "
      size="min(760px, 96vw)"
    >
      <div v-loading="classDetailLoading" class="class-analysis">
        <template v-if="classDetail">
          <section class="analysis-grid">
            <article>
              <span>在册学长</span
              ><strong>{{ classDetail.active_member_count }} 人</strong>
            </article>
            <article>
              <span>经营者占比</span
              ><strong>{{
                percentLabel(classDetail.entrepreneur_ratio)
              }}</strong>
            </article>
            <article>
              <span>高管占比</span
              ><strong>{{ percentLabel(classDetail.executive_ratio) }}</strong>
            </article>
            <article>
              <span>业绩增长占比</span
              ><strong>{{
                classDetail.revenue_growth_authorized
                  ? percentLabel(classDetail.revenue_growth_ratio)
                  : "无权查看"
              }}</strong>
            </article>
            <article>
              <span>班会参会率</span
              ><strong>{{
                percentLabel(classDetail.class_attendance.rate)
              }}</strong>
            </article>
            <article>
              <span>学习月份</span
              ><strong>{{
                classDetail.learning_month
                  ? `第 ${classDetail.learning_month} 个月`
                  : "待维护"
              }}</strong>
            </article>
          </section>

          <el-alert
            :title="classDetail.position_classification_note"
            type="info"
            :closable="false"
            class="analysis-note"
          />

          <el-form label-position="top" class="operations-form">
            <div class="form-grid">
              <el-form-item label="周例会时间">
                <el-date-picker
                  v-model="classForm.weekly_meeting_at"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  placeholder="待维护"
                  :disabled="!canManageClassOperations"
                />
              </el-form-item>
              <el-form-item label="计划班会时间">
                <el-date-picker
                  v-model="classForm.planned_class_meeting_at"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  placeholder="待维护"
                  :disabled="!canManageClassOperations"
                />
              </el-form-item>
              <el-form-item label="班会学习第几个月">
                <el-input-number
                  v-model="classForm.learning_month"
                  :min="1"
                  :max="240"
                  :disabled="!canManageClassOperations"
                />
              </el-form-item>
              <el-form-item
                v-if="classDetail.revenue_growth_authorized"
                label="业绩增长人数 / 可比人数"
              >
                <div class="count-pair">
                  <el-input-number
                    v-model="classForm.revenue_growing_member_count"
                    :min="0"
                    :disabled="!canManageClassOperations"
                  />
                  <span>/</span>
                  <el-input-number
                    v-model="classForm.revenue_comparable_member_count"
                    :min="0"
                    :disabled="!canManageClassOperations"
                  />
                </div>
              </el-form-item>
            </div>
            <el-form-item label="学习进度到哪里">
              <el-input
                v-model="classForm.learning_progress"
                type="textarea"
                :rows="3"
                placeholder="例如：经营十二条第 4 条、课题进度与本月行动"
                :disabled="!canManageClassOperations"
              />
            </el-form-item>
          </el-form>

          <h3>本月班会</h3>
          <el-table
            :data="classMeetingRows"
            size="small"
            empty-text="本月尚未维护班会排期"
          >
            <el-table-column prop="event_date" label="日期" width="120" />
            <el-table-column prop="title" label="事项" min-width="220" />
          </el-table>
          <p
            v-if="
              classDetail.planned_class_meeting_at &&
              !classDetail.class_meetings.length
            "
            class="form-hint"
          >
            这是已维护的计划时间；班会次序仍需正式活动事实接入后确认，不会根据计划日期估算。
          </p>

          <h3>小组运营与参会率</h3>
          <el-table
            :data="classForm.groups"
            size="small"
            empty-text="当前班级暂无正式小组"
          >
            <el-table-column prop="name" label="小组" min-width="120" />
            <el-table-column label="小组会时间" min-width="220">
              <template #default="{ row }">
                <el-date-picker
                  v-model="row.planned_meeting_at"
                  type="datetime"
                  value-format="YYYY-MM-DD HH:mm:ss"
                  placeholder="待维护"
                  :disabled="!canManageClassOperations"
                />
              </template>
            </el-table-column>
            <el-table-column label="本月参会率" width="120">
              <template #default="{ row }">
                {{
                  percentLabel(
                    classDetail.groups.find(
                      group => group.id === row.group_org_unit_id
                    )?.attendance.rate
                  )
                }}
              </template>
            </el-table-column>
          </el-table>

          <div v-if="canManageClassOperations" class="drawer-actions">
            <el-button
              type="primary"
              :loading="classSaving"
              @click="saveClassOperations"
              >保存班级运营事项</el-button
            >
            <span v-if="classDetail.updated_at" class="save-status"
              >最近保存：{{
                dayjs(classDetail.updated_at).format("YYYY-MM-DD HH:mm")
              }}</span
            >
          </div>
        </template>
      </div>
    </el-drawer>

    <section class="section-heading mp-heading">
      <div>
        <p class="eyebrow dark">ANNUAL MP</p>
        <h2>年度 MP 目标追踪</h2>
      </div>
      <div class="mp-filters">
        <el-select
          v-model="planId"
          aria-label="年度方案"
          placeholder="选择年度方案"
          @change="changePlan"
        >
          <el-option
            v-for="plan in plans"
            :key="plan.id"
            :label="`${plan.year}年度 · V${plan.version}`"
            :value="plan.id"
          />
        </el-select>
        <el-select
          v-model="selectedMetricKey"
          aria-label="指标"
          placeholder="选择指标"
        >
          <el-option
            v-for="metric in metrics"
            :key="metric.key"
            :label="metric.name"
            :value="metric.key"
          />
        </el-select>
      </div>
    </section>

    <el-alert
      v-if="currentPlan && !currentPlan.write_enabled"
      title="当前为只读核对阶段"
      description="年度方案尚未取得业务批准，所有导入值可查看、可核对，但不能写入。"
      type="warning"
      :closable="false"
      show-icon
    />

    <section class="summary-grid">
      <article class="summary-card">
        <span>当前查看指标</span>
        <strong class="metric-name">{{
          selectedMetric?.name ?? "请选择指标"
        }}</strong>
        <small
          >{{ unitLabel(selectedMetric?.unit) }}口径，六分中心横向比较</small
        >
      </article>
      <article class="summary-card">
        <span>已填实绩中心</span>
        <strong>{{ actualCount }} / {{ centers.length }}</strong>
        <small>本月已有实绩的分中心数量</small>
      </article>
      <article class="summary-card">
        <span>平均预定达成</span>
        <strong>{{
          averageAchievement === null
            ? "—"
            : `${(averageAchievement * 100).toFixed(1)}%`
        }}</strong>
        <small>仅计算当前指标：实绩 ÷ 预定</small>
      </article>
      <article class="summary-card accent">
        <span>达到或超过预定</span>
        <strong>{{ reachedForecastCount }} 个</strong>
        <small>当前指标达成率不低于 100%</small>
      </article>
    </section>

    <el-alert
      v-if="selectedVariance && selectedVariance.difference !== 0"
      :title="`${selectedMetric?.name ?? '当前指标'}存在年度目标分解差额`"
      :description="`苏州塾总目标与六分中心${selectedVariance.aggregation === 'SUM' ? '合计' : '平均'}相差 ${formatValue(selectedVariance.difference, selectedMetric?.unit)}，该差额已保留，待业务说明。`"
      type="warning"
      :closable="false"
      show-icon
      class="variance-alert"
    />

    <section class="content-card">
      <div class="section-title">
        <h2>六分中心 · {{ selectedMetric?.name ?? "指标明细" }}</h2>
        <p>
          年目标是全年方向；月MP是本月基准；预定是本月预计完成值；实绩是实际完成值。
        </p>
      </div>
      <div class="metric-guide">
        <span><b>月MP</b>：月度目标基准</span>
        <span><b>预定</b>：预计本月完成</span>
        <span><b>实绩</b>：本月实际完成</span>
        <span><b>预定达成率</b>：实绩 ÷ 预定</span>
        <span><b>年度目标达成率</b>：当月实绩 ÷ 年度目标</span>
      </div>
      <el-table
        :data="selectedItems"
        stripe
        empty-text="当前月份暂无该指标数据"
      >
        <el-table-column prop="org_name" label="区域分中心" min-width="150" />
        <el-table-column label="年度目标" min-width="125" align="right">
          <template #default="{ row }">
            {{ formatValue(row.annual_target, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="月MP" min-width="115" align="right">
          <template #default="{ row }">
            {{ formatValue(row.mp?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="预定" min-width="115" align="right">
          <template #default="{ row }">
            {{ formatValue(row.forecast?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="实绩" min-width="115" align="right">
          <template #default="{ row }">
            {{ formatValue(row.actual?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="预定达成率" min-width="135" align="right">
          <template #default="{ row }">
            <el-tag
              v-if="toNumber(row.forecast_achievement) !== null"
              :type="
                toNumber(row.forecast_achievement)! >= 1 ? 'success' : 'warning'
              "
            >
              {{ formatAchievement(row.forecast_achievement) }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="年度目标达成率" min-width="155" align="right">
          <template #default="{ row }">
            <el-tag
              v-if="annualAchievement(row) !== null"
              :type="annualAchievement(row)! >= 1 ? 'success' : 'info'"
            >
              {{ formatAchievement(annualAchievement(row)) }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.page-shell {
  min-height: 100%;
  padding: 24px;
  background: #f3f7f5;
}
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 32px;
  margin-bottom: 18px;
  color: #f7fffb;
  background:
    radial-gradient(circle at 85% 15%, rgb(160 218 188 / 22%), transparent 34%),
    linear-gradient(130deg, #123f32, #1f654f);
  border-radius: 18px;
  box-shadow: 0 18px 45px rgb(18 63 50 / 16%);
}
.eyebrow {
  margin: 0 0 8px;
  color: #bce3d2;
  font-size: 13px;
  letter-spacing: 0.14em;
}
h1 {
  margin: 0;
  font-size: clamp(28px, 3vw, 42px);
  line-height: 1.15;
}
.subtitle {
  max-width: 720px;
  margin: 12px 0 0;
  color: #d9ede5;
  line-height: 1.7;
}
.filters {
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
}
.filters .el-select {
  width: 145px;
}
.filters .el-select:first-child {
  width: 210px;
}
.birthday-filters {
  display: flex;
  gap: 8px;
}
.birthday-filters .el-select {
  width: 150px;
}
.birthday-filters .birthday-month-filter {
  width: 125px;
}
.birthday-greeting-drawer {
  min-height: 240px;
}
.birthday-profile-card {
  display: grid;
  gap: 7px;
  padding: 16px;
  margin-bottom: 16px;
  color: #426458;
  background: #f1f8f4;
  border: 1px solid #d4eadc;
  border-radius: 12px;
}
.birthday-profile-card strong {
  color: #173f33;
  font-size: 20px;
}
.birthday-note {
  margin-bottom: 10px;
}
.birthday-drawer-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: 22px 0 10px;
}
.birthday-drawer-heading h3 {
  margin: 0;
  color: #173f33;
}
.birthday-drawer-heading p {
  margin: 5px 0 0;
  color: #82958d;
  font-size: 13px;
}
.birthday-memory-list {
  display: grid;
  gap: 8px;
}
.birthday-memory {
  display: flex;
  align-items: flex-start;
  height: auto;
  padding: 10px 12px;
  margin: 0 !important;
  background: #fafcfb;
  border: 1px solid #e3eee8;
  border-radius: 10px;
}
.birthday-memory :deep(.el-checkbox__label) {
  display: grid;
  gap: 3px;
  white-space: normal;
  color: #294d40;
}
.birthday-memory small {
  color: #8a9e95;
  font-size: 12px;
}
.birthday-draft-section {
  padding-top: 4px;
}
.birthday-tone-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.birthday-draft-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
.rhythm-card {
  margin-top: 18px;
}
.rhythm-heading {
  align-items: flex-start;
}
.rhythm-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 16px 0;
}
.rhythm-summary article {
  padding: 14px 16px;
  background: #f4f8f6;
  border-radius: 10px;
}
.rhythm-summary article.attention {
  background: #fff5f0;
}
.rhythm-summary span,
.rhythm-summary strong {
  display: block;
}
.rhythm-summary span {
  color: #72877e;
  font-size: 13px;
}
.rhythm-summary strong {
  margin-top: 6px;
  color: #194b3b;
  font-size: 24px;
}
.rhythm-summary .attention strong {
  color: #b34f2f;
}
.rhythm-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
}
.rhythm-filters {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.rhythm-filters .el-select {
  width: 132px;
}
.rhythm-toolbar > span {
  color: #82958d;
  font-size: 12px;
}
.rhythm-external-role {
  display: block;
  margin-top: 3px;
  color: #8a9e95;
  font-size: 12px;
}
.rhythm-source-note {
  display: block;
  margin-top: 3px;
  color: #3f8067;
  font-size: 11px;
}
.birthday-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 18px 0;
}
.care-center-card {
  margin-bottom: 18px;
}
.care-center-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.care-center-heading-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.care-center-count {
  color: #1b6049;
  font-size: 18px;
}
.care-center-alert {
  margin-top: 12px;
}
.care-center-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 9px;
  margin: 4px 0 16px;
}
.care-center-summary span {
  padding: 8px 12px;
  color: #60756c;
  background: #f3f8f5;
  border-radius: 10px;
  font-size: 13px;
}
.care-center-summary span.overdue {
  color: #9e3d31;
  background: #fff0ed;
}
.care-center-summary span.today {
  color: #245a94;
  background: #edf5ff;
}
.care-center-summary span.attention {
  color: #996217;
  background: #fff6e7;
}
.care-center-summary span.birthday {
  color: #8a5a2b;
  background: #fff7e8;
}
.care-center-summary span.renewal {
  color: #17624b;
  background: #e8f7f0;
}
.care-center-summary b {
  margin-left: 4px;
  color: inherit;
}
.care-action-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.muted-inline {
  color: #82958d;
  font-size: 12px;
}
.care-person-profile {
  display: grid;
  gap: 6px;
  padding: 15px 16px;
  margin-bottom: 14px;
  color: #5e756a;
  background: #f1f8f4;
  border: 1px solid #d4eadc;
  border-radius: 12px;
}
.care-person-profile strong {
  color: #173f33;
  font-size: 20px;
}
.care-person-actions {
  display: grid;
  gap: 10px;
}
.care-person-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  background: #fbfdfc;
  border: 1px solid #e0ebe6;
  border-radius: 11px;
}
.care-person-action-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.care-person-action p {
  margin: 8px 0 4px;
  color: #60756c;
}
.care-person-action small {
  color: #82958d;
  font-size: 12px;
}
.management-card {
  margin-bottom: 18px;
}
.annual-renewal-card {
  margin-bottom: 18px;
}
.annual-renewal-summary-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin: 8px 0 18px;
}
.annual-renewal-summary-grid article {
  min-height: 88px;
  padding: 14px 15px;
  background: #f4f8f6;
  border-radius: 10px;
}
.annual-renewal-summary-grid article.success {
  background: #eaf8ef;
}
.annual-renewal-summary-grid article.warning {
  background: #fff6e7;
}
.annual-renewal-summary-grid span,
.annual-renewal-summary-grid strong {
  display: block;
}
.annual-renewal-summary-grid span {
  color: #72877e;
  font-size: 12px;
}
.annual-renewal-summary-grid strong {
  margin-top: 7px;
  color: #194b3b;
  font-size: 24px;
}
.annual-renewal-quality,
.annual-renewal-timing {
  padding: 15px 16px;
  margin-bottom: 16px;
  background: #f7faf8;
  border: 1px solid #e0ebe6;
  border-radius: 11px;
}
.annual-renewal-quality {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}
.annual-renewal-quality h3,
.annual-renewal-timing h3 {
  margin: 0;
  color: #245f4b;
  font-size: 16px;
}
.annual-renewal-quality p,
.annual-renewal-timing p {
  margin: 6px 0 0;
  color: #60756c;
  font-size: 13px;
  line-height: 1.6;
}
.annual-renewal-evidence-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.annual-renewal-timing-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.annual-renewal-stage-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}
.annual-renewal-stage-grid article {
  padding: 12px;
  text-align: center;
  background: #fff;
  border: 1px solid #e0ebe6;
  border-radius: 10px;
}
.annual-renewal-stage-grid span,
.annual-renewal-stage-grid strong {
  display: block;
}
.annual-renewal-stage-grid span {
  color: #72877e;
  font-size: 12px;
}
.annual-renewal-stage-grid strong {
  margin-top: 7px;
  color: #194b3b;
  font-size: 22px;
}
.annual-renewal-org-table {
  margin-bottom: 4px;
}
.management-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.management-summary-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin: 8px 0 14px;
}
.management-summary-grid article {
  min-height: 94px;
  padding: 14px 15px;
  background: #f4f8f6;
  border-radius: 10px;
}
.management-summary-grid article.danger {
  background: #fff0ed;
}
.management-summary-grid article.warning {
  background: #fff6e7;
}
.management-summary-grid span,
.management-summary-grid strong,
.management-summary-grid small {
  display: block;
}
.management-summary-grid span {
  color: #72877e;
  font-size: 12px;
}
.management-summary-grid strong {
  margin-top: 7px;
  color: #194b3b;
  font-size: 24px;
}
.management-summary-grid article.danger strong {
  color: #a33d32;
}
.management-summary-grid article.warning strong {
  color: #996217;
}
.management-summary-grid small {
  margin-top: 3px;
  color: #82958d;
  font-size: 11px;
}
.management-coverage {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  margin-bottom: 16px;
  color: #60756c;
  font-size: 13px;
  background: #f5f9f7;
  border-radius: 10px;
}
.management-subheading {
  margin: 18px 0 10px;
  color: #245f4b;
  font-size: 16px;
}
.management-breakdown {
  color: #60756c;
  font-size: 12px;
  white-space: nowrap;
}
.management-exception-table .el-table__row:hover,
.management-org-table .el-table__row:hover {
  cursor: default;
}
.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 26px 2px 14px;
}
.section-heading h2 {
  margin: 0;
  color: #173f33;
  font-size: 24px;
}
.section-heading > span {
  color: #82958d;
  font-size: 13px;
}
.eyebrow.dark {
  margin-bottom: 4px;
  color: #3f8067;
}
.operations-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 12px;
}
.operations-card {
  min-height: 132px;
  padding: 18px;
  background: #fff;
  border: 1px solid #dfeae5;
  border-radius: 14px;
  box-shadow: 0 8px 24px rgb(31 78 61 / 6%);
}
.operations-card > span {
  color: #60756c;
  font-size: 13px;
}
.operations-card strong {
  display: block;
  margin: 9px 0 5px;
  color: #173f33;
  font-size: 32px;
}
.operations-card strong small {
  margin-left: 4px;
  font-size: 14px;
}
.operations-card p {
  margin: 0;
  color: #8b9d95;
  font-size: 12px;
  line-height: 1.5;
}
.operations-card.unavailable {
  background: #f7f8f7;
  border-style: dashed;
}
.operations-card strong.not-ready {
  color: #9aa8a2;
  font-size: 21px;
}
.data-alert {
  margin-top: 14px;
}
.operations-panels {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  margin-top: 16px;
}
.center-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.center-list div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 14px;
  background: #f4f8f6;
  border-radius: 10px;
}
.center-list span {
  color: #60756c;
}
.center-list strong {
  color: #1e604a;
}
.schedule-card {
  margin-top: 16px;
}
.class-analysis h3 {
  margin: 24px 0 12px;
  color: #173f33;
}
.analysis-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.analysis-grid article {
  padding: 16px;
  background: #f3f8f5;
  border-radius: 12px;
}
.analysis-grid span,
.analysis-grid strong {
  display: block;
}
.analysis-grid span {
  color: #72877e;
  font-size: 13px;
}
.analysis-grid strong {
  margin-top: 7px;
  color: #194b3b;
  font-size: 21px;
}
.analysis-note {
  margin-top: 14px;
}
.operations-form {
  margin-top: 18px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}
.form-grid :deep(.el-date-editor),
.count-pair {
  width: 100%;
}
.count-pair {
  display: flex;
  align-items: center;
  gap: 8px;
}
.count-pair :deep(.el-input-number) {
  flex: 1;
  width: 0;
}
.drawer-actions {
  display: flex;
  justify-content: flex-end;
  padding: 20px 0;
}
.mp-heading {
  padding-top: 8px;
  border-top: 1px solid #dce8e2;
}
.mp-filters {
  display: flex;
  gap: 10px;
}
.mp-filters .el-select {
  width: 230px;
}
.summary-card,
.content-card {
  background: #fff;
  border: 1px solid #e1ebe6;
  border-radius: 14px;
  box-shadow: 0 8px 24px rgb(31 78 61 / 6%);
}
.summary-card {
  display: flex;
  flex-direction: column;
  min-height: 132px;
  padding: 20px;
}
.summary-card span {
  color: #60756c;
  font-size: 13px;
}
.summary-card strong {
  margin: 8px 0 4px;
  color: #173f33;
  font-size: 32px;
}
.summary-card strong.metric-name {
  font-size: 23px;
  line-height: 1.35;
}
.summary-card small,
.section-title p {
  color: #82958d;
}
.summary-card.accent {
  background: #eff8f3;
  border-color: #c8e5d6;
}
.variance-alert {
  margin-bottom: 18px;
}
.content-card {
  padding: 22px;
}
.section-title h2 {
  margin: 0;
  color: #173f33;
}
.section-title p {
  margin: 6px 0 18px;
}
.metric-guide {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
  padding: 12px 14px;
  margin-bottom: 16px;
  color: #60756c;
  font-size: 13px;
  background: #f5f9f7;
  border-radius: 10px;
}
.metric-guide b {
  color: #245f4b;
}
@media (max-width: 900px) {
  .hero {
    align-items: stretch;
    flex-direction: column;
  }
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .operations-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .operations-panels {
    grid-template-columns: 1fr;
  }
  .rhythm-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .care-center-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .management-summary-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .annual-renewal-summary-grid,
  .annual-renewal-stage-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .annual-renewal-quality {
    flex-direction: column;
  }
  .annual-renewal-evidence-tags {
    justify-content: flex-start;
  }
  .management-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .analysis-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 560px) {
  .page-shell {
    padding: 14px;
  }
  .summary-grid {
    grid-template-columns: 1fr;
  }
  .birthday-title,
  .birthday-filters {
    flex-direction: column;
  }
  .birthday-filters,
  .birthday-filters .el-select {
    width: 100%;
  }
  .operations-grid,
  .center-list,
  .analysis-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }
  .rhythm-summary {
    grid-template-columns: 1fr;
  }
  .management-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .annual-renewal-summary-grid,
  .annual-renewal-stage-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .annual-renewal-timing-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .care-center-heading-actions,
  .care-person-action {
    align-items: stretch;
    flex-direction: column;
  }
  .section-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .mp-filters {
    display: grid;
  }
  .mp-filters .el-select {
    width: 100%;
  }
  .filters {
    display: grid;
  }
  .filters .el-select {
    width: 100%;
  }
  .rhythm-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .rhythm-filters {
    justify-content: stretch;
  }
  .rhythm-filters .el-select {
    flex: 1;
    width: auto;
  }
}
</style>
