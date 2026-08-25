<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  getLearningPlanCourseCreditConfig,
  createLearningPlanCourseCreditConfigVersion,
  updateLearningPlanCourseCreditConfig,
  getLearningPlanGroupMeetingFlows,
  type LearningPlanC6ReviewItem,
  type LearningPlanCourseCreditConfig,
  type LearningPlanCourseCreditRule,
  type LearningPlanGroupFlow,
  type LearningPlanGroupFlowCatalog,
  type LearningPlanGroupFlowStep
} from "@/api/seiwajyuku";

defineOptions({ name: "LearningPlanGroupMeetings" });

type StepDraft = Pick<LearningPlanGroupFlowStep, "title" | "content" | "is_required"> & {
  notes: string | null;
};
type FlowDraft = { steps: StepDraft[]; notes: string | null };
type C6Draft = {
  resolution_status: string | null;
  reviewed_by: string;
  notes: string;
  resolved_flow_key: string;
  resolved_course_key: string;
  resolved_credit_points: number | null;
};
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
const C6_STORAGE_KEY = "seiwajyuku-learning-plan-c6-review-drafts-v1";
const c6Drafts = ref<Record<string, C6Draft>>({});
const c6Search = ref("");
const c6EditorVisible = ref(false);
const advancedC6Visible = ref<string[]>([]);
const selectedC6ReviewId = ref("");
const c6Form = ref<C6Draft>({
  resolution_status: null,
  reviewed_by: "",
  notes: "",
  resolved_flow_key: "",
  resolved_course_key: "",
  resolved_credit_points: null
});
const creditConfig = ref<LearningPlanCourseCreditConfig>();
const creditValues = ref<Record<string, number>>({});
const creditSavingKey = ref("");
const creditOptions = computed(() => creditConfig.value?.available_credit_points ?? [0, 15, 20, 30, 40]);

const fingerprint = computed(() => {
  if (!catalog.value) return "";
  return JSON.stringify({
    source_commit: catalog.value.source_commit,
    source_json_sha256: catalog.value.source_json_sha256,
    source_workbooks: catalog.value.source_workbooks,
    base_group_flow_source_files: catalog.value.base_group_flow_source_files,
    base_course_credit_rules_sha256: catalog.value.base_course_credit_rules_sha256,
    c6_source_fingerprint: catalog.value.c6_review?.source_fingerprint
  });
});

