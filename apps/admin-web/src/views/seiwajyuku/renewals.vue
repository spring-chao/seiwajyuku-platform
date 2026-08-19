<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import {
  createRenewalCycleFromMember,
  createRenewalFollowup,
  getRenewalActionCard,
  getRenewalAssignees,
  getRenewalCoverage,
  getRenewalCycles,
  getRenewalFollowups,
  getRenewalOverview,
  getSystemEnvironment,
  updateRenewalCycle,
  type RenewalActionCard,
  type RenewalFollowup,
  type FollowupAssignee,
  type RenewalCycle,
  type RenewalCoverage,
  type RenewalCoverageRow,
  type RenewalOverviewRow
} from "@/api/seiwajyuku";

defineOptions({ name: "RenewalOperations" });

const year = ref(2026);
const loading = ref(false);
const rows = ref<RenewalOverviewRow[]>([]);
const cycles = ref<RenewalCycle[]>([]);
const coverage = ref<RenewalCoverage>({
  year: 2026,
  summary: {
    member_total: 0,
    active_member_total: 0,
    cycle_total: 0,
    ready_to_create_count: 0,
    missing_renewal_month_count: 0,
    inactive_member_count: 0,
    suspended_member_count: 0
  },
  rows: [],
  truncated: false
});
const cycleCreatingMemberId = ref<number>();
const cycleDetailVisible = ref(false);
const cycleDetailLoading = ref(false);
const cycleSaving = ref(false);
const followupSaving = ref(false);
const selectedCycle = ref<RenewalCycle>();
const actionCard = ref<RenewalActionCard>();
const followups = ref<RenewalFollowup[]>([]);
const cycleAssignees = ref<FollowupAssignee[]>([]);
const writeEnabled = ref(true);
const filters = reactive<{
  org_unit_id: string;
  due_month: number | undefined;
  renewal_status: "UNRENEWED" | "RENEWED" | "ALL";
  member_name: string;
}>({
  org_unit_id: "",
  due_month: undefined,
  renewal_status: "UNRENEWED",
  member_name: ""
});
const cycleForm = reactive({
  status: "",
  result: "",
  assigned_user_id: undefined as number | undefined
});
const followupForm = reactive({
  channel: "PHONE",
  summary: "",
  intention: "",
  needs_support: false,
  next_action: "",
  next_followup_at: ""
});
const router = useRouter();
const canManageMembers = computed(() =>
  useUserStoreHook().permissions.includes("members:manage")
);

const centerNames = computed(() => [
  ...new Set(rows.value.map(item => item.org_name))
]);
const centerOptions = computed(() => {
  const values = new Map<string, string>();
  rows.value.forEach(item => values.set(item.org_unit_id, item.org_name));
  return [...values.entries()].map(([id, name]) => ({ id, name }));
});
const monthOptions = Array.from({ length: 12 }, (_, index) => index + 1);
const total = computed(() =>
  rows.value.reduce((sum, item) => sum + Number(item.count), 0)
);
const renewed = computed(() =>
  rows.value
    .filter(item => item.status === "RENEWED")
    .reduce((sum, item) => sum + Number(item.count), 0)
);
const needsAttention = computed(() =>
  rows.value
    .filter(item =>
      [
        "PENDING_FIRST_CONTACT",
        "CONTACTED_WAITING_REPLY",
        "IN_COMMUNICATION"
      ].includes(item.status)
    )
    .reduce((sum, item) => sum + Number(item.count), 0)
);
const monthlyRows = computed(() =>
  Array.from({ length: 12 }, (_, index) => {
    const month = index + 1;
    const monthRows = rows.value.filter(item => item.due_month === month);
    return {
      month,
      total: monthRows.reduce((sum, item) => sum + Number(item.count), 0),
      renewed: monthRows
        .filter(item => item.status === "RENEWED")
        .reduce((sum, item) => sum + Number(item.count), 0),
      attention: monthRows
        .filter(item => item.status !== "RENEWED")
        .reduce((sum, item) => sum + Number(item.count), 0)
    };
  })
);

const statusLabel = (status: string) =>
  ({
    PENDING_FIRST_CONTACT: "待首次联系",
    CONTACTED_WAITING_REPLY: "已联系待回复",
    IN_COMMUNICATION: "沟通中",
    RENEWED: "已续费",
    NOT_RENEWING: "明确不续费",
    DEFERRED: "延期/暂停",
    EXITED: "已退出"
  })[status] ?? status;

