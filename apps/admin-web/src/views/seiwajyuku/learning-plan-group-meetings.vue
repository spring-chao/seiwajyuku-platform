<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import {
  getLearningPlanGroupMeetings,
  type LearningPlanGroupMeetingCatalog,
  type LearningPlanGroupMeetingTask
} from "@/api/seiwajyuku";

defineOptions({ name: "LearningPlanGroupMeetings" });

type GroupMeetingDraft = {
  title: string;
  description: string;
  credit_points: number | null;
  is_required: boolean;
  notes: string | null;
};

const router = useRouter();
const STORAGE_KEY = "seiwajyuku-learning-plan-group-meeting-drafts-v1";
const loading = ref(false);
const error = ref("");
const catalog = ref<LearningPlanGroupMeetingCatalog>();
const cohortFilter = ref("all");
const cycleFilter = ref<number>();
const search = ref("");
const drawerVisible = ref(false);
const selectedTaskKey = ref("");
const drafts = ref<Record<string, GroupMeetingDraft>>({});
const form = reactive<GroupMeetingDraft>({
  title: "",
  description: "",
  credit_points: null,
  is_required: true,
  notes: ""
});

const fingerprint = computed(() => {
  if (!catalog.value) return "";
  return JSON.stringify({
    source_commit: catalog.value.source_commit,
    source_json_sha256: catalog.value.source_json_sha256,
    source_workbooks: catalog.value.source_workbooks
  });
});

const selectedTask = computed(() =>
  catalog.value?.tasks.find(task => task.task_key === selectedTaskKey.value)
);

const changedCount = computed(() => Object.keys(drafts.value).length);

const filteredTasks = computed(() => {
  const query = search.value.trim().toLowerCase();
  return (catalog.value?.tasks ?? []).filter(task => {
    if (cohortFilter.value !== "all" && String(task.cohort_month) !== cohortFilter.value) {
      return false;
    }
    if (cycleFilter.value && task.cycle_index !== cycleFilter.value) return false;
    if (!query) return true;
    const metadata = task.metadata ?? {};
    return [
      task.title,
      task.description,
      metadata.source_text,
      metadata.source_sheet,
      metadata.source_cell
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
});

const asGroupMeetingTask = (value: unknown) => value as LearningPlanGroupMeetingTask;

const effectiveTask = (value: unknown) => {
  const task = asGroupMeetingTask(value);
  return { ...task, ...(drafts.value[task.task_key] ?? {}) };
};

const sourceText = (value: unknown) => {
  const task = asGroupMeetingTask(value);
  const text = task.metadata?.source_text;
  return typeof text === "string" && text.trim() ? text : "—";
};

const sourceRef = (value: unknown) => {
  const task = asGroupMeetingTask(value);
  const sheet = task.metadata?.source_sheet;
  const cell = task.metadata?.source_cell;
  return sheet && cell ? `${sheet}!${cell}` : "—";
};

const saveLocalDraft = () => {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ fingerprint: fingerprint.value, drafts: drafts.value })
  );
};

const loadLocalDraft = () => {
  if (!catalog.value) return;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (saved?.fingerprint === fingerprint.value && saved.drafts) {
      drafts.value = saved.drafts;
    } else {
      localStorage.removeItem(STORAGE_KEY);
      drafts.value = {};
    }
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    drafts.value = {};
  }
};

const loadCatalog = async () => {
  loading.value = true;
  error.value = "";
  try {
    const response = await getLearningPlanGroupMeetings();
    catalog.value = response.data;
    loadLocalDraft();
  } catch (requestError) {
    error.value = "小组学习会调整清单加载失败，请刷新重试。";
    console.error(requestError);
  } finally {
    loading.value = false;
  }
};

const openTask = (value: unknown) => {
  const task = asGroupMeetingTask(value);
  const current = effectiveTask(task);
  selectedTaskKey.value = task.task_key;
  form.title = current.title ?? "";
  form.description = current.description ?? "";
  form.credit_points = current.credit_points ?? null;
  form.is_required = current.is_required;
  form.notes = drafts.value[task.task_key]?.notes ?? "";
  drawerVisible.value = true;
};

