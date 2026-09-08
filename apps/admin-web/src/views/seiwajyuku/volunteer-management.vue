<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { getIdentityOrgOptions, type IdentityOrgOption } from "@/api/identityAdmin";
import {
  acceptVolunteerRecommendation,
  changeVolunteerAppointmentStatus,
  createVolunteerAppointment,
  createVolunteerRecommendationRule,
  createVolunteerServiceUnit,
  getVolunteerAppointments,
  getVolunteerMigrationPreview,
  getVolunteerPositionOptions,
  getVolunteerRecommendationRules,
  getVolunteerServiceUnits,
  type VolunteerAppointment,
  type VolunteerPositionOption,
  type VolunteerRecommendationRule,
  type VolunteerServiceUnit
} from "@/api/volunteerManagement";

defineOptions({ name: "VolunteerManagement" });

const loading = ref(false);
const appointments = ref<VolunteerAppointment[]>([]);
const serviceUnits = ref<VolunteerServiceUnit[]>([]);
const orgOptions = ref<IdentityOrgOption[]>([]);
const positionOptions = ref<VolunteerPositionOption[]>([]);
const recommendationRules = ref<VolunteerRecommendationRule[]>([]);
const appointmentDialogVisible = ref(false);
const serviceUnitDialogVisible = ref(false);
const migrationDialogVisible = ref(false);
const migrationPreview = ref<{ counts: Record<string, number>; entries: Array<Record<string, unknown>> }>();

const filters = reactive({
  member_name: "",
  system_type: "",
  line_type: "",
  home_shuku_org_unit_id: "",
  service_unit_id: "",
  position_key: "",
  service_target_org_unit_id: "",
  status: ""
});

const appointmentForm = reactive({
  member_id: undefined as number | undefined,
  service_unit_id: "",
  position_key: "",
  confirmation_note: ""
});
const serviceUnitForm = reactive({
  unit_code: "",
  name: "",
  system_type: "CLASS_TEAM" as VolunteerServiceUnit["system_type"],
  line_type: "GENERAL" as VolunteerServiceUnit["line_type"],
  parent_id: "",
  home_shuku_org_unit_id: "",
  service_target_org_unit_id: "",
  sort_order: 0
});
const recommendationForm = reactive({
  source_service_unit_id: "",
  source_position_key: "",
  target_service_unit_id: "",
  target_position_key: ""
});

const currentServiceUnit = computed(() =>
  serviceUnits.value.find(item => item.id === appointmentForm.service_unit_id)
);
const positionFilterOptions = computed(() => {
  const byKey = new Map<string, string>();
  appointments.value.forEach(item => byKey.set(item.position_key, item.position_name));
  return [...byKey.entries()].map(([position_key, position_name]) => ({ position_key, position_name }));
});
const targetPositionOptions = ref<VolunteerPositionOption[]>([]);
const sourcePositionOptions = ref<VolunteerPositionOption[]>([]);

async function load() {
  loading.value = true;
  try {
    const params = Object.fromEntries(
      Object.entries(filters).filter(([, value]) => value !== "")
    );
    const [appointmentResponse, unitResponse, ruleResponse] = await Promise.all([
      getVolunteerAppointments(params),
      getVolunteerServiceUnits({ active_only: false }),
      getVolunteerRecommendationRules()
    ]);
    appointments.value = appointmentResponse.data;
    serviceUnits.value = unitResponse.data;
    recommendationRules.value = ruleResponse.data;
  } finally {
    loading.value = false;
  }
}

async function loadPositionOptions() {
  appointmentForm.position_key = "";
  positionOptions.value = [];
  if (!appointmentForm.service_unit_id) return;
  const response = await getVolunteerPositionOptions(appointmentForm.service_unit_id);
  positionOptions.value = response.data.positions;
}

async function loadTargetPositionOptions() {
  recommendationForm.target_position_key = "";
  targetPositionOptions.value = [];
  if (!recommendationForm.target_service_unit_id) return;
  const response = await getVolunteerPositionOptions(recommendationForm.target_service_unit_id);
  targetPositionOptions.value = response.data.positions;
}

