<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs from "dayjs";
import {
  getAnnualPlans,
  getMpDashboard,
  getOperationsSnapshot,
  getTargetVariances,
  type AnnualPlan,
  type DashboardItem,
  type OperationsSnapshot
} from "@/api/seiwajyuku";

defineOptions({ name: "MpDashboard" });

const loading = ref(false);
const plans = ref<AnnualPlan[]>([]);
const planId = ref<number>();
const year = ref(new Date().getFullYear());
const month = ref(Math.min(new Date().getMonth() + 1, 12));
const operations = ref<OperationsSnapshot>();
const items = ref<DashboardItem[]>([]);
const selectedMetricKey = ref("active_member_count");
const variances = ref<
  { metric_key: string; difference: number; aggregation: string }[]
>([]);

const currentPlan = computed(() =>
  plans.value.find(item => item.id === planId.value)
);
const centers = computed(() => [
  ...new Set(items.value.map(item => item.org_name))
]);
const metrics = computed(() => {
  const result = new Map<
    string,
    { key: string; name: string; unit: string }
  >();
  items.value.forEach(item => {
    result.set(item.metric_key, {
      key: item.metric_key,
      name: item.metric_name,
      unit: item.unit
    });
  });
  return [...result.values()];
});
const selectedMetric = computed(() =>
  metrics.value.find(item => item.key === selectedMetricKey.value)
);
const selectedItems = computed(() =>
  items.value.filter(item => item.metric_key === selectedMetricKey.value)
);
const toNumber = (value: number | string | null | undefined) => {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};
const achievementValues = computed(() =>
  selectedItems.value
    .map(item => toNumber(item.forecast_achievement))
    .filter((value): value is number => value !== null)
);
const averageAchievement = computed(() => {
  const values = achievementValues.value;
  return values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;
});
const actualCount = computed(
  () =>
    selectedItems.value.filter(item => toNumber(item.actual?.value) !== null)
      .length
);
const reachedForecastCount = computed(
  () =>
    selectedItems.value.filter(item => {
      const achievement = toNumber(item.forecast_achievement);
      return achievement !== null && achievement >= 1;
    }).length
);
const yearOptions = computed(() => {
  const currentYear = new Date().getFullYear();
  return Array.from(
    new Set([
      currentYear - 2,
      currentYear - 1,
      currentYear,
      currentYear + 1,
      ...plans.value.map(plan => plan.year)
    ])
  ).sort((a, b) => b - a);
});
const selectedVariance = computed(() =>
  variances.value.find(item => item.metric_key === selectedMetricKey.value)
);
const operationsCards = computed(() => {
  const summary = operations.value?.summary;
  if (!summary) return [];
  return [
    {
      label: "本月续费",
      value: operations.value?.data_quality.renewal_source_authorized
        ? summary.renewed_member_count
        : null,
      unit: "位",
      note: operations.value?.data_quality.renewal_source_authorized
        ? "本月状态转为已续费"
        : "当前账号无续费查看权限"
    },
    {
      label: "本月新增",
      value: summary.new_member_count,
      unit: "位",
      note: "按学员主档入塾日期"
    },
    {
      label:
        operations.value?.scope_label === "苏州塾"
          ? "苏州塾总在册"
          : "当前在册",
      value: summary.active_member_count,
      unit: "位",
      note: operations.value?.scope_label || "授权范围"
    },
    {
      label: "本月生日",
      value: summary.birthday_member_count,
      unit: "位",
      note: "仅统计当前在册学长"
    },
    {
      label: "本月班会",
      value: operations.value?.data_quality.attendance_schedule_source_ready
        ? summary.class_meeting_count
        : null,
      unit: "次",
      note: operations.value?.data_quality.attendance_schedule_source_ready
        ? "按活动组计次"
        : "班会排期数据尚未接入"
    },
    {
      label: "本月课程",
      value: operations.value?.data_quality.course_schedule_source_ready
        ? summary.course_count
        : null,
      unit: "次",
      note: operations.value?.data_quality.course_schedule_source_ready
        ? "按课程活动组计次"
        : "课程排期数据尚未接入"
    },
    {
      label: "本月其他活动",
      value: operations.value?.data_quality.attendance_schedule_source_ready
        ? summary.activity_count
        : null,
      unit: "次",
      note: operations.value?.data_quality.attendance_schedule_source_ready
        ? "不含班会与课程"
        : "活动排期数据尚未接入"
    }
  ];
});
const scheduleRows = computed(() => [
  ...(operations.value?.class_meeting_schedule || []).map(item => ({
    ...item,
    category: "班会",
    sequence: item.year_sequence ? `本年第 ${item.year_sequence} 次` : "待排期"
  })),
  ...(operations.value?.courses || []).map(item => ({
    ...item,
    category: "课程",
    sequence: "—"
  })),
  ...(operations.value?.activities || []).map(item => ({
    ...item,
    category: "活动",
    sequence: "—"
  }))
]);