const selectedFlow = computed(() =>
  catalog.value?.flows.find(flow => flow.flow_key === selectedFlowKey.value)
);
const changedCount = computed(() => Object.keys(drafts.value).length);
const c6Items = computed<LearningPlanC6ReviewItem[]>(() => {
  const review = catalog.value?.c6_review;
  if (!review) return [];
  return [
    ...review.mapping_conflicts,
    ...review.mapping_missing,
    ...review.qr_review_required,
    ...review.course_nodes,
    ...review.flow_samples
  ];
});
const c6FilteredItems = computed(() => {
  const query = c6Search.value.trim().toLowerCase();
  return c6Items.value.filter(item => {
    if (!query) return true;
    return [item.review_id, item.kind, item.mapping_key, item.flow_key, item.context_text, item.source?.filename]
      .filter(Boolean).join(" ").toLowerCase().includes(query);
  });
});
const c6EffectiveDraft = (value: unknown): C6Draft | undefined => {
  const item = value as LearningPlanC6ReviewItem;
  if (!item) return undefined;
  const saved = c6Drafts.value[item.review_id];
  if (saved) return saved;
  if (item.review_status !== "CONFIRMED" || !item.resolution_status) return undefined;
  return {
    resolution_status: item.resolution_status,
    reviewed_by: item.reviewed_by ?? "",
    notes: item.notes ?? "",
    resolved_flow_key: item.resolved_flow_key ?? "",
    resolved_course_key: item.resolved_course_key ?? "",
    resolved_credit_points: item.resolved_credit_points ?? null
  };
};
const c6PendingCount = computed(() => c6Items.value.filter(item => !c6EffectiveDraft(item)?.resolution_status).length);
const selectedC6Item = computed(() => c6Items.value.find(item => item.review_id === selectedC6ReviewId.value));
const creditRules = computed(() => creditConfig.value?.rules ?? []);
const creditRuleYears = computed(() => [1, 2, 3].map(year => ({
  year,
  label: `第${year}学年`,
  rules: creditRules.value.filter(rule => rule.year_index === year)
})));
const creditPendingGroupCount = computed(() => creditRules.value.filter(rule => rule.status === "PENDING").length);
const configuredCreditCount = computed(() => creditRules.value.filter(rule => rule.status === "CONFIGURED").length);
const creditValueFor = (rule: any) => creditValues.value[rule.course_key] ?? rule.credit_points;
const setCreditValue = (rule: any, value: number | string | boolean | undefined) => {
  if (typeof value === "boolean") return;
  const next = Number(value);
  if (Number.isInteger(next) && next >= 0 && next <= 999) creditValues.value = { ...creditValues.value, [rule.course_key]: next };
};
const confirmedCourseNodeCount = computed(() => c6Items.value.filter(item => {
  if (item.kind !== "COURSE_NODE") return false;
  const draft = c6EffectiveDraft(item);
  return draft?.resolution_status === "COURSE_CONFIRMED" || draft?.resolution_status === "COURSE_CONFIRMED_CREDIT_PENDING";
}).length);
const technicalPendingCount = computed(() => c6Items.value.filter(item => {
  if (item.kind === "QR_REVIEW_REQUIRED") return false;
  return !c6EffectiveDraft(item)?.resolution_status;
}).length);
const sourceMissingCount = computed(() => c6Items.value.filter(item => c6EffectiveDraft(item)?.resolution_status === "SOURCE_MISSING").length);
const c6StatusOptions = computed(() => {
  const kind = selectedC6Item.value?.kind;
  if (kind === "MAPPING_CONFLICT") return ["MAPPED"];
  if (kind === "MAPPING_MISSING") return ["MAPPED", "EXEMPTED", "SOURCE_MISSING"];
  if (kind === "FLOW_SAMPLE") return ["CONFIRMED"];
  return ["COURSE_CONFIRMED", "COURSE_CONFIRMED_CREDIT_PENDING", "NON_COURSE_QR", "EXCLUDED_AFTER_KONPA"];
});

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

const loadLocalC6Draft = () => {
  if (!catalog.value) return;
  try {
    const saved = JSON.parse(localStorage.getItem(C6_STORAGE_KEY) ?? "null");
    if (saved?.fingerprint === fingerprint.value && saved.drafts) c6Drafts.value = saved.drafts;
    else { localStorage.removeItem(C6_STORAGE_KEY); c6Drafts.value = {}; }
  } catch { localStorage.removeItem(C6_STORAGE_KEY); c6Drafts.value = {}; }
};

const flowReviewSignature = (flow: LearningPlanGroupFlow) => JSON.stringify({
  boundary: flow.boundary,
  steps: flow.steps.map(step => ({ title: step.title, content: step.content, is_required: step.is_required })),
  course_nodes: flow.course_nodes.map(node => ({
    node_type: node.node_type,
    media_target: node.media_target,
    context_text: node.context_text,
    course_key: node.course_key,
    credit_points: node.credit_points,
    credit_status: node.credit_status
  }))
});

