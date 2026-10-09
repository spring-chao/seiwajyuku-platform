<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import { creditSettlementErrorMessage } from "@/utils/creditSettlementError";
import {
  approveSettlementBatch,
  closeSettlementBatch,
  dryRunActivityCreditBatch,
  dryRunClassMeetingBatch,
  dryRunHistoricalCreditBatches,
  dryRunStudyMeetingBatch,
  getSettlementBatch,
  getSettlementBatches,
  getCreditLedgerOverview,
  getLearningCreditEntries,
  postSettlementBatch,
  reconcileStalePostingBatch,
  reverseLearningCreditEntry,
  submitSettlementBatch,
  type LearningCreditLedgerEntry,
  type CreditLedgerOverview,
  type SettlementBatchDetail,
  type SettlementBatchList,
  type SettlementBatchListItem,
  type SettlementBatchStatus,
  type SettlementBatchType
} from "@/api/learning-credits";

defineOptions({ name: "LearningCreditSettlements" });

type SourceKind = "STUDY_MEETING" | "CLASS_MEETING" | "DAILY_READING" | "EXCELLENT_SHARE" | "HISTORICAL_IMPORT";

const permissions = computed(() => useUserStoreHook().permissions || []);
const canManage = computed(() => permissions.value.includes("plans:credit_settlement_manage"));
const canApprove = computed(() => permissions.value.includes("plans:credit_settlement_approve"));
const canPost = computed(() => permissions.value.includes("plans:credit_settlement_post"));
const canReconcile = computed(() => permissions.value.includes("plans:credit_settlement_reconcile"));
const canClose = computed(() => permissions.value.includes("plans:credit_settlement_close"));
const canReverse = computed(() => permissions.value.includes("plans:credit_settlement_reverse"));
const canManageHistory = computed(() => permissions.value.includes("plans:historical_credit_import_manage"));
const canReadLedger = computed(() => permissions.value.includes("plans:credit_settlement_preview"));
const loading = ref(false);
const ledgerLoading = ref(false);
const overviewLoading = ref(false);
const acting = ref(false);
const error = ref("");
const ledgerError = ref("");
const overviewError = ref("");
const response = ref<SettlementBatchList>();
const batches = ref<SettlementBatchListItem[]>([]);
const totalCount = ref(0);
const detailVisible = ref(false);
const detailLoading = ref(false);
const detail = ref<SettlementBatchDetail>();
const ledgerEntries = ref<LearningCreditLedgerEntry[]>([]);
const ledgerOverview = ref<CreditLedgerOverview>();
const sourceKind = ref<SourceKind>("STUDY_MEETING");
const sourceId = ref("");
const activityForm = reactive({
  class_org_unit_id: "",
  occurred_from: "",
  occurred_to: ""
});
const filters = reactive<{
  status: "" | SettlementBatchStatus;
  batch_type: "" | SettlementBatchType;
}>({ status: "", batch_type: "" });
const ledgerFilters = reactive({
  member_id: "",
  occurred_from: "",
  occurred_to: "",
  credit_category: ""
});

const gate = (key: keyof SettlementBatchList["feature_gates"]) =>
  response.value?.feature_gates[key] === true;
const writesReady = computed(() => response.value?.storage_available === true && gate("dry_run_enabled"));
const statusLabel = (status: SettlementBatchStatus) => ({
  DRAFT: "草稿", DRY_RUN: "待提交", PENDING_APPROVAL: "待审批", APPROVED: "已批准",
  POSTING: "入账中", POSTED: "已入账", PARTIAL_FAILED: "部分失败", CLOSED: "已封账", CANCELLED: "已取消"
}[status]);
const statusType = (status: SettlementBatchStatus) => ({
  DRAFT: "info", DRY_RUN: "warning", PENDING_APPROVAL: "warning", APPROVED: "success",
  POSTING: "primary", POSTED: "success", PARTIAL_FAILED: "danger", CLOSED: "info", CANCELLED: "info"
}[status] as "info" | "warning" | "success" | "primary" | "danger");
const sourceLabel = (source: string) => ({
  STUDY_MEETING: "小组学习会", CLASS_MEETING: "班级学习会",
  DAILY_READING: "每日读书", EXCELLENT_SHARE: "优秀分享", LEGACY_SUZHOU_2026_V1: "历史学分"
}[source] || source);
const periodLabel = (row: any) => {
  if (row.period_precision === "YEAR") return `${row.period_year} 年`;
  if (row.period_precision === "MONTH") return `${row.period_year}-${String(row.period_month || 0).padStart(2, "0")}`;
  return `${row.period_start || "?"} 至 ${row.period_end || "?"}`;
};
const errorMessage = creditSettlementErrorMessage;

