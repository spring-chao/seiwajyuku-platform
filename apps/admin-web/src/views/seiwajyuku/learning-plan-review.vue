<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  getLearningPlanReview,
  type LearningPlanReview
} from "@/api/seiwajyuku";

defineOptions({ name: "LearningPlanReview" });

type LocalReview = {
  status: "CONFIRMED";
  reviewed_by: string;
  reviewed_at: string;
  notes: string | null;
};

const STORAGE_KEY = "seiwajyuku-learning-plan-review-draft-v1";
const loading = ref(false);
const error = ref("");
const review = ref<LearningPlanReview>();
const activeCohort = ref("1");
const drawerVisible = ref(false);
const selectedCheckpointId = ref("");
const localReviews = ref<Record<string, LocalReview>>({});
const form = reactive({
  reviewed_by: "",
  reviewed_at: "",
  notes: ""
});

const cohortMonths = [1, 4, 7, 10];
const workbookYears = ["1", "2", "3"];

const fingerprint = computed(() => {
  if (!review.value) return "";
  return JSON.stringify({
    source_commit: review.value.source_commit,
    source_json_sha256: review.value.source_json_sha256,
    source_workbooks: review.value.source_workbooks
  });
});

const effectiveCheckpoints = computed(() =>
  (review.value?.checkpoints ?? []).map(checkpoint => ({
    ...checkpoint,
    ...(localReviews.value[checkpoint.checkpoint_id] ?? {})
  }))
);

const selectedCheckpoint = computed(() =>
  effectiveCheckpoints.value.find(
    checkpoint => checkpoint.checkpoint_id === selectedCheckpointId.value
  )
);

const currentStatus = computed<"PENDING" | "CONFIRMED">(() => {
  const checkpoints = effectiveCheckpoints.value;
  return checkpoints.length === 36 && checkpoints.every(
    checkpoint =>
      checkpoint.status === "CONFIRMED" &&
      Boolean(checkpoint.reviewed_by?.trim()) &&
      Boolean(checkpoint.reviewed_at?.trim())
  )
    ? "CONFIRMED"
    : "PENDING";
});

const confirmedCount = computed(
  () => effectiveCheckpoints.value.filter(item => item.status === "CONFIRMED").length
);

const progress = computed(() => Math.round((confirmedCount.value / 36) * 100));

const currentCohortCheckpoints = computed(() =>
  effectiveCheckpoints.value.filter(
    checkpoint => String(checkpoint.cohort_month) === activeCohort.value
  )
);

const taskTypeSummary = (taskTypeCounts: Record<string, number>) =>
  Object.entries(taskTypeCounts)
    .map(([type, count]) => `${type} ${count}`)
    .join("、");

const sourceText = (metadata?: Record<string, unknown> | null) => {
  const value = metadata?.source_text;
  return typeof value === "string" && value.trim() ? value : "—";
};

const formatReviewedAt = (value?: string | null) =>
  value ? value.replace("T", " ").replace("Z", "") : "—";

const defaultReviewedAt = () =>
  new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

const loadLocalDraft = () => {
  if (!review.value) return;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (saved?.fingerprint === fingerprint.value && saved.reviews) {
      localReviews.value = saved.reviews;
    } else {
      localStorage.removeItem(STORAGE_KEY);
      localReviews.value = {};
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    localReviews.value = {};
  }
};

const saveLocalDraft = () => {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ fingerprint: fingerprint.value, reviews: localReviews.value })
  );
};

const loadReview = async () => {
  loading.value = true;
  error.value = "";
  try {
    const response = await getLearningPlanReview();
    review.value = response.data;
    loadLocalDraft();
  } catch (requestError) {
    error.value = "学习计划审核清单加载失败，请刷新重试。";
    console.error(requestError);
  } finally {
    loading.value = false;
  }
};

const openCheckpoint = (checkpointId: string) => {
  const checkpoint = effectiveCheckpoints.value.find(
    item => item.checkpoint_id === checkpointId
  );
  if (!checkpoint) return;
  selectedCheckpointId.value = checkpoint.checkpoint_id;
  form.reviewed_by = checkpoint.reviewed_by ?? "";
  form.reviewed_at = checkpoint.reviewed_at ?? defaultReviewedAt();
  form.notes = checkpoint.notes ?? "";
  drawerVisible.value = true;
};

