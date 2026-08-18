<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  changeIdentityAccountStatus,
  changeIdentityAssignmentStatus,
  createAccountEmployment,
  createAccountTechnicalAssignment,
  createAccountVolunteerAppointment,
  createIdentityUser,
  getIdentityAccounts,
  getIdentityCatalog,
  getIdentityOrgOptions,
  initializeAccountIdentity,
  type IdentityAccount,
  type IdentityCatalog,
  type IdentityOrgOption
} from "@/api/identityAdmin";

defineOptions({ name: "IdentityAdmin" });

type DialogMode = "initialize" | "employment" | "volunteer" | "technical";

const positionLabels: Record<string, string> = {
  operations_admin: "苏州塾运营管理员",
  ops_center_director: "运营中心负责人",
  ops_center_operations: "分中心运营专员",
  ops_center_learning: "学习践行专员",
  ops_center_development: "发展建设专员",
  ops_center_management: "运营管理专员",
  ops_center_data: "数据中心专员",
  ops_center_finance: "财务专员",
  ops_center_administration: "行政专员"
};
const appointmentLabels: Record<string, string> = {
  volunteer_director: "理事志工",
  volunteer_regional_lead: "三级分中心负责人志工",
  volunteer_regional_service: "三级分中心志工",
  volunteer_class_counselor: "班主任志工",
  volunteer_class_committee: "班委志工",
  volunteer_group_leader: "组长志工",
  volunteer_group_committee: "组委志工",
  volunteer_activity: "专项活动志工"
};
const statusLabels: Record<string, string> = {
  PLANNED: "待生效",
  ACTIVE: "有效",
  LEAVE: "休假",
  SUSPENDED: "已停用",
  ENDED: "已结束",
  REVOKED: "已撤销"
};

const loading = ref(false);
const saving = ref(false);
const unavailableMessage = ref("");
const keyword = ref("");
const rows = ref<IdentityAccount[]>([]);
const orgs = ref<IdentityOrgOption[]>([]);
const catalog = ref<IdentityCatalog>();
const dialogVisible = ref(false);
const accountDialogVisible = ref(false);
const writeGuideVisible = ref(false);
const dialogMode = ref<DialogMode>("initialize");
const activeAccount = ref<IdentityAccount>();
const guideAccount = ref<IdentityAccount>();
const accountSaving = ref(false);

const accountForm = reactive({
  username: "盛和塾",
  display_name: "盛和塾",
  password: ""
});

const form = reactive({
  source_reference: "",
  confirmation_note: "",
  position_keys: [] as string[],
  started_on: "",
  ended_on: "",
  responsibility_org_unit_id: "",
  responsibility_scope_type: "UNIT" as "UNIT" | "SUBTREE",
  appointment_key: "",
  org_unit_id: "",
  scope_type: "UNIT" as "UNIT" | "SUBTREE",
  starts_at: "",
  ends_at: "",
  assignment_purpose: ""
});