const saveDraft = () => {
  if (!selectedTask.value) return;
  if (!form.description.trim()) {
    ElMessage.warning("请填写小组学习会流程内容");
    return;
  }
  const creditPoints =
    form.credit_points === null || form.credit_points === undefined
      ? null
      : Number(form.credit_points);
  if (creditPoints !== null && (!Number.isFinite(creditPoints) || creditPoints < 0)) {
    ElMessage.warning("学分必须是大于或等于0的数字");
    return;
  }
  drafts.value = {
    ...drafts.value,
    [selectedTask.value.task_key]: {
      title: form.title.trim(),
      description: form.description.trim(),
      credit_points: creditPoints,
      is_required: form.is_required,
      notes: form.notes.trim() || null
    }
  };
  saveLocalDraft();
  drawerVisible.value = false;
  ElMessage.success("调整草稿已暂存在本浏览器，尚未改变当前学习计划");
};

const resetDraft = () => {
  if (!selectedTask.value) return;
  const next = { ...drafts.value };
  delete next[selectedTask.value.task_key];
  drafts.value = next;
  saveLocalDraft();
  drawerVisible.value = false;
  ElMessage.success("已恢复当前确认版本");
};

const exportDrafts = () => {
  if (!catalog.value) return;
  if (!changedCount.value) {
    ElMessage.warning("还没有需要导出的调整项");
    return;
  }
  const payload = {
    adjustment_schema_version: 1,
    plan_key: catalog.value.plan_key,
    version_label: catalog.value.version_label,
    scope: "GROUP_MEETING",
    status: "DRAFT",
    base_review_status: catalog.value.review_status,
    base_source_commit: catalog.value.source_commit,
    base_source_json: catalog.value.source_json,
    base_source_json_sha256: catalog.value.source_json_sha256,
    created_at: new Date().toISOString(),
    changes: Object.entries(drafts.value).map(([task_key, change]) => ({
      task_key,
      ...change
    }))
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8"
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "standard-3y-2026-group-meeting-adjustments.json";
  anchor.click();
  URL.revokeObjectURL(url);
  ElMessage.success("已导出调整草稿；需重新审核后才能进入下一版计划");
};

onMounted(loadCatalog);
</script>

<template>
  <div class="learning-plan-group-meetings page-container">
    <el-card shadow="never" class="config-card">
      <template #header>
        <div class="config-header">
          <div>
            <div class="config-title">学习计划配置 · 小组学习会</div>
            <div class="config-subtitle">
              已确认版本的后续调整入口：流程内容与学分调整只生成下一版草稿
            </div>
          </div>
          <div class="config-actions">
            <el-button @click="router.push('/operations/learning-plan-review')">
              返回学习计划审核
            </el-button>
            <el-button type="primary" :disabled="!catalog || !changedCount" @click="exportDrafts">
              导出调整草稿（{{ changedCount }}）
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

      <template v-if="catalog">
        <el-alert
          class="config-notice"
          title="当前2026版本已经确认。这里不会覆盖已确认计划、不会写入数据库；保存和导出的是下一版小组学习会调整草稿，须重新审核并通过受控 B2 流程后才能生效。"
          type="warning"
          :closable="false"
          show-icon
        />

        <div class="config-summary">
          <div class="summary-item">
            <span>当前版本</span>
            <el-tag type="success">{{ catalog.version_label }} 已确认</el-tag>
          </div>
          <div class="summary-item">
            <span>小组学习会任务</span>
            <strong>{{ catalog.task_count }}</strong>
          </div>
          <div class="summary-item">
            <span>本地调整草稿</span>
            <strong>{{ changedCount }}</strong>
          </div>
        </div>

        <el-descriptions :column="2" border class="fingerprints">
          <el-descriptions-item label="审核提交">
            <span class="fingerprint">{{ catalog.source_commit }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="标准 JSON SHA-256">
            <span class="fingerprint">{{ catalog.source_json_sha256 }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <div class="filters">
          <el-select v-model="cohortFilter" style="width: 170px">
            <el-option label="全部开班轨道" value="all" />
            <el-option label="1月开班" value="1" />
            <el-option label="4月开班" value="4" />
            <el-option label="7月开班" value="7" />
            <el-option label="10月开班" value="10" />
          </el-select>
          <el-input-number
            v-model="cycleFilter"
            :min="1"
            :max="36"
            :controls="false"
            placeholder="周期"
            clearable
          />
          <el-input
            v-model="search"
            clearable
            placeholder="搜索流程内容、源工作表或源单元格"
            style="max-width: 360px"
          />
          <span class="filter-count">显示 {{ filteredTasks.length }} / {{ catalog.task_count }} 项</span>
        </div>

        <el-table
          v-loading="loading"
          :data="filteredTasks"
          border
          stripe
          height="calc(100vh - 440px)"
          row-key="task_key"
          empty-text="暂无匹配的小组学习会任务"
        >
          <el-table-column label="开班" width="78">
            <template #default="{ row }">{{ row.cohort_month }}月</template>
          </el-table-column>
          <el-table-column label="周期" width="78">
            <template #default="{ row }">第{{ row.cycle_index }}周期</template>
          </el-table-column>
          <el-table-column label="名义月份" width="90">
            <template #default="{ row }">{{ row.nominal_calendar_month ?? "—" }}月</template>
          </el-table-column>
          <el-table-column label="流程内容" min-width="320">
            <template #default="{ row }">{{ effectiveTask(row).description || "—" }}</template>
          </el-table-column>
          <el-table-column label="学分" width="78">
            <template #default="{ row }">
              {{ effectiveTask(row).credit_points ?? "—" }}
            </template>
          </el-table-column>
          <el-table-column label="源单元格" min-width="190">
            <template #default="{ row }">{{ sourceRef(row) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag v-if="drafts[row.task_key]" type="warning">有调整草稿</el-tag>
              <el-tag v-else type="info">沿用确认版</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openTask(row)">调整草稿</el-button>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-card>

    <el-drawer
      v-model="drawerVisible"
      :title="selectedTask ? `调整小组学习会 · ${selectedTask.cohort_month}月开班第${selectedTask.cycle_index}周期` : '调整小组学习会'"
      size="620px"
    >
      <template v-if="selectedTask">
        <el-alert
          title="调整的是下一版计划草稿；原始审核版本和当前生产数据不会被覆盖。"
          type="info"
          :closable="false"
          show-icon
        />
        <el-descriptions :column="2" border class="drawer-meta">
          <el-descriptions-item label="任务类型">GROUP_MEETING</el-descriptions-item>
          <el-descriptions-item label="源单元格">{{ sourceRef(selectedTask) }}</el-descriptions-item>
          <el-descriptions-item label="原流程内容" :span="2">
            {{ selectedTask.description || "—" }}
          </el-descriptions-item>
          <el-descriptions-item label="原学分">
            {{ selectedTask.credit_points ?? "—" }}
          </el-descriptions-item>
          <el-descriptions-item label="源内容" :span="2">
            {{ sourceText(selectedTask) }}
          </el-descriptions-item>
        </el-descriptions>

        <el-form label-position="top" class="adjust-form">
          <el-form-item label="流程标题">
            <el-input v-model="form.title" maxlength="255" show-word-limit />
          </el-form-item>
          <el-form-item label="小组学习会流程内容" required>
            <el-input v-model="form.description" type="textarea" :rows="7" maxlength="4000" show-word-limit />
          </el-form-item>
          <el-form-item label="确认学分">
            <el-input-number v-model="form.credit_points" :min="0" :max="1000" :step="20" clearable />
            <span class="form-hint">留空表示该流程暂不确认学分</span>
          </el-form-item>
          <el-form-item label="是否为必做流程">
            <el-switch v-model="form.is_required" active-text="必做" inactive-text="可选" />
          </el-form-item>
          <el-form-item label="调整说明">
            <el-input
              v-model="form.notes"
              type="textarea"
              :rows="4"
              maxlength="2000"
              show-word-limit
              placeholder="填写教材变化、运营规则依据或学分依据"
            />
          </el-form-item>
          <div class="drawer-actions">
            <el-button @click="drawerVisible = false">取消</el-button>
            <el-button v-if="drafts[selectedTask.task_key]" @click="resetDraft">
              恢复确认版
            </el-button>
            <el-button type="primary" @click="saveDraft">保存调整草稿</el-button>
          </div>
        </el-form>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.learning-plan-group-meetings {
  padding: 16px;
}

.config-card {
  min-height: calc(100vh - 120px);
}

.config-header,
.config-actions,
.config-summary,
.summary-item,
.filters,
.drawer-actions {
  display: flex;
  align-items: center;
}

.config-header {
  justify-content: space-between;
  gap: 16px;
}

.config-actions {
  gap: 8px;
}

.config-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.config-subtitle {
  margin-top: 6px;
  color: var(--el-text-color-secondary);
}

.config-notice {
  margin-bottom: 16px;
}

.config-summary {
  gap: 32px;
  margin-bottom: 16px;
  padding: 16px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
}

.summary-item {
  gap: 10px;
}

.fingerprints {
  margin-bottom: 16px;
}

.fingerprint {
  word-break: break-all;
  font-family: monospace;
}

.filters {
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}

.filter-count,
.form-hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.drawer-meta,
.adjust-form {
  margin-top: 16px;
}

.drawer-actions {
  justify-content: flex-end;
  gap: 8px;
}
</style>