const autoConfirmTechnicalItems = () => {
  if (!catalog.value) return;
  const next = { ...c6Drafts.value };
  for (const item of c6Items.value) {
    if (next[item.review_id]) continue;
    if (item.kind === "FLOW_SAMPLE") {
      next[item.review_id] = {
        resolution_status: "CONFIRMED",
        reviewed_by: "系统自动核对",
        notes: "系统已核对：从首个小组学习会开始，到首个空巴结束；空巴后的班会二维码已排除。",
        resolved_flow_key: "",
        resolved_course_key: "",
        resolved_credit_points: null
      };
      continue;
    }
    if (item.kind === "COURSE_NODE" && item.source_credit_status === "MAPPED" && item.source_course_key) {
      next[item.review_id] = {
        resolution_status: "COURSE_CONFIRMED",
        reviewed_by: "系统自动核对",
        notes: "系统已按正式课程积分规则核对。",
        resolved_flow_key: "",
        resolved_course_key: item.source_course_key,
        resolved_credit_points: typeof item.source_credit_points === "number" ? item.source_credit_points : null
      };
      continue;
    }
    if (item.kind === "MAPPING_CONFLICT" && item.candidate_flow_keys?.length) {
      const candidates = item.candidate_flow_keys
        .map(key => catalog.value?.flows.find(flow => flow.flow_key === key))
        .filter((flow): flow is LearningPlanGroupFlow => Boolean(flow));
      if (candidates.length === item.candidate_flow_keys.length && candidates.every(flow => flowReviewSignature(flow) === flowReviewSignature(candidates[0]))) {
        next[item.review_id] = {
          resolution_status: "MAPPED",
          reviewed_by: "系统自动核对",
          notes: "候选流程的步骤、空巴边界和课程节点完全一致，系统固定使用首个源文件作为证据键。",
          resolved_flow_key: item.candidate_flow_keys[0],
          resolved_course_key: "",
          resolved_credit_points: null
        };
      }
      continue;
    }
    if (item.kind === "MAPPING_MISSING") {
      next[item.review_id] = {
        resolution_status: "SOURCE_MISSING",
        reviewed_by: "系统自动核对",
        notes: "系统已核对权威目录：对应文件目前标记为“待完善”，未纳入正式运行源；该项由后台补齐，不需要运营人员选择流程。",
        resolved_flow_key: "",
        resolved_course_key: "",
        resolved_credit_points: null
      };
    }
  }
  c6Drafts.value = next;
  localStorage.setItem(C6_STORAGE_KEY, JSON.stringify({ fingerprint: fingerprint.value, drafts: next }));
};

const loadCatalog = async () => {
  loading.value = true; error.value = "";
  try {
    const [flowResponse, creditResponse] = await Promise.all([
      getLearningPlanGroupMeetingFlows(),
      getLearningPlanCourseCreditConfig({ plan_key: "STANDARD_3Y_2026", version_label: "2026.1" })
    ]);
    catalog.value = flowResponse.data;
    creditConfig.value = creditResponse.data;
    creditValues.value = Object.fromEntries(creditResponse.data.rules.map(rule => [rule.course_key, rule.credit_points]));
    loadLocalDraft();
    loadLocalC6Draft();
    autoConfirmTechnicalItems();
  }
  catch (requestError) { error.value = "小组学习会完整流程加载失败，请刷新重试。"; console.error(requestError); }
  finally { loading.value = false; }
};

const openC6Item = (value: unknown) => {
  const item = value as LearningPlanC6ReviewItem;
  selectedC6ReviewId.value = item.review_id;
  const saved = c6Drafts.value[item.review_id];
  c6Form.value = saved ? { ...saved } : {
    resolution_status: item.resolution_status,
    reviewed_by: item.reviewed_by ?? "",
    notes: item.notes ?? "",
    resolved_flow_key: item.resolved_flow_key ?? "",
    resolved_course_key: item.resolved_course_key ?? item.source_course_key ?? "",
    resolved_credit_points: item.resolved_credit_points ?? item.source_credit_points ?? null
  };
  c6EditorVisible.value = true;
};

