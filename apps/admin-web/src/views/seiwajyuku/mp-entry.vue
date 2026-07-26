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

const currentPlan = computed(() =>
  plans.value.find(item => item.id === planId.value)
);
const editable = computed(() => Boolean(currentPlan.value?.write_enabled));
const groupedRows = computed(() => {
  const groups = new Map<string, any>();
  rows.value.forEach(row => {
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

async function load() {
  if (!planId.value) return;
  loading.value = true;
  try {
    const response = await getPeriodValues({
      plan_id: planId.value,
      month: month.value
    });
    rows.value = response.data;
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
          numeric_value: item.numeric_value,
          value_state:
            item.numeric_value === 0 ? "ZERO_IS_VALID" : "VALUE"
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
      <el-table :data="groupedRows" height="calc(100vh - 275px)" stripe>
        <el-table-column prop="org_name" label="区域分中心" width="150" fixed />
        <el-table-column prop="metric_name" label="指标" min-width="190" fixed />
        <el-table-column label="月MP" width="145" align="right">
          <template #default="{ row }">
            {{ row.mp?.numeric_value ?? "—" }}
          </template>
        </el-table-column>
        <el-table-column label="预定" width="190">
          <template #default="{ row }">
            <el-input-number
              v-if="row.forecast"
              v-model="row.forecast.numeric_value"
              :disabled="!editable"
              :controls="false"
              class="number-input"
            />
          </template>
        </el-table-column>
        <el-table-column label="实绩" width="190">
          <template #default="{ row }">
            <el-input-number
              v-if="row.actual"
              v-model="row.actual.numeric_value"
              :disabled="!editable"
              :controls="false"
              class="number-input"
            />
          </template>
        </el-table-column>
        <el-table-column label="数据状态" min-width="150">
          <template #default="{ row }">
            <el-tag
              :type="row.actual?.value_state === 'VALUE' ? 'success' : 'info'"
            >
              {{ row.actual?.value_state ?? "NO_DATA" }}
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
}
</style>
