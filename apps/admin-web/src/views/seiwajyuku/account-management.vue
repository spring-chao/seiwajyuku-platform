<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  getManagedAccounts,
  resetManagedAccountPassword,
  type ManagedAccount
} from "@/api/identityAdmin";

defineOptions({ name: "AccountManagement" });

const loading = ref(false);
const keyword = ref("");
const rows = ref<ManagedAccount[]>([]);
const roleLabels: Record<string, string> = {
  system_admin: "系统管理员",
  technical_admin: "技术管理员",
  operations_admin: "运营管理员",
  read_only: "只读观察员"
};

const filteredRows = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  if (!term) return rows.value;
  return rows.value.filter(row =>
    [row.username, row.display_name, ...row.roles]
      .join(" ")
      .toLowerCase()
      .includes(term)
  );
});

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || "操作失败";
}

async function load() {
  loading.value = true;
  try {
    rows.value = (await getManagedAccounts()).data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    loading.value = false;
  }
}

async function resetPassword(row: ManagedAccount) {
  try {
    const passwordResult = await ElMessageBox.prompt(
      "新密码至少 10 位。提交后旧登录会话会立即失效，系统不会展示或保存明文密码。",
      `重置密码 · ${row.display_name}`,
      {
        inputType: "password",
        inputPlaceholder: "请输入新密码（至少 10 位）",
        inputValidator: value => value.length >= 10 || "密码至少需要 10 位",
        confirmButtonText: "下一步",
        cancelButtonText: "取消"
      }
    );
    const reasonResult = await ElMessageBox.prompt(
      "请记录本次重置的业务原因或批准依据。",
      "填写重置依据",
      {
        inputPlaceholder: "至少 6 个字符",
        inputValidator: value => value.trim().length >= 6 || "重置原因至少填写 6 个字符",
        confirmButtonText: "确认重置",
        cancelButtonText: "取消",
        type: "warning"
      }
    );
    await resetManagedAccountPassword(row.id, {
      password: passwordResult.value,
      reason: reasonResult.value.trim()
    });
    ElMessage.success("密码已重置，该账号的旧登录会话已全部失效");
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

onMounted(load);
</script>

<template>
  <div class="account-page">
    <section class="page-head">
      <div>
        <p>最小权限与可追溯管理</p>
        <h1>账号管理</h1>
        <span>查看账号状态并受控重置密码；密码不会在页面、接口返回或审计中显示。</span>
      </div>
      <el-input v-model="keyword" placeholder="搜索账号、姓名或角色" clearable />
    </section>

    <el-alert
      title="重置密码后，该账号已有的登录令牌会立即失效；请通过安全渠道告知新密码。"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <el-table v-loading="loading" :data="filteredRows" stripe empty-text="暂无账号">
        <el-table-column prop="display_name" label="人员" min-width="140" />
        <el-table-column prop="username" label="账号" min-width="170" />
        <el-table-column label="角色" min-width="220">
          <template #default="{ row }">
            <el-tag v-for="role in row.roles" :key="role" size="small" effect="plain">
              {{ roleLabels[role] || role }}
            </el-tag>
            <span v-if="!row.roles.length" class="muted">待按身份与任职确认</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">
              {{ row.is_active ? "有效" : "停用" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最近登录" min-width="170">
          <template #default="{ row }">
            {{ row.last_login_at ? dayjs(row.last_login_at).format("YYYY-MM-DD HH:mm") : "尚未登录" }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="!row.is_active" @click="resetPassword(row as ManagedAccount)">
              修改密码
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.account-page { display: grid; gap: 18px; padding: 20px; }
.page-head { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 28px; color: #f7fbff; background: linear-gradient(125deg, #17324d, #2b6d83); border-radius: 18px; }
.page-head p { margin: 0 0 8px; color: #a9dbe5; letter-spacing: .16em; }
.page-head h1 { margin: 0 0 10px; font-size: 28px; }
.page-head span { color: #d3eaf0; }
.page-head :deep(.el-input) { width: 260px; }
.muted { color: var(--el-text-color-secondary); }
@media (max-width: 900px) { .page-head { align-items: flex-start; flex-direction: column; } }
</style>