const loadBatches = async () => {
  loading.value = true;
  error.value = "";
  try {
    const result = await getSettlementBatches({
      status: filters.status || undefined,
      batch_type: filters.batch_type || undefined,
      limit: 100,
      offset: 0
    });
    response.value = result.data;
    batches.value = result.data.batches || [];
    totalCount.value = result.data.total_count || 0;
  } catch (err) {
    error.value = errorMessage(err, "结算批次读取失败");
    batches.value = [];
    ElMessage.error(error.value);
  } finally {
    loading.value = false;
  }
};

const loadLedger = async () => {
  if (!canReadLedger.value) return;
  ledgerLoading.value = true;
  ledgerError.value = "";
  try {
    const memberId = ledgerFilters.member_id.trim() ? Number(ledgerFilters.member_id) : undefined;
    if (memberId !== undefined && (!Number.isSafeInteger(memberId) || memberId < 1)) {
      throw new Error("学员 ID 必须为正整数");
    }
    const result = await getLearningCreditEntries({
      member_id: memberId,
      occurred_from: ledgerFilters.occurred_from || undefined,
      occurred_to: ledgerFilters.occurred_to || undefined,
      credit_category: ledgerFilters.credit_category || undefined
    });
    ledgerEntries.value = result.data.entries || [];
  } catch (err) {
    ledgerError.value = errorMessage(err, "学分账本读取失败");
    ledgerEntries.value = [];
    ElMessage.error(ledgerError.value);
  } finally { ledgerLoading.value = false; }
};

const loadLedgerOverview = async () => {
  if (!canReadLedger.value) return;
  overviewLoading.value = true;
  overviewError.value = "";
  try {
    ledgerOverview.value = (await getCreditLedgerOverview()).data;
  } catch (err) {
    overviewError.value = errorMessage(err, "学分总览读取失败");
    ledgerOverview.value = undefined;
  } finally {
    overviewLoading.value = false;
  }
};

const refreshLedger = async () => {
  await Promise.all([loadLedger(), loadLedgerOverview()]);
};

const categoryLabel = (category: string) => ({
  STANDARD_LEARNING: "标准学习",
  EXTENSION_ACTIVITY: "拓展活动"
}[category] || category);

const openDetail = async (batch: any) => {
  detailVisible.value = true;
  detailLoading.value = true;
  detail.value = undefined;
  try {
    detail.value = (await getSettlementBatch(batch.id)).data;
  } catch (err) {
    ElMessage.error(errorMessage(err, "批次详情读取失败"));
  } finally {
    detailLoading.value = false;
  }
};

const runDryRun = async () => {
  if (!writesReady.value || !canManage.value || acting.value) return;
  const id = Number(sourceId.value);
  if (["STUDY_MEETING", "CLASS_MEETING", "HISTORICAL_IMPORT"].includes(sourceKind.value) && (!Number.isSafeInteger(id) || id < 1)) {
    ElMessage.warning("请输入有效的来源记录 ID");
    return;
  }
  if (["DAILY_READING", "EXCELLENT_SHARE"].includes(sourceKind.value) &&
      (!activityForm.class_org_unit_id.trim() || !activityForm.occurred_from || !activityForm.occurred_to)) {
    ElMessage.warning("请填写班级组织 ID 和完整日期范围");
    return;
  }
  acting.value = true;
  try {
    if (sourceKind.value === "STUDY_MEETING") {
      await dryRunStudyMeetingBatch(id);
    } else if (sourceKind.value === "CLASS_MEETING") {
      await dryRunClassMeetingBatch(id);
    } else if (sourceKind.value === "HISTORICAL_IMPORT") {
      if (!canManageHistory.value) throw new Error("当前账号没有历史学分管理权限");
      const result = await dryRunHistoricalCreditBatches(id);
      const data = result.data;
      ElMessage.success(`历史 DRY-RUN 完成：${data.settlement_batches?.length || 0} 个批次；就绪 ${data.ready_item_count || 0} 条，阻塞 ${data.group_blocked_item_count || 0} 条`);
    } else {
      await dryRunActivityCreditBatch(
        sourceKind.value === "DAILY_READING" ? "daily-reading" : "excellent-share",
        activityForm
      );
    }
    if (sourceKind.value !== "HISTORICAL_IMPORT") ElMessage.success("DRY-RUN 批次已生成；正式账本未写入");
    await loadBatches();
  } catch (err) {
    ElMessage.error(errorMessage(err, "DRY-RUN 失败；未执行正式入账"));
  } finally {
    acting.value = false;
  }
};