const cycleStatusLabel = (status: string) => statusLabel(status);
const coverageStatusLabel = (status: RenewalCoverageRow["sync_status"]) =>
  ({
    SYNCED: "已同步",
    SYNCED_INACTIVE: "已有周期，学员已流失",
    SYNCED_SUSPENDED: "已有周期，学员已暂停",
    READY_TO_CREATE: "可建立周期",
    MISSING_RENEWAL_MONTH: "缺少续费月份",
    INACTIVE: "流失，不进入新周期",
    SUSPENDED: "暂停，不进入新周期"
  })[status];
const coverageStatusType = (status: RenewalCoverageRow["sync_status"]) =>
  ({
    SYNCED: "success",
    SYNCED_INACTIVE: "warning",
    SYNCED_SUSPENDED: "warning",
    READY_TO_CREATE: "primary",
    MISSING_RENEWAL_MONTH: "danger",
    INACTIVE: "info",
    SUSPENDED: "info"
  })[status] as "success" | "warning" | "primary" | "danger" | "info";
const channelLabel = (channel: string) =>
  ({
    NONE: "无需联系",
    PHONE: "电话",
    WECHAT: "微信",
    MEETING: "面谈",
    VISIT: "走访",
    OTHER: "其他"
  })[channel] ?? channel;
const stageTagType = (code?: string) =>
  ({
    PREPARE: "info",
    OBSERVE_3: "success",
    RENEW_2: "primary",
    FOLLOW_1: "warning",
    DUE_NOW: "danger",
    RECOVERY: "danger",
    CLOSED: "info"
  })[code || ""] as "success" | "primary" | "warning" | "danger" | "info";
const cycleStatusOptions = [
  ["PENDING_FIRST_CONTACT", "待首次联系"],
  ["CONTACTED_WAITING_REPLY", "已联系待回复"],
  ["IN_COMMUNICATION", "沟通中"],
  ["RENEWED", "已续费"],
  ["NOT_RENEWING", "明确不续费"],
  ["DEFERRED", "延期/暂停"],
  ["EXITED", "已退出"]
] as const;

function errorText(error: any, fallback: string) {
  const detail = error?.response?.data?.detail;
  const normalizedDetail = Array.isArray(detail)
    ? detail
        .map(item => item?.msg)
        .filter(Boolean)
        .join("；")
    : typeof detail === "string"
      ? detail
      : "";
  if (error?.response?.status === 403) {
    return (
      normalizedDetail ||
      "当前环境处于只读状态，修改不会保存；请先开启已批准的写入窗口"
    );
  }
  return normalizedDetail || fallback;
}

async function loadEnvironment() {
  try {
    const response = await getSystemEnvironment();
    writeEnabled.value = !(
      response.deployment_read_only ||
      (response.production && !response.production_mutations_allowed)
    );
  } catch {
    // The endpoint is informational. Keep the controls available in local
    // development; the server remains the final write gate.
  }
}

async function load() {
  loading.value = true;
  try {
    const memberName = filters.member_name.trim();
    const [overviewResponse, cycleResponse, coverageResponse] =
      await Promise.all([
        getRenewalOverview(year.value),
        getRenewalCycles(year.value, {
          org_unit_id: filters.org_unit_id || undefined,
          due_month: filters.due_month,
          renewal_status: filters.renewal_status,
          member_name: memberName || undefined
        }),
        getRenewalCoverage(year.value, {
          org_unit_id: filters.org_unit_id || undefined,
          member_name: memberName || undefined,
          include_synced: false,
          actionable_only: true,
          limit: 200
        })
      ]);
    rows.value = overviewResponse.data.rows;
    cycles.value = cycleResponse.data;
    coverage.value = coverageResponse.data;
  } catch (error: any) {
    ElMessage.error(errorText(error, "续费台账加载失败，请稍后重试"));
  } finally {
    loading.value = false;
  }
}

async function createMissingCycle(row: RenewalCoverageRow | any) {
  if (!row.can_create_cycle || !row.due_month) return;
  if (!writeEnabled.value) {
    ElMessage.warning("当前环境处于只读状态，不能建立续费周期");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `将依据学员管理中的续费月份，为“${row.member_name}”建立${year.value}年度${row.due_month}月续费周期。`,
      "确认建立单个续费周期",
      {
        confirmButtonText: "确认建立",
        cancelButtonText: "取消",
        type: "warning"
      }
    );
  } catch {
    return;
  }
  cycleCreatingMemberId.value = row.member_id;
  try {
    await createRenewalCycleFromMember(row.member_id, year.value);
    ElMessage.success("续费周期已建立并写入审计");
    await load();
  } catch (error: any) {
    ElMessage.error(errorText(error, "建立续费周期失败"));
  } finally {
    cycleCreatingMemberId.value = undefined;
  }
}

