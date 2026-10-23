<script setup lang="ts">
import { computed, ref } from "vue";
import { http } from "@/utils/http";
import { useUserStoreHook } from "@/store/modules/user";

type Audit = { checked_members: number; membership_issue_count: number; participation_difference_count: number;
  membership_issues: {member_id:number; name:string; class_name:string; current_group_name:string; related_groups:string; reasons:string}[];
  participation_differences: {session_id:number; name:string; meeting_date:string; class_name:string; meeting_group_name:string; registered_group_name:string; current_group_name:string; registered_type:string; current_type:string; message:string}[];
  truncated: boolean };
const allowed = computed(() => ["org:manage", "members:read"].every(p => useUserStoreHook().permissions.includes(p)));
const visible = ref(false), loading = ref(false), error = ref("");
const data = ref<Audit>();
async function load() {
  if (!allowed.value || loading.value) return;
  visible.value = true; loading.value = true; error.value = ""; data.value = undefined;
  try { data.value = (await http.request<{success:boolean;data:Audit}>("get", "/api/v1/iam/org-units/study-membership-audit", {params:{limit:500},timeout:60000})).data; }
  catch { error.value = "归组核查暂未完成，请稍后重试；没有修改任何归属或登记记录。"; }
  finally { loading.value = false; }
}
const typeLabel = (value:string) => ({HOME_GROUP:"本组",CROSS_GROUP:"跨组",UNRESOLVED:"待核对"})[value] || value;
</script>
<template>
  <el-button v-if="allowed" :loading="loading" @click="load">全局归组核查</el-button>
  <el-dialog v-model="visible" title="全局归组核查（只读）" width="min(1200px, 96vw)">
    <el-alert title="当前归属统一以班级与小组管理为准。历史登记差异需区分后来调组与原登记误报；未入账可核实，已入账走更正流程。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-loading="loading">
      <template v-if="data">
        <p>已核查在册学员 {{ data.checked_members }} 人；归属关系待核对 {{ data.membership_issue_count }} 人；历史登记差异 {{ data.participation_difference_count }} 条。</p>
        <el-alert v-if="data.truncated" title="明细每类最多显示500条，汇总数量包含当前授权范围全部核查结果。" type="warning" :closable="false" />
        <h3>归属关系待核对</h3>
        <el-table :data="data.membership_issues" max-height="330" border empty-text="未发现归属关系冲突">
          <el-table-column prop="name" label="姓名" width="100" />
          <el-table-column prop="class_name" label="当前班级" min-width="140" />
          <el-table-column prop="current_group_name" label="采用的小组" min-width="140" />
          <el-table-column prop="related_groups" label="仍有效的关系" min-width="180" />
          <el-table-column prop="reasons" label="核查发现" min-width="260" />
        </el-table>
        <h3>历史登记与当前归组差异</h3>
        <el-table :data="data.participation_differences" max-height="360" border empty-text="当前授权范围未发现历史登记差异">
          <el-table-column prop="meeting_date" label="日期" width="110" />
          <el-table-column prop="session_id" label="场次编号" width="90" />
          <el-table-column prop="name" label="姓名" width="100" />
          <el-table-column prop="class_name" label="班级" min-width="130" />
          <el-table-column prop="meeting_group_name" label="参加的小组" min-width="130" />
          <el-table-column prop="registered_group_name" label="登记时归组" min-width="130" />
          <el-table-column prop="current_group_name" label="当前归组" min-width="130" />
          <el-table-column label="登记／当前类型" min-width="130"><template #default="{row}">{{ typeLabel(row.registered_type) }} ／ {{ typeLabel(row.current_type) }}</template></el-table-column>
          <el-table-column prop="message" label="处理提示" min-width="250" />
        </el-table>
      </template>
    </div>
    <template #footer><el-button :loading="loading" @click="load">重新核查</el-button><el-button @click="visible=false">关闭</el-button></template>
  </el-dialog>
</template>
