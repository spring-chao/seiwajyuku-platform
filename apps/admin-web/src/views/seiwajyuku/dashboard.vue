<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  getAnnualPlans,
  getMpDashboard,
  getTargetVariances,
  type AnnualPlan,
  type DashboardItem
} from "@/api/seiwajyuku";

defineOptions({ name: "MpDashboard" });

const loading = ref(false);
const plans = ref<AnnualPlan[]>([]);
const planId = ref<number>();
const month = ref(Math.min(new Date().getMonth() + 1, 12));
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
const selectedVariance = computed(() =>
  variances.value.find(item => item.metric_key === selectedMetricKey.value)
);

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
  if (!planId.value) return;
  loading.value = true;
  try {
    const [dashboard, variance] = await Promise.all([
      getMpDashboard({ plan_id: planId.value, month: month.value }),
      getTargetVariances(planId.value)
    ]);
    items.value = dashboard.data.items;
    variances.value = variance.data;
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
</script>

<template>
  <div class="page-shell" v-loading="loading">
    <section class="hero">
      <div>
        <p class="eyebrow">组织目标 · 数据下钻 · 行动闭环</p>
        <h1>年度 MP 运营驾驶舱</h1>
        <p class="subtitle">
          用统一口径看清六个区域分中心的目标、预定与实绩，并保留每一个差额和空值的真实含义。
        </p>
      </div>
      <div class="filters">
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
        <el-select v-model="planId" aria-label="年度方案" @change="load">
          <el-option
            v-for="plan in plans"
            :key="plan.id"
            :label="`${plan.year}年度 · V${plan.version}`"
            :value="plan.id"
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
}
@media (max-width: 560px) {
  .page-shell {
    padding: 14px;
  }
  .summary-grid {
    grid-template-columns: 1fr;
  }
  .filters {
    display: grid;
  }
  .filters .el-select {
    width: 100%;
  }
}
</style>
