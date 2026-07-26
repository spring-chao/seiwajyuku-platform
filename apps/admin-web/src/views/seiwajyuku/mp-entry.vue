<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { message } from "@/utils/message";
import {
  getAnnualPlans,
  getPeriodValues,
  savePeriodValues,
  type AnnualPlan,
  type PeriodValue
} from "@/api/seiwajyuku";

defineOptions({ name: "MpEntry" });

const loading = ref(false);
const saving = ref(false);
const plans = ref<AnnualPlan[]>([]);
const planId = ref<number>();
const month = ref(Math.min(new Date().getMonth() + 1, 12));
const rows = ref<PeriodValue[]>([]);
const orgUnitId = ref("");

const currentPlan = computed(() =>
  plans.value.find(item => item.id === planId.value)
);
const editable = computed(() => Boolean(currentPlan.value?.write_enabled));
const centers = computed(() => {
  const result = new Map<string, string>();
  rows.value.forEach(row => result.set(row.org_unit_id, row.org_name));
  return [...result].map(([id, name]) => ({ id, name }));
});
const selectedCenter = computed(() =>
  centers.value.find(center => center.id === orgUnitId.value)
);
const groupedRows = computed(() => {
  const groups = new Map<string, any>();
  rows.value
    .filter(row => row.org_unit_id === orgUnitId.value)
    .forEach(row => {
    const key = `${row.org_unit_id}:${row.metric_key}`;
    const group = groups.get(key) ?? {
      org_name: row.org_name,
      metric_name: row.metric_name,
      unit: row.unit
    };
    group[row.value_kind.toLowerCase()] = row;
    groups.set(key, group);
  });
  return [...groups.values()];
});

