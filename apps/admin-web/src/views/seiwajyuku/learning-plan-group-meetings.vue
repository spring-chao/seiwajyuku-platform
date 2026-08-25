<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  getLearningPlanGroupMeetingFlows,
  type LearningPlanGroupFlow,
  type LearningPlanGroupFlowCatalog,
  type LearningPlanGroupFlowStep
} from "@/api/seiwajyuku";

defineOptions({ name: "LearningPlanGroupMeetings" });

type StepDraft = Pick<LearningPlanGroupFlowStep, "title" | "content" | "is_required"> & {
  notes: string | null;
};
type FlowDraft = { steps: StepDraft[]; notes: string | null };

const router = useRouter();
const STORAGE_KEY = "seiwajyuku-learning-plan-group-flow-drafts-v1";
const loading = ref(false);
const error = ref("");
const catalog = ref<LearningPlanGroupFlowCatalog>();
const yearFilter = ref("all");
const cohortFilter = ref("all");
const cycleFilter = ref<number>();
const statusFilter = ref("all");
const search = ref("");
const drawerVisible = ref(false);
const selectedFlowKey = ref("");
const drafts = ref<Record<string, FlowDraft>>({});
const stepForms = ref<StepDraft[]>([]);
const flowNotes = ref("");

const fingerprint = computed(() => {
  if (!catalog.value) return "";
  return JSON.stringify({
    source_commit: catalog.value.source_commit,
    source_json_sha256: catalog.value.source_json_sha256,
    source_workbooks: catalog.value.source_workbooks,
    base_group_flow_source_files: catalog.value.base_group_flow_source_files,
    base_course_credit_rules_sha256: catalog.value.base_course_credit_rules_sha256
  });
});

const selectedFlow = computed(() =>
  catalog.value?.flows.find(flow => flow.flow_key === selectedFlowKey.value)
);
const changedCount = computed(() => Object.keys(drafts.value).length);

const filteredFlows = computed(() => {
  const query = search.value.trim().toLowerCase();
  return (catalog.value?.flows ?? []).filter(flow => {
    if (yearFilter.value !== "all" && String(flow.year_index) !== yearFilter.value) return false;
    if (cohortFilter.value !== "all" && !flow.eligible_cohort_months.includes(Number(cohortFilter.value))) return false;
    if (cycleFilter.value && flow.cycle_index !== cycleFilter.value) return false;
    if (statusFilter.value !== "all" && flow.status !== statusFilter.value) return false;
    if (!query) return true;
    return [flow.title, flow.source.filename, flow.source.relative_path]
      .filter(Boolean).join(" ").toLowerCase().includes(query);
  });
});

const effectiveSteps = (flow: LearningPlanGroupFlow) => drafts.value[flow.flow_key]?.steps ?? flow.steps.map(step => ({
  title: step.title, content: step.content, is_required: step.is_required, notes: null
}));

const saveLocalDraft = () => localStorage.setItem(STORAGE_KEY, JSON.stringify({ fingerprint: fingerprint.value, drafts: drafts.value }));

const loadLocalDraft = () => {
  if (!catalog.value) return;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (saved?.fingerprint === fingerprint.value && saved.drafts) drafts.value = saved.drafts;
    else { localStorage.removeItem(STORAGE_KEY); drafts.value = {}; }
  } catch { localStorage.removeItem(STORAGE_KEY); drafts.value = {}; }
};

const loadCatalog = async () => {
  loading.value = true; error.value = "";
  try { const response = await getLearningPlanGroupMeetingFlows(); catalog.value = response.data; loadLocalDraft(); }
  catch (requestError) { error.value = "小组学习会完整流程加载失败，请刷新重试。"; console.error(requestError); }
  finally { loading.value = false; }
};

const openFlow = (value: unknown) => {
  const flow = value as LearningPlanGroupFlow;
  selectedFlowKey.value = flow.flow_key;
  stepForms.value = effectiveSteps(flow).map(step => ({ ...step }));
  flowNotes.value = drafts.value[flow.flow_key]?.notes ?? "";
  drawerVisible.value = true;
};