async function loadSourcePositionOptions() {
  recommendationForm.source_position_key = "";
  sourcePositionOptions.value = [];
  if (!recommendationForm.source_service_unit_id) return;
  const response = await getVolunteerPositionOptions(recommendationForm.source_service_unit_id);
  sourcePositionOptions.value = response.data.positions;
}

function openAppointmentDialog() {
  Object.assign(appointmentForm, {
    member_id: undefined,
    service_unit_id: "",
    position_key: "",
    confirmation_note: ""
  });
  positionOptions.value = [];
  appointmentDialogVisible.value = true;
}

async function submitAppointment() {
  if (!appointmentForm.member_id || !appointmentForm.service_unit_id || !appointmentForm.position_key) {
    ElMessage.warning("请完整选择在册学长、志工服务组织和岗位");
    return;
  }
  if (appointmentForm.confirmation_note.trim().length < 8) {
    ElMessage.warning("请填写不少于 8 个字符的业务确认说明");
    return;
  }
  const response = await createVolunteerAppointment({
    member_id: appointmentForm.member_id,
    service_unit_id: appointmentForm.service_unit_id,
    position_key: appointmentForm.position_key,
    confirmation_note: appointmentForm.confirmation_note
  });
  appointmentDialogVisible.value = false;
  await load();
  const rules = response.data.recommendations;
  ElMessage.success(rules.length ? "任职已建立；可按提示确认推荐的另一项任职" : "志工任职已建立");
}

async function changeStatus(row: VolunteerAppointment) {
  const { value } = await ElMessageBox.prompt("填写状态调整说明", "调整志工任职状态", {
    inputPattern: /[\s\S]{6,}/,
    inputErrorMessage: "说明至少 6 个字符",
    confirmButtonText: "继续",
    cancelButtonText: "取消"
  });
  const { value: status } = await ElMessageBox.prompt("输入 ACTIVE、SUSPENDED、ENDED 或 REVOKED", "选择状态", {
    inputValue: row.status === "ACTIVE" ? "SUSPENDED" : "ACTIVE",
    inputPattern: /^(ACTIVE|SUSPENDED|ENDED|REVOKED)$/,
    inputErrorMessage: "请输入允许的状态值"
  });
  await changeVolunteerAppointmentStatus(row.id, { status: status as "ACTIVE" | "SUSPENDED" | "ENDED" | "REVOKED", reason: value });
  ElMessage.success("状态已更新");
  await load();
}

async function acceptRecommendation(row: VolunteerAppointment, rule: VolunteerRecommendationRule) {
  const { value } = await ElMessageBox.prompt(
    `确认将“${row.member_name}”另行任命为“${rule.target_service_unit_name} / ${rule.target_position_name}”。这会创建独立任职，不会替换原岗位。`,
    "确认推荐任职",
    { inputPattern: /[\s\S]{8,}/, inputErrorMessage: "确认说明至少 8 个字符" }
  );
  await acceptVolunteerRecommendation(row.id, rule.id, value);
  ElMessage.success("已建立独立的推荐任职");
  await load();
}

async function submitServiceUnit() {
  if (!serviceUnitForm.service_target_org_unit_id) {
    ElMessage.warning("请选择服务对象正式组织");
    return;
  }
  await createVolunteerServiceUnit({ ...serviceUnitForm, parent_id: serviceUnitForm.parent_id || null, home_shuku_org_unit_id: serviceUnitForm.home_shuku_org_unit_id || null });
  serviceUnitDialogVisible.value = false;
  ElMessage.success("志工服务组织已创建");
  await load();
}

async function submitRecommendationRule() {
  if (Object.values(recommendationForm).some(value => !value)) {
    ElMessage.warning("请完整选择来源和目标任职");
    return;
  }
  await createVolunteerRecommendationRule(recommendationForm);
  ElMessage.success("推荐规则已保存；后续任职仍须人工确认");
  await load();
}