const saveC6Item = () => {
  const item = selectedC6Item.value;
  const form = c6Form.value;
  if (!item || !form.resolution_status) { ElMessage.warning("请选择业务结论"); return; }
  if (!form.reviewed_by.trim()) { ElMessage.warning("请填写审核人"); return; }
  if (item.kind === "MAPPING_CONFLICT" && !form.resolved_flow_key.trim()) { ElMessage.warning("冲突项必须选择正确流程"); return; }
  if (item.kind === "MAPPING_MISSING" && form.resolution_status === "MAPPED" && !form.resolved_flow_key.trim()) { ElMessage.warning("MAPPED 必须填写流程"); return; }
  if (["COURSE_CONFIRMED", "COURSE_CONFIRMED_CREDIT_PENDING"].includes(form.resolution_status) && !form.resolved_course_key.trim()) { ElMessage.warning("课程确认项必须填写 course_key"); return; }
  if (form.resolution_status === "COURSE_CONFIRMED" && form.resolved_credit_points == null) { ElMessage.warning("COURSE_CONFIRMED 必须填写积分"); return; }
  c6Drafts.value = { ...c6Drafts.value, [item.review_id]: {
    ...form,
    reviewed_by: form.reviewed_by.trim(),
    notes: form.notes.trim(),
    resolved_flow_key: form.resolved_flow_key.trim(),
    resolved_course_key: form.resolved_course_key.trim()
  }};
  localStorage.setItem(C6_STORAGE_KEY, JSON.stringify({ fingerprint: fingerprint.value, drafts: c6Drafts.value }));
  c6EditorVisible.value = false;
  ElMessage.success("C6 复核暂存在本浏览器，导出后交由后端校验");
};

const saveCreditRule = async (rule: any) => {
  if (!creditConfig.value?.can_manage || !creditConfig.value.can_edit || !creditConfig.value.storage_available) {
    ElMessage.warning("当前环境只读，需受控开启积分配置写入");
    return;
  }
  const points = creditValueFor(rule);
  creditSavingKey.value = rule.course_key;
  try {
    const response = await updateLearningPlanCourseCreditConfig(rule.course_key, {
      version_label: creditConfig.value.version_label,
      credit_points: points,
      status: "CONFIGURED",
      course_name: rule.course_name,
      year_index: rule.year_index ?? undefined,
      aliases: rule.aliases
    });
    creditConfig.value = response.data;
    creditValues.value = Object.fromEntries(response.data.rules.map(item => [item.course_key, item.credit_points]));
    ElMessage.success(`${rule.course_name} 已保存为 ${points} 分；后续周期自动沿用`);
  } catch (requestError: any) {
    const status = requestError?.response?.status;
    ElMessage.error(status === 403 ? "当前部署仍为只读，积分配置写入尚未开启" : "积分配置保存失败，请稍后重试");
  } finally {
    creditSavingKey.value = "";
  }
};

const createCreditVersion = async () => {
  if (!creditConfig.value?.can_manage || !creditConfig.value.storage_available) {
    ElMessage.warning("当前账号没有创建积分版本的权限");
    return;
  }
  try {
    const prompt = await ElMessageBox.prompt("请输入新的候选版本号，例如 2026.2", "创建课程积分候选版本", {
      confirmButtonText: "创建",
      cancelButtonText: "取消",
      inputPattern: /^[A-Za-z0-9._-]{1,64}$/,
      inputErrorMessage: "版本号只能包含字母、数字、点、下划线或短横线"
    });
    const response = await createLearningPlanCourseCreditConfigVersion({
      plan_key: creditConfig.value.plan_key,
      version_label: prompt.value,
      based_on_version_label: creditConfig.value.version_label
    });
    creditConfig.value = response.data;
    creditValues.value = Object.fromEntries(response.data.rules.map(item => [item.course_key, item.credit_points]));
    ElMessage.success(`已创建 ${prompt.value} 候选版本，后续调整不会影响原版本`);
  } catch (requestError: any) {
    if (requestError === "cancel" || requestError?.message === "cancel") return;
    ElMessage.error("候选版本创建失败，请检查版本号或稍后重试");
  }
};

