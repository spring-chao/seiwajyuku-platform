<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs from "dayjs";
import {
  getAttendanceEventGroups,
  type AttendanceEventGroup
} from "@/api/seiwajyuku";

defineOptions({ name: "ActivityAdmin" });

const loading = ref(false);
const month = ref(dayjs().format("YYYY-MM"));
const rows = ref<AttendanceEventGroup[]>([]);
const totalEligible = computed(() =>
  rows.value.reduce((sum, item) => sum + item.record_count, 0)
);
const totalCompleted = computed(() =>
  rows.value.reduce((sum, item) => sum + item.present_count, 0)
);

async function load() {
  loading.value = true;
  try {
    const response = await getAttendanceEventGroups(month.value);
    rows.value = response.data;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
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

    <el-card shadow="never">
      <el-table :data="rows" stripe>
        <el-table-column prop="event_date" label="活动日期" min-width="130" />
        <el-table-column prop="title" label="活动" min-width="180" />
        <el-table-column prop="org_name" label="所属分中心" min-width="150" />
        <el-table-column prop="activity_type" label="活动类型" min-width="150" />
        <el-table-column prop="session_count" label="场次数" width="90" align="right" />
        <el-table-column prop="record_count" label="签到记录" width="100" align="right" />
        <el-table-column prop="present_count" label="已签到" width="90" align="right" />
        <el-table-column prop="source_key" label="数据源" min-width="130" />
        <el-table-column prop="status" label="状态" width="100" />
      </el-table>
    </el-card>
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
@media (max-width: 900px) {
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
