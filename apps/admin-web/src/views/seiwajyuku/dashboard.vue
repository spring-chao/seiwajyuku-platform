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
const variances = ref<{ difference: number }[]>([]);

const currentPlan = computed(() =>
  plans.value.find(item => item.id === planId.value)
);
const centers = computed(() => [
  ...new Set(items.value.map(item => item.org_name))
]);
const activeSummary = computed(() =>
  items.value.filter(item => item.metric_key === "active_member_count")
);
const averageAchievement = computed(() => {
  const values = items.value
    .map(item => item.forecast_achievement)
    .filter((value): value is number => value !== null);
  return values.length
    ? values.reduce((sum, value) => sum + value, 0) / values.length
    : null;
});

const formatValue = (value: number | null | undefined, unit?: string) => {
  if (value === null || value === undefined) return "—";
  if (unit === "PERCENT") return `${(value * 100).toFixed(1)}%`;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
};

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
        <span>可见区域</span>
        <strong>{{ centers.length }}</strong>
        <small>权限范围内的区域分中心</small>
      </article>
      <article class="summary-card">
        <span>当月指标记录</span>
        <strong>{{ items.length }}</strong>
        <small>指标与组织交叉记录</small>
      </article>
      <article class="summary-card">
        <span>平均预定达成</span>
        <strong>{{
          averageAchievement === null
            ? "—"
            : `${(averageAchievement * 100).toFixed(1)}%`
        }}</strong>
        <small>无数据或分母为 0 时不计入</small>
      </article>
      <article class="summary-card accent">
        <span>待说明差额</span>
        <strong>{{ variances.filter(item => item.difference !== 0).length }}</strong>
        <small>总目标与分解聚合不一致</small>
      </article>
    </section>

    <section class="content-card">
      <div class="section-title">
        <h2>六分中心核心进展</h2>
        <p>先看组织全貌，再下钻到具体指标。</p>
      </div>
      <el-table :data="activeSummary" stripe>
        <el-table-column prop="org_name" label="区域分中心" min-width="150" />
        <el-table-column label="年目标" align="right">
          <template #default="{ row }">
            {{ formatValue(row.annual_target, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="月MP" align="right">
          <template #default="{ row }">
            {{ formatValue(row.mp?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="预定" align="right">
          <template #default="{ row }">
            {{ formatValue(row.forecast?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="实绩" align="right">
          <template #default="{ row }">
            {{ formatValue(row.actual?.value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column label="预定达成率" align="right">
          <template #default="{ row }">
            {{
              row.forecast_achievement === null
                ? "—"
                : `${(row.forecast_achievement * 100).toFixed(1)}%`
            }}
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
.summary-card small,
.section-title p {
  color: #82958d;
}
.summary-card.accent {
  background: #eff8f3;
  border-color: #c8e5d6;
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

