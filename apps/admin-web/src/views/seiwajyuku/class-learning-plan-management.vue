<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  bindLearningPlan,
  correctLearningPlan,
  getLearningPlanHealth,
  getLearningPlanHistory,
  getLearningPlanRecommendation,
  getLearningPlans,
  restartLearningPlan,
  resumeLearningPlan,
  type LearningPlan,
  type LearningPlanHealth,
  type LearningPlanHealthClass,
  type LearningPlanHealthIssue,
  type LearningPlanHistory
} from "@/api/learning-plan-management";

defineOptions({ name: "ClassLearningPlanManagement" });

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const health = ref<LearningPlanHealth>();
const plans = ref<LearningPlan[]>([]);
const selectedClassId = ref("");
const history = ref<LearningPlanHistory>();
const activeTab = ref("settings");

const initialForm = reactive({
  plan_version_id: 0,
  cohort_month: 4 as number | null,
  started_at: "",
  start_cycle_index: 1
});
const correctionForm = reactive({
  plan_version_id: 0,
  cohort_month: 4 as number | null,
  learning_cycle_index: 1,
  reason: ""
});
const resumeForm = reactive({
  plan_version_id: 0,
  cohort_month: 4 as number | null,
  started_at: "",
  start_cycle_index: 1,
  reason: ""
});
const restartForm = reactive({
  plan_version_id: 0,
  cohort_month: 4,
  started_at: "",
  reason: ""
});

const cohortOptions = [1, 4, 7, 10];
const selectedClass = computed<LearningPlanHealthClass | undefined>(() =>
  health.value?.classes.find(item => item.class_org_unit_id === selectedClassId.value)
);
const classOptions = computed(() =>
  [...(health.value?.classes || [])].sort((left, right) =>
    `${left.class_name}${left.unit_code}`.localeCompare(`${right.class_name}${right.unit_code}`, "zh-CN")
  )
);
const publishedPlans = computed(() =>
  plans.value.filter(plan => plan.status === "PUBLISHED")
);
const correctionPlans = computed(() => {
  const currentPlanId = selectedClass.value?.binding?.plan_version_id;
  const currentPlan = currentPlanId
    ? plans.value.find(plan => plan.id === currentPlanId)
    : undefined;
  if (currentPlan && currentPlan.status !== "PUBLISHED") {
    return [currentPlan, ...publishedPlans.value.filter(plan => plan.id !== currentPlan.id)];
  }
  return publishedPlans.value;
});
const summary = computed(() => health.value?.summary ?? {});
const summaryNumber = (key: string) => Number(summary.value[key] ?? 0);
const isBindingRequired = (item?: { business_expectation?: LearningPlanHealthClass["business_expectation"] | null } | null) => {
  const expectation = item?.business_expectation;
  return !expectation
    || (expectation.binding_requirement !== "NOT_REQUIRED"
      && expectation.learning_plan_scope !== "OUT_OF_SCOPE");
};
const issueLabel = (issueType: string) => ({
  MISSING_BINDING: "未绑定学习计划",
  MULTIPLE_ACTIVE_BINDINGS: "存在多个有效绑定",
  INVALID_PLAN_VERSION: "计划版本状态异常",
  INVALID_COHORT_TEMPLATE: "开班模板异常",
  MISSING_CURRENT_CYCLE: "缺少当前学习周期",
  PLAN_CYCLE_MISMATCH: "plan_cycle 对应错位",
  GROUP_MEETING_CONFIG_MISSING: "小组学习会配置缺失",
  GROUP_WITHOUT_ACTIVE_MEMBERS: "小组没有有效学员",
  GROUP_CLASS_RELATION_MISMATCH: "小组与班级关系异常",
  NO_ACTIVE_GROUPS: "没有有效小组",
  VOLUNTEER_PERMISSION_MISSING: "志工权限待另行核验",
  DUPLICATE_CLASS_NAME: "班级名称重复，需按 ID 核对",
  EXPECTED_CYCLE_MISMATCH: "业务预期周期不一致",
  EXPECTED_TEMPLATE_MISMATCH: "业务预期模板不一致",
  EXPECTED_PLAN_VERSION_MISMATCH: "业务预期计划版本不一致",
  EXPECTED_STATUS_MISMATCH: "业务预期状态不一致",
  MANUAL_REVIEW_REQUIRED: "需要人工确认",
  BASELINE_ID_NAME_MISMATCH: "基线 ID 与名称不一致"
}[issueType] ?? issueType);
const statusType = (status: string) => status === "READY" ? "success" : status === "NOT_APPLICABLE" ? "info" : "danger";
const statusLabel = (status: string) => status === "READY" ? "可验收" : status === "NOT_APPLICABLE" ? "无需绑定" : "阻塞";
const volunteerPermissionLabel = (permission: LearningPlanHealthClass["volunteer_permission"]) => permission === "NOT_APPLICABLE"
  ? "不适用"
  : permission === "PASS"
    ? "已通过"
    : "未核验（不影响学习计划确认）";
