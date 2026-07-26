<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  getFollowupTasks,
  type FollowupTask
} from "@/api/seiwajyuku";

defineOptions({ name: "FollowupTasks" });

const loading = ref(false);
const status = ref<string>();
const rows = ref<FollowupTask[]>([]);
const openCount = computed(
  () => rows.value.filter(item => item.status !== "CLOSED").length
);

const statusText: Record<string, string> = {
  OPEN: "待执行",
  IN_PROGRESS: "跟进中",
  CLOSED: "已关闭"
};

async function load() {
  loading.value = true;
  try {
    const response = await getFollowupTasks(status.value);
    rows.value = response.data;
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="followup-page" v-loading="loading">
    <section class="page-head">
      <div>
        <p>关怀任务闭环</p>
        <h1>电话跟进与企业走访</h1>
        <span>联系方式默认脱敏，只有当前有效任务责任人可按用途临时查看。</span>
      </div>
      <el-select
        v-model="status"
        clearable
        placeholder="全部状态"
        style="width: 150px"
        @change="load"
      >
        <el-option label="待执行" value="OPEN" />
        <el-option label="跟进中" value="IN_PROGRESS" />
        <el-option label="已关闭" value="CLOSED" />
      </el-select>
    </section>

    <el-alert
      :title="`当前显示 ${rows.length} 项，其中 ${openCount} 项尚未关闭`"
      type="info"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <el-table :data="rows" stripe>
        <el-table-column prop="member_name" label="学长" min-width="110" />
        <el-table-column prop="phone_masked" label="联系方式" min-width="130" />
        <el-table-column prop="company_name" label="企业" min-width="150" />
        <el-table-column prop="org_name" label="所属中心" min-width="140" />
        <el-table-column prop="service_purpose" label="服务目的" min-width="220" />
        <el-table-column prop="assignee_name" label="责任人" min-width="110" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag
              :type="
                row.status === 'CLOSED'
                  ? 'success'
                  : row.status === 'IN_PROGRESS'
                    ? 'warning'
                    : 'info'
              "
            >
              {{ statusText[row.status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="next_followup_at" label="下次跟进" min-width="180" />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.followup-page {
  display: grid;
  gap: 18px;
  padding: 20px;
}
.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  padding: 28px;
  color: #f6fff9;
  background: linear-gradient(125deg, #123c2e, #25704e);
  border-radius: 18px;
}
.page-head p {
  margin: 0 0 8px;
  color: #9fe0bd;
  letter-spacing: 0.18em;
}
.page-head h1 {
  margin: 0 0 10px;
  font-size: 28px;
}
.page-head span {
  color: #cbe9d8;
}
</style>