const filteredRows = computed(() => {
  const query = keyword.value.trim().toLowerCase();
  if (!query) return rows.value;
  return rows.value.filter(
    row =>
      row.username.toLowerCase().includes(query) ||
      row.display_name.toLowerCase().includes(query)
  );
});
function accountRoleLabels(row: any) {
  if (row.username === "admin") return ["平台最高管理账号"];
  const labels = new Set<string>();
  for (const employment of row.employments || []) {
    if (["ENDED", "REVOKED"].includes(employment.status)) continue;
    for (const position of employment.positions || []) {
      if (["ENDED", "REVOKED"].includes(position.status)) continue;
      labels.add(positionLabels[position.position_key] || position.position_key);
    }
  }
  for (const appointment of row.volunteer_appointments || []) {
    if (["ENDED", "REVOKED"].includes(appointment.status)) continue;
    labels.add(appointmentLabels[appointment.appointment_key] || appointment.appointment_key);
  }
  for (const assignment of row.technical_assignments || []) {
    if (["ENDED", "REVOKED"].includes(assignment.status)) continue;
    labels.add("技术管理职责");
  }
  return labels.size ? [...labels] : ["尚未建立任职（不等于故障）"];
}
const writesEnabled = computed(() => catalog.value?.writes_enabled === true);
const guideAccountName = computed(() => guideAccount.value?.display_name || "当前账号");
const permissionMatrix = computed(() => catalog.value?.permission_matrix || []);
const permissionLevelLabels: Record<string, string> = {
  INTERNAL: "内部",
  SENSITIVE: "敏感",
  RESTRICTED: "受限"
};
const permissionLevelTypes: Record<string, "info" | "warning" | "danger"> = {
  INTERNAL: "info",
  SENSITIVE: "warning",
  RESTRICTED: "danger"
};
const dialogTitle = computed(() => {
  const name = activeAccount.value?.display_name || "";
  return {
    initialize: `确认自然人关联 · ${name}`,
    employment: `建立运营中心雇佣 · ${name}`,
    volunteer: `建立志工任职 · ${name}`,
    technical: `建立技术管理员任期 · ${name}`
  }[dialogMode.value];
});

function isPlatformAdmin(account: any) {
  return account.username === "admin";
}

function identityLinkLabel(account: any) {
  if (account.person_id) return "已确认关联";
  if (isPlatformAdmin(account)) return "平台账号（不自动绑定）";
  return writesEnabled.value ? "待确认" : "当前只读（未绑定）";
}

function openWriteGuide(account?: any) {
  guideAccount.value = account as IdentityAccount | undefined;
  writeGuideVisible.value = true;
}

function errorText(error: any, fallback = "操作失败") {
  const status = error?.response?.status;
  if (status === 401) return "登录已失效，请重新登录后再打开此页面";
  if (error?.code === "ERR_NETWORK" || error?.message === "Network Error") {
    return "无法连接身份服务，请刷新页面后重新登录；如仍失败，请联系管理员检查网络";
  }
  if (error?.code === "ECONNABORTED") {
    return "身份服务响应较慢，请稍后刷新页面重试";
  }
  return error?.response?.data?.detail || error?.message || fallback;
}

function isRetryableReadError(error: any) {
  const status = error?.response?.status;
  return (
    !status ||
    [408, 425, 429, 500, 502, 503, 504].includes(status) ||
    error?.code === "ERR_NETWORK" ||
    error?.code === "ECONNABORTED"
  );
}

async function readWithRetry<T>(request: () => Promise<T>) {
  try {
    return await request();
  } catch (error) {
    if (!isRetryableReadError(error)) throw error;
    await new Promise(resolve => window.setTimeout(resolve, 600));
    return request();
  }
}

function resetForm() {
  Object.assign(form, {
    source_reference: "",
    confirmation_note: "",
    position_keys: [],
    started_on: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
    ended_on: "",
    responsibility_org_unit_id: "",
    responsibility_scope_type: "UNIT",
    appointment_key: "",
    org_unit_id: "",
    scope_type: "UNIT",
    starts_at: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
    ends_at: dayjs().add(1, "year").format("YYYY-MM-DDTHH:mm:ss"),
    assignment_purpose: ""
  });
}

