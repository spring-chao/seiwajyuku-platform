<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createIdentityUser,
  getManagedAccounts,
  getIdentityCatalog,
  resetManagedAccountPassword,
  type ManagedAccount
} from "@/api/identityAdmin";

defineOptions({ name: "AccountManagement" });

const loading = ref(false);
const keyword = ref("");
const rows = ref<ManagedAccount[]>([]);
const writeGateLoading = ref(true);
const writeEnabled = ref(false);
const writeGateMessage = ref("正在核验身份写入门禁…");
const createDialogVisible = ref(false);
const createSaving = ref(false);
const createForm = reactive({
  username: "",
  display_name: "",
  password: ""
});
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

function errorText(error: any, fallback = "操作失败") {
  return error?.response?.data?.detail || error?.message || fallback;
}

function generatedTemporaryPassword() {
  return `Temp-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
}

function resetCreateForm() {
  Object.assign(createForm, {
    username: "",
    display_name: "",
    password: generatedTemporaryPassword()
  });
}

function openCreateDialog() {
  if (!writeEnabled.value) {
    ElMessage.warning("身份写入门禁当前关闭，暂不能创建账号");
    return;
  }
  resetCreateForm();
  createDialogVisible.value = true;
}

async function loadWriteGate() {
  writeGateLoading.value = true;
  try {
    const response = await getIdentityCatalog();
    writeEnabled.value = response.data.writes_enabled;
    writeGateMessage.value = response.data.writes_enabled
      ? "身份写入已获准，可以创建无角色初始账号。创建后请继续到“身份与任职”配置人员、岗位和组织范围。"
      : "身份写入门禁当前关闭。当前页面仍可查看账号，但不能创建账号；开启门禁后请刷新页面。";
  } catch (error: any) {
    writeEnabled.value = false;
    writeGateMessage.value = errorText(
      error,
      "无法核验身份写入门禁，创建账号已暂时禁用"
    );
  } finally {
    writeGateLoading.value = false;
  }
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

async function createAccount() {
  const username = createForm.username.trim();
  const displayName = createForm.display_name.trim();
  if (username.length < 3) {
    ElMessage.error("账号至少填写 3 个字符");
    return;
  }
  if (!displayName) {
    ElMessage.error("请填写人员名称");
    return;
  }
  if (createForm.password.length < 10) {
    ElMessage.error("临时密码至少需要 10 位");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "仅创建无角色、无组织范围的初始账号。后续请到“身份与任职”配置自然人、岗位和服务范围。",
      "确认创建账号",
      {
        confirmButtonText: "创建账号",
        cancelButtonText: "取消",
        type: "warning"
      }
    );
    createSaving.value = true;
    await createIdentityUser({
      username,
      display_name: displayName,
      password: createForm.password
    });
    const temporaryPassword = createForm.password;
    createDialogVisible.value = false;
    await load();
    await ElMessageBox.alert(
      `账号：${username}\n临时密码：${temporaryPassword}\n\n请通过安全渠道告知账号本人，并在后续身份与任职流程中完成权限配置。该密码不会再次显示。`,
      "账号创建成功",
      {
        confirmButtonText: "我已保存临时密码",
        type: "success"
      }
    );
    resetCreateForm();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  } finally {
    createSaving.value = false;
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
        inputValidator: value =>
          value.trim().length >= 6 || "重置原因至少填写 6 个字符",
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

onMounted(() => {
  void Promise.all([load(), loadWriteGate()]);
});
</script>

<template>
  <div class="account-page">
    <section class="page-head">
      <div>
        <p>最小权限与可追溯管理</p>
        <h1>账号管理</h1>
        <span
          >查看账号状态并受控重置密码；密码不会在页面、接口返回或审计中显示。</span
        >
      </div>
      <div class="head-actions">
        <el-input
          v-model="keyword"
          placeholder="搜索账号、姓名或角色"
          clearable
        />
        <el-button
          type="primary"
          :disabled="writeGateLoading || !writeEnabled"
          @click="openCreateDialog"
        >
          创建账号
        </el-button>
      </div>
    </section>

    <el-alert
      v-if="!writeGateLoading && !writeEnabled"
      :title="writeGateMessage"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="!writeGateLoading"
      :title="writeGateMessage"
      type="info"
      :closable="false"
      show-icon
    />

    <el-alert
      title="重置密码后，该账号已有的登录令牌会立即失效；请通过安全渠道告知新密码。"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="filteredRows"
        stripe
        empty-text="暂无账号"
      >
        <el-table-column prop="display_name" label="人员" min-width="140" />
        <el-table-column prop="username" label="账号" min-width="170" />
        <el-table-column label="角色" min-width="220">
          <template #default="{ row }">
            <el-tag
              v-for="role in row.roles"
              :key="role"
              size="small"
              effect="plain"
            >
              {{ roleLabels[role] || role }}
            </el-tag>
            <span v-if="!row.roles.length" class="muted"
              >待按身份与任职确认</span
            >
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
            {{
              row.last_login_at
                ? dayjs(row.last_login_at).format("YYYY-MM-DD HH:mm")
                : "尚未登录"
            }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :disabled="!row.is_active"
              @click="resetPassword(row as ManagedAccount)"
            >
              修改密码
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="createDialogVisible"
      title="创建初始账号"
      width="min(520px, 92vw)"
      destroy-on-close
    >
      <el-form label-position="top" size="large">
        <el-form-item label="登录账号" required>
          <el-input
            v-model="createForm.username"
            autocomplete="username"
            maxlength="128"
            placeholder="至少 3 个字符"
          />
        </el-form-item>
        <el-form-item label="人员名称" required>
          <el-input
            v-model="createForm.display_name"
            autocomplete="name"
            maxlength="255"
            placeholder="填写账号对应的真实人员名称"
          />
        </el-form-item>
        <el-form-item label="临时密码" required>
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            autocomplete="new-password"
            maxlength="256"
            placeholder="至少 10 位"
          >
            <template #append>
              <el-button
                @click="createForm.password = generatedTemporaryPassword()"
              >
                重新生成
              </el-button>
            </template>
          </el-input>
          <p class="form-hint">
            创建后只显示一次临时密码；账号初始没有角色和组织范围，需继续完成身份与任职配置。
          </p>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="createSaving"
          @click="createAccount"
        >
          创建账号
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.account-page {
  display: grid;
  gap: 18px;
  padding: 20px;
}
.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  padding: 28px;
  color: #f7fbff;
  background: linear-gradient(125deg, #17324d, #2b6d83);
  border-radius: 18px;
}
.page-head p {
  margin: 0 0 8px;
  color: #a9dbe5;
  letter-spacing: 0.16em;
}
.page-head h1 {
  margin: 0 0 10px;
  font-size: 28px;
}
.page-head span {
  color: #d3eaf0;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.head-actions :deep(.el-input) {
  width: 260px;
}
.form-hint {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.muted {
  color: var(--el-text-color-secondary);
}
@media (max-width: 900px) {
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .head-actions {
    width: 100%;
    align-items: stretch;
    flex-direction: column;
  }
  .head-actions :deep(.el-input) {
    width: 100%;
  }
}
</style>
