<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs from "dayjs";
import {
  getAttendanceEventGroups,
  getAttendanceReconciliationBreakdown,
  getAttendanceReconciliationQueue,
  getAttendanceReconciliationSummary,
  getAttendanceSyncStatus,
  type AttendanceEventGroup,
  type AttendanceReconciliationBreakdownRow,
  type AttendanceReconciliationQueueRow,
  type AttendanceReconciliationItem,
  type AttendanceSyncStatus
} from "@/api/seiwajyuku";

defineOptions({ name: "ActivityAdmin" });

const loading = ref(false);
const month = ref(dayjs().format("YYYY-MM"));
const rows = ref<AttendanceEventGroup[]>([]);
const syncStatus = ref<AttendanceSyncStatus | null>(null);
const reconciliationItems = ref<AttendanceReconciliationItem[]>([]);
const reviewRows = ref<AttendanceReconciliationQueueRow[]>([]);
const reviewBreakdownRows = ref<AttendanceReconciliationBreakdownRow[]>([]);
const reviewTotal = ref(0);
const reviewVisible = ref(false);
const reviewLoading = ref(false);
const reviewPage = ref(1);
const reviewPageSize = 20;
const reviewIssue = ref<AttendanceReconciliationItem["key"]>(
  "unmatched_attendance_records"
);
const totalEligible = computed(() =>
  rows.value.reduce((sum, item) => sum + item.record_count, 0)
);
const totalCompleted = computed(() =>
  rows.value.reduce((sum, item) => sum + item.present_count, 0)
);
const syncAlert = computed(() => {
  const status = syncStatus.value;
  if (!status || status.state === "NO_RUNS") {
    return { type: "info" as const, title: "签到自动同步尚无运行记录" };
  }
  if (status.state === "CRITICAL") {
    return {
      type: "error" as const,
      title: `签到自动同步已连续异常 ${status.consecutive_failure_count} 次，请技术管理员检查`
    };
  }
  if (status.state === "WARNING") {
    return {
      type: "warning" as const,
      title: `最近一次签到自动同步异常（连续 ${status.consecutive_failure_count} 次）`
    };
  }
  if (status.state === "RUNNING") {
    return { type: "info" as const, title: "签到数据正在同步" };
  }
  const finishedAt = status.last_run?.finished_at
    ? dayjs(status.last_run.finished_at).format("YYYY-MM-DD HH:mm")
    : "最近";
  return {
    type: "success" as const,
    title: `签到自动同步正常，最近完成于 ${finishedAt}`
  };
});
const reconciliationLabels: Record<AttendanceReconciliationItem["key"], string> = {
  unmatched_attendance_records: "待人工匹配签到",
  active_members_missing_phone_hash: "在册学员缺少手机号摘要",
  active_members_missing_primary_region: "在册学员缺少发展归属",
  active_members_missing_study_class: "在册学员未分学习班级",
  active_members_missing_study_group: "在册学员未分学习小组"
};
const activityStatusLabels: Record<string, string> = {
  ACTIVE: "正常",
  INACTIVE: "已停用",
  CANCELLED: "已取消",
  CLOSED: "已结束",
  DRAFT: "草稿"
};

function activityStatusLabel(status: string) {
  return activityStatusLabels[status] || status;
}
const activityTypeLabels: Record<string, string> = {
  CLASS_MEETING: "班级学习会",
  CENTER_QUARTERLY_REPORT: "分中心季度报告会",
  CENTER_MONTHLY_REPORT: "分中心月度报告会",
  READING_SESSION: "读书会",
  OTHER: "其他活动"
};

function activityTypeLabel(activityType: string) {
  return activityTypeLabels[activityType] || activityType;
}

async function load() {
  loading.value = true;
  try {
    const [eventResponse, syncResponse, reconciliationResponse] = await Promise.all([
      getAttendanceEventGroups(month.value),
      getAttendanceSyncStatus(),
      getAttendanceReconciliationSummary()
    ]);
    rows.value = eventResponse.data;
    syncStatus.value = syncResponse.data;
    reconciliationItems.value = reconciliationResponse.data.items;
  } finally {
    loading.value = false;
  }
}

onMounted(load);

async function loadReviewQueue() {
  reviewLoading.value = true;
  try {
    const [queueResponse, breakdownResponse] = await Promise.all([
      getAttendanceReconciliationQueue({
        issue: reviewIssue.value,
        limit: reviewPageSize,
        offset: (reviewPage.value - 1) * reviewPageSize
      }),
      getAttendanceReconciliationBreakdown(reviewIssue.value)
    ]);
    reviewRows.value = queueResponse.data.rows;
    reviewTotal.value = queueResponse.data.total;
    reviewBreakdownRows.value = breakdownResponse.data.rows;
  } finally {
    reviewLoading.value = false;
  }
}

async function openReviewQueue(issue: AttendanceReconciliationItem["key"]) {
  reviewIssue.value = issue;
  reviewPage.value = 1;
  reviewVisible.value = true;
  await loadReviewQueue();
}
</script>

