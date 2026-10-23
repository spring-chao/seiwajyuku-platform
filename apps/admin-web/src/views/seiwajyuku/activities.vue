<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import dayjs from "dayjs";
import { activityTypeLabel, activitySessionLabel, registrationCount, isClassMeeting } from "@/utils/business-labels.mjs";
import { activityCenter, filterActivitySessions, groupActivitySessions } from "@/utils/activity-list.mjs";
import { chinaTimestampDate, chinaTime } from "@/utils/business-date";
import { ElMessage, ElMessageBox } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import { hasPerms } from "@/utils/auth";
import {
  getAttendanceActivityRows,
  getMemberOrgCatalog,
  type MemberOrgCatalog,
  deleteAttendanceEventGroup,
  getAttendanceEventGroupDetail,
  getAttendanceRecords,
  downloadAttendanceRecords,
  getAttendanceSyncStatus,
  type AttendanceActivityRow,
  type AttendanceEventGroupDetail,
  type AttendanceClassParticipation,
  type AttendanceRecord,
  type AttendanceSyncStatus
} from "@/api/seiwajyuku";

defineOptions({ name: "ActivityAdmin" });

const loading = ref(false);
const deletingGroupId = ref<number | null>(null);
const canDeleteActivity = computed(() =>
  ["attendance:manage", "attendance:view"].every(permission =>
    useUserStoreHook().permissions.includes(permission)
  )
);

async function deleteActivity(row: any) {
  if (deletingGroupId.value !== null) return;
  if (!Number.isInteger(row.id)) return;
  deletingGroupId.value = row.id;
  const related = row.sessions || [];
  try {
    try {
      await ElMessageBox.confirm(
      `请确认“${row.title || "未命名活动"}”是测试或无效活动。${related.length > 1 ? `该活动的 ${related.length} 个场次将一起删除。` : ""}删除后活动及测试签到不再显示在活动列表、签到候选和个人参与记录中。已结算学分或人工裁定的活动需先复核。`,
      "删除测试或无效活动", { type: "warning", confirmButtonText: "确认是测试活动并删除", cancelButtonText: "取消" }
      );
    } catch {
      return;
    }
    await deleteAttendanceEventGroup(row.id, row.title || "");
    ElMessage.success("活动已删除");
    await load();
  } finally {
    deletingGroupId.value = null;
  }
}
const dateRange = ref<[string, string] | null>([
  dayjs().startOf("month").format("YYYY-MM-DD"), dayjs().endOf("month").format("YYYY-MM-DD")
]);
const filters = reactive({ center_id: "", class_id: "", activity_type: "" });
const rows = ref<AttendanceActivityRow[]>([]);
const catalog = ref<MemberOrgCatalog | null>(null);
const organizations = computed(() => catalog.value ? [
  ...catalog.value.units, ...catalog.value.shukus, ...catalog.value.management_units, ...catalog.value.classes, ...catalog.value.groups
] : []);
const centers = computed(() => {
  const items = new Map<string, { id: string; name: string }>();
  for (const row of rows.value) {
    const center = activityCenter(row, organizations.value);
    items.set(center.id, center);
  }
  return [...items.values()].sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
});
const classes = computed(() => {
  const items = new Map<string, { id: string; name: string }>();
  for (const row of rows.value) {
    if (filters.center_id && activityCenter(row, organizations.value).id !== filters.center_id) continue;
    const id = row.study_org_unit_id || (row.org_unit_type === "CLASS" ? row.org_unit_id : "");
    if (id) items.set(id, { id, name: row.class_name || row.org_name });
  }
  return [...items.values()].sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
});
watch(() => filters.center_id, () => { filters.class_id = ""; });
const activityTypes = computed(() => [...new Set(rows.value.map(row => row.activity_type))].sort());
const syncStatus = ref<AttendanceSyncStatus | null>(null);
const detailVisible = ref(false);
const detailLoading = ref(false);
const detailSessionId = ref(0);
const detailDisplayTitle = ref("");
const downloadingRecords = ref(false);
const detail = ref<AttendanceEventGroupDetail | null>(null);
const detailRecords = ref<AttendanceRecord[]>([]);
const detailClassBreakdown = computed<AttendanceClassParticipation[]>(() =>
  detail.value?.class_breakdown || []
);
const detailSummary = computed<any>(() => {
  if (!detail.value) return null;
  if (isClassMeeting(detail.value.group.activity_type)) return { ...detail.value.group,
    registration_count: detail.value.group.overall_registration_count ?? null,
    present_count: detail.value.group.overall_present_count ?? null };
  const session = detail.value.sessions.find(
    session => session.id === detailSessionId.value
  );
  return session ? { ...detail.value.group, ...session } : detail.value.group;
});
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
  return participationScope(row) === "NONE" ? 0 : row.present_count;
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
  return detail.value && participationScope(detail.value.group) !== "NONE"
    ? session.present_count
    : 0;
}