const issueDescription = (issue: LearningPlanHealthIssue) => {
  const expected = issue.current_data.expected;
  const actual = issue.current_data.actual;
  if (issue.issue_type === "EXPECTED_CYCLE_MISMATCH") {
    return `业务预期第${expected ?? "—"}周期，当前第${actual ?? "—"}周期`;
  }
  if (issue.issue_type === "EXPECTED_TEMPLATE_MISMATCH") {
    return `业务预期${expected ?? "—"}月模板，当前${actual ?? "—"}月模板`;
  }
  if (issue.issue_type === "EXPECTED_PLAN_VERSION_MISMATCH") {
    return `业务预期计划版本 ${expected ?? "—"}，当前 ${actual ?? "—"}`;
  }
  return issueLabel(issue.issue_type);
};
const issueSummary = (item?: LearningPlanHealthClass) =>
  item?.issues.map(issueDescription).join("；") || "请点击重新扫描查看最新结果";
const formatDateTime = (value?: string | null) => value ? value.replace("T", " ").replace("Z", "") : "—";
const runtimeStatusLabel = (status?: string | null) => ({
  NORMAL: "正常",
  POSTPONED: "延期/暂停",
  NOT_STARTED: "尚未开始",
  COMPLETED: "已完成",
  MISSING_CURRENT_CYCLE: "缺少当前周期",
  UNBOUND: "未绑定",
  NOT_APPLICABLE: "不适用"
}[status || ""] ?? status ?? "—");
const businessExpectationTitle = (item: LearningPlanHealthClass) => {
  if (!isBindingRequired(item)) return "业务预期：无需绑定学习计划";
  const expectation = item.business_expectation;
  const baseline = `${expectation?.expected_plan_version || "版本待定"} · ${expectation?.expected_cohort_month ? `${expectation.expected_cohort_month}月模板` : "模板待定"} · ${expectation?.expected_current_cycle ?? "未开始/待确认"}`;
  if (item.business_expectation_resolution?.mode === "EXPLICIT_CONFIRMATION") {
    const corrected = `${item.binding?.version_label || "版本待定"} · ${item.binding?.cohort_month ? `${item.binding.cohort_month}月模板` : "模板待定"} · ${item.current_cycle?.learning_cycle_index ?? "未开始/待确认"}`;
    return `已按人工修正为准：${corrected} · 原业务基线：${baseline}`;
  }
  return `业务基线：${baseline}`;
};
const businessExpectationDescription = (item: LearningPlanHealthClass) => {
  const expectation = item.business_expectation;
  if (item.business_expectation_resolution?.mode === "EXPLICIT_CONFIRMATION") {
    return `已记录人工确认，按本次绑定/接续/重新开始/修正结果验收；原业务基线仅作参考。确认原因：${item.business_expectation_resolution.reason || "未提供"}`;
  }
  return expectation?.adjustment_reason || `证据：${expectation?.evidence_source || "未提供"}；状态：${expectation?.migration_status || "未提供"}`;
};
const dateOnly = (value?: string | null) => value ? value.slice(0, 10) : "";
const formatDateOnly = (value?: string | null) => dateOnly(value) || "—";
const todayDate = () => {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
};