const refreshDetail = async () => {
  if (detail.value) await openDetail(detail.value);
  await loadBatches();
  await refreshLedger();
};

const submitForApproval = async () => {
  if (!detail.value || acting.value || !canManage.value || !gate("dry_run_enabled")) return;
  const batch = detail.value;
  try {
    await ElMessageBox.confirm(
      `提交批次 ${batch.batch_no} 的 ${batch.proposed_entry_count} 条就绪提案（${batch.proposed_points} 分）供独立审批？${batch.blocked_count ? `另有 ${batch.blocked_count} 条阻塞记录会留在批次中，不会进入审批或入账。` : ""}`,
      "提交结算批次",
      { type: "warning", confirmButtonText: "提交审批", cancelButtonText: "取消" }
    );
  } catch { return; }
  acting.value = true;
  try {
    await submitSettlementBatch(batch.id);
    ElMessage.success("已提交独立审批；正式账本未写入");
    await refreshDetail();
  } catch (err) {
    ElMessage.error(errorMessage(err, "提交审批失败"));
  } finally { acting.value = false; }
};

const approve = async () => {
  if (!detail.value || acting.value || !canApprove.value || !gate("approval_enabled")) return;
  const batch = detail.value;
  if (batch.created_by_current_actor) {
    ElMessage.warning("批次创建人不能审批自己的批次，请由另一位有审批权限的运营人员处理");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `批准批次 ${batch.batch_no} 的 ${batch.proposed_entry_count} 条就绪提案（${batch.proposed_points} 分）？本动作只批准冻结提案，不写入正式账本。${batch.blocked_count ? `另有 ${batch.blocked_count} 条阻塞记录保持未入账。` : ""}`,
      "独立审批",
      { type: "warning", confirmButtonText: "批准提案", cancelButtonText: "取消" }
    );
  } catch { return; }
  acting.value = true;
  try {
    await approveSettlementBatch(batch.id);
    ElMessage.success("审批完成；正式账本未写入");
    await refreshDetail();
  } catch (err) {
    ElMessage.error(errorMessage(err, "批次审批失败"));
  } finally { acting.value = false; }
};

const post = async (resume = false) => {
  if (!detail.value || acting.value || !canPost.value || !canManage.value || !gate("post_enabled") || !gate("settlement_enabled")) return;
  const batch = detail.value;
  if (batch.batch_type === "HISTORICAL_IMPORT" && (!canManageHistory.value || !gate("historical_post_enabled"))) return;
  const partial = batch.status === "PARTIAL_FAILED";
  if (partial && !resume) return;
  try {
    await ElMessageBox.confirm(
      partial
        ? `确认续跑批次 ${batch.batch_no}？系统只会对账并处理尚未入账的失败/待处理条目，已入账记录依靠幂等键保护。`
        : `确认将批次 ${batch.batch_no} 的 ${batch.proposed_entry_count} 条就绪提案（${batch.proposed_points} 分）正式写入学分账本？${batch.blocked_count ? `另有 ${batch.blocked_count} 条阻塞记录不入账。` : ""}`,
      partial ? "续跑部分失败批次" : "正式入账确认",
      { type: "warning", confirmButtonText: partial ? "确认续跑" : "确认入账", cancelButtonText: "取消" }
    );
  } catch { return; }
  acting.value = true;
  try {
    await postSettlementBatch(batch.id, partial);
    ElMessage.success(partial ? "批次续跑与对账完成" : "正式入账完成");
    await refreshDetail();
  } catch (err) {
    ElMessage.error(errorMessage(err, "正式入账失败；请保留批次状态并核对详情"));
  } finally { acting.value = false; }
};

const reconcilePosting = async () => {
  if (!detail.value || acting.value || !canManage.value || !canReconcile.value || !gate("post_enabled")) return;
  if (detail.value.batch_type === "HISTORICAL_IMPORT" && !canManageHistory.value) return;
  const batch = detail.value;
  try {
    await ElMessageBox.confirm(
      `确认对批次 ${batch.batch_no} 执行中断对账？系统只读取并核对现有账本记录，再将批次状态收口为“已入账”或“部分失败”；不会新增、修改或删除任何学分账本记录。仅当最后心跳已超过30分钟时才会执行。`,
      "恢复入账中断批次",
      { type: "warning", confirmButtonText: "只读对账并收口状态", cancelButtonText: "取消" }
    );
  } catch { return; }
  acting.value = true;
  try {
    await reconcileStalePostingBatch(batch.id);
    ElMessage.success("中断对账完成；账本未写入");
    await refreshDetail();
  } catch (err) {
    ElMessage.error(errorMessage(err, "对账恢复未完成；请保留批次状态并核对"));
  } finally { acting.value = false; }
};