const visibleSessionRows = computed(() => filterActivitySessions(rows.value, filters, organizations.value));
const visibleRows = computed(() => groupActivitySessions(visibleSessionRows.value));
const totalEligible = computed<number | null>(() => {
  const counts = visibleRows.value.map(item => registrationCount(item));
  return counts.some(count => count === null) ? null : counts.reduce((sum, count) => sum + (count || 0), 0);
});
const totalCompleted = computed<number | null>(() => {
  const counts = visibleRows.value.map(item => item.present_count);
  return counts.some(count => count === null) ? null : counts.reduce((sum, count) => sum + count, 0);
});
function overallRate(row: any) {
  return typeof row.overall_participation_rate === "number" ? `${row.overall_participation_rate.toFixed(1)}%` : "—";
}
const visibleDetailSessions = computed(() => detail.value?.sessions.filter(session =>
  detailSessionId.value === 0 || session.id === detailSessionId.value) || []);
const visibleDetailRecords = computed(() => detailRecords.value.filter(record =>
  detailSessionId.value === 0 || record.attendance_session_id === detailSessionId.value));
function resetFilters() {
  filters.center_id = ""; filters.class_id = ""; filters.activity_type = "";
  dateRange.value = [dayjs().startOf("month").format("YYYY-MM-DD"), dayjs().endOf("month").format("YYYY-MM-DD")];
  void load();
}
const syncAlert = computed(() => {
  const status = syncStatus.value;
  if (!status || status.state === "NO_RUNS") {
    return { type: "info" as const, title: "签到自动同步尚无运行记录" };
  }
  if (status.state === "STALE") {
    const finishedAt = status.last_run?.finished_at
      ? dayjs(status.last_run.finished_at).format("YYYY-MM-DD HH:mm")
      : "未知时间";
    return {
      type: "error" as const,
      title: `签到自动同步已中断，最近完成于 ${finishedAt}，请技术管理员检查`
    };
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
const activityStatusLabels: Record<string, string> = {
  ACTIVE: "正常",
  INACTIVE: "已停用",
  CANCELLED: "已取消",
  CLOSED: "已结束",
  DRAFT: "草稿"
};

function activityStatusLabel(status: string) {
  return activityStatusLabels[status?.toUpperCase()] || "待核对";
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
  return attendanceStatusLabels[status?.toUpperCase()] || "待核对";
}

function participantTypeLabel(type: string) {
  return participantTypeLabels[type?.toUpperCase()] || "其他参加人";
}

function recordAttendanceRate(present: number, total: number | null) {
  return total !== null && total > 0 ? `${((present / total) * 100).toFixed(1)}%` : "—";
}

function participationRate(present: number, roster: number) {
  return roster > 0 ? `${((present / roster) * 100).toFixed(1)}%` : "—";
}

function formatDateTime(value?: string | null) {
  return value ? `${chinaTimestampDate(value).slice(5)} ${chinaTime(value)}` : "—";
}

function checkedAtLabel(row: Pick<AttendanceRecord, "checked_at" | "checked_at_review_status">) {
  return row.checked_at_review_status === "TIME_BEFORE_CHECKIN_START"
    ? "待核对"
    : formatDateTime(row.checked_at);
}

function attendanceFlagLabel(value?: number | null, finalPoints?: number | string | null) {
  if (finalPoints === null || finalPoints === undefined) return "—";
  return Number(value) ? "是" : "否";
}

function attendancePointsLabel(value?: number | string | null) {
  return value === null || value === undefined ? "—" : String(value);
}

let loadRequest = 0;
async function load() {
  const request = ++loadRequest;
  loading.value = true;
  try {
    const [eventResponse, syncResponse, catalogResponse] = await Promise.all([
      getAttendanceActivityRows({ date_from: dateRange.value?.[0], date_to: dateRange.value?.[1] }),
      getAttendanceSyncStatus(), getMemberOrgCatalog()
    ]);
    if (request !== loadRequest) return;
    rows.value = eventResponse.data;
    syncStatus.value = syncResponse.data;
    catalog.value = catalogResponse.data;
  } catch {
    if (request === loadRequest) { rows.value = []; ElMessage.error("活动加载失败，请重试查询"); }
  } finally {
    if (request === loadRequest) loading.value = false;
  }
}

onMounted(load);

let detailRequest = 0;
async function openActivityDetail(row: any) {
  const request = ++detailRequest;
  detailVisible.value = true;
  detailLoading.value = true;
  detail.value = null;
  detailRecords.value = [];
  detailSessionId.value = 0;
  detailDisplayTitle.value = row.display_title || row.title || "未命名活动";
  try {
    const [detailResponse, recordsResponse] = await Promise.all([
      getAttendanceEventGroupDetail(row.id),
      getAttendanceRecords(row.id)
    ]);
    if (request !== detailRequest) return;
    detail.value = detailResponse.data;
    detailRecords.value = recordsResponse.data;
  } catch {
    if (request === detailRequest) ElMessage.error("活动明细加载失败，请关闭后重试");
  } finally {
    if (request === detailRequest) detailLoading.value = false;
  }
}

async function downloadDetailRecords() {
  if (!detail.value) return;
  downloadingRecords.value = true;
  try {
    const response = await downloadAttendanceRecords(
      detail.value.group.id,
      detailSessionId.value || undefined
    );
    const blob = response instanceof Blob
      ? response
      : new Blob([response], {
          type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `签到明细-${detail.value.group.event_date}${detailSessionId.value ? `-${detailSessionId.value}` : "-全部场次"}.xlsx`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    ElMessage.success("签到明细 Excel 已下载");
  } finally {
    downloadingRecords.value = false;
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
        <router-link v-if="hasPerms('attendance:view')" to="/operations/checkin-management">
          <el-button type="primary">签到现场管理</el-button>
        </router-link>
      </div>
    </section>
    <el-card shadow="never" class="filter-card">
      <div class="activity-filters">
        <el-select v-model="filters.center_id" clearable filterable placeholder="全部分中心" aria-label="分中心筛选">
          <el-option v-for="item in centers" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="filters.class_id" clearable filterable placeholder="全部班级" aria-label="班级筛选">
          <el-option v-for="item in classes" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-select v-model="filters.activity_type" clearable placeholder="全部活动类型" aria-label="活动类型筛选">
          <el-option v-for="type in activityTypes" :key="type" :label="activityTypeLabel(type)" :value="type" />
        </el-select>
        <el-date-picker v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
          start-placeholder="开始日期" end-placeholder="结束日期" aria-label="活动日期范围" />
        <el-button type="primary" :loading="loading" @click="load">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>
    </el-card>

    <el-alert
      :title="syncAlert.title"
      :type="syncAlert.type"
      :closable="false"
      show-icon
    />

    <section class="summary">
      <el-statistic title="活动数" :value="visibleRows.length" />
      <el-statistic title="扫码场次数" :value="visibleSessionRows.length" />
      <el-statistic title="报名合计" :value="totalEligible ?? 0" :formatter="() => totalEligible === null ? '待核实' : String(totalEligible)" />
      <el-statistic title="已签到合计" :value="totalCompleted ?? 0" :formatter="() => totalCompleted === null ? '待核实' : String(totalCompleted)" />
    </section>

    <el-card shadow="never">
      <div class="rate-definition">
        每行显示一场活动。班级会报名人数按本班当前在册人数计一次；上午、下午有效签到按身份去重，计入本班在册学员，以及正式为本班服务的跨班班主任、副班主任、辅导员。空巴、其他跨班人员和嘉宾不计入整体参会率，保留在明细中。整体参会率＝上述去重人数÷本班人数。
      </div>
      <el-table :data="visibleRows" row-key="id" stripe class="activity-table">
        <el-table-column prop="event_date" label="活动日期" min-width="130" />
        <el-table-column label="活动" min-width="220">
          <template #default="{ row }">{{ row.display_title || row.title || "未命名活动" }}</template>
        </el-table-column>
        <el-table-column prop="class_name" label="所属班级" min-width="130">
          <template #default="{ row }">{{ row.class_name || "—" }}</template>
        </el-table-column>
        <el-table-column label="所属分中心" min-width="150"><template #default="{ row }">{{ activityCenter(row, organizations).name }}</template></el-table-column>
        <el-table-column label="活动类型" min-width="150">
          <template #default="{ row }">
            {{ activityTypeLabel(row.activity_type) }}
          </template>
        </el-table-column>
        <el-table-column label="扫码场次" width="100"><template #default="{ row }">{{ row.session_count }} 场次</template></el-table-column>
        <el-table-column label="报名人数" width="110" align="right"><template #default="{ row }">{{ registrationCount(row) ?? "待核实" }}</template></el-table-column>
        <el-table-column label="签到人数" width="120" align="right"><template #default="{ row }">{{ row.present_count ?? "待核实" }}</template></el-table-column>
        <el-table-column label="整体参会率" width="130" align="right"><template #default="{ row }">{{ overallRate(row) }}</template></el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openActivityDetail(row)">
              查看明细
            </el-button>
            <el-button v-if="canDeleteActivity" type="danger" link
              :loading="deletingGroupId === row.id"
              :disabled="deletingGroupId !== null && deletingGroupId !== row.id"
              @click="deleteActivity(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="detailVisible"
      :title="detail ? `活动明细 · ${detailDisplayTitle || detail.group.title || '未命名活动'}` : '活动明细'"
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
              <strong>{{ activityCenter(detail.group, organizations).name }}</strong>
            </div>
            <div>
              <span>活动类型</span>
              <strong>{{ activityTypeLabel(detail.group.activity_type) }}</strong>
            </div>
            <div>
              <span>参会口径</span>
              <strong>{{ participationScopeLabel(detail.group) }}</strong>
            </div>
            <div>
              <span>{{ isClassMeeting(detail.group.activity_type) ? "报名人数（本班）" : "报名人次" }}</span>
              <strong>{{ registrationCount(detailSummary) ?? "待核实" }}</strong>
            </div>
            <div><span>{{ isClassMeeting(detail.group.activity_type) ? "整体签到人数（本班及本班师资去重）" : "已签到人次" }}</span><strong>{{ detailSummary.present_count ?? "待核实" }}</strong></div>
            <div v-if="isClassMeeting(detail.group.activity_type)"><span>整体参会率</span><strong>{{ overallRate(detail.group) }}</strong></div>
            <div><span>扫码场次</span><strong>{{ visibleDetailSessions.length }}</strong></div>
          </div>

          <el-alert v-if="detail.group.overall_unresolved_record_count" type="warning" :closable="false"
            :title="`上午／下午已确认 ${detail.group.overall_identified_count} 人，另有 ${detail.group.overall_unresolved_record_count} 条签到缺少唯一身份，整体人数和参会率待核实。`" />

          <section v-if="detailClassBreakdown.length" class="detail-section">
            <div class="detail-section__heading">
              <h3>班级参会率明细</h3>
              <span>报告会按分中心汇总；此处可继续查看分中心内各班级的在册学员参会情况</span>
            </div>
            <el-table :data="detailClassBreakdown" size="small" stripe class="activity-table">
              <el-table-column prop="class_name" label="班级" min-width="220" />
              <el-table-column label="应参会人数" width="120" align="right">
                <template #default="{ row }">{{ row.class_member_count }}</template>
              </el-table-column>
              <el-table-column label="已签到学员" width="120" align="right">
                <template #default="{ row }">{{ row.class_present_count }}</template>
              </el-table-column>
              <el-table-column label="班级参会率" width="130" align="right">
                <template #default="{ row }">
                  {{ participationRate(row.class_present_count, row.class_member_count) }}
                </template>
              </el-table-column>
            </el-table>
          </section>

          <section class="detail-section">
            <el-radio-group v-model="detailSessionId" class="session-switch" aria-label="查看扫码场次">
              <el-radio-button :value="0">全部场次</el-radio-button>
              <el-radio-button v-for="session in detail.sessions" :key="session.id" :value="session.id">
                {{ activitySessionLabel(detail.group.activity_type, session.session_code) }}
              </el-radio-button>
            </el-radio-group>
            <div class="detail-section__heading">
              <h3>场次汇总</h3>
              <span>班级会顶部整体统计不随场次切换：上午、下午签到合并去重，计入本班在册学员及正式为本班服务的班主任、副班主任、辅导员；分母仍为本班在册人数。空巴、其他跨班人员和嘉宾不计入整体参会率；各场报名、签到和积分保留在下方明细中。其他活动按原参会口径统计。</span>
            </div>
            <el-table :data="visibleDetailSessions" size="small" stripe class="activity-table">
              <el-table-column prop="session_name" label="场次" min-width="180">
                <template #default="{ row }">{{ activitySessionLabel(row.activity_type || detail?.group.activity_type, row.session_code) }}</template>
              </el-table-column>
              <el-table-column label="计划时间" width="150">
                <template #default="{ row }">{{ formatDateTime(row.scheduled_start_at) }}</template>
              </el-table-column>
              <el-table-column label="报名人数" width="100" align="right">
                <template #default="{ row }">{{ registrationCount(row) ?? "待核实" }}</template>
              </el-table-column>
              <el-table-column label="应参会人数" width="110" align="right">
                <template #default="{ row }">{{ detailParticipationRoster(row) || "—" }}</template>
              </el-table-column>
              <el-table-column label="参会人数" width="100" align="right">
                <template #default="{ row }">{{ row.present_count }}</template>
              </el-table-column>
              <el-table-column label="签到率" width="100" align="right">
                <template #default="{ row }">{{ recordAttendanceRate(row.present_count, registrationCount(row)) }}</template>
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
              <div class="detail-section__actions">
                <span>只读展示签到状态、时间、迟到、早退和积分，不提供修改入口</span>
                <el-button
                  type="primary"
                  plain
                  size="small"
                  :loading="downloadingRecords"
                  @click="downloadDetailRecords"
                >
                  下载签到明细 Excel
                </el-button>
              </div>
            </div>
            <el-table :data="visibleDetailRecords" size="small" stripe max-height="380">
              <el-table-column prop="name_snapshot" label="姓名" min-width="120" />
              <el-table-column prop="member_code_snapshot" label="学员编号" min-width="150" />
              <el-table-column label="场次" min-width="140">
                <template #default="{ row }">{{ activitySessionLabel(row.activity_type || detail?.group.activity_type, row.session_code) }}</template>
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
                <template #default="{ row }">{{ checkedAtLabel(row) }}</template>
              </el-table-column>
              <el-table-column label="迟到" width="80">
                <template #default="{ row }">{{ attendanceFlagLabel(row.is_late, row.final_points) }}</template>
              </el-table-column>
              <el-table-column label="早退" width="80">
                <template #default="{ row }">{{ attendanceFlagLabel(row.is_early_leave, row.final_points) }}</template>
              </el-table-column>
              <el-table-column label="积分" width="80">
                <template #default="{ row }">{{ attendancePointsLabel(row.final_points) }}</template>
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
.activity-filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.activity-filters .el-select { width: 180px; }
.activity-filters :deep(.el-date-editor) { max-width: 340px; }
.session-switch { margin-bottom: 18px; }
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
.detail-section__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  gap: 12px;
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
  .detail-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .detail-overview {
    grid-template-columns: 1fr;
  }
  .detail-section__actions {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