const saveCheckpoint = () => {
  if (!selectedCheckpoint.value) return;
  if (!form.reviewed_by.trim()) {
    ElMessage.warning("请填写审核人");
    return;
  }
  if (!form.reviewed_at.trim()) {
    ElMessage.warning("请填写审核时间");
    return;
  }
  localReviews.value = {
    ...localReviews.value,
    [selectedCheckpoint.value.checkpoint_id]: {
      status: "CONFIRMED",
      reviewed_by: form.reviewed_by.trim(),
      reviewed_at: form.reviewed_at.trim(),
      notes: form.notes.trim() || null
    }
  };
  saveLocalDraft();
  drawerVisible.value = false;
  ElMessage.success("已暂存在本浏览器，尚未写入服务器或数据库");
};

const exportReview = () => {
  if (!review.value) return;
  const checkpoints = review.value.checkpoints.map(checkpoint => ({
    ...checkpoint,
    ...(localReviews.value[checkpoint.checkpoint_id] ?? {})
  }));
  const allConfirmed = checkpoints.every(
    checkpoint =>
      checkpoint.status === "CONFIRMED" &&
      Boolean(checkpoint.reviewed_by?.trim()) &&
      Boolean(checkpoint.reviewed_at?.trim())
  );
  const reviewers = [...new Set(
    checkpoints
      .map(checkpoint => checkpoint.reviewed_by?.trim())
      .filter((value): value is string => Boolean(value))
  )].sort();
  const reviewedAt = checkpoints
    .map(checkpoint => checkpoint.reviewed_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1) ?? null;
  const payload = {
    ...review.value,
    status: allConfirmed ? "CONFIRMED" : "PENDING",
    confirmed_by: allConfirmed ? reviewers.join(",") : null,
    confirmed_at: allConfirmed ? reviewedAt : null,
    checkpoints: checkpoints.map(({ tasks: _tasks, ...checkpoint }) => checkpoint)
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8"
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "standard-3y-2026.review.json";
  anchor.click();
  URL.revokeObjectURL(url);
  ElMessage.success("已导出审核清单文件");
};

onMounted(loadReview);
</script>

<template>
  <div class="learning-plan-review page-container">
    <el-card shadow="never" class="review-card">
      <template #header>
        <div class="review-header">
          <div>
            <div class="review-title">2026 标准三年学习计划审核</div>
            <div class="review-subtitle">
              4 条开班轨道 × 9 个关键周期，共 36 项业务确认
            </div>
          </div>
          <div class="review-actions">
            <el-button :loading="loading" @click="loadReview">刷新清单</el-button>
            <el-button type="primary" :disabled="!review" @click="exportReview">
              导出审核清单
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
      />

      <template v-if="review">
        <el-alert
          class="review-notice"
          title="当前为审核草稿模式：逐项确认暂存在本浏览器，不会写入学习计划数据库；全部确认后请导出清单进入受控 B2 流程。"
          type="warning"
          :closable="false"
          show-icon
        />

        <div class="review-summary">
          <div class="summary-item">
            <span>当前状态</span>
            <el-tag :type="currentStatus === 'CONFIRMED' ? 'success' : 'warning'">
              {{ currentStatus === "CONFIRMED" ? "已完成业务确认" : "待业务确认" }}
            </el-tag>
          </div>
          <div class="summary-item">
            <span>完成进度</span>
            <strong>{{ confirmedCount }}/36</strong>
          </div>
          <div class="summary-item summary-progress">
            <span>审核进度</span>
            <el-progress :percentage="progress" :show-text="false" />
          </div>
        </div>

        <el-descriptions :column="2" border class="fingerprints">
          <el-descriptions-item label="固定提交">
            <span class="fingerprint">{{ review.source_commit }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="标准 JSON SHA-256">
            <span class="fingerprint">{{ review.source_json_sha256 }}</span>
          </el-descriptions-item>
          <el-descriptions-item
            v-for="year in workbookYears"
            :key="year"
            :label="`第${year}年 Excel SHA-256`"
          >
            <span>
              <span>{{ review.source_workbooks[year]?.file }}</span><br />
              <span class="fingerprint">{{ review.source_workbooks[year]?.sha256 }}</span>
            </span>
          </el-descriptions-item>
        </el-descriptions>

        <el-tabs v-model="activeCohort" class="review-tabs">
          <el-tab-pane
            v-for="cohort in cohortMonths"
            :key="cohort"
            :name="String(cohort)"
            :label="`${cohort}月开班轨道`"
          >
            <el-table
              :data="currentCohortCheckpoints"
              border
              stripe
              row-key="checkpoint_id"
              empty-text="暂无审核项"
            >
              <el-table-column label="周期" width="110">
                <template #default="{ row }">
                  第 {{ row.cycle_index }} 周期
                </template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'CONFIRMED' ? 'success' : 'warning'">
                    {{ row.status === "CONFIRMED" ? "已确认" : "待确认" }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="task_count" label="任务数" width="90" />
              <el-table-column label="任务拆分" min-width="280">
                <template #default="{ row }">
                  <span class="task-summary">{{ taskTypeSummary(row.task_type_counts) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="已确认学分" width="120">
                <template #default="{ row }">
                  {{ row.confirmed_credits.length
                    ? row.confirmed_credits.map(item => `${item.credit_points}分`).join("、")
                    : "—" }}
                </template>
              </el-table-column>
              <el-table-column label="审核记录" min-width="160">
                <template #default="{ row }">
                  <span v-if="row.reviewed_by">
                    {{ row.reviewed_by }}<br />{{ formatReviewedAt(row.reviewed_at) }}
                  </span>
                  <span v-else class="muted">尚未填写</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openCheckpoint(row.checkpoint_id)">
                    查看并审核
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-card>

    <el-drawer
      v-model="drawerVisible"
      :title="selectedCheckpoint ? `审核 ${selectedCheckpoint.checkpoint_id}（第${selectedCheckpoint.cycle_index}周期）` : '审核周期'"
      size="68%"
    >
      <template v-if="selectedCheckpoint">
        <el-alert
          title="请重点核对班会内容、小组内容、线上课程拆分、任务名称及已确认学分依据。"
          type="info"
          :closable="false"
          show-icon
        />
        <el-descriptions :column="2" border class="drawer-meta">
          <el-descriptions-item label="开班轨道">
            {{ selectedCheckpoint.cohort_month }} 月
          </el-descriptions-item>
          <el-descriptions-item label="名义月份">
            {{ selectedCheckpoint.nominal_calendar_month ?? "—" }} 月
          </el-descriptions-item>
          <el-descriptions-item label="源单元格" :span="2">
            {{ selectedCheckpoint.source_refs.map(item => `${item.source_sheet}!${item.source_cell}`).join("、") || "—" }}
          </el-descriptions-item>
        </el-descriptions>

        <el-table :data="selectedCheckpoint.tasks" border stripe class="task-table">
          <el-table-column prop="task_type" label="类型" width="150" />
          <el-table-column prop="title" label="任务名称" min-width="280" />
          <el-table-column prop="description" label="说明" min-width="220" />
          <el-table-column label="源内容" min-width="240">
            <template #default="{ row }">{{ sourceText(row.metadata) }}</template>
          </el-table-column>
          <el-table-column label="学分" width="80">
            <template #default="{ row }">{{ row.credit_points ?? "—" }}</template>
          </el-table-column>
        </el-table>

        <el-form label-position="top" class="review-form">
          <el-form-item label="审核人" required>
            <el-input v-model="form.reviewed_by" placeholder="填写真实姓名或运营账号" />
          </el-form-item>
          <el-form-item label="审核时间" required>
            <el-input v-model="form.reviewed_at" placeholder="例如 2026-08-25T09:30:00+08:00" />
          </el-form-item>
          <el-form-item label="审核备注">
            <el-input
              v-model="form.notes"
              type="textarea"
              :rows="4"
              placeholder="如有特殊拆分、学分依据或待跟进事项，请写明"
            />
          </el-form-item>
          <div class="drawer-actions">
            <el-button @click="drawerVisible = false">取消</el-button>
            <el-button type="primary" @click="saveCheckpoint">暂存此项确认</el-button>
          </div>
        </el-form>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.learning-plan-review {
  padding: 16px;
}

.review-card {
  min-height: calc(100vh - 120px);
}

.review-header,
.review-actions,
.review-summary,
.summary-item,
.drawer-actions {
  display: flex;
  align-items: center;
}

.review-header {
  justify-content: space-between;
  gap: 16px;
}

.review-actions {
  gap: 8px;
}

.review-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.review-subtitle {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
}

.review-notice {
  margin-bottom: 16px;
}

.review-summary {
  gap: 32px;
  margin-bottom: 16px;
  padding: 16px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
}

.summary-item {
  gap: 10px;
  white-space: nowrap;
}

.summary-item > span {
  color: var(--el-text-color-secondary);
}

.summary-progress {
  flex: 1;
}

.summary-progress .el-progress {
  min-width: 180px;
}

.fingerprints {
  margin-bottom: 20px;
}

.fingerprint {
  word-break: break-all;
  font-family: monospace;
  font-size: 12px;
}

.review-tabs {
  margin-top: 8px;
}

.task-summary {
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

.muted {
  color: var(--el-text-color-placeholder);
}

.drawer-meta,
.task-table {
  margin-top: 16px;
}

.review-form {
  margin-top: 22px;
}

.drawer-actions {
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 900px) {
  .review-header,
  .review-summary {
    align-items: flex-start;
    flex-direction: column;
  }

  .summary-progress {
    width: 100%;
  }
}
</style>