const setDefaults = () => {
  const binding = selectedClass.value?.binding;
  const defaultPlan = publishedPlans.value[0];
  const planId = binding?.plan_version_id ?? defaultPlan?.id ?? 0;
  const cohort = binding?.cohort_month ?? 4;
  const cycle = selectedClass.value?.current_cycle?.learning_cycle_index ?? 1;
  initialForm.plan_version_id = planId;
  initialForm.cohort_month = cohort;
  initialForm.started_at = binding?.started_at ? dateOnly(binding.started_at) : todayDate();
  initialForm.start_cycle_index = binding?.start_cycle_index ?? 1;
  correctionForm.plan_version_id = planId;
  correctionForm.cohort_month = cohort;
  correctionForm.learning_cycle_index = cycle;
  resumeForm.plan_version_id = planId;
  resumeForm.cohort_month = cohort;
  resumeForm.started_at = todayDate();
  resumeForm.start_cycle_index = cycle;
  restartForm.plan_version_id = defaultPlan?.id ?? planId;
  restartForm.cohort_month = 4;
  restartForm.started_at = todayDate();
};

const loadHistory = async () => {
  if (!selectedClassId.value) {
    history.value = undefined;
    return;
  }
  try {
    history.value = (await getLearningPlanHistory(selectedClassId.value)).data;
  } catch {
    history.value = undefined;
  }
};

const load = async () => {
  loading.value = true;
  error.value = "";
  try {
    const [healthResponse, plansResponse] = await Promise.all([
      getLearningPlanHealth(undefined, { cacheBust: true }),
      getLearningPlans()
    ]);
    health.value = healthResponse.data;
    plans.value = plansResponse.data;
    if (!health.value.classes.some(item => item.class_org_unit_id === selectedClassId.value)) {
      selectedClassId.value = health.value.classes[0]?.class_org_unit_id ?? "";
    }
    setDefaults();
    await loadHistory();
  } catch (requestError: any) {
    error.value = requestError?.response?.data?.detail || "班级学习计划健康扫描失败";
    ElMessage.error(error.value);
  } finally {
    loading.value = false;
  }
};

const selectClass = async (row: LearningPlanHealthClass) => {
  selectedClassId.value = row.class_org_unit_id;
  setDefaults();
  await loadHistory();
};

const selectClassById = async (classOrgUnitId: string) => {
  const row = health.value?.classes.find(item => item.class_org_unit_id === classOrgUnitId);
  if (row) await selectClass(row);
};

const requireReason = (reason: string, label: string) => {
  if (!reason.trim()) {
    ElMessage.warning(`${label}必须填写原因`);
    return false;
  }
  return true;
};

const afterSave = async (message: string) => {
  await load();
  const current = selectedClass.value;
  if (current?.status === "READY") {
    ElMessage.success(`${message}，已重新扫描，当前状态：可验收`);
  } else if (current?.status === "BLOCKED") {
    ElMessage.warning(`${message}，已重新扫描；仍有阻塞：${issueSummary(current)}`);
  } else {
    ElMessage.success(`${message}，已重新扫描`);
  }
};

const saveInitial = async () => {
  if (!selectedClassId.value || !initialForm.plan_version_id) return;
  saving.value = true;
  try {
    await bindLearningPlan(selectedClassId.value, initialForm);
    await afterSave("首次绑定已完成，历史记录已保留");
  } catch (requestError: any) {
    ElMessage.error(requestError?.response?.data?.detail || "首次绑定失败");
  } finally { saving.value = false; }
};

const saveCorrection = async () => {
  if (!selectedClassId.value || !correctionForm.plan_version_id) return;
  if (!requireReason(correctionForm.reason, "修正原因")) return;
  saving.value = true;
  try {
    await correctLearningPlan(selectedClassId.value, correctionForm);
    await afterSave("当前学习设置已修正，操作已审计");
    correctionForm.reason = "";
  } catch (requestError: any) {
    ElMessage.error(requestError?.response?.data?.detail || "学习设置修正失败");
  } finally { saving.value = false; }
};

const saveResume = async () => {
  if (!selectedClassId.value || !resumeForm.plan_version_id) return;
  if (!requireReason(resumeForm.reason, "接续原因")) return;
  saving.value = true;
  try {
    await resumeLearningPlan(selectedClassId.value, resumeForm);
    await afterSave("新的接续学习轮次已建立");
    resumeForm.reason = "";
  } catch (requestError: any) {
    ElMessage.error(requestError?.response?.data?.detail || "接续学习轮次失败");
  } finally { saving.value = false; }
};