function openMemberMaintenance(row: RenewalCoverageRow | any) {
  if (!canManageMembers.value) {
    ElMessage.warning("当前账号没有学员维护权限，请联系学员维护人员");
    return;
  }
  router.push({
    path: "/operations/members",
    query: {
      member_id: String(row.member_id),
      open: "edit",
      return_to: "renewals"
    }
  });
}

function resetFilters() {
  Object.assign(filters, {
    org_unit_id: "",
    due_month: undefined,
    renewal_status: "UNRENEWED",
    member_name: ""
  });
  load();
}

function resetFollowupForm() {
  Object.assign(followupForm, {
    channel: "PHONE",
    summary: "",
    intention: "",
    needs_support: false,
    next_action: "",
    next_followup_at: ""
  });
}

async function loadActionCard(cycleId: number) {
  const response = await getRenewalActionCard(cycleId);
  actionCard.value = response.data;
  if (response.data.action.recommended_channel !== "NONE") {
    followupForm.channel = response.data.action.recommended_channel;
  }
}

async function copyReference(value?: string | null) {
  if (!value) return;
  try {
    await navigator.clipboard.writeText(value);
    ElMessage.success("参考内容已复制，请结合真实沟通情况确认后使用");
  } catch {
    ElMessage.warning("浏览器未允许复制，请手动选择文字复制");
  }
}

async function openCycleDetail(cycle: any) {
  selectedCycle.value = cycle;
  Object.assign(cycleForm, {
    status: cycle.status,
    result: cycle.result || "",
    assigned_user_id: cycle.assigned_user_id
  });
  resetFollowupForm();
  followups.value = [];
  actionCard.value = undefined;
  cycleAssignees.value = [];
  cycleDetailVisible.value = true;
  cycleDetailLoading.value = true;
  try {
    const [actionCardResult, followupResult, assigneeResult] =
      await Promise.allSettled([
        loadActionCard(cycle.id),
        getRenewalFollowups(cycle.id),
        getRenewalAssignees(cycle.org_unit_id)
      ]);
    if (actionCardResult.status === "rejected") {
      ElMessage.error(errorText(actionCardResult.reason, "今日行动卡加载失败"));
    }
    if (followupResult.status === "fulfilled") {
      followups.value = followupResult.value.data;
    } else {
      ElMessage.error(errorText(followupResult.reason, "加载跟进记录失败"));
    }
    if (assigneeResult.status === "fulfilled") {
      cycleAssignees.value = assigneeResult.value.data;
    } else {
      ElMessage.warning(
        errorText(assigneeResult.reason, "责任人列表加载失败，仍可查看跟进记录")
      );
    }
  } finally {
    cycleDetailLoading.value = false;
  }
}

async function saveCycleDetail() {
  if (!selectedCycle.value) return;
  if (!writeEnabled.value) {
    ElMessage.warning("当前环境处于只读状态，修改不会保存");
    return;
  }
  cycleSaving.value = true;
  try {
    await updateRenewalCycle(selectedCycle.value.id, {
      status: cycleForm.status,
      result: cycleForm.result || undefined,
      assigned_user_id: cycleForm.assigned_user_id
    });
    ElMessage.success("续费周期已更新并写入审计");
    const currentCycle = selectedCycle.value;
    const assignee = cycleAssignees.value.find(
      item => item.id === cycleForm.assigned_user_id
    );
    await load();
    selectedCycle.value = {
      ...currentCycle,
      status: cycleForm.status,
      result: cycleForm.result,
      assigned_user_id: cycleForm.assigned_user_id,
      assigned_user_name:
        assignee?.display_name ?? currentCycle.assigned_user_name
    };
    await loadActionCard(currentCycle.id);
  } catch (error: any) {
    ElMessage.error(errorText(error, "续费周期更新失败"));
  } finally {
    cycleSaving.value = false;
  }
}

async function submitFollowup() {
  if (!selectedCycle.value || followupForm.summary.trim().length < 4) {
    ElMessage.warning("请填写至少 4 个字符的跟进摘要");
    return;
  }
  if (!writeEnabled.value) {
    ElMessage.warning("当前环境处于只读状态，跟进记录不会保存");
    return;
  }
  followupSaving.value = true;
  try {
    await createRenewalFollowup(selectedCycle.value.id, {
      channel: followupForm.channel,
      summary: followupForm.summary.trim(),
      intention: followupForm.intention.trim() || undefined,
      needs_support: followupForm.needs_support,
      next_action: followupForm.next_action.trim() || undefined,
      next_followup_at: followupForm.next_followup_at.trim() || undefined
    });
    ElMessage.success("跟进记录已保存并写入审计");
    followups.value = (await getRenewalFollowups(selectedCycle.value.id)).data;
    await loadActionCard(selectedCycle.value.id);
    resetFollowupForm();
  } catch (error: any) {
    ElMessage.error(errorText(error, "跟进记录保存失败"));
  } finally {
    followupSaving.value = false;
  }
}