const toNumber = (value: number | string | null | undefined) => {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

const displayValue = (
  value: number | string | null | undefined,
  unit: string
) => {
  const numeric = toNumber(value);
  if (numeric === null) return null;
  return Math.round(unit === "PERCENT" ? numeric * 100 : numeric);
};

const formatValue = (
  value: number | string | null | undefined,
  unit: string
) => {
  const displayed = displayValue(value, unit);
  if (displayed === null) return "—";
  if (unit === "PERCENT") return `${displayed}%`;
  if (unit === "PERSON") return `${displayed} 人`;
  if (unit === "SCORE") return `${displayed} 分`;
  return String(displayed);
};

const updateDisplayValue = (
  row: PeriodValue,
  unit: string,
  value: number | undefined
) => {
  if (value === undefined) {
    row.numeric_value = null;
    return;
  }
  row.numeric_value = unit === "PERCENT" ? value / 100 : Math.round(value);
};

const unitLabel = (unit: string) =>
  ({ PERCENT: "%", PERSON: "人", SCORE: "分" })[unit] ?? "";

const stateInfo = (row: any) => {
  if (!row.actual && toNumber(row.forecast?.numeric_value) !== null) {
    return { label: "预定已填", type: "info" } as const;
  }
  const state =
    row.actual?.value_state ??
    row.forecast?.value_state ??
    row.mp?.value_state ??
    "NO_DATA";
  const mapping = {
    VALUE: { label: "已填", type: "success" },
    ZERO_IS_VALID: { label: "已填（0）", type: "success" },
    NOT_APPLICABLE: { label: "不适用", type: "info" },
    NOT_DUE: { label: "未到期", type: "info" },
    NO_DATA: { label: "未填", type: "warning" }
  } as const;
  return mapping[state] ?? mapping.NO_DATA;
};

async function load() {
  if (!planId.value) return;
  loading.value = true;
  try {
    const response = await getPeriodValues({
      plan_id: planId.value,
      month: month.value
    });
    rows.value = response.data;
    if (!centers.value.some(center => center.id === orgUnitId.value)) {
      orgUnitId.value = centers.value[0]?.id ?? "";
    }
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!planId.value || !editable.value) return;
  saving.value = true;
  try {
    const updates = groupedRows.value.flatMap(row =>
      [row.forecast, row.actual]
        .filter(Boolean)
        .map((item: PeriodValue) => ({
          id: item.id,
          numeric_value: toNumber(item.numeric_value),
          value_state:
            toNumber(item.numeric_value) === 0 ? "ZERO_IS_VALID" : "VALUE"
        }))
    );
    await savePeriodValues(planId.value, updates);
    message(`已保存 ${updates.length} 条预定/实绩`, { type: "success" });
    await load();
  } finally {
    saving.value = false;
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
  <div class="entry-page">
    <header>
      <div>
        <p>年度 MP / 月度执行</p>
        <h1>预定与实绩填报</h1>
      </div>
      <div class="toolbar">
        <el-select v-model="planId" @change="load">
          <el-option
            v-for="plan in plans"
            :key="plan.id"
            :label="`${plan.year}年度 · V${plan.version}`"
            :value="plan.id"
          />
        </el-select>
        <el-select v-model="month" @change="load">
          <el-option
            v-for="value in 12"
            :key="value"
            :label="`${value}月`"
            :value="value"
          />
        </el-select>
        <el-select
          v-model="orgUnitId"
          aria-label="分中心"
          placeholder="选择分中心"
        >
          <el-option
            v-for="center in centers"
            :key="center.id"
            :label="center.name"
            :value="center.id"
          />
        </el-select>
        <el-button
          type="primary"
          :disabled="!editable"
          :loading="saving"
          @click="save"
        >
          保存本月填报
        </el-button>
      </div>
    </header>

    <el-alert
      v-if="!editable"
      title="当前方案只读"
      description="业务负责人批准并由授权管理员启用写入后，才能修改预定和实绩；月MP基准始终保留发布留痕。"
      type="warning"
      show-icon
      :closable="false"
    />

    <section class="table-card" v-loading="loading">
      <div class="center-heading">
        <div>
          <h2>{{ selectedCenter?.name ?? "请选择分中心" }}</h2>
          <p>{{ month }}月 MP、预定与实绩，共 {{ groupedRows.length }} 项指标</p>
        </div>
        <el-tag type="info">百分比及数值均按整数显示</el-tag>
      </div>
      <el-table :data="groupedRows" height="calc(100vh - 350px)" stripe>
        <el-table-column prop="metric_name" label="指标" min-width="190" fixed />
        <el-table-column label="单位" width="80" align="center">
          <template #default="{ row }">
            {{ unitLabel(row.unit) }}
          </template>
        </el-table-column>
        <el-table-column
          label="月MP"
          width="170"
          align="right"
          header-align="right"
        >
          <template #default="{ row }">
            {{ formatValue(row.mp?.numeric_value, row.unit) }}
          </template>
        </el-table-column>
        <el-table-column
          label="预定"
          width="190"
          align="right"
          header-align="right"
        >
          <template #default="{ row }">
            <span v-if="!editable" class="readonly-value">
              {{ formatValue(row.forecast?.numeric_value, row.unit) }}
            </span>
            <el-input-number
              v-else-if="row.forecast"
              :model-value="
                displayValue(row.forecast.numeric_value, row.unit)
              "
              :precision="0"
              :controls="false"
              class="number-input"
              @update:model-value="
                updateDisplayValue(row.forecast, row.unit, $event)
              "
            />
          </template>
        </el-table-column>
        <el-table-column
          label="实绩"
          width="190"
          align="right"
          header-align="right"
        >
          <template #default="{ row }">
            <span v-if="!editable" class="readonly-value">
              {{ formatValue(row.actual?.numeric_value, row.unit) }}
            </span>
            <el-input-number
              v-else-if="row.actual"
              :model-value="displayValue(row.actual.numeric_value, row.unit)"
              :precision="0"
              :controls="false"
              class="number-input"
              @update:model-value="
                updateDisplayValue(row.actual, row.unit, $event)
              "
            />
          </template>
        </el-table-column>
        <el-table-column
          label="数据状态"
          min-width="150"
          align="center"
          header-align="center"
        >
          <template #default="{ row }">
            <el-tag :type="stateInfo(row).type">
              {{ stateInfo(row).label }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.entry-page {
  min-height: 100%;
  padding: 24px;
  background: #f4f7f5;
}
header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 18px;
}
header p {
  margin: 0 0 5px;
  color: #328064;
  font-size: 13px;
  letter-spacing: 0.08em;
}
header h1 {
  margin: 0;
  color: #173f33;
  font-size: 30px;
}
.toolbar {
  display: flex;
  gap: 10px;
}
.toolbar .el-select {
  width: 145px;
}
.table-card {
  padding: 18px;
  margin-top: 18px;
  background: #fff;
  border: 1px solid #e2ebe7;
  border-radius: 14px;
}
.center-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.center-heading h2 {
  margin: 0;
  color: #173f33;
  font-size: 21px;
}
.center-heading p {
  margin: 5px 0 0;
  color: #82958d;
  font-size: 13px;
}
.readonly-value {
  display: block;
  color: #344b42;
  text-align: right;
}
.number-input {
  width: 100%;
}
@media (max-width: 760px) {
  header {
    align-items: stretch;
    flex-direction: column;
  }
  .toolbar {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .toolbar .el-select {
    width: 100%;
  }
  .toolbar .el-button {
    grid-column: 1 / -1;
  }
  .center-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