const recommendRestart = async () => {
  if (!restartForm.started_at) return;
  try {
    const recommendation = (await getLearningPlanRecommendation(restartForm.started_at)).data;
    restartForm.plan_version_id = recommendation.plan_version_id;
    restartForm.cohort_month = recommendation.cohort_month;
    ElMessage.success(`已推荐 ${recommendation.version_label} · ${recommendation.cohort_month}月模板`);
  } catch (requestError: any) {
    ElMessage.error(requestError?.response?.data?.detail || "暂时无法生成计划推荐");
  }
};

const saveRestart = async () => {
  if (!selectedClassId.value || !restartForm.plan_version_id) return;
  if (!requireReason(restartForm.reason, "重新开始原因")) return;
  try {
    await ElMessageBox.confirm(
      "确认结束当前学习轮次并新建第 1 学习周期？原班会、小组会、签到、合影和学分事实不会删除。",
      "确认重新开始学习",
      { type: "warning", confirmButtonText: "确认建立新轮次", cancelButtonText: "取消" }
    );
  } catch { return; }
  saving.value = true;
  try {
    await restartLearningPlan(selectedClassId.value, restartForm);
    await afterSave("重新开始学习已建立新的轮次");
    restartForm.reason = "";
  } catch (requestError: any) {
    ElMessage.error(requestError?.response?.data?.detail || "重新开始学习失败");
  } finally { saving.value = false; }
};

onMounted(load);
</script>