onMounted(() => {
  loadEnvironment();
  load();
});
</script>

<template>
  <div v-loading="loading" class="renewal-page">
    <section class="hero">
      <div>
        <p class="eyebrow">年度续费运营中心</p>
        <h1>按续费周期，把名单变成可跟进的行动</h1>
        <p class="subtitle">
          学习班级与发展归属分别保留；先锋班、黄埔班按直属班级学习，续费责任仍归六大分中心。
        </p>
      </div>
      <el-select v-model="year" class="year-select" @change="load">
        <el-option :value="2026" label="2026年度" />
        <el-option :value="2027" label="2027年度" />
      </el-select>
    </section>

    <section class="summary-grid">
      <article>
        <span>年度续费对象</span>
        <strong>{{ total }}</strong>
        <small>已进入正式续费周期的学员</small>
      </article>
      <article>
        <span>覆盖分中心</span>
        <strong>{{ centerNames.length }} / 6</strong>
        <small>续费发展归属按六大分中心统计</small>
      </article>
      <article>
        <span>已完成续费</span>
        <strong>{{ renewed }}</strong>
        <small>状态已确认完成</small>
      </article>
      <article class="attention">
        <span>当前需跟进</span>
        <strong>{{ needsAttention }}</strong>
        <small>待联系、待回复或沟通中的学员</small>
      </article>
    </section>

    <section class="content-grid">
      <el-card shadow="never" class="timeline-card">
        <template #header>
          <div class="card-title">
            <div>
              <h2>年度续费节奏</h2>
              <p>按到期月份查看全年工作量与完成情况</p>
            </div>
          </div>
        </template>
        <el-table :data="monthlyRows" stripe>
          <el-table-column prop="month" label="到期月份" min-width="100">
            <template #default="{ row }">{{ row.month }}月</template>
          </el-table-column>
          <el-table-column
            prop="total"
            label="续费对象"
            min-width="100"
            align="right"
          />
          <el-table-column
            prop="renewed"
            label="已续费"
            min-width="100"
            align="right"
          />
          <el-table-column
            prop="attention"
            label="待推进"
            min-width="100"
            align="right"
          />
          <el-table-column label="完成率" min-width="130" align="right">
            <template #default="{ row }">
              {{ row.total ? Math.round((row.renewed / row.total) * 100) : 0 }}%
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>

    <el-alert
      class="source-alert"
      title="学员数据唯一来源：学员管理数据库"
      description="姓名、归属、班级、小组和续费月份均实时读取学员管理。已建立周期继续保留跟进状态；未建立周期或缺少续费月份的学员会在同步检查中明确显示，不再静默缺失。"
      type="success"
      :closable="false"
      show-icon
    />

    <el-card shadow="never" class="coverage-card">
      <template #header>
        <div class="card-title">
          <div>
            <h2>学员主档同步检查</h2>
            <p>
              仅显示当前有可执行操作的同步差异；流失、暂停等历史状态保留在汇总中，不提供新周期操作。
            </p>
          </div>
        </div>
      </template>
      <div class="coverage-summary">
        <span
          >主档匹配 <b>{{ coverage.summary.member_total }}</b></span
        >
        <span
          >在册 <b>{{ coverage.summary.active_member_total }}</b></span
        >
        <span
          >已建周期 <b>{{ coverage.summary.cycle_total }}</b></span
        >
        <span class="ready"
          >可建立 <b>{{ coverage.summary.ready_to_create_count }}</b></span
        >
        <span class="missing"
          >缺续费月份
          <b>{{ coverage.summary.missing_renewal_month_count }}</b></span
        >
        <span
          >流失 <b>{{ coverage.summary.inactive_member_count }}</b></span
        >
        <span
          >暂停 <b>{{ coverage.summary.suspended_member_count }}</b></span
        >
      </div>
      <el-alert
        v-if="coverage.truncated"
        title="可执行同步操作较多，当前仅展示前200条；请按分中心或姓名缩小范围。"
        type="warning"
        :closable="false"
        show-icon
        class="coverage-alert"
      />
      <el-table
        :data="coverage.rows"
        stripe
        max-height="380"
        empty-text="当前范围内暂无可执行的同步操作"
      >
        <el-table-column prop="member_name" label="学员" min-width="120" />
        <el-table-column prop="org_name" label="续费归属" min-width="150" />
        <el-table-column prop="member_class_name" label="班级" min-width="130">
          <template #default="{ row }">{{
            row.member_class_name || "—"
          }}</template>
        </el-table-column>
        <el-table-column label="学员续费月份" min-width="130">
          <template #default="{ row }">{{
            row.renewal_month || "未维护"
          }}</template>
        </el-table-column>
        <el-table-column label="同步状态" min-width="180">
          <template #default="{ row }">
            <el-tag :type="coverageStatusType(row.sync_status)">
              {{ coverageStatusLabel(row.sync_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.can_create_cycle"
              link
              type="primary"
              :disabled="!writeEnabled"
              :loading="cycleCreatingMemberId === row.member_id"
              @click="createMissingCycle(row)"
            >
              建立{{ year }}周期
            </el-button>
            <el-button
              v-else-if="row.sync_status === 'MISSING_RENEWAL_MONTH'"
              link
              type="primary"
              :disabled="!canManageMembers"
              @click="openMemberMaintenance(row)"
            >
              维护月份
            </el-button>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="cycle-card">
      <template #header>
        <div class="card-title">
          <div>
            <h2>续费跟进台账</h2>
            <p>
              默认显示当月至12月的未续费学员，可按分中心、月份、是否续费和姓名查询。
            </p>
          </div>
        </div>
      </template>
      <div class="cycle-filters">
        <el-select
          v-model="filters.org_unit_id"
          clearable
          placeholder="全部分中心"
          class="filter-control"
        >
          <el-option
            v-for="center in centerOptions"
            :key="center.id"
            :label="center.name"
            :value="center.id"
          />
        </el-select>
        <el-select
          v-model="filters.due_month"
          clearable
          placeholder="默认当月至12月"
          class="filter-control"
        >
          <el-option
            v-for="month in monthOptions"
            :key="month"
            :label="`${month}月`"
            :value="month"
          />
        </el-select>
        <el-select
          v-model="filters.renewal_status"
          class="filter-control"
          aria-label="是否续费"
        >
          <el-option label="未续费" value="UNRENEWED" />
          <el-option label="已续费" value="RENEWED" />
          <el-option label="全部状态" value="ALL" />
        </el-select>
        <el-input
          v-model="filters.member_name"
          clearable
          placeholder="按姓名查询"
          class="filter-control name-filter"
          @keyup.enter="load"
        />
        <el-button type="primary" :loading="loading" @click="load"
          >查询</el-button
        >
        <el-button :disabled="loading" @click="resetFilters">重置</el-button>
      </div>
      <el-alert
        v-if="!cycles.length"
        class="cycle-empty-alert"
        title="当前筛选条件下暂无续费周期"
        description="可调整分中心、月份、是否续费或姓名后重新查询。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-table :data="cycles" stripe empty-text="暂无正式续费周期">
        <el-table-column prop="member_name" label="学员" min-width="120" />
        <el-table-column
          prop="org_name"
          label="学员所属分中心"
          min-width="150"
        />
        <el-table-column label="到期月" width="100">
          <template #default="{ row }">{{ row.due_month }}月</template>
        </el-table-column>
        <el-table-column label="自动阶段" min-width="110">
          <template #default="{ row }">
            <el-tag :type="stageTagType(row.stage?.code)" effect="light">
              {{ row.stage?.label || "待判断" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" min-width="130">
          <template #default="{ row }">{{
            cycleStatusLabel(row.status)
          }}</template>
        </el-table-column>
        <el-table-column
          prop="assigned_user_name"
          label="责任人"
          min-width="130"
        >
          <template #default="{ row }">{{
            row.assigned_user_name || "待分配"
          }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="最近更新" min-width="180" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openCycleDetail(row)">
              今日行动
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="cycleDetailVisible"
      :title="`${selectedCycle?.member_name ?? '续费周期'} · 今日行动`"
      width="980px"
    >
      <div v-loading="cycleDetailLoading" class="cycle-detail">
        <el-alert
          v-if="!writeEnabled"
          title="当前为只读状态"
          description="可以查看续费周期和历史跟进，但修改与新增保存已被禁用。"
          type="warning"
          :closable="false"
          show-icon
          class="readonly-alert"
        />
        <section v-if="actionCard" class="action-card">
          <div class="action-card-head">
            <div>
              <div class="action-card-tags">
                <el-tag
                  :type="stageTagType(actionCard.stage.code)"
                  size="large"
                >
                  {{ actionCard.stage.label }}
                </el-tag>
                <el-tag effect="plain">
                  {{ actionCard.cycle.renewal_year }}年{{
                    actionCard.cycle.due_month
                  }}月续费
                </el-tag>
                <el-tag
                  v-if="actionCard.current_context.needs_support"
                  type="warning"
                >
                  需要协助
                </el-tag>
              </div>
              <h3>{{ actionCard.member.name }} · 本次目标</h3>
              <p>{{ actionCard.action.goal }}</p>
            </div>
            <div class="member-facts">
              <span
                >责任人：{{
                  actionCard.cycle.assigned_user_name || "待分配"
                }}</span
              >
              <span
                >同行：{{
                  actionCard.member.membership_years ?? "待维护"
                }}年</span
              >
              <span>班级：{{ actionCard.member.class_name || "待维护" }}</span>
              <span>小组：{{ actionCard.member.group_name || "待维护" }}</span>
            </div>
          </div>

          <div class="today-advice">
            <span>今日建议</span>
            <strong>
              {{ channelLabel(actionCard.action.recommended_channel) }} ·
              {{ actionCard.action.goal }}
            </strong>
            <p>{{ actionCard.action.recommendation_reason }}</p>
          </div>

          <div v-if="actionCard.latest_followup" class="continuity-card">
            <strong>最近一次续费关爱</strong>
            <span>
              {{ actionCard.latest_followup.followed_at }} ·
              {{ channelLabel(actionCard.latest_followup.channel) }}
            </span>
            <p>{{ actionCard.latest_followup.summary || "未记录摘要" }}</p>
            <small>
              意愿：{{
                actionCard.current_context.intention || "暂无明确记录"
              }}； 下一步：{{
                actionCard.current_context.next_action || "待确认"
              }}； 下次联系：{{
                actionCard.current_context.next_followup_at || "待安排"
              }}
            </small>
          </div>

          <div class="memory-block">
            <strong>经验证的共同经历</strong>
            <div v-if="actionCard.verified_memories.length" class="memory-list">
              <el-tag
                v-for="memory in actionCard.verified_memories"
                :key="memory.id"
                type="success"
                effect="plain"
              >
                {{ memory.year }}年{{ memory.month }}月 · {{ memory.title }}
              </el-tag>
            </div>
            <p v-else>
              暂无可核验的本人学习或活动经历，本次建议已降级为基础关爱，不会编造共同经历。
            </p>
          </div>

          <div class="reference-grid">
            <article>
              <div class="reference-title">
                <strong>微信参考</strong>
                <el-button
                  v-if="actionCard.action.wechat_reference"
                  link
                  type="primary"
                  @click="copyReference(actionCard.action.wechat_reference)"
                >
                  复制
                </el-button>
              </div>
              <p>
                {{
                  actionCard.action.wechat_reference ||
                  "当前阶段无需发送续费关爱信息。"
                }}
              </p>
            </article>
            <article>
              <div class="reference-title">
                <strong>电话开场参考</strong>
                <el-button
                  v-if="actionCard.action.phone_opening_reference"
                  link
                  type="primary"
                  @click="
                    copyReference(actionCard.action.phone_opening_reference)
                  "
                >
                  复制
                </el-button>
              </div>
              <p>
                {{
                  actionCard.action.phone_opening_reference ||
                  "当前阶段无需发起续费电话。"
                }}
              </p>
            </article>
          </div>

          <div class="guidance-grid">
            <article>
              <strong>建议询问</strong>
              <ol v-if="actionCard.action.questions.length">
                <li
                  v-for="question in actionCard.action.questions"
                  :key="question"
                >
                  {{ question }}
                </li>
              </ol>
              <p v-else>当前周期已闭环，无需继续询问续费事项。</p>
            </article>
            <article class="do-not-card">
              <strong>本次不要做</strong>
              <ul>
                <li v-for="item in actionCard.action.do_not" :key="item">
                  {{ item }}
                </li>
              </ul>
            </article>
          </div>
          <p class="action-policy">{{ actionCard.policy }}</p>
        </section>

        <el-divider content-position="left">周期状态与责任人</el-divider>
        <el-descriptions v-if="selectedCycle" :column="3" border>
          <el-descriptions-item label="学员所属分中心">{{
            selectedCycle.org_name
          }}</el-descriptions-item>
          <el-descriptions-item label="班级">{{
            selectedCycle.member_class_name || "—"
          }}</el-descriptions-item>
          <el-descriptions-item label="小组">{{
            selectedCycle.member_group_name || "—"
          }}</el-descriptions-item>
          <el-descriptions-item label="到期月份"
            >{{ selectedCycle.due_month }}月</el-descriptions-item
          >
          <el-descriptions-item label="学员编号">{{
            selectedCycle.member_code
          }}</el-descriptions-item>
        </el-descriptions>
        <el-form :model="cycleForm" inline class="cycle-edit-form">
          <el-form-item label="状态">
            <el-select
              v-model="cycleForm.status"
              :disabled="!writeEnabled"
              style="width: 170px"
            >
              <el-option
                v-for="item in cycleStatusOptions"
                :key="item[0]"
                :label="item[1]"
                :value="item[0]"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="结果">
            <el-input
              v-model="cycleForm.result"
              :disabled="!writeEnabled"
              maxlength="64"
              placeholder="简要记录结果"
            />
          </el-form-item>
          <el-form-item label="责任人">
            <el-select
              v-model="cycleForm.assigned_user_id"
              :disabled="!writeEnabled"
              placeholder="选择续费归属范围内的责任人"
              style="width: 220px"
            >
              <el-option
                v-for="user in cycleAssignees"
                :key="user.id"
                :label="user.display_name"
                :value="user.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              :loading="cycleSaving"
              :disabled="!writeEnabled"
              @click="saveCycleDetail"
            >
              保存周期状态
            </el-button>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">新增跟进</el-divider>
        <el-form
          :model="followupForm"
          label-position="top"
          class="followup-form"
        >
          <el-form-item label="联系渠道">
            <el-select
              v-model="followupForm.channel"
              :disabled="!writeEnabled"
              style="width: 150px"
            >
              <el-option label="电话" value="PHONE" />
              <el-option label="微信" value="WECHAT" />
              <el-option label="面谈" value="MEETING" />
              <el-option label="走访" value="VISIT" />
              <el-option label="其他" value="OTHER" />
            </el-select>
          </el-form-item>
          <el-form-item label="跟进摘要" required>
            <el-input
              v-model="followupForm.summary"
              :disabled="!writeEnabled"
              type="textarea"
              :rows="2"
              maxlength="4000"
            />
          </el-form-item>
          <el-form-item label="意愿">
            <el-input
              v-model="followupForm.intention"
              :disabled="!writeEnabled"
              maxlength="64"
            />
          </el-form-item>
          <el-form-item label="下一步行动">
            <el-input
              v-model="followupForm.next_action"
              :disabled="!writeEnabled"
              maxlength="4000"
            />
          </el-form-item>
          <el-form-item label="下次跟进时间">
            <el-input
              v-model="followupForm.next_followup_at"
              :disabled="!writeEnabled"
              placeholder="YYYY-MM-DD HH:mm"
            />
          </el-form-item>
          <el-form-item label="需要协助">
            <el-switch
              v-model="followupForm.needs_support"
              :disabled="!writeEnabled"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="success"
              :loading="followupSaving"
              :disabled="!writeEnabled"
              @click="submitFollowup"
            >
              保存跟进记录
            </el-button>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">历史跟进</el-divider>
        <el-table :data="followups" stripe empty-text="暂无跟进记录">
          <el-table-column prop="followed_at" label="时间" min-width="160" />
          <el-table-column label="渠道" width="90">
            <template #default="{ row }">{{
              channelLabel(row.channel)
            }}</template>
          </el-table-column>
          <el-table-column
            prop="summary"
            label="摘要"
            min-width="240"
            show-overflow-tooltip
          />
          <el-table-column prop="intention" label="意愿" min-width="120" />
          <el-table-column
            prop="next_action"
            label="下一步"
            min-width="180"
            show-overflow-tooltip
          />
          <el-table-column label="协助" width="80">
            <template #default="{ row }">{{
              row.needs_support ? "需要" : "—"
            }}</template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.renewal-page {
  display: grid;
  gap: 18px;
  padding: 20px;
  color: #163d32;
}
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 30px 34px;
  color: #f7fffc;
  background:
    radial-gradient(circle at 85% 15%, rgb(91 183 142 / 28%), transparent 34%),
    linear-gradient(125deg, #0e4435, #217153);
  border-radius: 20px;
}
.eyebrow {
  margin: 0 0 10px;
  color: #9ee2c6;
  letter-spacing: 0.18em;
}
.hero h1 {
  margin: 0 0 12px;
  font-size: clamp(26px, 3vw, 38px);
  line-height: 1.25;
}
.subtitle {
  max-width: 760px;
  margin: 0;
  color: #d3eee3;
  line-height: 1.8;
}
.year-select {
  width: 140px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.summary-grid article {
  display: grid;
  gap: 8px;
  padding: 20px 22px;
  background: var(--el-bg-color);
  border: 1px solid #dce9e3;
  border-radius: 16px;
}
.summary-grid span {
  color: #6d8179;
}
.summary-grid strong {
  font-size: 30px;
  color: #123f32;
}
.summary-grid small {
  color: #879991;
}
.summary-grid .attention {
  background: #fff9ee;
  border-color: #f1d9ad;
}
.content-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}
.timeline-card {
  border-color: #dce9e3;
  border-radius: 16px;
}
.source-alert {
  border: 1px solid #cce9dc;
}
.coverage-card {
  border-color: #dce9e3;
  border-radius: 16px;
}
.coverage-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}
.coverage-summary span {
  padding: 8px 12px;
  color: #657a71;
  background: #f2f7f5;
  border-radius: 10px;
}
.coverage-summary span.ready {
  color: #17624b;
  background: #e8f7f0;
}
.coverage-summary span.missing {
  color: #a15b16;
  background: #fff4e5;
}
.coverage-summary b {
  margin-left: 4px;
  color: inherit;
}
.coverage-alert {
  margin-bottom: 14px;
}
.card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.card-title h2 {
  margin: 0 0 6px;
  font-size: 20px;
}
.card-title p {
  margin: 0;
  color: #82958d;
}
.cycle-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  padding: 14px;
  background: #f5f9f7;
  border: 1px solid #e2ece7;
  border-radius: 12px;
}
.filter-control {
  width: 170px;
}
.name-filter {
  width: 200px;
}
.readonly-alert {
  margin-bottom: 16px;
}
.action-card {
  display: grid;
  gap: 16px;
  padding: 20px;
  background: #f7fbf9;
  border: 1px solid #d8ebe2;
  border-radius: 16px;
}
.action-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
}
.action-card-tags,
.memory-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.action-card h3 {
  margin: 14px 0 6px;
  color: #153f33;
  font-size: 20px;
}
.action-card p {
  margin: 0;
  line-height: 1.75;
}
.member-facts {
  display: grid;
  min-width: 245px;
  gap: 7px;
  color: #657a71;
  font-size: 13px;
}
.today-advice {
  padding: 18px 20px;
  color: #f4fff9;
  background: linear-gradient(120deg, #17624b, #2c8a68);
  border-radius: 14px;
}
.today-advice span {
  display: block;
  margin-bottom: 6px;
  color: #bcebd8;
  font-size: 13px;
}
.today-advice strong {
  display: block;
  margin-bottom: 7px;
  font-size: 19px;
}
.today-advice p {
  color: #e0f4eb;
}
.continuity-card,
.memory-block {
  display: grid;
  gap: 8px;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid #e0ebe6;
  border-radius: 12px;
}
.continuity-card span,
.continuity-card small,
.memory-block p {
  color: #70847b;
}
.reference-grid,
.guidance-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.reference-grid article,
.guidance-grid article {
  padding: 16px;
  background: #fff;
  border: 1px solid #dfeae5;
  border-radius: 12px;
}
.reference-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 9px;
}
.guidance-grid ol,
.guidance-grid ul {
  display: grid;
  gap: 7px;
  margin: 10px 0 0;
  padding-left: 22px;
  color: #546a61;
  line-height: 1.65;
}
.do-not-card {
  background: #fffaf1 !important;
  border-color: #f0dfbc !important;
}
.action-policy {
  color: #81948c;
  font-size: 12px;
}
.upload-list {
  display: grid;
  gap: 12px;
}
.upload-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px;
  background: #f5f9f7;
  border: 1px solid #e2ece7;
  border-radius: 12px;
}
.upload-row div {
  display: grid;
  min-width: 0;
  gap: 5px;
}
.upload-row span {
  overflow: hidden;
  color: #82958d;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-button {
  width: 100%;
  margin-top: 16px;
}
.import-note {
  margin: 14px 0 0;
  color: #82958d;
  font-size: 13px;
  line-height: 1.7;
}
.result-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.result-summary span {
  padding: 9px 13px;
  color: #657a71;
  background: #f2f7f5;
  border-radius: 10px;
}
.result-summary b {
  margin-left: 5px;
  color: #174d3c;
  font-size: 17px;
}
@media (max-width: 1100px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .content-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 680px) {
  .renewal-page {
    padding: 12px;
  }
  .hero {
    align-items: stretch;
    flex-direction: column;
    padding: 24px;
  }
  .summary-grid {
    grid-template-columns: 1fr;
  }
  .action-card-head {
    flex-direction: column;
  }
  .member-facts {
    min-width: 0;
  }
  .reference-grid,
  .guidance-grid {
    grid-template-columns: 1fr;
  }
}
</style>