const formatValue = (
  value: number | string | null | undefined,
  unit?: string
) => {
  const numeric = toNumber(value);
  if (numeric === null) return "—";
  if (unit === "PERCENT") return `${(numeric * 100).toFixed(1)}%`;
  if (unit === "PERSON") return `${Math.round(numeric)} 人`;
  if (unit === "SCORE") return `${numeric.toFixed(1)} 分`;
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2);
};
const formatAchievement = (value: number | string | null | undefined) => {
  const numeric = toNumber(value);
  return numeric === null ? "—" : `${(numeric * 100).toFixed(1)}%`;
};
const annualAchievement = (row: any) => {
  const actual = toNumber(row.actual?.value);
  const annualTarget = toNumber(row.annual_target);
  if (actual === null || annualTarget === null || annualTarget === 0) {
    return null;
  }
  return actual / annualTarget;
};
const unitLabel = (unit?: string) =>
  ({ PERCENT: "百分比", PERSON: "人数", SCORE: "分数" })[unit ?? ""] ??
  "数值";

async function load() {
  loading.value = true;
  try {
    const [snapshot, dashboard, variance] = await Promise.all([
      getOperationsSnapshot({ year: year.value, month: month.value }),
      planId.value
        ? getMpDashboard({ plan_id: planId.value, month: month.value })
        : Promise.resolve(null),
      planId.value ? getTargetVariances(planId.value) : Promise.resolve(null)
    ]);
    operations.value = snapshot.data;
    items.value = dashboard?.data.items || [];
    variances.value = variance?.data || [];
    if (
      !items.value.some(item => item.metric_key === selectedMetricKey.value)
    ) {
      selectedMetricKey.value = items.value[0]?.metric_key ?? "";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  const response = await getAnnualPlans();
  plans.value = response.data;
  planId.value = plans.value[0]?.id;
  await load();
});

function changePlan() {
  load();
}
</script>

<template>
  <div class="page-shell" v-loading="loading">
    <section class="hero">
      <div>
        <p class="eyebrow">月度实况 · 组织盘面 · 服务节奏</p>
        <h1>运营驾驶舱</h1>
        <p class="subtitle">
          先看当月续费、新增、在册、生日与活动排期，再下钻年度 MP 目标差距；所有数字来自统一平台数据库。
        </p>
      </div>
      <div class="filters">
        <el-select v-model="year" aria-label="运营年份" @change="load">
          <el-option
            v-for="option in yearOptions"
            :key="option"
            :label="`${option} 年`"
            :value="option"
          />
        </el-select>
        <el-select v-model="month" aria-label="月份" @change="load">
          <el-option
            v-for="value in 12"
            :key="value"
            :label="`${value}月`"
            :value="value"
          />
        </el-select>
      </div>
    </section>

    <section class="section-heading">
      <div>
        <p class="eyebrow dark">MONTHLY OPERATIONS</p>
        <h2>{{ year }} 年 {{ month }} 月运营实况</h2>
      </div>
      <span>在册为当前快照；新增、续费和排期按所选月份统计</span>
    </section>

    <section class="operations-grid">
      <article
        v-for="card in operationsCards"
        :key="card.label"
        class="operations-card"
        :class="{ unavailable: card.value === null }"
      >
        <span>{{ card.label }}</span>
        <strong v-if="card.value !== null">{{ card.value }}<small>{{ card.unit }}</small></strong>
        <strong v-else class="not-ready">未接入</strong>
        <p>{{ card.note }}</p>
      </article>
    </section>

    <el-alert
      v-if="operations?.data_quality.missing_join_date_count"
      :title="`${operations.data_quality.missing_join_date_count} 位在册学长缺少入塾日期，未计入本月新增`"
      type="warning"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <el-alert
      v-if="operations?.data_quality.unscheduled_class_count"
      :title="`${operations.data_quality.unscheduled_class_count} 个班级本月尚未接入班会排期`"
      description="驾驶舱会保留这些班级并显示“待排期”，不会把缺少排期误报为已召开 0 次。"
      type="info"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <el-alert
      v-if="operations?.data_quality.unlinked_class_meeting_count"
      :title="`${operations.data_quality.unlinked_class_meeting_count} 场班会尚未关联正式班级`"
      description="这些班会计入本月总次数并保留在日历中，但不会据活动名称自动猜测班级。"
      type="warning"
      :closable="false"
      show-icon
      class="data-alert"
    />

    <section class="operations-panels">
      <article class="content-card">
        <div class="section-title">
          <h2>各分中心当前在册</h2>
          <p>按学员管理主档所属分中心统计；直属学习班保留独立口径，不并入六个分中心。</p>
        </div>
        <div class="center-list">
          <div v-for="center in operations?.centers || []" :key="center.id">
            <span>{{ center.name }}</span>
            <strong>{{ center.active_member_count }} 人</strong>
          </div>
        </div>
      </article>

      <article class="content-card">
        <div class="section-title">
          <h2>本月生日关怀</h2>
          <p>仅展示生日月日，不展示出生年份及其他敏感资料。</p>
        </div>
        <el-table
          :data="operations?.birthday_members || []"
          size="small"
          max-height="300"
          empty-text="本月暂无在册学长生日"
        >
          <el-table-column prop="birthday" label="日期" width="86" />
          <el-table-column prop="name" label="学长" min-width="100" />
          <el-table-column prop="org_name" label="分中心" min-width="130" />
        </el-table>
      </article>
    </section>

    <section class="content-card schedule-card">
      <div class="section-title">
        <h2>本月服务与活动日历</h2>
        <p>一个活动组只计一次；班会序号按本年度同一班级已排班会顺序计算。</p>
      </div>
      <el-table :data="scheduleRows" stripe empty-text="本月暂无已接入的班会、课程或活动排期">
        <el-table-column label="日期" width="105">
          <template #default="{ row }">
            {{ row.event_date ? dayjs(row.event_date).format("MM 月 DD 日") : "待排期" }}
          </template>
        </el-table-column>
        <el-table-column prop="category" label="类型" width="80" />
        <el-table-column label="班级/组织" min-width="150">
          <template #default="{ row }">{{ row.class_name || row.org_name }}</template>
        </el-table-column>
        <el-table-column prop="sequence" label="班会次序" width="125" />
        <el-table-column prop="title" label="事项" min-width="220" />
      </el-table>
    </section>

    <section class="section-heading mp-heading">
      <div>
        <p class="eyebrow dark">ANNUAL MP</p>
        <h2>年度 MP 目标追踪</h2>
      </div>
      <div class="mp-filters">
        <el-select
          v-model="planId"
          aria-label="年度方案"
          placeholder="选择年度方案"
          @change="changePlan"
        >
          <el-option
            v-for="plan in plans"
            :key="plan.id"
            :label="`${plan.year}年度 · V${plan.version}`"
            :value="plan.id"
          />
        </el-select>
        <el-select
          v-model="selectedMetricKey"
          aria-label="指标"
          placeholder="选择指标"
        >
          <el-option
            v-for="metric in metrics"
            :key="metric.key"
            :label="metric.name"
            :value="metric.key"
          />
        </el-select>
      </div>
    </section>

    <el-alert
      v-if="currentPlan && !currentPlan.write_enabled"
      title="当前为只读核对阶段"
      description="年度方案尚未取得业务批准，所有导入值可查看、可核对，但不能写入。"
      type="warning"
      :closable="false"
      show-icon
    />

    <section class="summary-grid">
      <article class="summary-card">
        <span>当前查看指标</span>
        <strong class="metric-name">{{
          selectedMetric?.name ?? "请选择指标"
        }}</strong>
        <small>{{ unitLabel(selectedMetric?.unit) }}口径，六分中心横向比较</small>
      </article>
      <article class="summary-card">
        <span>已填实绩中心</span>
        <strong>{{ actualCount }} / {{ centers.length }}</strong>
        <small>本月已有实绩的分中心数量</small>
      </article>
      <article class="summary-card">
        <span>平均预定达成</span>
        <strong>{{
          averageAchievement === null
            ? "—"
            : `${(averageAchievement * 100).toFixed(1)}%`
        }}</strong>
        <small>仅计算当前指标：实绩 ÷ 预定</small>
      </article>
      <article class="summary-card accent">
        <span>达到或超过预定</span>
        <strong>{{ reachedForecastCount }} 个</strong>
        <small>当前指标达成率不低于 100%</small>
      </article>
    </section>

    <el-alert
      v-if="selectedVariance && selectedVariance.difference !== 0"
      :title="`${selectedMetric?.name ?? '当前指标'}存在年度目标分解差额`"
      :description="`苏州塾总目标与六分中心${selectedVariance.aggregation === 'SUM' ? '合计' : '平均'}相差 ${formatValue(selectedVariance.difference, selectedMetric?.unit)}，该差额已保留，待业务说明。`"
      type="warning"
      :closable="false"
      show-icon
      class="variance-alert"
    />

    <section class="content-card">
      <div class="section-title">
        <h2>六分中心 · {{ selectedMetric?.name ?? "指标明细" }}</h2>
        <p>
          年目标是全年方向；月MP是本月基准；预定是本月预计完成值；实绩是实际完成值。
        </p>
      </div>
      <div class="metric-guide">
        <span><b>月MP</b>：月度目标基准</span>
        <span><b>预定</b>：预计本月完成</span>
        <span><b>实绩</b>：本月实际完成</span>
        <span><b>预定达成率</b>：实绩 ÷ 预定</span>
        <span><b>年度目标达成率</b>：当月实绩 ÷ 年度目标</span>
      </div>
      <el-table :data="selectedItems" stripe empty-text="当前月份暂无该指标数据">
        <el-table-column prop="org_name" label="区域分中心" min-width="150" />
        <el-table-column label="年度目标" min-width="125" align="right">
          <template #default="{ row }">
            {{ formatValue(row.annual_target, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="月MP" min-width="115" align="right">
          <template #default="{ row }">
            {{ formatValue(row.mp?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="预定" min-width="115" align="right">
          <template #default="{ row }">
            {{ formatValue(row.forecast?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="实绩" min-width="115" align="right">
          <template #default="{ row }">
            {{ formatValue(row.actual?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="预定达成率" min-width="135" align="right">
          <template #default="{ row }">
            <el-tag
              v-if="toNumber(row.forecast_achievement) !== null"
              :type="
                toNumber(row.forecast_achievement)! >= 1
                  ? 'success'
                  : 'warning'
              "
            >
              {{ formatAchievement(row.forecast_achievement) }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column
          label="年度目标达成率"
          min-width="155"
          align="right"
        >
          <template #default="{ row }">
            <el-tag
              v-if="annualAchievement(row) !== null"
              :type="annualAchievement(row)! >= 1 ? 'success' : 'info'"
            >
              {{ formatAchievement(annualAchievement(row)) }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.page-shell {
  min-height: 100%;
  padding: 24px;
  background: #f3f7f5;
}
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 32px;
  margin-bottom: 18px;
  color: #f7fffb;
  background:
    radial-gradient(circle at 85% 15%, rgb(160 218 188 / 22%), transparent 34%),
    linear-gradient(130deg, #123f32, #1f654f);
  border-radius: 18px;
  box-shadow: 0 18px 45px rgb(18 63 50 / 16%);
}
.eyebrow {
  margin: 0 0 8px;
  color: #bce3d2;
  font-size: 13px;
  letter-spacing: 0.14em;
}
h1 {
  margin: 0;
  font-size: clamp(28px, 3vw, 42px);
  line-height: 1.15;
}
.subtitle {
  max-width: 720px;
  margin: 12px 0 0;
  color: #d9ede5;
  line-height: 1.7;
}
.filters {
  display: flex;
  flex: 0 0 auto;
  gap: 10px;
}
.filters .el-select {
  width: 145px;
}
.filters .el-select:first-child {
  width: 210px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin: 18px 0;
}
.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 26px 2px 14px;
}
.section-heading h2 {
  margin: 0;
  color: #173f33;
  font-size: 24px;
}
.section-heading > span {
  color: #82958d;
  font-size: 13px;
}
.eyebrow.dark {
  margin-bottom: 4px;
  color: #3f8067;
}
.operations-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 12px;
}
.operations-card {
  min-height: 132px;
  padding: 18px;
  background: #fff;
  border: 1px solid #dfeae5;
  border-radius: 14px;
  box-shadow: 0 8px 24px rgb(31 78 61 / 6%);
}
.operations-card > span {
  color: #60756c;
  font-size: 13px;
}
.operations-card strong {
  display: block;
  margin: 9px 0 5px;
  color: #173f33;
  font-size: 32px;
}
.operations-card strong small {
  margin-left: 4px;
  font-size: 14px;
}
.operations-card p {
  margin: 0;
  color: #8b9d95;
  font-size: 12px;
  line-height: 1.5;
}
.operations-card.unavailable {
  background: #f7f8f7;
  border-style: dashed;
}
.operations-card strong.not-ready {
  color: #9aa8a2;
  font-size: 21px;
}
.data-alert {
  margin-top: 14px;
}
.operations-panels {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 16px;
  margin-top: 16px;
}
.center-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.center-list div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 14px;
  background: #f4f8f6;
  border-radius: 10px;
}
.center-list span {
  color: #60756c;
}
.center-list strong {
  color: #1e604a;
}
.schedule-card {
  margin-top: 16px;
}
.mp-heading {
  padding-top: 8px;
  border-top: 1px solid #dce8e2;
}
.mp-filters {
  display: flex;
  gap: 10px;
}
.mp-filters .el-select {
  width: 230px;
}
.summary-card,
.content-card {
  background: #fff;
  border: 1px solid #e1ebe6;
  border-radius: 14px;
  box-shadow: 0 8px 24px rgb(31 78 61 / 6%);
}
.summary-card {
  display: flex;
  flex-direction: column;
  min-height: 132px;
  padding: 20px;
}
.summary-card span {
  color: #60756c;
  font-size: 13px;
}
.summary-card strong {
  margin: 8px 0 4px;
  color: #173f33;
  font-size: 32px;
}
.summary-card strong.metric-name {
  font-size: 23px;
  line-height: 1.35;
}
.summary-card small,
.section-title p {
  color: #82958d;
}
.summary-card.accent {
  background: #eff8f3;
  border-color: #c8e5d6;
}
.variance-alert {
  margin-bottom: 18px;
}
.content-card {
  padding: 22px;
}
.section-title h2 {
  margin: 0;
  color: #173f33;
}
.section-title p {
  margin: 6px 0 18px;
}
.metric-guide {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
  padding: 12px 14px;
  margin-bottom: 16px;
  color: #60756c;
  font-size: 13px;
  background: #f5f9f7;
  border-radius: 10px;
}
.metric-guide b {
  color: #245f4b;
}
@media (max-width: 900px) {
  .hero {
    align-items: stretch;
    flex-direction: column;
  }
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .operations-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .operations-panels {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 560px) {
  .page-shell {
    padding: 14px;
  }
  .summary-grid {
    grid-template-columns: 1fr;
  }
  .operations-grid,
  .center-list {
    grid-template-columns: 1fr;
  }
  .section-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .mp-filters {
    display: grid;
  }
  .mp-filters .el-select {
    width: 100%;
  }
  .filters {
    display: grid;
  }
  .filters .el-select {
    width: 100%;
  }
}
</style>