<template>
  <div class="class-learning-plan-management page-container">
    <el-card shadow="never" class="intro-card">
      <div class="page-header">
        <div>
          <div class="page-title">班级学习计划管理</div>
          <div class="page-subtitle">
            逐班处理首次绑定、当前设置修正、指定周期接续和重新开始；历史学习事实不覆盖、不删除。
          </div>
        </div>
        <el-button :loading="loading" @click="load">重新扫描</el-button>
      </div>
      <el-alert
        v-if="health"
        :title="`当前发布门禁：${health.assessment} · 扫描 ${formatDateTime(health.generated_at)}`"
        :type="health.assessment === 'GO' ? 'success' : 'warning'"
        show-icon
        :closable="false"
      />
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    </el-card>

    <div class="stat-grid">
      <el-card shadow="never"><el-statistic title="正式班级" :value="summaryNumber('total_classes')" /></el-card>
      <el-card shadow="never"><el-statistic title="已正确绑定" :value="summaryNumber('correctly_bound')" /></el-card>
      <el-card shadow="never"><el-statistic title="未绑定" :value="summaryNumber('unbound')" /></el-card>
      <el-card shadow="never"><el-statistic title="无需绑定" :value="summaryNumber('not_applicable_classes')" /></el-card>
      <el-card shadow="never"><el-statistic title="当前周期错位" :value="summaryNumber('plan_cycle_mismatch')" /></el-card>
      <el-card shadow="never"><el-statistic title="可验收班级" :value="summaryNumber('ready_classes')" /></el-card>
    </div>

    <el-card shadow="never" class="classes-card">
      <template #header>
        <div class="section-header">
          <div>
            <span>逐班健康扫描</span>
            <span class="muted">按组织 ID 识别班级，同名班级不会自动合并</span>
          </div>
          <el-select
            v-model="selectedClassId"
            class="class-selector"
            filterable
            clearable
            placeholder="选择班级确认"
            @change="selectClassById"
          >
            <el-option
              v-for="item in classOptions"
              :key="item.class_org_unit_id"
              :label="`${item.class_name} · ${item.unit_code}`"
              :value="item.class_org_unit_id"
            />
          </el-select>
        </div>
      </template>
      <el-table
        v-loading="loading"
        :data="health?.classes || []"
        row-key="class_org_unit_id"
        highlight-current-row
        @row-click="selectClass"
      >
        <el-table-column prop="class_name" label="班级" min-width="150" />
        <el-table-column prop="unit_code" label="组织编码" min-width="180" />
        <el-table-column label="学习计划" min-width="190">
          <template #default="{ row }">
            <span v-if="row.binding">{{ row.binding.version_label }} · {{ row.binding.cohort_month || "通用" }}月</span>
            <span v-else-if="!isBindingRequired(row)" class="muted">无需绑定</span>
            <span v-else class="muted">未绑定</span>
          </template>
        </el-table-column>
        <el-table-column label="当前周期" width="100">
          <template #default="{ row }">{{ row.current_cycle?.learning_cycle_index || (row.runtime_status === "NOT_STARTED" ? "未开始" : "—") }}</template>
        </el-table-column>
        <el-table-column label="运行状态" width="110">
          <template #default="{ row }">{{ runtimeStatusLabel(row.runtime_status) }}</template>
        </el-table-column>
        <el-table-column label="业务预期" min-width="170">
          <template #default="{ row }">
            <span v-if="row.business_expectation && !isBindingRequired(row)">无需绑定学习计划</span>
            <span v-else-if="row.business_expectation">{{ row.business_expectation.expected_plan_version || "版本待定" }} · {{ row.business_expectation.expected_cohort_month ? `${row.business_expectation.expected_cohort_month}月` : "模板待定" }} · {{ row.business_expectation.expected_current_cycle ?? "未开始/待确认" }}</span>
            <span v-else class="muted">未提供</span>
          </template>
        </el-table-column>
        <el-table-column label="小组" width="80" prop="group_count" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="异常" min-width="260">
          <template #default="{ row }">
            <span>{{ row.issues.map((item: any) => issueLabel(item.issue_type)).join("；") || "—" }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="selectedClass" shadow="never" class="management-card">
      <template #header>
        <div class="section-header">
          <div>
            <span>{{ selectedClass.class_name }}</span>
            <span class="muted code">{{ selectedClass.class_org_unit_id }}</span>
          </div>
          <el-tag :type="statusType(selectedClass.status)">{{ selectedClass.status === "READY" ? "数据可验收" : selectedClass.status === "NOT_APPLICABLE" ? "无需绑定" : "存在发布阻塞" }}</el-tag>
        </div>
      </template>

      <el-descriptions :column="4" border class="current-summary">
        <el-descriptions-item label="当前计划">{{ selectedClass.binding?.plan_name || (isBindingRequired(selectedClass) ? "未绑定" : "无需绑定") }}</el-descriptions-item>
        <el-descriptions-item label="版本">{{ selectedClass.binding?.version_label || "—" }}</el-descriptions-item>
        <el-descriptions-item label="模板">{{ selectedClass.binding?.cohort_month ? `${selectedClass.binding.cohort_month}月` : "—" }}</el-descriptions-item>
        <el-descriptions-item label="学习轮次">{{ selectedClass.binding ? `第${selectedClass.binding.learning_round}轮` : "—" }}</el-descriptions-item>
        <el-descriptions-item label="当前周期">{{ selectedClass.current_cycle?.learning_cycle_index || "—" }}</el-descriptions-item>
        <el-descriptions-item label="运行状态">{{ runtimeStatusLabel(selectedClass.runtime_status) }}</el-descriptions-item>
        <el-descriptions-item label="开始日期">{{ formatDateOnly(selectedClass.binding?.started_at) }}</el-descriptions-item>
        <el-descriptions-item label="小组关系">{{ selectedClass.group_count }} 个有效小组</el-descriptions-item>
        <el-descriptions-item label="志工权限（另行校验）">{{ volunteerPermissionLabel(selectedClass.volunteer_permission) }}</el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="selectedClass.business_expectation"
        class="expectation-alert"
        type="info"
        :closable="false"
        :title="businessExpectationTitle(selectedClass)"
        :description="businessExpectationDescription(selectedClass)"
      />
      <el-alert
        v-if="selectedClass.status === 'BLOCKED'"
        class="health-issues-alert"
        type="warning"
        show-icon
        :closable="false"
        title="本次操作已保存，页面已重新扫描"
        :description="`当前仍有阻塞：${issueSummary(selectedClass)}`"
      />

      <el-tabs v-model="activeTab" class="management-tabs">
        <el-tab-pane label="首次绑定 / 当前修正" name="settings">
          <el-alert v-if="!selectedClass.binding && !isBindingRequired(selectedClass)" title="该班级不纳入学习计划绑定管理，无需绑定。" type="info" show-icon :closable="false" />
          <el-alert v-if="!selectedClass.binding && isBindingRequired(selectedClass)" title="该班级尚未绑定学习计划，请先完成首次绑定。" type="warning" show-icon :closable="false" />
          <el-form v-if="!selectedClass.binding && isBindingRequired(selectedClass)" label-width="130px" class="operation-form">
            <el-form-item label="学习计划版本"><el-select v-model="initialForm.plan_version_id" placeholder="选择已发布版本"><el-option v-for="plan in publishedPlans" :key="plan.id" :label="`${plan.version_label} · ${plan.plan_name}`" :value="plan.id" /></el-select></el-form-item>
            <el-form-item label="开班模板"><el-select v-model="initialForm.cohort_month"><el-option v-for="month in cohortOptions" :key="month" :label="`${month}月模板`" :value="month" /></el-select></el-form-item>
            <el-form-item label="正式开始日期"><el-date-picker v-model="initialForm.started_at" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" placeholder="选择正式开始日期" /><span class="muted field-hint">只选日期，系统按当天起算</span></el-form-item>
            <el-form-item label="起始学习周期"><el-input-number v-model="initialForm.start_cycle_index" :min="1" :max="240" /></el-form-item>
            <el-form-item><el-button type="primary" :loading="saving" @click="saveInitial">完成首次绑定</el-button></el-form-item>
          </el-form>
          <el-form v-if="selectedClass.binding" label-width="130px" class="operation-form">
            <el-alert title="当前设置修正不会新建学习轮次；若要从第1期重新学习，请使用‘重新开始学习’。" type="info" :closable="false" />
            <el-form-item label="学习计划版本"><el-select v-model="correctionForm.plan_version_id"><el-option v-for="plan in correctionPlans" :key="plan.id" :label="`${plan.version_label} · ${plan.plan_name}${plan.status === 'PUBLISHED' ? '' : ' · 当前旧版本'}`" :value="plan.id" /></el-select></el-form-item>
            <el-form-item label="开班模板"><el-select v-model="correctionForm.cohort_month"><el-option v-for="month in cohortOptions" :key="month" :label="`${month}月模板`" :value="month" /></el-select></el-form-item>
            <el-form-item label="修正后当前周期"><el-input-number v-model="correctionForm.learning_cycle_index" :min="1" :max="240" /></el-form-item>
            <el-form-item label="修正原因"><el-input v-model="correctionForm.reason" type="textarea" :rows="3" maxlength="1000" show-word-limit /></el-form-item>
            <el-form-item><el-button type="primary" :loading="saving" @click="saveCorrection">保存当前设置修正</el-button></el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="从指定周期接续" name="resume">
          <el-alert title="接续会结束当前绑定并新建一个 RESUME 学习轮次；旧轮次仍可在下方历史中查看。" type="warning" :closable="false" />
          <el-form label-width="130px" class="operation-form">
            <el-form-item label="学习计划版本"><el-select v-model="resumeForm.plan_version_id"><el-option v-for="plan in publishedPlans" :key="plan.id" :label="`${plan.version_label} · ${plan.plan_name}`" :value="plan.id" /></el-select></el-form-item>
            <el-form-item label="开班模板"><el-select v-model="resumeForm.cohort_month"><el-option v-for="month in cohortOptions" :key="month" :label="`${month}月模板`" :value="month" /></el-select></el-form-item>
            <el-form-item label="正式开始日期"><el-date-picker v-model="resumeForm.started_at" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" placeholder="选择接续开始日期" /><span class="muted field-hint">只选日期，系统按当天起算</span></el-form-item>
            <el-form-item label="起始学习周期"><el-input-number v-model="resumeForm.start_cycle_index" :min="1" :max="240" /></el-form-item>
            <el-form-item label="接续原因"><el-input v-model="resumeForm.reason" type="textarea" :rows="3" maxlength="1000" show-word-limit /></el-form-item>
            <el-form-item><el-button type="primary" :loading="saving" @click="saveResume">建立接续轮次</el-button></el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="重新开始学习" name="restart">
          <el-alert title="重新开始只创建新的学习轮次，不会把旧 current_cycle 改成第1期，也不会删除任何学习事实。" type="warning" show-icon :closable="false" />
          <el-form label-width="130px" class="operation-form">
            <el-form-item label="新轮次正式开始日期"><el-date-picker v-model="restartForm.started_at" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" placeholder="选择新轮次开始日期" /><el-button class="recommend-button" @click="recommendRestart">按日期推荐</el-button><span class="muted field-hint">只选日期，系统按当天起算</span></el-form-item>
            <el-form-item label="推荐/确认版本"><el-select v-model="restartForm.plan_version_id"><el-option v-for="plan in publishedPlans" :key="plan.id" :label="`${plan.version_label} · ${plan.plan_name}`" :value="plan.id" /></el-select></el-form-item>
            <el-form-item label="新开班模板"><el-select v-model="restartForm.cohort_month"><el-option v-for="month in cohortOptions" :key="month" :label="`${month}月模板`" :value="month" /></el-select></el-form-item>
            <el-form-item label="重新开始原因"><el-input v-model="restartForm.reason" type="textarea" :rows="3" maxlength="1000" show-word-limit /></el-form-item>
            <el-form-item><el-button type="danger" :loading="saving" @click="saveRestart">确认重新开始学习</el-button></el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="轮次历史与审计" name="history">
          <el-alert title="历史 binding、班会、小组学习会、签到、合影和学分事实均保留；此处只读。" type="info" :closable="false" />
          <el-table :data="history?.bindings || []" row-key="id" class="history-table">
            <el-table-column label="轮次" width="80"><template #default="{ row }">第{{ row.learning_round }}轮</template></el-table-column>
            <el-table-column label="变更类型" width="100" prop="transition_type" />
            <el-table-column label="计划" min-width="180"><template #default="{ row }">{{ row.version_label }} · {{ row.plan_name }}</template></el-table-column>
            <el-table-column label="模板/起始期" width="120"><template #default="{ row }">{{ row.cohort_month || "通用" }}月 / 第{{ row.start_cycle_index }}期</template></el-table-column>
            <el-table-column label="状态" width="90" prop="status" />
            <el-table-column label="开始/结束日期" min-width="190"><template #default="{ row }">{{ formatDateOnly(row.started_at) }}<br />{{ row.ended_at ? formatDateOnly(row.ended_at) : "进行中" }}</template></el-table-column>
            <el-table-column label="结束原因" min-width="200" prop="ended_reason" />
          </el-table>
          <div v-if="history?.events.length" class="audit-list">
            <div v-for="event in history.events" :key="`${event.action}-${event.created_at}`" class="audit-item">
              <span>{{ formatDateTime(event.created_at) }}</span><strong>{{ event.action }}</strong><span>{{ event.purpose || "—" }}</span>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<style scoped>
.page-header, .section-header { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.page-title { font-size: 20px; font-weight: 600; }
.page-subtitle, .muted { color: var(--el-text-color-secondary); font-size: 13px; }
.page-subtitle { margin-top: 6px; margin-bottom: 16px; }
.intro-card, .classes-card, .management-card { margin-bottom: 16px; }
.stat-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin: 16px 0; }
.class-selector { width: 360px; max-width: 100%; }
.code { margin-left: 12px; font-family: monospace; }
.current-summary { margin-bottom: 18px; }
.health-issues-alert { margin-bottom: 18px; }
.management-tabs { margin-top: 18px; }
.operation-form { max-width: 760px; padding: 18px 4px 0; }
.operation-form :deep(.el-select), .operation-form :deep(.el-input), .operation-form :deep(.el-date-editor) { width: 420px; max-width: 100%; }
.recommend-button { margin-left: 8px; }
.field-hint { margin-left: 8px; }
.history-table { margin-top: 18px; }
.audit-list { margin-top: 18px; border-top: 1px solid var(--el-border-color-lighter); }
.audit-item { display: grid; grid-template-columns: 180px 260px 1fr; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--el-border-color-lighter); font-size: 13px; }
@media (max-width: 1100px) { .stat-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 720px) { .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .page-header, .section-header { align-items: flex-start; flex-direction: column; } .audit-item { grid-template-columns: 1fr; gap: 4px; } }
</style>