function openAccountDialog() {
  accountForm.username = "盛和塾";
  accountForm.display_name = "盛和塾";
  accountForm.password = `Temp-${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
  accountDialogVisible.value = true;
}

async function createAccount() {
  if (!accountForm.username.trim() || accountForm.username.trim().length < 3) {
    ElMessage.error("账号至少填写 3 个字符");
    return;
  }
  if (!accountForm.display_name.trim() || accountForm.password.length < 10) {
    ElMessage.error("请填写人员名称，并确保临时密码至少 10 个字符");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "仅创建无旧角色、无组织范围的身份账号；后续由身份与任职流程配置岗位。",
      "确认创建账号",
      {
        confirmButtonText: "创建并继续",
        cancelButtonText: "取消",
        type: "warning"
      }
    );
    accountSaving.value = true;
    await createIdentityUser({
      username: accountForm.username.trim(),
      display_name: accountForm.display_name.trim(),
      password: accountForm.password
    });
    ElMessage.success("账号已创建，可在下方继续确认自然人并建立雇佣");
    accountDialogVisible.value = false;
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  } finally {
    accountSaving.value = false;
  }
}

async function load() {
  loading.value = true;
  unavailableMessage.value = "";
  try {
    const [catalogResult, accountResult] = await Promise.all([
      readWithRetry(getIdentityCatalog),
      readWithRetry(getIdentityAccounts)
    ]);
    catalog.value = catalogResult.data;
    rows.value = accountResult.data;

    // 生产只读时不需要组织范围选项，避免一个仅供写入表单使用的请求阻塞页面查看。
    if (catalogResult.data.writes_enabled) {
      try {
        const orgResult = await readWithRetry(getIdentityOrgOptions);
        orgs.value = orgResult.data;
      } catch (error) {
        orgs.value = [];
        ElMessage.warning("组织范围选项暂不可用，当前身份信息仍可查看");
      }
    } else {
      orgs.value = [];
    }
  } catch (error) {
    unavailableMessage.value = errorText(
      error,
      "身份与任职管理暂不可用，请确认灰度开关"
    );
  } finally {
    loading.value = false;
  }
}

function openDialog(
  mode: DialogMode,
  account: IdentityAccount | Record<string, unknown>
) {
  resetForm();
  dialogMode.value = mode;
  activeAccount.value = account as IdentityAccount;
  dialogVisible.value = true;
}

async function submit() {
  const account = activeAccount.value;
  if (!account) return;
  saving.value = true;
  try {
    const confirmation = {
      source_reference: form.source_reference.trim(),
      confirmation_note: form.confirmation_note.trim()
    };
    if (dialogMode.value === "initialize") {
      await initializeAccountIdentity(account.id, confirmation);
    } else if (dialogMode.value === "employment") {
      await createAccountEmployment(account.id, {
        ...confirmation,
        position_keys: form.position_keys,
        started_on: form.started_on,
        ended_on: form.ended_on || undefined,
        service_responsibilities: form.responsibility_org_unit_id
          ? [
              {
                org_unit_id: form.responsibility_org_unit_id,
                scope_type: form.responsibility_scope_type
              }
            ]
          : []
      });
    } else if (dialogMode.value === "volunteer") {
      await createAccountVolunteerAppointment(account.id, {
        ...confirmation,
        appointment_key: form.appointment_key,
        org_unit_id: form.org_unit_id,
        scope_type: form.scope_type,
        starts_at: form.starts_at,
        ends_at: form.ends_at
      });
    } else {
      await createAccountTechnicalAssignment(account.id, {
        ...confirmation,
        assignment_purpose: form.assignment_purpose.trim(),
        starts_at: form.starts_at,
        ends_at: form.ends_at
      });
    }
    ElMessage.success("身份与任职记录已保存，并已写入审计");
    dialogVisible.value = false;
    await load();
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

async function changeStatus(
  assignmentType: string,
  assignmentId: number,
  status: "SUSPENDED" | "ENDED" | "REVOKED"
) {
  try {
    const { value } = await ElMessageBox.prompt(
      "本操作不会删除历史记录。请填写业务原因和批准依据。",
      `${statusLabels[status]}任职`,
      {
        inputPlaceholder: "至少 6 个字符",
        inputValidator: value => value.trim().length >= 6 || "原因至少填写 6 个字符",
        confirmButtonText: "确认变更",
        cancelButtonText: "取消"
      }
    );
    await changeIdentityAssignmentStatus(assignmentType, assignmentId, {
      status,
      reason: value.trim()
    });
    ElMessage.success("状态已更新并写入审计");
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

async function changeAccountStatus(row: any) {
  const targetStatus = row.is_active ? "SUSPENDED" : "ACTIVE";
  const label = targetStatus === "SUSPENDED" ? "停用账号" : "重新启用账号";
  try {
    const { value } = await ElMessageBox.prompt(
      "本操作会写入审计；停用账号会立即使现有登录会话失效。请填写业务原因。",
      label,
      {
        inputPlaceholder: "至少 6 个字符",
        inputValidator: value => value.trim().length >= 6 || "原因至少填写 6 个字符",
        confirmButtonText: `确认${label}`,
        cancelButtonText: "取消",
        type: targetStatus === "SUSPENDED" ? "warning" : "info"
      }
    );
    await changeIdentityAccountStatus(row.id, {
      status: targetStatus,
      reason: value.trim()
    });
    ElMessage.success(`账号已${targetStatus === "SUSPENDED" ? "停用" : "重新启用"}并写入审计`);
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

function canChange(status: string) {
  return writesEnabled.value && !["ENDED", "REVOKED"].includes(status);
}

onMounted(load);
</script>

<template>
  <div class="identity-page" v-loading="loading">
    <section class="page-head">
      <div>
        <p>最小权限与可审计授权</p>
        <h1>身份与任职管理</h1>
        <span>自然人、专职雇佣、服务责任、志工任职和技术职责分别记录；admin 为平台最高管理账号。</span>
      </div>
      <div class="page-actions">
        <el-input v-model="keyword" clearable placeholder="搜索账号或姓名" />
        <el-button v-if="writesEnabled" type="primary" @click="openAccountDialog">
          新增账号
        </el-button>
      </div>
    </section>

    <el-alert
      v-if="unavailableMessage"
      :title="unavailableMessage"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="!writesEnabled"
      title="当前为只读灰度状态：IDENTITY_ADMIN_WRITES_ENABLED 尚未开启"
      type="info"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else
      title="写入灰度已开启。每次操作都必须填写确认依据和业务说明，并写入审计。"
      type="success"
      :closable="false"
      show-icon
    />

    <el-card v-if="!unavailableMessage && !writesEnabled" shadow="never" class="read-only-guide">
      <div>
        <strong>现在无需创建测试账号或确认自然人</strong>
        <p>现有账号仍可查看。真实身份绑定会创建人员、任职和审计记录，因此生产写入关闭时不能点击；隔离测试由平台使用合成账号完成。</p>
      </div>
      <el-button type="primary" plain @click="openWriteGuide()">查看操作说明</el-button>
    </el-card>

    <el-card v-if="!unavailableMessage" shadow="never" class="permission-card">
      <template #header>
        <div class="card-heading">
          <div>
            <strong>权限矩阵（只读）</strong>
            <p>展示角色模板的最小权限边界；实际授权仍受有效期、组织范围和服务端校验共同约束。</p>
          </div>
          <el-tag type="info" effect="plain">不改变现有授权</el-tag>
        </div>
      </template>
      <el-table
        :data="permissionMatrix"
        stripe
        size="small"
        max-height="420"
        empty-text="暂无权限模板"
      >
        <el-table-column prop="role_name" label="角色模板" min-width="190" />
        <el-table-column prop="role_key" label="内部键" min-width="190" />
        <el-table-column label="权限项" min-width="480">
          <template #default="{ row }">
            <div class="permission-tags">
              <el-tooltip
                v-for="permission in row.permissions"
                :key="permission.permission_key"
                :content="`${permission.permission_key} · ${permission.permission_name}`"
                placement="top"
              >
                <el-tag
                  size="small"
                  effect="plain"
                  :type="permissionLevelTypes[permission.sensitive_level] || 'info'"
                >
                  {{ permission.permission_name }} ·
                  {{ permissionLevelLabels[permission.sensitive_level] || permission.sensitive_level }}
                </el-tag>
              </el-tooltip>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="!unavailableMessage" shadow="never">
      <el-table :data="filteredRows" stripe empty-text="暂无账号">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="assignment-grid">
              <section>
                <h3>运营中心雇佣与岗位</h3>
                <div v-if="!row.employments.length" class="empty">暂无记录</div>
                <article v-for="employment in row.employments" :key="employment.id">
                  <p>
                    {{ employment.institution_name }} ·
                    {{ statusLabels[employment.status] || employment.status }}
                  </p>
                  <p v-for="position in employment.positions" :key="position.id">
                    岗位：{{ positionLabels[position.position_key] || position.position_key }}
                    <el-tag size="small">{{ statusLabels[position.status] || position.status }}</el-tag>
                  </p>
                  <p
                    v-for="scope in employment.service_responsibilities"
                    :key="scope.id"
                  >
                    服务责任：{{ scope.org_name }}（{{ scope.scope_type }}）
                    <el-tag size="small">{{ statusLabels[scope.status] || scope.status }}</el-tag>
                  </p>
                  <div v-if="canChange(employment.status)" class="status-actions">
                    <el-button link @click="changeStatus('employment', employment.id, 'SUSPENDED')">停用</el-button>
                    <el-button link @click="changeStatus('employment', employment.id, 'ENDED')">结束</el-button>
                    <el-button link type="danger" @click="changeStatus('employment', employment.id, 'REVOKED')">撤销</el-button>
                  </div>
                </article>
              </section>

              <section>
                <h3>志工任职</h3>
                <div v-if="!row.volunteer_appointments.length" class="empty">暂无记录</div>
                <article
                  v-for="appointment in row.volunteer_appointments"
                  :key="appointment.id"
                >
                  <p>
                    {{ appointmentLabels[appointment.appointment_key] || appointment.appointment_key }}
                    · {{ appointment.org_name }}（{{ appointment.scope_type }}）
                  </p>
                  <p>
                    {{ dayjs(appointment.starts_at).format("YYYY-MM-DD") }} 至
                    {{ dayjs(appointment.ends_at).format("YYYY-MM-DD") }}
                    <el-tag size="small">{{ statusLabels[appointment.status] || appointment.status }}</el-tag>
                  </p>
                  <div v-if="canChange(appointment.status)" class="status-actions">
                    <el-button link @click="changeStatus('volunteer', appointment.id, 'SUSPENDED')">停用</el-button>
                    <el-button link @click="changeStatus('volunteer', appointment.id, 'ENDED')">结束</el-button>
                    <el-button link type="danger" @click="changeStatus('volunteer', appointment.id, 'REVOKED')">撤销</el-button>
                  </div>
                </article>
              </section>

              <section>
                <h3>技术管理员任期</h3>
                <div v-if="!row.technical_assignments.length" class="empty">暂无记录</div>
                <article
                  v-for="assignment in row.technical_assignments"
                  :key="assignment.id"
                >
                  <p>{{ assignment.assignment_purpose }}</p>
                  <p>
                    {{ dayjs(assignment.starts_at).format("YYYY-MM-DD") }} 至
                    {{ dayjs(assignment.ends_at).format("YYYY-MM-DD") }}
                    <el-tag size="small">{{ statusLabels[assignment.status] || assignment.status }}</el-tag>
                  </p>
                  <div v-if="canChange(assignment.status)" class="status-actions">
                    <el-button link @click="changeStatus('technical', assignment.id, 'SUSPENDED')">停用</el-button>
                    <el-button link @click="changeStatus('technical', assignment.id, 'ENDED')">结束</el-button>
                    <el-button link type="danger" @click="changeStatus('technical', assignment.id, 'REVOKED')">撤销</el-button>
                  </div>
                </article>
              </section>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="人员" min-width="120" />
        <el-table-column prop="username" label="账号" min-width="150" />
        <el-table-column label="账号角色" min-width="180">
          <template #default="{ row }">
            <div class="account-role-tags">
              <el-tag
                v-for="label in accountRoleLabels(row)"
                :key="label"
                :type="row.username === 'admin' ? 'danger' : label.includes('尚未建立') ? 'warning' : 'info'"
                effect="plain"
              >
                {{ label }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="自然人关联" min-width="185">
          <template #default="{ row }">
            <el-tag :type="row.person_id ? 'success' : isPlatformAdmin(row) ? 'info' : 'warning'">
              {{ identityLinkLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="有效状态" width="100">
          <template #default="{ row }">
            {{ row.is_active ? "有效" : "停用" }}
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="350" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="!row.person_id && writesEnabled && row.is_active && !isPlatformAdmin(row)"
              link
              type="primary"
              @click="openDialog('initialize', row)"
            >
              确认自然人
            </el-button>
            <el-button v-else-if="!row.person_id" link type="info" @click="openWriteGuide(row)">
              为什么不能确认？
            </el-button>
            <template v-else>
              <el-button link :disabled="!writesEnabled || !row.is_active" @click="openDialog('employment', row)">
                建立雇佣
              </el-button>
              <el-button link :disabled="!writesEnabled || !row.is_active" @click="openDialog('volunteer', row)">
                建立志工任职
              </el-button>
              <el-button link :disabled="!writesEnabled || !row.is_active" @click="openDialog('technical', row)">
                建立技术任期
              </el-button>
            </template>
            <el-button
              v-if="row.username !== 'admin'"
              link
              :type="row.is_active ? 'danger' : 'success'"
              :disabled="!writesEnabled"
              @click="changeAccountStatus(row)"
            >
              {{ row.is_active ? "停用账号" : "重新启用" }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="accountDialogVisible" title="新增身份账号" width="520px">
      <el-alert
        title="新账号默认不授予旧角色和组织范围；岗位权限必须通过本页的身份与任职流程建立。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-form :model="accountForm" label-position="top" class="account-form">
        <el-form-item label="统一用户名">
          <el-input v-model="accountForm.username" maxlength="128" />
        </el-form-item>
        <el-form-item label="人员名称">
          <el-input v-model="accountForm.display_name" maxlength="255" />
        </el-form-item>
        <el-form-item label="临时初始密码">
          <el-input v-model="accountForm.password" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="accountDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="accountSaving" @click="createAccount">
          创建并刷新账号列表
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="writeGuideVisible" title="身份确认操作说明" width="680px">
      <el-alert
        :title="`${guideAccountName} 当前不能直接确认自然人`"
        type="info"
        :closable="false"
        show-icon
      />
      <ol class="write-guide-list">
        <li>“待确认”只表示尚未建立自然人关联，不代表账号异常，也不需要你先创建测试账号。</li>
        <li>当前生产身份写入处于关闭状态；点击确认本应创建真实人员与审计记录，因此按钮不会开放。</li>
        <li><code>admin</code> 是平台最高管理账号，系统永久禁止把它作为自然人、雇佣或任职试点对象。</li>
        <li>“盛和塾”账号是否对应某位实际使用人尚未确认；在确认实际使用人前，系统不会自动绑定，也不会按账号名猜测。</li>
        <li>隔离测试由平台使用合成账号完成。只有未来确有新增个人账号时，才需一次性提供人员身份、任职依据、组织范围、任期和回滚责任；不需要提供密码。</li>
      </ol>
      <template #footer>
        <el-button type="primary" @click="writeGuideVisible = false">我知道了</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="680px">
      <el-form :model="form" label-position="top">
        <template v-if="dialogMode === 'employment'">
          <el-form-item label="运营中心岗位">
            <el-select
              v-model="form.position_keys"
              multiple
              collapse-tags
              collapse-tags-tooltip
              clearable
              placeholder="请选择一个或多个已确认岗位"
            >
              <el-option
                v-for="key in catalog?.position_keys || []"
                :key="key"
                :label="positionLabels[key] || key"
                :value="key"
              />
            </el-select>
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="入职时间">
              <el-date-picker v-model="form.started_on" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
            <el-form-item label="计划离职时间（可选）">
              <el-date-picker v-model="form.ended_on" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
            <el-form-item label="服务责任范围（可选，不是组织归属）">
              <el-select v-model="form.responsibility_org_unit_id" clearable filterable>
                <el-option v-for="org in orgs" :key="org.id" :label="`${org.name} · ${org.unit_type}`" :value="org.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="范围类型">
              <el-select v-model="form.responsibility_scope_type">
                <el-option label="仅本组织" value="UNIT" />
                <el-option label="本组织及下级" value="SUBTREE" />
              </el-select>
            </el-form-item>
          </div>
        </template>

        <template v-if="dialogMode === 'volunteer'">
          <div class="form-grid">
            <el-form-item label="志工任职">
              <el-select v-model="form.appointment_key">
                <el-option
                  v-for="key in catalog?.appointment_keys || []"
                  :key="key"
                  :label="appointmentLabels[key] || key"
                  :value="key"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="任职组织">
              <el-select v-model="form.org_unit_id" filterable>
                <el-option v-for="org in orgs" :key="org.id" :label="`${org.name} · ${org.unit_type}`" :value="org.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="任职范围">
              <el-select v-model="form.scope_type">
                <el-option label="仅本组织" value="UNIT" />
                <el-option label="本组织及下级" value="SUBTREE" />
              </el-select>
            </el-form-item>
          </div>
        </template>

        <template v-if="dialogMode === 'volunteer' || dialogMode === 'technical'">
          <el-form-item v-if="dialogMode === 'technical'" label="技术管理用途">
            <el-input v-model="form.assignment_purpose" maxlength="500" />
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="开始时间">
              <el-date-picker v-model="form.starts_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
            <el-form-item label="结束时间">
              <el-date-picker v-model="form.ends_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
            </el-form-item>
          </div>
        </template>

        <el-divider />
        <el-form-item label="确认依据">
          <el-input
            v-model="form.source_reference"
            placeholder="任命文件、审批单、会议决议或业务负责人确认编号"
            maxlength="500"
          />
        </el-form-item>
        <el-form-item label="业务确认说明">
          <el-input
            v-model="form.confirmation_note"
            type="textarea"
            :rows="3"
            maxlength="1000"
            placeholder="说明已确认的身份事实、范围和期限；至少 8 个字符"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">确认并写入审计</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.identity-page {
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
.page-head :deep(.el-input) {
  width: 260px;
}
.page-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.read-only-guide {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.read-only-guide p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
}
.assignment-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  padding: 12px 28px;
}
.assignment-grid section {
  padding: 14px;
  background: var(--el-fill-color-light);
  border-radius: 12px;
}
.permission-card {
  overflow: hidden;
}
.card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.card-heading p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  font-weight: 400;
}
.account-role-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.permission-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.assignment-grid h3 {
  margin: 0 0 12px;
}
.assignment-grid article {
  padding: 10px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}
.assignment-grid p {
  margin: 4px 0;
}
.empty {
  color: var(--el-text-color-secondary);
}
.status-actions {
  margin-top: 8px;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}
.form-grid :deep(.el-select),
.form-grid :deep(.el-date-editor),
form :deep(.el-select) {
  width: 100%;
}
.account-form {
  margin-top: 18px;
}
.write-guide-list {
  margin: 18px 0 0;
  padding-left: 22px;
  color: var(--el-text-color-regular);
  line-height: 1.8;
}
@media (max-width: 900px) {
  .page-head,
  .assignment-grid {
    grid-template-columns: 1fr;
  }
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .read-only-guide {
    align-items: flex-start;
    flex-direction: column;
  }
  .assignment-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