const closeBatch = async () => {
  if (!detail.value || acting.value || !canManage.value || !canClose.value ||
      !gate("post_enabled") || !gate("settlement_enabled") || detail.value.status !== "POSTED") return;
  const batch = detail.value;
  try {
    await ElMessageBox.confirm(
      `封账前会只读核对本批次所有就绪提案与正式账本。封账不会新增或修改账本；${batch.blocked_count ? `另有 ${batch.blocked_count} 条阻塞记录保留为未入账异常，` : ""}封账后本批次不可重新入账。确认继续？`,
      "对账并封账",
      { type: "warning", confirmButtonText: "核对后封账", cancelButtonText: "取消" }
    );
  } catch { return; }
  acting.value = true;
  try {
    await closeSettlementBatch(batch.id);
    ElMessage.success("批次已对账并封账；正式账本未写入");
    await refreshDetail();
  } catch (err) {
    ElMessage.error(errorMessage(err, "封账未完成；请保留当前状态并核对账本"));
  } finally { acting.value = false; }
};

const reverseEntry = async (entry: any) => {
  if (!canManage.value || !canReverse.value || !gate("settlement_enabled") || entry.status !== "POSTED" || entry.reversal_of_entry_id || entry.reversal_entry_id) return;
  let reason = "";
  try {
    const result = await ElMessageBox.prompt(
      `将为 ${entry.member_name} 新增一条 ${(-Number(entry.points)).toFixed(2)} 分的冲销账本记录；原始 ${Number(entry.points).toFixed(2)} 分记录保持不变。请填写可审计的冲销原因。`,
      `冲销账本记录 #${entry.id}`,
      {
        inputPlaceholder: "填写核对后的业务原因",
        inputValidator: value => value.trim().length >= 4 || "冲销原因至少填写 4 个字",
        confirmButtonText: "继续确认",
        cancelButtonText: "取消"
      }
    );
    reason = result.value.trim();
    await ElMessageBox.confirm(
      `最后确认：只追加冲销记录，不修改或删除原账本。原因：${reason}`,
      "追加冲销记录",
      { type: "warning", confirmButtonText: "确认冲销", cancelButtonText: "返回" }
    );
  } catch { return; }
  acting.value = true;
  try {
    await reverseLearningCreditEntry(entry.id, reason);
    ElMessage.success("冲销记录已追加；原账本记录保持不变");
    await loadLedger();
  } catch (err) {
    ElMessage.error(errorMessage(err, "冲销失败；请重新读取账本状态"));
  } finally { acting.value = false; }
};

const itemStatusType = (status: string) =>
  status === "POSTED" ? "success" : status === "BLOCKED" || status === "FAILED" ? "danger" : status === "APPROVED" ? "primary" : "info";

onMounted(() => {
  void loadBatches();
  void refreshLedger();
});
</script>