async function openMigrationPreview() {
  const response = await getVolunteerMigrationPreview();
  migrationPreview.value = response.data;
  migrationDialogVisible.value = true;
}

watch(() => appointmentForm.service_unit_id, loadPositionOptions);
watch(() => recommendationForm.source_service_unit_id, loadSourcePositionOptions);
watch(() => recommendationForm.target_service_unit_id, loadTargetPositionOptions);

onMounted(async () => {
  const orgResponse = await getIdentityOrgOptions();
  orgOptions.value = orgResponse.data;
  await load();
});
</script>

<template>
  <div class="volunteer-management page-container">
    <el-alert
      type="info"
      :closable="false"
      title="日常单个学长的志工任职，请在“学员管理 → 编辑学员”中维护；本页主要用于批量、服务组织和高级管理。"
      show-icon
    />
    <el-card class="filter-card" shadow="never">
      <el-form :inline="true" label-width="72px">
        <el-form-item label="学长姓名"><el-input v-model="filters.member_name" clearable placeholder="按姓名查询" /></el-form-item>
        <el-form-item label="体系"><el-select v-model="filters.system_type" clearable><el-option label="班级志工团队" value="CLASS_TEAM" /><el-option label="治理组织" value="GOVERNANCE" /><el-option label="三大委纵向组织" value="COMMITTEE_LINE" /><el-option label="专项活动组织" value="ACTIVITY" /></el-select></el-form-item>
        <el-form-item label="归属塾"><el-select v-model="filters.home_shuku_org_unit_id" clearable filterable><el-option v-for="org in orgOptions.filter(item => item.unit_type === 'ROOT')" :key="org.id" :label="org.name" :value="org.id" /></el-select></el-form-item>
        <el-form-item label="线别"><el-select v-model="filters.line_type" clearable><el-option label="学习践行线" value="LEARNING" /><el-option label="运营管理线" value="OPERATIONS" /><el-option label="发展建设线" value="DEVELOPMENT" /><el-option label="综合" value="GENERAL" /><el-option label="监事线" value="SUPERVISION" /></el-select></el-form-item>
        <el-form-item label="服务组织"><el-select v-model="filters.service_unit_id" clearable filterable><el-option v-for="unit in serviceUnits" :key="unit.id" :label="unit.name" :value="unit.id" /></el-select></el-form-item>
        <el-form-item label="岗位"><el-select v-model="filters.position_key" clearable filterable><el-option v-for="position in positionFilterOptions" :key="position.position_key" :label="position.position_name" :value="position.position_key" /></el-select></el-form-item>
        <el-form-item label="服务对象"><el-select v-model="filters.service_target_org_unit_id" clearable filterable><el-option v-for="org in orgOptions" :key="org.id" :label="org.name" :value="org.id" /></el-select></el-form-item>
        <el-form-item label="状态"><el-select v-model="filters.status" clearable><el-option label="当前有效" value="ACTIVE" /><el-option label="已暂停" value="SUSPENDED" /><el-option label="已结束" value="ENDED" /><el-option label="已撤销" value="REVOKED" /></el-select></el-form-item>
        <el-form-item><el-button type="primary" @click="load">查询</el-button><el-button @click="Object.assign(filters, { member_name: '', system_type: '', line_type: '', home_shuku_org_unit_id: '', service_unit_id: '', position_key: '', service_target_org_unit_id: '', status: '' }); load()">重置</el-button></el-form-item>
      </el-form>
    </el-card>

    <div class="toolbar">
      <el-button type="primary" @click="openAppointmentDialog">新增志工任职</el-button>
      <el-button @click="serviceUnitDialogVisible = true">维护志工服务组织</el-button>
      <el-button @click="openMigrationPreview">历史任职迁移预览</el-button>
    </div>
    <el-table v-loading="loading" :data="appointments" border>
      <el-table-column prop="member_name" label="学长" min-width="100" />
      <el-table-column prop="home_shuku_name" label="塾" min-width="100" />
      <el-table-column label="体系 / 线别" min-width="160"><template #default="{ row }">{{ row.system_name || '历史记录' }}<br />{{ row.line_name || '—' }}</template></el-table-column>
      <el-table-column prop="service_unit_name" label="志工服务组织" min-width="160"><template #default="{ row }">{{ row.service_unit_name || '历史任职（待迁移确认）' }}</template></el-table-column>
      <el-table-column prop="position_name" label="岗位" min-width="130" />
      <el-table-column prop="service_target_name" label="服务对象" min-width="130" />
      <el-table-column label="能力" min-width="150"><template #default="{ row }">{{ row.capabilities.length ? row.capabilities.join('、') : '未配置移动端能力' }}</template></el-table-column>
      <el-table-column prop="status_name" label="状态" width="100" />
      <el-table-column label="操作" width="170" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="changeStatus(row as VolunteerAppointment)">调整状态</el-button><el-dropdown v-if="row.status === 'ACTIVE' && row.service_unit_id"><el-button link type="primary">推荐任职</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-for="rule in recommendationRules.filter(item => item.source_service_unit_id === row.service_unit_id && item.source_position_key === row.position_key && item.is_active)" :key="rule.id" @click="acceptRecommendation(row as VolunteerAppointment, rule)">{{ rule.target_service_unit_name }} / {{ rule.target_position_name }}</el-dropdown-item><el-dropdown-item v-if="!recommendationRules.some(item => item.source_service_unit_id === row.service_unit_id && item.source_position_key === row.position_key && item.is_active)" disabled>暂无配置的推荐规则</el-dropdown-item></el-dropdown-menu></template></el-dropdown></template></el-table-column>
    </el-table>

    <el-card class="rule-card" shadow="never">
      <template #header>任职推荐规则（仅提示，须人工确认）</template>
      <el-form :inline="true">
        <el-form-item label="来源服务组织"><el-select v-model="recommendationForm.source_service_unit_id" filterable><el-option v-for="unit in serviceUnits.filter(item => item.is_active)" :key="unit.id" :label="unit.name" :value="unit.id" /></el-select></el-form-item>
        <el-form-item label="来源岗位"><el-select v-model="recommendationForm.source_position_key" filterable><el-option v-for="position in sourcePositionOptions" :key="position.position_key" :label="position.position_name" :value="position.position_key" /></el-select></el-form-item>
        <el-form-item label="目标服务组织"><el-select v-model="recommendationForm.target_service_unit_id" filterable><el-option v-for="unit in serviceUnits.filter(item => item.is_active)" :key="unit.id" :label="unit.name" :value="unit.id" /></el-select></el-form-item>
        <el-form-item label="目标岗位"><el-select v-model="recommendationForm.target_position_key" filterable><el-option v-for="position in targetPositionOptions" :key="position.position_key" :label="position.position_name" :value="position.position_key" /></el-select></el-form-item>
        <el-form-item><el-button @click="submitRecommendationRule">保存规则</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-dialog v-model="appointmentDialogVisible" title="新增志工任职" width="560px">
      <el-form label-width="120px">
        <el-form-item label="在册学长 ID" required><el-input-number v-model="appointmentForm.member_id" :min="1" /><div class="field-help">仅在册学长可建立志工任职；同一学长可拥有多个当前岗位。</div></el-form-item>
        <el-form-item label="志工服务组织" required><el-select v-model="appointmentForm.service_unit_id" filterable style="width: 100%"><el-option v-for="unit in serviceUnits.filter(item => item.is_active)" :key="unit.id" :label="`${unit.name}（服务：${unit.service_target_name || '正式组织'}）`" :value="unit.id" /></el-select></el-form-item>
        <el-form-item v-if="currentServiceUnit" label="服务对象"><el-text>{{ currentServiceUnit.service_target_name }}（由服务组织确定）</el-text></el-form-item>
        <el-form-item label="岗位" required><el-select v-model="appointmentForm.position_key" filterable style="width: 100%"><el-option v-for="position in positionOptions" :key="position.position_key" :label="position.position_name" :value="position.position_key" /></el-select></el-form-item>
        <el-form-item label="业务确认说明" required><el-input v-model="appointmentForm.confirmation_note" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="appointmentDialogVisible = false">取消</el-button><el-button type="primary" @click="submitAppointment">确认建立</el-button></template>
    </el-dialog>

    <el-dialog v-model="serviceUnitDialogVisible" title="维护志工服务组织" width="620px">
      <el-alert type="warning" :closable="false" title="上级志工服务组织描述治理路径；服务对象描述实际服务的正式组织，两者不可互相推断。" show-icon />
      <el-form label-width="120px" class="dialog-form">
        <el-form-item label="编码" required><el-input v-model="serviceUnitForm.unit_code" /></el-form-item><el-form-item label="名称" required><el-input v-model="serviceUnitForm.name" /></el-form-item>
        <el-form-item label="体系"><el-select v-model="serviceUnitForm.system_type"><el-option label="班级志工团队" value="CLASS_TEAM" /><el-option label="治理组织" value="GOVERNANCE" /><el-option label="三大委纵向组织" value="COMMITTEE_LINE" /><el-option label="专项活动组织" value="ACTIVITY" /></el-select></el-form-item>
        <el-form-item label="线别"><el-select v-model="serviceUnitForm.line_type"><el-option label="学习践行线" value="LEARNING" /><el-option label="运营管理线" value="OPERATIONS" /><el-option label="发展建设线" value="DEVELOPMENT" /><el-option label="综合" value="GENERAL" /><el-option label="监事线" value="SUPERVISION" /></el-select></el-form-item>
        <el-form-item label="上级服务组织"><el-select v-model="serviceUnitForm.parent_id" clearable filterable><el-option v-for="unit in serviceUnits" :key="unit.id" :label="unit.name" :value="unit.id" /></el-select></el-form-item>
        <el-form-item label="归属塾"><el-select v-model="serviceUnitForm.home_shuku_org_unit_id" clearable filterable><el-option v-for="org in orgOptions.filter(item => item.unit_type === 'ROOT')" :key="org.id" :label="org.name" :value="org.id" /></el-select></el-form-item>
        <el-form-item label="服务对象" required><el-select v-model="serviceUnitForm.service_target_org_unit_id" filterable><el-option v-for="org in orgOptions" :key="org.id" :label="`${org.name}（${org.unit_type}）`" :value="org.id" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="serviceUnitDialogVisible = false">取消</el-button><el-button type="primary" @click="submitServiceUnit">保存</el-button></template>
    </el-dialog>

    <el-dialog v-model="migrationDialogVisible" title="历史志工任职迁移预览" width="900px"><el-alert type="warning" :closable="false" title="此页面只做预览，绝不自动迁移、结束或重写生产志工任职。" show-icon /><el-descriptions v-if="migrationPreview" :column="3" border><el-descriptions-item label="已是 V2">{{ migrationPreview.counts.ALREADY_V2 || 0 }}</el-descriptions-item><el-descriptions-item label="可人工确认">{{ migrationPreview.counts.SAFE_TO_MIGRATE || 0 }}</el-descriptions-item><el-descriptions-item label="需人工核验">{{ migrationPreview.counts.MANUAL_REVIEW || 0 }}</el-descriptions-item></el-descriptions><el-table v-if="migrationPreview" :data="migrationPreview.entries" max-height="420"><el-table-column label="原任职"><template #default="{ row }">{{ (row.appointment as any).member_name }} / {{ (row.appointment as any).position_name }}</template></el-table-column><el-table-column prop="classification" label="分类" /><el-table-column prop="reason" label="原因" min-width="380" /></el-table></el-dialog>
  </div>
</template>

<style scoped>
.volunteer-management { padding: 16px; }
.filter-card, .rule-card { margin-top: 16px; }
.toolbar { display: flex; gap: 12px; margin: 16px 0; }
.field-help { color: var(--el-text-color-secondary); font-size: 12px; line-height: 18px; margin-top: 4px; }
.dialog-form { margin-top: 16px; }
</style>