const saveDraft = () => {
  if (!selectedFlow.value) return;
  if (stepForms.value.some(step => !step.content.trim())) { ElMessage.warning("每个流程步骤都需要填写内容"); return; }
  drafts.value = { ...drafts.value, [selectedFlow.value.flow_key]: {
    steps: stepForms.value.map(step => ({ title: step.title.trim(), content: step.content.trim(), is_required: step.is_required, notes: step.notes?.trim() || null })),
    notes: flowNotes.value.trim() || null
  }};
  saveLocalDraft(); drawerVisible.value = false;
  ElMessage.success("流程调整已暂存在本浏览器，尚未改变确认版本");
};

const resetDraft = () => {
  if (!selectedFlow.value) return;
  const next = { ...drafts.value }; delete next[selectedFlow.value.flow_key]; drafts.value = next; saveLocalDraft(); drawerVisible.value = false;
  ElMessage.success("已恢复当前确认版流程");
};

const exportDrafts = () => {
  if (!catalog.value) return;
  if (!changedCount.value) { ElMessage.warning("还没有需要导出的流程调整项"); return; }
  const payload = {
    adjustment_schema_version: 1,
    plan_key: catalog.value.plan_key,
    base_version_label: catalog.value.base_version_label,
    candidate_version_label: "2026.1",
    scope: "GROUP_MEETING_FLOW_AND_COURSE_CREDITS",
    status: "DRAFT",
    overwrite_confirmed: false,
    requires_new_review_manifest: true,
    base_source_commit: catalog.value.source_commit,
    base_source_json: catalog.value.source_json,
    base_source_json_sha256: catalog.value.source_json_sha256,
    base_source_workbooks: catalog.value.source_workbooks,
    base_group_flow_source_files: catalog.value.base_group_flow_source_files,
    base_course_credit_rules_sha256: catalog.value.base_course_credit_rules_sha256,
    credit_policy_snapshot: catalog.value.credit_policy,
    created_at: new Date().toISOString(),
    changes: Object.entries(drafts.value).map(([flow_key, change]) => ({ flow_key, steps: change.steps, notes: change.notes }))
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "group-meeting-flow-adjustments.json"; anchor.click(); URL.revokeObjectURL(url);
  ElMessage.success("已导出完整流程调整草稿；需生成新候选版本并重新审核");
};

onMounted(loadCatalog);
</script>

<template>
  <div class="learning-plan-group-meetings page-container">
    <el-card shadow="never" class="config-card">
      <template #header>
        <div class="config-header">
          <div><div class="config-title">学习计划配置 · 小组学习会完整流程</div><div class="config-subtitle">基于 2026 CONFIRMED 只读底座产生 2026.1 候选版本；流程到“空巴”结束</div></div>
          <div class="config-actions"><el-button @click="router.push('/operations/learning-plan-review')">查看学习计划审核</el-button><el-button type="primary" :disabled="!catalog || !changedCount" @click="exportDrafts">导出流程调整草稿（{{ changedCount }}）</el-button></div>
        </div>
      </template>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <template v-if="catalog">
        <el-alert class="config-notice" title="2026 CONFIRMED 版本不可覆盖。这里编辑完整小组学习会流程草稿：只读保留575条 GROUP_MEETING 源片段，流程到首个空巴为止；空巴后的班会二维码排除。保存和导出均不写数据库。" type="warning" :closable="false" show-icon />
        <div class="config-summary">
          <div class="summary-item"><span>基线版本</span><el-tag type="success">{{ catalog.base_version_label }} 已确认</el-tag></div>
          <div class="summary-item"><span>完整流程源文件</span><strong>{{ catalog.flow_count }}</strong></div>
          <div class="summary-item"><span>源片段证据</span><strong>{{ catalog.source_fragment_count }}</strong></div>
          <div class="summary-item"><span>周期映射</span><strong>{{ catalog.mapping_quality_report.mapped_count ?? 0 }} / 144</strong></div>
          <div class="summary-item credit-policy-summary"><span>小组会基础分</span><strong>{{ catalog.credit_policy.credit_points_per_person }}分/人/周期一次</strong></div>
          <div class="summary-item"><span>本地草稿</span><strong>{{ changedCount }}</strong></div>
        </div>
        <el-descriptions :column="3" border class="fingerprints">
          <el-descriptions-item label="审核提交"><span class="fingerprint">{{ catalog.source_commit }}</span></el-descriptions-item>
          <el-descriptions-item label="标准 JSON SHA-256"><span class="fingerprint">{{ catalog.source_json_sha256 }}</span></el-descriptions-item>
          <el-descriptions-item label="课程积分规则 SHA-256"><span class="fingerprint">{{ catalog.base_course_credit_rules_sha256 }}</span></el-descriptions-item>
        </el-descriptions>
        <div class="quality-strip"><el-tag type="success">首个空巴边界已识别</el-tag><el-tag type="info">空巴后二维码排除</el-tag><el-tag type="warning">二维码待人工确认 {{ catalog.quality_report.qr_review_required_count ?? 0 }}</el-tag><el-tag type="warning">映射冲突/缺失 {{ (catalog.mapping_quality_report.conflict_count ?? 0) + (catalog.mapping_quality_report.missing_count ?? 0) }}</el-tag></div>
        <div class="filters">
          <el-select v-model="yearFilter" style="width: 140px"><el-option label="全部学年" value="all" /><el-option label="第一学年" value="1" /><el-option label="第二学年" value="2" /><el-option label="第三学年" value="3" /></el-select>
          <el-select v-model="cohortFilter" style="width: 140px"><el-option label="全部开班" value="all" /><el-option label="1月开班" value="1" /><el-option label="4月开班" value="4" /><el-option label="7月开班" value="7" /><el-option label="10月开班" value="10" /></el-select>
          <el-input-number v-model="cycleFilter" :min="1" :max="36" :controls="false" placeholder="周期" clearable />
          <el-select v-model="statusFilter" style="width: 160px"><el-option label="全部解析状态" value="all" /><el-option label="已解析" value="PARSED" /><el-option label="待复核" value="REVIEW_REQUIRED" /></el-select>
          <el-input v-model="search" clearable placeholder="搜索流程标题或源文件" style="max-width: 340px" /><span class="filter-count">显示 {{ filteredFlows.length }} / {{ catalog.flow_count }} 份源流程</span>
        </div>
        <el-table v-loading="loading" :data="filteredFlows" border stripe height="calc(100vh - 500px)" row-key="flow_key" empty-text="暂无匹配的小组学习会完整流程">
          <el-table-column label="学年" width="70"><template #default="{ row }">第{{ row.year_index }}年</template></el-table-column>
          <el-table-column label="周期" width="82"><template #default="{ row }">第{{ row.cycle_index }}周期</template></el-table-column>
          <el-table-column label="适用开班" width="150"><template #default="{ row }">{{ row.eligible_cohort_months.join("、") }}月</template></el-table-column>
          <el-table-column label="流程步骤" width="100"><template #default="{ row }">{{ row.steps.length }} 步</template></el-table-column>
          <el-table-column label="课程二维码" width="120"><template #default="{ row }">{{ row.course_nodes.length }} 个</template></el-table-column>
          <el-table-column label="流程标题" min-width="260"><template #default="{ row }">{{ row.title }}</template></el-table-column>
          <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag v-if="drafts[row.flow_key]" type="warning">有调整草稿</el-tag><el-tag v-else-if="row.status === 'PARSED'" type="success">已解析</el-tag><el-tag v-else type="warning">待复核</el-tag></template></el-table-column>
          <el-table-column label="操作" width="120" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openFlow(row)">查看/调整</el-button></template></el-table-column>
        </el-table>
      </template>
    </el-card>

    <el-drawer v-model="drawerVisible" :title="selectedFlow ? `小组学习会完整流程 · 第${selectedFlow.cycle_index}周期` : '小组学习会完整流程'" size="720px">
      <template v-if="selectedFlow">
        <el-alert title="只读证据：课程积分由课程规则文件决定，不能在流程步骤上直接修改；GROUP_MEETING 基础4分按人、按周期计一次。调整保存为本地草稿，导出后生成2026.1候选版本并重新审核。" type="info" :closable="false" show-icon />
        <el-descriptions :column="2" border class="drawer-meta">
          <el-descriptions-item label="源文件" :span="2">{{ selectedFlow.source.filename }}</el-descriptions-item>
          <el-descriptions-item label="源 SHA-256" :span="2"><span class="fingerprint">{{ selectedFlow.source.sha256 }}</span></el-descriptions-item>
          <el-descriptions-item label="完整流程边界" :span="2">首个“小组学习会”标记 → 首个“空巴”，之后内容已排除</el-descriptions-item>
          <el-descriptions-item label="课程节点" :span="2"><span v-for="(node, index) in selectedFlow.course_nodes" :key="`${node.relationship_id}-${index}`" class="course-node"><el-tag :type="node.credit_status === 'MAPPED' ? 'success' : 'warning'">{{ node.course_key ?? "QR_REVIEW_REQUIRED" }} {{ node.credit_points == null ? "学分待复核" : `${node.credit_points}分` }}</el-tag></span></el-descriptions-item>
        </el-descriptions>
        <el-form label-position="top" class="adjust-form">
          <el-form-item v-for="(step, index) in stepForms" :key="index" :label="`步骤 ${index + 1}：${step.title || '未命名'}`"><el-input v-model="step.title" maxlength="255" show-word-limit placeholder="流程步骤标题" /><el-input v-model="step.content" type="textarea" :rows="4" maxlength="4000" show-word-limit class="step-content" placeholder="完整流程内容" /><el-switch v-model="step.is_required" active-text="必做" inactive-text="可选" /><el-input v-model="step.notes" type="textarea" :rows="2" maxlength="1000" show-word-limit class="step-notes" placeholder="本步骤调整说明（可选）" /></el-form-item>
          <el-form-item label="流程调整说明"><el-input v-model="flowNotes" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="填写教材变化、运营依据或复核说明" /></el-form-item>
          <div class="drawer-actions"><el-button @click="drawerVisible = false">取消</el-button><el-button v-if="drafts[selectedFlow.flow_key]" @click="resetDraft">恢复确认版</el-button><el-button type="primary" @click="saveDraft">保存流程草稿</el-button></div>
        </el-form>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.learning-plan-group-meetings { padding: 16px; }
.config-card { min-height: calc(100vh - 120px); }
.config-header, .config-actions, .config-summary, .summary-item, .filters, .drawer-actions, .quality-strip { display: flex; align-items: center; }
.config-header { justify-content: space-between; gap: 16px; }
.config-actions { gap: 8px; }
.config-title { font-size: 20px; font-weight: 700; color: var(--el-text-color-primary); }
.config-subtitle { margin-top: 6px; color: var(--el-text-color-secondary); }
.config-notice { margin-bottom: 16px; }
.config-summary { flex-wrap: wrap; gap: 24px; margin-bottom: 16px; padding: 16px; background: var(--el-fill-color-light); border-radius: 8px; }
.summary-item { gap: 10px; }
.fingerprints { margin-bottom: 16px; }
.fingerprint { word-break: break-all; font-family: monospace; }
.quality-strip { flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
.filters { flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
.filter-count { color: var(--el-text-color-secondary); font-size: 13px; }
.drawer-meta, .adjust-form { margin-top: 16px; }
.drawer-actions { justify-content: flex-end; gap: 8px; }
.course-node { display: inline-block; margin-right: 6px; margin-bottom: 6px; }
.step-content, .step-notes { margin-top: 8px; }
</style>