<template>
  <div class="page-container credit-settlements">
    <el-card shadow="never" class="intro-card">
      <div class="page-header">
        <div>
          <div class="page-title">学分结算工作台</div>
          <div class="page-subtitle">事实冻结 → DRY-RUN → 独立审批 → 正式入账。阻塞项保留在批次中，不会混入已批准提案。</div>
        </div>
        <el-button :loading="loading" @click="loadBatches">刷新</el-button>
      </div>
      <el-alert
        v-if="response && !response.storage_available"
        class="notice"
        type="warning"
        show-icon
        :closable="false"
        title="结算批次表尚不可用；当前页面只读，所有批次动作均关闭。"
      />
      <el-alert
        v-else-if="response && (!gate('post_enabled') || !gate('settlement_enabled'))"
        class="notice"
        type="info"
        show-icon
        :closable="false"
        title="正式入账开关当前关闭。页面不会尝试开启开关；仅能执行已授权且已启用的阶段。"
      />
      <el-alert v-if="error" class="notice" :title="error" type="error" show-icon :closable="false" />
    </el-card>

    <el-card shadow="never" class="dry-run-card">
      <template #header><div class="section-title">生成结算 DRY-RUN</div></template>
      <el-alert
        v-if="!writesReady"
        class="notice"
        type="warning"
        show-icon
        :closable="false"
        title="DRY-RUN 批次写入门禁未开启，不能创建批次。页面不会修改任何功能开关。"
      />
      <el-form inline class="dry-run-form">
        <el-form-item label="业务来源">
          <el-select v-model="sourceKind" :disabled="!canManage">
            <el-option label="小组学习会" value="STUDY_MEETING" />
            <el-option label="班级学习会" value="CLASS_MEETING" />
            <el-option label="每日读书" value="DAILY_READING" />
            <el-option label="优秀分享" value="EXCELLENT_SHARE" />
            <el-option v-if="canManageHistory" label="历史学分导入批次" value="HISTORICAL_IMPORT" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="['STUDY_MEETING','CLASS_MEETING','HISTORICAL_IMPORT'].includes(sourceKind)" label="来源记录 ID">
          <el-input v-model="sourceId" type="number" min="1" placeholder="填写已存在的记录 ID" />
        </el-form-item>
        <template v-else>
          <el-form-item label="班级组织 ID"><el-input v-model="activityForm.class_org_unit_id" placeholder="班级组织 ID" /></el-form-item>
          <el-form-item label="日期范围">
            <el-date-picker v-model="activityForm.occurred_from" value-format="YYYY-MM-DD" type="date" placeholder="开始日期" />
            <span class="date-separator">至</span>
            <el-date-picker v-model="activityForm.occurred_to" value-format="YYYY-MM-DD" type="date" placeholder="结束日期" />
          </el-form-item>
        </template>
        <el-form-item>
          <el-button type="primary" :loading="acting" :disabled="!canManage || !writesReady" @click="runDryRun">生成 DRY-RUN</el-button>
        </el-form-item>
      </el-form>
      <div class="muted">创建 DRY-RUN 会写入结算批次与审计记录，但不会写 learning_credit_entries。生产环境必须等待对应数据结构和开关分别获批。</div>
    </el-card>

    <el-card shadow="never" class="batch-card">
      <template #header>
        <div class="section-header">
          <div><span class="section-title">结算批次</span><span class="muted">共 {{ totalCount }} 条，最多显示最近 100 条</span></div>
          <div class="filters">
            <el-select v-model="filters.status" clearable placeholder="全部状态" @change="loadBatches">
              <el-option v-for="item in ['DRY_RUN','PENDING_APPROVAL','APPROVED','POSTING','POSTED','PARTIAL_FAILED','CLOSED','CANCELLED']" :key="item" :label="statusLabel(item as SettlementBatchStatus)" :value="item" />
            </el-select>
            <el-select v-model="filters.batch_type" clearable placeholder="全部类型" @change="loadBatches">
              <el-option label="常规结算" value="REGULAR" />
              <el-option v-if="canManageHistory" label="历史导入" value="HISTORICAL_IMPORT" />
              <el-option label="更正批次" value="CORRECTION" />
            </el-select>
          </div>
        </div>
      </template>
      <div v-if="response" class="batch-status-summary" aria-label="批次状态汇总">
        <span>待审批 {{ response.status_counts.PENDING_APPROVAL || 0 }}</span>
        <span>已批准 {{ response.status_counts.APPROVED || 0 }}</span>
        <span>处理中 {{ response.status_counts.POSTING || 0 }}</span>
        <span>部分失败 {{ response.status_counts.PARTIAL_FAILED || 0 }}</span>
        <span>已入账 {{ response.status_counts.POSTED || 0 }}</span>
      </div>
      <el-table v-loading="loading" :data="batches" row-key="id" :empty-text="error ? '批次读取失败，请重试' : response?.storage_available === false ? '结算批次结构尚未就绪' : '暂无可见结算批次'">
        <el-table-column prop="batch_no" label="批次号" min-width="230" show-overflow-tooltip />
        <el-table-column label="来源" width="125"><template #default="{ row }">{{ sourceLabel(row.source_type) }}</template></el-table-column>
        <el-table-column label="班级 / 期间" min-width="180"><template #default="{ row }">{{ row.class_name || "—" }}<br /><span class="muted">{{ periodLabel(row) }}</span></template></el-table-column>
        <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="就绪提案" width="150"><template #default="{ row }">{{ row.proposed_entry_count }} 条 · {{ row.proposed_points }} 分</template></el-table-column>
        <el-table-column label="阻塞" width="90"><template #default="{ row }"><el-tag v-if="row.blocked_count" type="danger">{{ row.blocked_count }}</el-tag><span v-else>0</span></template></el-table-column>
        <el-table-column label="重复" width="90"><template #default="{ row }">{{ row.duplicate_entry_count ?? "—" }}</template></el-table-column>
        <el-table-column label="已入账" width="140"><template #default="{ row }">{{ row.posted_entry_count }} 条 · {{ row.posted_points }} 分</template></el-table-column>
        <el-table-column label="更新" min-width="170"><template #default="{ row }">{{ row.updated_at }}</template></el-table-column>
        <el-table-column label="操作" width="100" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openDetail(row)">详情 / 处理</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="ledger-card">
      <template #header>
        <div class="section-header">
          <div><span class="section-title">正式学分账本</span><span class="muted">只读查询；冲销仅追加负分记录，原记录不变</span></div>
          <el-button v-if="canReadLedger" :loading="ledgerLoading || overviewLoading" @click="refreshLedger">刷新账本</el-button>
        </div>
      </template>
      <el-alert
        v-if="!canReadLedger"
        type="info"
        show-icon
        :closable="false"
        title="当前账号没有账本查询权限；批次工作台仍可按批次管理权限使用。"
      />
      <template v-else>
        <el-alert v-if="overviewError" class="notice" :title="overviewError" type="error" show-icon :closable="false" />
        <div v-loading="overviewLoading" v-if="ledgerOverview" class="overview-grid" aria-label="正式学分总览">
          <div class="overview-item overview-total"><span>账本净学分</span><strong>{{ ledgerOverview.total_points }}</strong></div>
          <div class="overview-item"><span>正式记录</span><strong>{{ ledgerOverview.entry_count }}</strong></div>
          <div v-for="item in ledgerOverview.categories" :key="item.credit_category" class="overview-item">
            <span>{{ categoryLabel(item.credit_category) }}</span>
            <strong>{{ item.points }}</strong>
            <small>{{ item.entry_count }} 条</small>
          </div>
        </div>
        <el-form inline class="ledger-filters" @submit.prevent="loadLedger">
          <el-form-item label="学员 ID"><el-input v-model="ledgerFilters.member_id" type="number" min="1" placeholder="可选" /></el-form-item>
          <el-form-item label="类别">
            <el-select v-model="ledgerFilters.credit_category" clearable placeholder="全部类别">
              <el-option label="标准学习" value="STANDARD_LEARNING" />
              <el-option label="拓展活动" value="EXTENSION_ACTIVITY" />
            </el-select>
          </el-form-item>
          <el-form-item label="期间">
            <el-date-picker v-model="ledgerFilters.occurred_from" value-format="YYYY-MM-DD" type="date" placeholder="开始日期" />
            <span class="date-separator">至</span>
            <el-date-picker v-model="ledgerFilters.occurred_to" value-format="YYYY-MM-DD" type="date" placeholder="结束日期" />
          </el-form-item>
          <el-form-item><el-button type="primary" :loading="ledgerLoading" @click="loadLedger">查询</el-button></el-form-item>
        </el-form>
        <el-alert v-if="ledgerError" class="notice" :title="ledgerError" type="error" show-icon :closable="false" />
        <el-table v-loading="ledgerLoading" :data="ledgerEntries" row-key="id" :empty-text="ledgerError ? '账本明细读取失败，请重试' : '没有符合筛选条件的正式账本记录'">
          <el-table-column prop="id" label="账本 ID" width="100" />
          <el-table-column label="学员" min-width="145"><template #default="{ row }">{{ row.member_name }} <span class="muted">#{{ row.member_id }}</span></template></el-table-column>
          <el-table-column label="分值" width="100"><template #default="{ row }"><span :class="Number(row.points) < 0 ? 'negative-points' : ''">{{ Number(row.points).toFixed(2) }}</span></template></el-table-column>
          <el-table-column label="类别 / 类型" min-width="170"><template #default="{ row }">{{ row.credit_category === "STANDARD_LEARNING" ? "标准学习" : row.credit_category === "EXTENSION_ACTIVITY" ? "拓展活动" : row.credit_category }}<br /><span class="muted">{{ row.credit_type }}</span></template></el-table-column>
          <el-table-column label="期间" width="120"><template #default="{ row }">{{ row.period_display || "—" }}<br /><span class="muted">{{ row.occurred_precision }}</span></template></el-table-column>
          <el-table-column label="来源追踪" min-width="190"><template #default="{ row }">{{ sourceLabel(row.source_type) }}<br /><span class="muted">{{ row.source_id }}</span></template></el-table-column>
          <el-table-column label="规则版本" min-width="150"><template #default="{ row }">{{ row.rule_key || "—" }}<br /><span class="muted">{{ row.rule_version || "—" }}</span></template></el-table-column>
          <el-table-column label="入账时间" min-width="170"><template #default="{ row }">{{ row.posted_at || "—" }}</template></el-table-column>
          <el-table-column label="冲销关系" min-width="145"><template #default="{ row }">
            <span v-if="row.reversal_of_entry_id">冲销记录 → #{{ row.reversal_of_entry_id }}</span>
            <span v-else-if="row.reversal_entry_id">已冲销 → #{{ row.reversal_entry_id }}</span>
            <span v-else>—</span>
          </template></el-table-column>
          <el-table-column label="操作" width="110" fixed="right"><template #default="{ row }">
            <el-button
              v-if="row.status === 'POSTED' && !row.reversal_of_entry_id && !row.reversal_entry_id"
              link
              type="danger"
              :disabled="!canManage || !canReverse || !gate('settlement_enabled')"
              :loading="acting"
              @click="reverseEntry(row)"
            >追加冲销</el-button>
            <span v-else class="muted">只读</span>
          </template></el-table-column>
        </el-table>
        <div class="muted ledger-limit">最多返回 1000 条；筛选日期、类别或学员可缩小查询范围。账本金额不会在此页面编辑。</div>
      </template>
    </el-card>

    <el-drawer v-model="detailVisible" title="结算批次详情" size="min(920px, 96vw)" destroy-on-close>
      <div v-loading="detailLoading" class="detail-body">
        <template v-if="detail">
          <div class="detail-heading">
            <div><div class="batch-no">{{ detail.batch_no }}</div><div class="muted">{{ sourceLabel(detail.source_type) }} · {{ detail.class_name || "未指定班级" }} · {{ periodLabel(detail) }}</div></div>
            <el-tag :type="statusType(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
          </div>
          <el-descriptions :column="2" border>
            <el-descriptions-item label="就绪提案">{{ detail.proposed_entry_count }} 条 / {{ detail.proposed_points }} 分</el-descriptions-item>
            <el-descriptions-item label="阻塞记录">{{ detail.blocked_count }} 条（不参与审批或入账）</el-descriptions-item>
            <el-descriptions-item label="已入账">{{ detail.posted_entry_count }} 条 / {{ detail.posted_points }} 分</el-descriptions-item>
            <el-descriptions-item label="批次类型">{{ detail.batch_type === "HISTORICAL_IMPORT" ? "历史导入" : detail.batch_type === "REGULAR" ? "常规" : "更正" }}</el-descriptions-item>
            <el-descriptions-item label="来源指纹"><span class="fingerprint">{{ detail.source_fingerprint }}</span></el-descriptions-item>
            <el-descriptions-item label="规则指纹"><span class="fingerprint">{{ detail.rule_fingerprint }}</span></el-descriptions-item>
            <el-descriptions-item label="审批时间">{{ detail.approved_at || "—" }}</el-descriptions-item>
            <el-descriptions-item label="审批人 ID">{{ detail.approved_by ?? "—" }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="detail.blocked_count" class="notice" type="warning" show-icon :closable="false" :title="`${detail.blocked_count} 条阻塞记录保留用于解释；本批次只审批和入账 DRY-RUN 中冻结的就绪提案。`" />
          <div class="actions">
            <el-button
              v-if="detail.status === 'DRY_RUN'"
              type="primary"
              :loading="acting"
              :disabled="!canManage || !gate('dry_run_enabled') || !detail.proposed_entry_count"
              @click="submitForApproval"
            >提交独立审批</el-button>
            <el-button
              v-if="detail.status === 'PENDING_APPROVAL'"
              type="warning"
              :loading="acting"
              :disabled="!canApprove || !gate('approval_enabled') || detail.created_by_current_actor"
              @click="approve"
            >批准冻结提案</el-button>
            <el-button
              v-if="detail.status === 'APPROVED'"
              type="danger"
              :loading="acting"
              :disabled="!canManage || !canPost || !gate('post_enabled') || !gate('settlement_enabled') || (detail.batch_type === 'HISTORICAL_IMPORT' && (!canManageHistory || !gate('historical_post_enabled')))
              "
              @click="post(false)"
            >正式入账</el-button>
            <el-button
              v-if="detail.status === 'PARTIAL_FAILED'"
              type="danger"
              :loading="acting"
              :disabled="!canManage || !canPost || !gate('post_enabled') || !gate('settlement_enabled') || (detail.batch_type === 'HISTORICAL_IMPORT' && (!canManageHistory || !gate('historical_post_enabled')))
              "
              @click="post(true)"
            >对账后续跑</el-button>
            <el-button
              v-if="detail.status === 'POSTING'"
              type="warning"
              :loading="acting"
              :disabled="!canManage || !canReconcile || (detail.batch_type === 'HISTORICAL_IMPORT' && !canManageHistory)"
              @click="reconcilePosting"
            >只读对账恢复（30 分钟超时）</el-button>
            <el-button
              v-if="detail.status === 'POSTED'"
              type="success"
              :loading="acting"
              :disabled="!canManage || !canClose || !gate('post_enabled') || !gate('settlement_enabled')"
              @click="closeBatch"
            >只读对账并封账</el-button>
          </div>
          <el-table :data="detail.items" row-key="id" class="item-table" empty-text="没有批次条目">
            <el-table-column label="学员" width="110"><template #default="{ row }">{{ row.member_name_masked }}</template></el-table-column>
            <el-table-column label="来源追踪" min-width="170"><template #default="{ row }">{{ row.source_ref?.source_sheet ? `${row.source_ref.source_sheet} · 第${row.source_ref.source_row_number}行 · ${row.source_ref.source_column_name || ""}` : sourceLabel(row.source_ref?.source_type || detail!.source_type) }}</template></el-table-column>
            <el-table-column label="规则" min-width="150"><template #default="{ row }">{{ row.rule_key || "—" }}<br /><span class="muted">{{ row.rule_version || "—" }}</span></template></el-table-column>
            <el-table-column label="分值" width="90"><template #default="{ row }">{{ row.points ?? "—" }}</template></el-table-column>
            <el-table-column label="期间" width="105"><template #default="{ row }">{{ row.period_precision === "YEAR" ? `${row.period_year}年` : row.period_precision === "MONTH" ? `${row.period_year}-${String(row.period_month || 0).padStart(2, "0")}` : `${row.period_year}年` }}</template></el-table-column>
            <el-table-column label="状态 / 阻塞原因" min-width="170"><template #default="{ row }"><el-tag size="small" :type="itemStatusType(row.status)">{{ row.status }}</el-tag><div v-if="row.blocking_reason || row.error_code" class="muted">{{ row.blocking_reason || row.error_code }}</div></template></el-table-column>
            <el-table-column label="账本记录" width="100"><template #default="{ row }">{{ row.ledger_entry_id ?? "—" }}</template></el-table-column>
          </el-table>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-header,.section-header,.detail-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.page-title { font-size:20px; font-weight:600; }
.page-subtitle,.muted { color:var(--el-text-color-secondary); font-size:13px; }
.page-subtitle { margin-top:6px; }
.notice { margin-top:14px; }
.dry-run-card,.batch-card { margin-top:16px; }
.ledger-card { margin-top:16px; }
.batch-status-summary { display:flex; flex-wrap:wrap; gap:16px; padding:0 0 12px; color:var(--el-text-color-secondary); font-size:13px; }
.overview-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
.overview-item { display:flex; flex-direction:column; gap:4px; border:1px solid var(--el-border-color-lighter); border-radius:6px; padding:12px 14px; color:var(--el-text-color-secondary); font-size:13px; }
.overview-item strong { color:var(--el-text-color-primary); font-size:20px; font-weight:600; }
.overview-item small { font-size:12px; }
.section-title { font-weight:600; }
.dry-run-form { margin-bottom:8px; }
.date-separator { margin:0 8px; color:var(--el-text-color-secondary); }
.filters { display:flex; gap:10px; }
.detail-body { min-height:120px; }
.batch-no { font-size:16px; font-weight:600; margin-bottom:6px; }
.fingerprint { font-family:monospace; word-break:break-all; }
.actions { display:flex; align-items:center; gap:10px; margin:18px 0; }
.item-table { margin-top:12px; }
.item-table .muted { margin-top:4px; }
.ledger-limit { margin-top:10px; }
.negative-points { color:var(--el-color-danger); font-weight:600; }
@media (max-width: 768px) {
  .page-header,.section-header,.detail-heading { align-items:flex-start; flex-direction:column; }
  .filters { width:100%; }
  .filters > * { flex:1; min-width:0; }
}
</style>
