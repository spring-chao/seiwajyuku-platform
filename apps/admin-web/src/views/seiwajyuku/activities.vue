<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs from "dayjs";
import {
  getAttendanceEventGroups,
  getAttendanceEventGroupDetail,
  getAttendanceRecords,
  getAttendanceReconciliationBreakdown,
  getAttendanceReconciliationQueue,
  getAttendanceReconciliationSummary,
  getAttendanceSyncStatus,
  type AttendanceEventGroup,
  type AttendanceEventGroupDetail,
  type AttendanceRecord,
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
const detailVisible = ref(false);
const detailLoading = ref(false);
const detail = ref<AttendanceEventGroupDetail | null>(null);
const detailRecords = ref<AttendanceRecord[]>([]);
type ParticipationFilter = "AUTO" | "CLASS" | "REGION";
const participationFilter = ref<ParticipationFilter>("AUTO");

function participationScope(row: any) {
  const activityType = String(row.activity_type || "").toUpperCase();
  if (activityType.includes("REPORT") || activityType.startsWith("CENTER_")) {
    return "REGION" as const;
  }
  if (row.study_org_unit_id) return "CLASS" as const;
  if (row.org_unit_type === "REGIONAL_CENTER") return "REGION" as const;
  return "NONE" as const;
}

function participationScopeLabel(row: any) {
  const scope = participationScope(row);
  return scope === "CLASS" ? "本班" : scope === "REGION" ? "分中心" : "未配置";
}

function participationRosterCount(row: any) {
  return participationScope(row) === "CLASS"
    ? row.class_member_count
    : participationScope(row) === "REGION"
      ? row.region_member_count
      : 0;
}

function participationPresentCount(row: any) {
  return participationScope(row) === "CLASS"
    ? row.class_present_count
    : participationScope(row) === "REGION"
      ? row.region_present_count
      : 0;
}

function participationRateValue(row: any) {
  const roster = participationRosterCount(row);
  return roster > 0 ? (participationPresentCount(row) / roster) * 100 : null;
}

function detailParticipationRoster(session: any) {
  return detail.value && participationScope(detail.value.group) === "REGION"
    ? session.region_member_count
    : session.class_member_count;
}

function detailParticipationPresent(session: any) {
  return detail.value && participationScope(detail.value.group) === "REGION"
    ? session.region_present_count
    : session.class_present_count;
}

const visibleRows = computed(() =>
  rows.value.filter(
    (row) =>
      participationFilter.value === "AUTO" ||
      participationScope(row) === participationFilter.value
  )
);
const totalEligible = computed(() =>
  visibleRows.value.reduce((sum, item) => sum + item.record_count, 0)
);
const totalCompleted = computed(() =>
  visibleRows.value.reduce((sum, item) => sum + item.present_count, 0)
);
const classRows = computed(() =>
  visibleRows.value.filter((item) => participationScope(item) === "CLASS")
);
const regionRows = computed(() =>
  visibleRows.value.filter((item) => participationScope(item) === "REGION")
);
const participationRows = computed(() =>
  visibleRows.value.filter((item) => participationRateValue(item) !== null)
);
const totalParticipationPresent = computed(() =>
  visibleRows.value.reduce((sum, item) => sum + item.present_count, 0)
);
const averageParticipationRate = computed(() => {
  if (!participationRows.value.length) return 0;
  return (
    participationRows.value.reduce(
      (sum, item) => sum + (participationRateValue(item) || 0),
      0
    ) / participationRows.value.length
  );
});
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
  active_members_missing_study_group: "在册学员未分学习小组",
  active_members_expected_no_study_group: "按规则不建学习小组"
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

const attendanceStatusLabels: Record<string, string> = {
  PRESENT: "已签到",
  MANUAL_PRESENT: "人工确认",
  ABSENT: "未签到",
  LEAVE: "请假",
  INVALIDATED: "已作废",
  UNMATCHED: "待匹配"
};

const participantTypeLabels: Record<string, string> = {
  MEMBER: "学员",
  GUEST: "嘉宾",
  OBSERVER: "旁听"
};

function attendanceStatusLabel(status: string) {
  return attendanceStatusLabels[status] || status;
}

function participantTypeLabel(type: string) {
  return participantTypeLabels[type] || type;
}

function recordAttendanceRate(present: number, total: number) {
  return total > 0 ? `${((present / total) * 100).toFixed(1)}%` : "—";
}

function participationRate(present: number, roster: number) {
  return roster > 0 ? `${((present / roster) * 100).toFixed(1)}%` : "—";
}

function formatDateTime(value?: string | null) {
  return value ? dayjs(value).format("MM-DD HH:mm") : "—";
}

function reconciliationTagType(item: AttendanceReconciliationItem) {
  return item.key === "active_members_expected_no_study_group"
    ? "success"
    : item.count
      ? "warning"
      : "success";
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

async function openActivityDetail(row: any) {
  detailVisible.value = true;
  detailLoading.value = true;
  detail.value = null;
  detailRecords.value = [];
  try {
    const [detailResponse, recordsResponse] = await Promise.all([
      getAttendanceEventGroupDetail(row.id),
      getAttendanceRecords(row.id)
    ]);
    detail.value = detailResponse.data;
    detailRecords.value = recordsResponse.data;
  } finally {
    detailLoading.value = false;
  }
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
      <div class="head-filters">
        <el-date-picker
          v-model="month"
          type="month"
          value-format="YYYY-MM"
          format="YYYY年MM月"
          @change="load"
        />
        <el-select v-model="participationFilter" aria-label="参会率查询口径" style="width: 190px">
          <el-option label="全部活动（按活动自动）" value="AUTO" />
          <el-option label="只看班级参会率" value="CLASS" />
          <el-option label="只看分中心参会率" value="REGION" />
        </el-select>
      </div>
    </section>

    <el-alert
      :title="syncAlert.title"
      :type="syncAlert.type"
      :closable="false"
      show-icon
    />

    <section class="summary">
      <el-statistic title="活动场组" :value="visibleRows.length" />
      <el-statistic title="签到记录" :value="totalEligible" />
      <el-statistic title="已签到记录" :value="totalCompleted" />
      <el-statistic
        title="签到率"
        :value="totalEligible ? (totalCompleted / totalEligible) * 100 : 0"
        suffix="%"
        :precision="1"
      />
      <el-statistic title="班级参会活动" :value="classRows.length" />
      <el-statistic title="分中心参会活动" :value="regionRows.length" />
      <el-statistic title="参会人数" :value="totalParticipationPresent" />
      <el-statistic
        title="平均参会率"
        :value="averageParticipationRate"
        suffix="%"
        :precision="1"
      />
    </section>

    <el-card shadow="never" class="reconciliation-card">
      <template #header>
        <div class="card-heading">
          <strong>数据复核待办</strong>
          <span>仅显示汇总数量，不显示个人资料；本页不提供写入或自动修正。</span>
        </div>
      </template>
      <div class="reconciliation-grid">
        <div
          v-for="item in reconciliationItems"
          :key="item.key"
          class="reconciliation-item"
          :class="`is-${reconciliationTagType(item)}`"
        >
          <div class="reconciliation-item__top">
            <span>{{ reconciliationLabels[item.key] }}</span>
            <strong>{{ item.count }}</strong>
          </div>
          <el-button
            v-if="item.count && item.key !== 'active_members_expected_no_study_group'"
            class="review-button"
            type="warning"
            link
            @click="openReviewQueue(item.key)"
          >
            查看明细
          </el-button>
          <span v-else class="reconciliation-item__hint">按规则保留，不需处理</span>
        </div>
      </div>
    </el-card>

    <el-card shadow="never">
      <div class="rate-definition">
        报名人数 = 签到报名记录数；参会人数 = 正常签到人数（含人工确认和非塾生）；应参会人数 = 对应班级或分中心在册人数。签到率 = 参会人数 ÷ 报名人数；班级活动的本班参会率 = 本班已签到学员（含人工确认） ÷ 本班在册人数，报告会等大型活动的分中心参会率 = 分中心已签到学员（含人工确认） ÷ 分中心在册人数。
      </div>
      <el-table :data="visibleRows" stripe class="activity-table">
        <el-table-column prop="event_date" label="活动日期" min-width="130" />
        <el-table-column prop="title" label="活动" min-width="180" />
        <el-table-column prop="class_name" label="所属班级" min-width="130">
          <template #default="{ row }">{{ row.class_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="org_name" label="所属分中心" min-width="150" />
        <el-table-column label="活动类型" min-width="150">
          <template #default="{ row }">
            {{ activityTypeLabel(row.activity_type) }}
          </template>
        </el-table-column>
        <el-table-column prop="session_count" label="场次数" width="90" align="right" />
        <el-table-column label="参会口径" width="100">
          <template #default="{ row }">{{ participationScopeLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="报名人数" width="100" align="right">
          <template #default="{ row }">{{ row.record_count }}</template>
        </el-table-column>
        <el-table-column label="应参会人数" width="110" align="right">
          <template #default="{ row }">{{ participationRosterCount(row) || "—" }}</template>
        </el-table-column>
        <el-table-column label="参会人数" width="100" align="right">
          <template #default="{ row }">{{ row.present_count }}</template>
        </el-table-column>
        <el-table-column label="签到率" width="100" align="right">
          <template #default="{ row }">
            {{ recordAttendanceRate(row.present_count, row.record_count) }}
          </template>
        </el-table-column>
        <el-table-column label="参会率" width="100" align="right">
          <template #default="{ row }">
            <span :class="{ 'rate-low': participationRateValue(row) !== null && (participationRateValue(row) || 0) < 50 }">
              {{ participationRateValue(row) === null ? "—" : participationRate(participationPresentCount(row), participationRosterCount(row)) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            {{ activityStatusLabel(row.status) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openActivityDetail(row)">
              查看明细
            </el-button>
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

    <el-dialog
      v-model="detailVisible"
      :title="detail ? `活动明细 · ${detail.group.title || '未命名活动'}` : '活动明细'"
      width="1120px"
      top="5vh"
      class="activity-detail-dialog"
    >
      <div v-loading="detailLoading">
        <template v-if="detail">
          <div class="detail-overview">
            <div>
              <span>活动日期</span>
              <strong>{{ detail.group.event_date }}</strong>
            </div>
            <div>
              <span>所属班级</span>
              <strong>{{ detail.group.class_name || "—" }}</strong>
            </div>
            <div>
              <span>所属分中心</span>
              <strong>{{ detail.group.org_name }}</strong>
            </div>
            <div>
              <span>活动类型</span>
              <strong>{{ activityTypeLabel(detail.group.activity_type) }}</strong>
            </div>
            <div>
              <span>签到记录</span>
              <strong>{{ detail.group.record_count }}</strong>
            </div>
            <div>
              <span>已签到记录</span>
              <strong>{{ detail.group.present_count }}</strong>
            </div>
            <div>
              <span>签到率</span>
              <strong>{{ recordAttendanceRate(detail.group.present_count, detail.group.record_count) }}</strong>
            </div>
            <div>
              <span>参会口径</span>
              <strong>{{ participationScopeLabel(detail.group) }}</strong>
            </div>
            <div>
              <span>报名人数</span>
              <strong>{{ detail.group.record_count }}</strong>
            </div>
            <div>
              <span>应参会人数</span>
              <strong>{{ participationRosterCount(detail.group) || "—" }}</strong>
            </div>
            <div>
              <span>参会人数</span>
              <strong>{{ detail.group.present_count }}</strong>
            </div>
            <div>
              <span>参会率</span>
              <strong class="detail-rate">{{ participationRateValue(detail.group) === null ? "—" : participationRate(participationPresentCount(detail.group), participationRosterCount(detail.group)) }}</strong>
            </div>
          </div>

          <section class="detail-section">
            <div class="detail-section__heading">
              <h3>场次汇总</h3>
              <span>同时展示签到率与参会率；班级活动按班级组织 ID，报告会等大型活动按分中心组织 ID 统计</span>
            </div>
            <el-table :data="detail.sessions" size="small" stripe class="activity-table">
              <el-table-column prop="session_name" label="场次" min-width="180">
                <template #default="{ row }">{{ row.session_name || row.session_code }}</template>
              </el-table-column>
              <el-table-column label="计划时间" width="150">
                <template #default="{ row }">{{ formatDateTime(row.scheduled_start_at) }}</template>
              </el-table-column>
              <el-table-column prop="record_count" label="签到记录" width="100" align="right" />
              <el-table-column prop="present_count" label="已签到记录" width="110" align="right" />
              <el-table-column label="签到率" width="100" align="right">
                <template #default="{ row }">{{ recordAttendanceRate(row.present_count, row.record_count) }}</template>
              </el-table-column>
              <el-table-column label="报名人数" width="100" align="right">
                <template #default="{ row }">{{ row.record_count }}</template>
              </el-table-column>
              <el-table-column label="应参会人数" width="110" align="right">
                <template #default="{ row }">{{ detailParticipationRoster(row) || "—" }}</template>
              </el-table-column>
              <el-table-column label="参会人数" width="100" align="right">
                <template #default="{ row }">{{ row.present_count }}</template>
              </el-table-column>
              <el-table-column label="参会率" width="100" align="right">
                <template #default="{ row }">{{ participationRate(detailParticipationPresent(row), detailParticipationRoster(row)) }}</template>
              </el-table-column>
              <el-table-column label="积分" width="90" align="right">
                <template #default="{ row }">{{ row.total_points ?? "—" }}</template>
              </el-table-column>
            </el-table>
          </section>

          <section class="detail-section">
            <div class="detail-section__heading">
              <h3>签到明细</h3>
              <span>只读展示姓名、学员编号、签到状态和时间，不提供修改入口</span>
            </div>
            <el-table :data="detailRecords" size="small" stripe max-height="380">
              <el-table-column prop="name_snapshot" label="姓名" min-width="120" />
              <el-table-column prop="member_code_snapshot" label="学员编号" min-width="150" />
              <el-table-column label="场次" min-width="140">
                <template #default="{ row }">{{ row.session_name || row.session_code }}</template>
              </el-table-column>
              <el-table-column label="参与类型" width="100">
                <template #default="{ row }">{{ participantTypeLabel(row.participant_type) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <el-tag :type="row.attendance_status === 'PRESENT' || row.attendance_status === 'MANUAL_PRESENT' ? 'success' : 'info'" size="small">
                    {{ attendanceStatusLabel(row.attendance_status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="签到时间" width="150">
                <template #default="{ row }">{{ formatDateTime(row.checked_at) }}</template>
              </el-table-column>
            </el-table>
          </section>
        </template>
      </div>
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
.head-filters {
  display: flex;
  align-items: center;
  gap: 10px;
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
.card-heading span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.reconciliation-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(220px, 1fr));
  gap: 12px;
}
.reconciliation-item {
  min-height: 88px;
  padding: 14px 16px 10px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
}
.reconciliation-item.is-warning {
  border-color: var(--el-color-warning-light-5);
  background: var(--el-color-warning-light-9);
}
.reconciliation-item.is-success {
  border-color: var(--el-color-success-light-5);
  background: var(--el-color-success-light-9);
}
.reconciliation-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.reconciliation-item__top strong {
  color: var(--el-text-color-primary);
  font-size: 22px;
  line-height: 1;
}
.reconciliation-item__hint {
  display: inline-block;
  margin-top: 12px;
  color: var(--el-color-success);
  font-size: 12px;
}
.review-button {
  margin-top: 10px;
  padding: 0;
}
.review-pagination {
  justify-content: flex-end;
  margin-top: 16px;
}
.review-breakdown {
  margin: 16px 0;
}
.rate-definition {
  margin-bottom: 12px;
  padding: 10px 14px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}
.activity-table {
  width: 100%;
}
.activity-table :deep(.el-table__header th) {
  color: var(--el-text-color-primary);
  font-weight: 600;
  background: var(--el-fill-color-light);
}
.activity-table :deep(.el-table__cell) {
  padding: 12px 0;
}
.activity-table :deep(.cell) {
  line-height: 1.45;
  white-space: normal;
  word-break: break-word;
}
.activity-table :deep(.el-table__body tr:hover > td) {
  background: var(--el-color-primary-light-9);
}
.rate-low {
  color: var(--el-color-danger);
  font-weight: 600;
}
.detail-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 22px;
}
.detail-overview > div {
  display: grid;
  gap: 7px;
  padding: 14px 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
}
.detail-overview span,
.detail-section__heading span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.detail-overview strong {
  color: var(--el-text-color-primary);
  font-size: 18px;
}
.detail-overview .detail-rate {
  color: var(--el-color-primary);
}
.detail-section + .detail-section {
  margin-top: 24px;
}
.detail-section__heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 10px;
}
.detail-section__heading h3 {
  margin: 0;
  color: var(--el-text-color-primary);
  font-size: 16px;
}
@media (max-width: 900px) {
  .page-head,
  .head-filters {
    align-items: stretch;
    flex-direction: column;
  }
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .reconciliation-grid,
  .detail-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .reconciliation-grid,
  .detail-overview {
    grid-template-columns: 1fr;
  }
}
</style>