<template>
  <div class="activity-page" v-loading="loading">
    <section class="page-head">
      <div>
        <p>管理视图</p>
        <h1>活动与签到记录</h1>
        <span>展示已从签到系统同步的活动、场次和真实签到记录。</span>
      </div>
      <el-date-picker
        v-model="month"
        type="month"
        value-format="YYYY-MM"
        format="YYYY年MM月"
        @change="load"
      />
    </section>

    <el-alert
      :title="syncAlert.title"
      :type="syncAlert.type"
      :closable="false"
      show-icon
    />

    <section class="summary">
      <el-statistic title="活动场组" :value="rows.length" />
      <el-statistic title="签到记录" :value="totalEligible" />
      <el-statistic title="已签到人次" :value="totalCompleted" />
      <el-statistic
        title="综合完成率"
        :value="totalEligible ? (totalCompleted / totalEligible) * 100 : 0"
        suffix="%"
        :precision="1"
      />
    </section>

    <el-card shadow="never" class="reconciliation-card">
      <template #header>
        <div>
          <strong>数据复核待办</strong>
          <span>仅显示汇总数量，不显示个人资料；本页不提供写入或自动修正。</span>
        </div>
      </template>
      <el-space wrap>
        <el-tag
          v-for="item in reconciliationItems"
          :key="item.key"
          :type="item.count ? 'warning' : 'success'"
          effect="light"
          size="large"
        >
          {{ reconciliationLabels[item.key] }}：{{ item.count }}
        </el-tag>
      </el-space>
      <template v-for="item in reconciliationItems" :key="`review-${item.key}`">
        <el-button
          v-if="item.count"
          class="review-button"
          type="warning"
          plain
          @click="openReviewQueue(item.key)"
        >
          查看{{ reconciliationLabels[item.key] }}
        </el-button>
      </template>
    </el-card>

    <el-card shadow="never">
      <el-table :data="rows" stripe>
        <el-table-column prop="event_date" label="活动日期" min-width="130" />
        <el-table-column prop="title" label="活动" min-width="180" />
        <el-table-column prop="org_name" label="所属分中心" min-width="150" />
        <el-table-column label="活动类型" min-width="150">
          <template #default="{ row }">
            {{ activityTypeLabel(row.activity_type) }}
          </template>
        </el-table-column>
        <el-table-column prop="session_count" label="场次数" width="90" align="right" />
        <el-table-column prop="record_count" label="签到记录" width="100" align="right" />
        <el-table-column prop="present_count" label="已签到" width="90" align="right" />
        <el-table-column prop="source_key" label="数据源" min-width="130" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            {{ activityStatusLabel(row.status) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="reviewVisible"
      :title="`${reconciliationLabels[reviewIssue]}（只读）`"
      width="920px"
    >
      <el-alert
        title="本窗口只提供人工核对所需的最小信息，不会自动修正或写入生产数据。"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-table :data="reviewBreakdownRows" size="small" stripe class="review-breakdown">
        <el-table-column prop="org_name" label="分布组织" min-width="220" />
        <el-table-column prop="count" label="数量" width="100" align="right" />
      </el-table>
      <el-table v-loading="reviewLoading" :data="reviewRows" stripe>
        <el-table-column prop="event_date" label="日期" width="120">
          <template #default="{ row }">{{ row.event_date || "—" }}</template>
        </el-table-column>
        <el-table-column prop="title" label="活动" min-width="180">
          <template #default="{ row }">{{ row.title || "—" }}</template>
        </el-table-column>
        <el-table-column prop="session_name" label="场次" min-width="140">
          <template #default="{ row }">{{ row.session_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="name_snapshot" label="姓名" width="110" />
        <el-table-column prop="member_code_snapshot" label="学员编号" width="140" />
        <el-table-column label="状态" width="100">
          <template #default>{{ reviewIssue === "unmatched_attendance_records" ? "待人工匹配" : "待人工核对" }}</template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="reviewPage"
        class="review-pagination"
        layout="prev, pager, next, total"
        :page-size="reviewPageSize"
        :total="reviewTotal"
        @current-change="loadReviewQueue"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
.activity-page {
  display: grid;
  gap: 18px;
  padding: 20px;
}
.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  padding: 28px;
  color: #f8fbff;
  background: linear-gradient(125deg, #18364e, #2c6680);
  border-radius: 18px;
}
.page-head p {
  margin: 0 0 8px;
  color: #a8d9eb;
  letter-spacing: 0.18em;
}
.page-head h1 {
  margin: 0 0 10px;
  font-size: 28px;
}
.page-head span {
  color: #d4eaf2;
}
.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.summary :deep(.el-statistic) {
  padding: 18px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 14px;
}
.reconciliation-card :deep(.el-card__header) div {
  display: grid;
  gap: 6px;
}
.reconciliation-card :deep(.el-card__header) span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.review-button {
  margin-top: 16px;
}
.review-pagination {
  justify-content: flex-end;
  margin-top: 16px;
}
.review-breakdown {
  margin: 16px 0;
}
@media (max-width: 900px) {
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