const exportC6Review = () => {
  if (!catalog.value) return;
  const review = JSON.parse(JSON.stringify(catalog.value.c6_review));
  for (const item of [...review.mapping_conflicts, ...review.mapping_missing, ...review.qr_review_required, ...review.course_nodes, ...review.flow_samples]) {
    const draft = c6Drafts.value[item.review_id];
    if (!draft) continue;
    item.review_status = "CONFIRMED";
    item.resolution_status = draft.resolution_status;
    item.reviewed_by = draft.reviewed_by;
    item.reviewed_at = new Date().toISOString();
    item.notes = draft.notes || null;
    item.resolved_flow_key = draft.resolved_flow_key || null;
    item.resolved_course_key = draft.resolved_course_key || null;
    item.resolved_credit_points = draft.resolution_status === "COURSE_CONFIRMED" ? draft.resolved_credit_points : null;
  }
  review.status = "PENDING";
  review.candidate_status = "DRAFT";
  review.exported_at = new Date().toISOString();
  review.export_note = "浏览器本地差异复核导出；须使用 scripts/review_group_meeting_c6.py --verify 校验指纹与逐项门禁。";
  const blob = new Blob([JSON.stringify(review, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "standard-3y-2026.1.review.json"; anchor.click(); URL.revokeObjectURL(url);
  ElMessage.success("已导出 C6 差异审核清单；未写数据库");
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
          <div class="config-actions"><el-button @click="router.push('/operations/learning-plan-review')">查看学习计划审核</el-button><el-button v-if="creditConfig" :disabled="!creditConfig.can_manage || !creditConfig.storage_available" @click="createCreditVersion">创建新积分候选版本</el-button><el-button type="primary" :disabled="!catalog || !changedCount" @click="exportDrafts">导出流程调整草稿（高级 {{ changedCount }}）</el-button></div>
        </div>
      </template>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <template v-if="catalog">
        <el-alert class="config-notice" :title="`${catalog.base_version_label} CONFIRMED 版本不可覆盖。当前课程积分版本为 ${creditConfig?.version_label ?? '2026.1'}；流程和空巴边界由系统自动核对，积分修改从候选版本生效。`" type="warning" :closable="false" show-icon />
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
        <div class="quality-strip"><el-tag type="success">首个空巴边界已识别</el-tag><el-tag type="info">空巴后二维码排除</el-tag><el-tag type="success">流程由系统自动核对</el-tag><el-tag type="warning">待配置课程 {{ creditPendingGroupCount }} 门</el-tag><el-tag type="success">已配置 {{ configuredCreditCount }} 门</el-tag></div>
        <el-card shadow="never" class="c6-card">
          <template #header>
            <div class="c6-header">
              <div>
                <strong>课程积分配置</strong>
                <div class="c6-subtitle">系统已核对课程和流程。第一学年正式积分已自动带入；新课程默认0分/待配置，修改一次后全局沿用。</div>
              </div>
              <el-tag :type="creditPendingGroupCount === 0 ? 'success' : 'warning'">{{ creditPendingGroupCount === 0 ? '全部已配置' : `待配置 ${creditPendingGroupCount} 门` }}</el-tag>
            </div>
          </template>
          <el-alert
            title="您不需要选择流程，也不需要判断二维码。只需在课程行选择积分或输入自定义积分，然后点击“保存配置”。"
            type="success"
            :closable="false"
            show-icon
          />
          <div class="c6-summary friendly-summary">
            <el-tag type="success">流程边界已核对 {{ catalog.flow_count }} 份</el-tag>
            <el-tag type="success">已识别课程节点 {{ confirmedCourseNodeCount }} 个</el-tag>
            <el-tag type="warning">待配置课程 {{ creditPendingGroupCount }} 门</el-tag>
            <el-tag v-if="sourceMissingCount" type="info">系统待补齐流程 {{ sourceMissingCount }} 项</el-tag>
            <el-tag type="info">小组会出席基础分 {{ catalog.credit_policy.credit_points_per_person }} 分/人/周期</el-tag>
          </div>
          <el-alert v-if="creditConfig && !creditConfig.can_manage" title="当前账号只有查看权限；如需维护积分，请由系统管理员授予“维护学习计划课程积分标准”权限。" type="info" :closable="false" show-icon />
          <el-alert v-if="creditConfig && !creditConfig.storage_available" title="积分配置数据表尚未进入当前部署的数据库；目前可查看标准目录，写入将在受控迁移后开放。" type="warning" :closable="false" show-icon />
          <el-alert v-else-if="creditConfig && !creditConfig.can_edit" title="当前积分版本不可修改；如需调整已发布规则，请先创建新的候选版本。" type="warning" :closable="false" show-icon />
          <div v-for="group in creditRuleYears" :key="group.year" class="credit-year-section">
            <div class="credit-year-title">{{ group.label }} <span class="credit-year-count">{{ group.rules.length }} 门课程</span></div>
            <el-table :data="group.rules" border stripe row-key="course_key" empty-text="暂无课程配置">
              <el-table-column label="课程" min-width="280">
                <template #default="{ row }"><div class="course-review-name">{{ row.course_name }}</div><div class="course-review-meta">{{ row.source === 'BASELINE' ? '第一学年正式标准' : row.status === 'PENDING' ? '系统默认0分，等待配置' : '运营配置，后续自动沿用' }}</div></template>
              </el-table-column>
              <el-table-column label="积分" min-width="360">
                <template #default="{ row }">
                  <div class="credit-editor"><el-radio-group :model-value="creditValueFor(row)" :disabled="!creditConfig?.can_manage || !creditConfig?.can_edit" size="small" @update:model-value="value => setCreditValue(row, value)"><el-radio-button v-for="value in creditOptions" :key="value" :label="value">{{ value }}分</el-radio-button></el-radio-group><el-input-number :model-value="creditValueFor(row)" :min="0" :max="999" :precision="0" :controls="false" :disabled="!creditConfig?.can_manage || !creditConfig?.can_edit" class="custom-credit-input" @update:model-value="value => setCreditValue(row, value ?? undefined)" /><span>自定义</span></div>
                </template>
              </el-table-column>
              <el-table-column label="当前状态" width="150"><template #default="{ row }"><el-tag :type="row.status === 'CONFIGURED' ? 'success' : 'warning'">{{ row.credit_points }}分 · {{ row.status === 'CONFIGURED' ? '已配置' : '待配置' }}</el-tag></template></el-table-column>
              <el-table-column label="操作" width="130" fixed="right"><template #default="{ row }"><el-button type="primary" :loading="creditSavingKey === row.course_key" :disabled="!creditConfig?.can_manage || !creditConfig?.can_edit" @click="saveCreditRule(row)">保存配置</el-button></template></el-table-column>
            </el-table>
          </div>
          <el-collapse v-model="advancedC6Visible" class="advanced-c6">
            <el-collapse-item title="查看系统核对详情（高级，不需要您操作）" name="details">
              <el-alert v-if="technicalPendingCount" title="仍有技术项待后台处理，已由系统记录，不需要您选择流程。" type="warning" :closable="false" show-icon />
              <div class="c6-toolbar"><el-input v-model="c6Search" clearable placeholder="搜索技术核对项" style="max-width: 360px" /><span class="filter-count">系统核对项 {{ c6FilteredItems.length }} / {{ c6Items.length }}</span></div>
              <el-table :data="c6FilteredItems" border stripe max-height="430" row-key="review_id" empty-text="暂无 C6 复核项">
                <el-table-column label="类型" width="150"><template #default="{ row }"><el-tag size="small" :type="row.kind === 'MAPPING_CONFLICT' ? 'danger' : row.kind === 'MAPPING_MISSING' || row.kind === 'QR_REVIEW_REQUIRED' ? 'warning' : 'info'">{{ row.kind }}</el-tag></template></el-table-column>
                <el-table-column label="周期/流程" min-width="190"><template #default="{ row }">{{ row.mapping_key || row.flow_key || row.review_id }}</template></el-table-column>
                <el-table-column label="系统结论" min-width="280" show-overflow-tooltip><template #default="{ row }">{{ c6EffectiveDraft(row)?.notes || row.context_text || row.review_prompt }}</template></el-table-column>
                <el-table-column label="状态" width="170"><template #default="{ row }"><el-tag v-if="c6EffectiveDraft(row)?.resolution_status" type="success">{{ c6EffectiveDraft(row)?.resolution_status }}</el-tag><el-tag v-else type="warning">待后台处理</el-tag></template></el-table-column>
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </el-card>
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

    <el-dialog v-model="c6EditorVisible" :title="selectedC6Item ? `C6 复核 · ${selectedC6Item.kind}` : 'C6 复核'" width="680px">
      <template v-if="selectedC6Item">
        <el-alert :title="selectedC6Item.review_prompt || '请填写业务复核结论'" type="info" :closable="false" show-icon />
        <el-descriptions :column="2" border class="c6-editor-meta">
          <el-descriptions-item label="审核项" :span="2">{{ selectedC6Item.review_id }}</el-descriptions-item>
          <el-descriptions-item label="周期/流程">{{ selectedC6Item.mapping_key || selectedC6Item.flow_key || '流程抽查' }}</el-descriptions-item>
          <el-descriptions-item label="源文件">{{ selectedC6Item.source?.filename || '-' }}</el-descriptions-item>
          <el-descriptions-item label="原始课程">{{ selectedC6Item.source_course_key || '待确认' }}</el-descriptions-item>
          <el-descriptions-item label="原始积分">{{ selectedC6Item.source_credit_points ?? 'NULL' }}</el-descriptions-item>
          <el-descriptions-item v-if="selectedC6Item.context_text" label="上下文" :span="2">{{ selectedC6Item.context_text }}</el-descriptions-item>
        </el-descriptions>
        <el-form label-position="top" class="c6-editor-form">
          <el-form-item label="业务结论"><el-select v-model="c6Form.resolution_status" placeholder="选择结论" style="width: 100%"><el-option v-for="status in c6StatusOptions" :key="status" :label="status" :value="status" /></el-select></el-form-item>
          <el-form-item v-if="['MAPPING_CONFLICT', 'MAPPING_MISSING'].includes(selectedC6Item.kind) && c6Form.resolution_status === 'MAPPED'" label="对应流程 flow_key"><el-select v-model="c6Form.resolved_flow_key" filterable allow-create default-first-option placeholder="选择或填写流程" style="width: 100%"><el-option v-for="key in selectedC6Item.candidate_flow_keys || []" :key="key" :label="key" :value="key" /></el-select></el-form-item>
          <el-form-item v-if="['COURSE_CONFIRMED', 'COURSE_CONFIRMED_CREDIT_PENDING'].includes(c6Form.resolution_status || '')" label="课程 course_key"><el-input v-model="c6Form.resolved_course_key" placeholder="填写正式课程标识" /></el-form-item>
          <el-form-item v-if="c6Form.resolution_status === 'COURSE_CONFIRMED'" label="课程积分"><el-input-number v-model="c6Form.resolved_credit_points" :min="0" :max="999" :precision="0" controls-position="right" /></el-form-item>
          <el-form-item label="审核人"><el-input v-model="c6Form.reviewed_by" placeholder="姓名/账号" /></el-form-item>
          <el-form-item label="审核说明"><el-input v-model="c6Form.notes" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="填写业务依据、异常处理或课程积分依据" /></el-form-item>
        </el-form>
      </template>
      <template #footer><el-button @click="c6EditorVisible = false">取消</el-button><el-button type="primary" @click="saveC6Item">保存本地复核</el-button></template>
    </el-dialog>

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
.c6-card { margin-bottom: 18px; }
.c6-header, .c6-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.c6-subtitle { margin-top: 5px; color: var(--el-text-color-secondary); font-size: 13px; }
.c6-summary { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.friendly-summary { margin-top: 14px; }
.credit-year-section { margin-top: 18px; }
.credit-year-title { margin: 14px 0 8px; font-size: 16px; font-weight: 700; color: var(--el-text-color-primary); }
.credit-year-count { margin-left: 8px; font-size: 13px; font-weight: 400; color: var(--el-text-color-secondary); }
.credit-editor { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.custom-credit-input { width: 90px; }
.course-review-name { font-weight: 600; color: var(--el-text-color-primary); }
.course-review-meta { margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
.advanced-c6 { margin-top: 16px; }
.c6-toolbar { justify-content: flex-start; margin-bottom: 12px; }
.c6-editor-meta, .c6-editor-form { margin-top: 16px; }
</style>
