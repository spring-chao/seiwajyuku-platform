<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createStaff,
  getStaff,
  getStaffCatalog,
  getStaffList,
  resetStaffPassword,
  updateStaff,
  type StaffCatalog,
  type StaffPayload,
  type StaffRecord,
  type StaffScopeType
} from "@/api/staffManagement";

defineOptions({ name: "StaffManagement" });

const PASSWORD_MIN_LENGTH = 6;

type StaffForm = {
  id: number | null;
  name: string;
  login_account: string;
  account_hint: string;
  phone: string;
  phone_hint: string;
  gender: "" | "MALE" | "FEMALE";
  institution_id: string;
  department_name: string;
  supervisor_user_id: number | null;
  position_keys: string[];
  responsibility_org_unit_id: string;
  responsibility_scope_type: StaffScopeType;
  is_active: boolean;
  employment_status: "ACTIVE" | "LEAVE";
  custom_password: boolean;
  temporary_password: string;
};

const loading = ref(false);
const saving = ref(false);
const catalogLoading = ref(false);
const catalog = ref<StaffCatalog>();
const rows = ref<StaffRecord[]>([]);
const drawerVisible = ref(false);
const permissionVisible = ref(false);
const permissionRecord = ref<StaffRecord>();
const currentRecord = ref<StaffRecord>();
const moreSections = ref<string[]>([]);
const initialAuthorization = ref("");

const filters = reactive({
  org_unit_id: "",
  position_key: "",
  is_active: "",
  query: ""
});

function emptyForm(): StaffForm {
  return {
    id: null,
    name: "",
    login_account: "",
    account_hint: "",
    phone: "",
    phone_hint: "",
    gender: "",
    institution_id: "",
    department_name: "",
    supervisor_user_id: null,
    position_keys: [],
    responsibility_org_unit_id: "",
    responsibility_scope_type: "SUBTREE",
    is_active: true,
    employment_status: "ACTIVE",
    custom_password: false,
    temporary_password: ""
  };
}

const form = reactive<StaffForm>(emptyForm());
const isEditing = computed(() => form.id !== null);
const writesEnabled = computed(() => Boolean(catalog.value?.writes_enabled));
const positions = computed(() =>
  (catalog.value?.positions || []).filter(
    position => position.mapping_status === "AUTO"
  )
);
const institutions = computed(() => catalog.value?.institutions || []);
const departments = computed(() => catalog.value?.departments || []);
const supervisors = computed(() => catalog.value?.supervisors || []);

function buildOrgTree(units: StaffCatalog["org_units"]) {
  const byId = new Map(
    units.map(unit => [
      unit.id,
      { id: unit.id, label: unit.name, children: [] as any[] }
    ])
  );
  const roots: Array<{ id: string; label: string; children: any[] }> = [];
  for (const unit of units) {
    const node = byId.get(unit.id);
    if (!node) continue;
    const parent = unit.parent_id ? byId.get(unit.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

const orgTree = computed(() => buildOrgTree(catalog.value?.org_units || []));
const selectedInstitution = computed(() =>
  institutions.value.find(item => item.id === form.institution_id)
);
const scopedOrgUnits = computed(() => {
  const rootId = selectedInstitution.value?.scope_root_org_unit_id;
  if (!rootId) return [];
  const units = catalog.value?.org_units || [];
  const included = new Set<string>([rootId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const unit of units) {
      if (unit.parent_id && included.has(unit.parent_id) && !included.has(unit.id)) {
        included.add(unit.id);
        changed = true;
      }
    }
  }
  return units.filter(unit => included.has(unit.id));
});
const scopeOrgTree = computed(() => buildOrgTree(scopedOrgUnits.value));

function handleInstitutionChange() {
  const institution = selectedInstitution.value;
  form.responsibility_org_unit_id = institution?.scope_root_org_unit_id || "";
  form.responsibility_scope_type = "SUBTREE";
}

function errorText(error: any, fallback = "操作失败，请稍后重试") {
  const status = error?.response?.status;
  if (status === 401) return "登录已失效，请重新登录后再操作";
  if (status === 403) {
    return error?.response?.data?.detail || "当前账号没有管理专职人员的权限";
  }
  if (error?.code === "ERR_NETWORK" || error?.message === "Network Error") {
    return "暂时无法连接服务，请检查网络后重试";
  }
  if (error?.code === "ECONNABORTED") return "服务响应超时，请稍后重试";
  return error?.response?.data?.detail || fallback;
}

function authorizationSignature(
  positionKeys: string[],
  orgUnitId: string,
  scopeType: StaffScopeType
) {
  return JSON.stringify({
    position_keys: [...positionKeys].sort(),
    org_unit_id: orgUnitId,
    scope_type: scopeType
  });
}

function resetForm(record?: StaffRecord) {
  const next = emptyForm();
  if (record) {
    const scope = record.scopes[0];
    next.id = record.id;
    next.name = record.name;
    next.account_hint = record.login_account;
    next.phone_hint = record.phone_masked || "";
    next.gender =
      record.gender === "MALE" || record.gender === "FEMALE"
        ? record.gender
        : "";
    next.institution_id = record.institution_id;
    next.department_name = record.department_name || "";
    next.supervisor_user_id = record.supervisor_user_id || null;
    next.position_keys = record.positions.map(item => item.position_key);
    next.responsibility_org_unit_id = scope?.org_unit_id || "";
    next.responsibility_scope_type = scope?.scope_type || "SUBTREE";
    next.is_active = record.is_active;
    next.employment_status =
      record.employment_status === "LEAVE" ? "LEAVE" : "ACTIVE";
  }
  Object.assign(form, next);
  initialAuthorization.value = authorizationSignature(
    next.position_keys,
    next.responsibility_org_unit_id,
    next.responsibility_scope_type
  );
  moreSections.value = [];
}

async function loadCatalog() {
  catalogLoading.value = true;
  try {
    catalog.value = (await getStaffCatalog()).data;
  } catch (error) {
    ElMessage.error(errorText(error, "无法载入专职人员配置项"));
  } finally {
    catalogLoading.value = false;
  }
}

async function loadStaff() {
  loading.value = true;
  try {
    rows.value = (
      await getStaffList({
        org_unit_id: filters.org_unit_id || undefined,
        position_key: filters.position_key || undefined,
        is_active:
          filters.is_active === "" ? undefined : filters.is_active === "true",
        query: filters.query.trim() || undefined
      })
    ).data;
  } catch (error) {
    ElMessage.error(errorText(error, "无法载入专职人员"));
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  Object.assign(filters, {
    org_unit_id: "",
    position_key: "",
    is_active: "",
    query: ""
  });
  void loadStaff();
}

function openCreate() {
  if (!writesEnabled.value) {
    ElMessage.warning("身份写入门禁当前关闭，暂不能新增专职人员");
    return;
  }
  currentRecord.value = undefined;
  resetForm();
  drawerVisible.value = true;
}

async function openEdit(row: StaffRecord) {
  if (!writesEnabled.value) return;
  try {
    const detail = (await getStaff(row.id)).data;
    currentRecord.value = detail;
    resetForm(detail);
    drawerVisible.value = true;
  } catch (error) {
    ElMessage.error(errorText(error, "无法读取人员详情"));
  }
}

function validateForm() {
  if (!form.name.trim()) return "请填写姓名";
  if (!form.gender) return "请选择性别";
  if (!isEditing.value && !form.phone.trim()) return "请填写手机号";
  if (isEditing.value && !form.phone_hint && !form.phone.trim()) return "请补充手机号";
  if (!isEditing.value && form.login_account.trim().length < 3) {
    return "登录账号至少填写 3 个字符";
  }
  if (!form.institution_id) return "请选择所属机构";
  if (selectedInstitution.value && !selectedInstitution.value.scope_available) {
    return "该机构尚未配置正式组织树，暂不能新增专职人员";
  }
  if (!form.position_keys.length) return "请选择岗位";
  if (!form.responsibility_org_unit_id || !scopedOrgUnits.value.some(unit => unit.id === form.responsibility_org_unit_id)) {
    return "请选择所属机构对应的负责范围";
  }
  if (
    !isEditing.value &&
    form.custom_password &&
    form.temporary_password.length < PASSWORD_MIN_LENGTH
  ) {
    return `临时密码至少需要 ${PASSWORD_MIN_LENGTH} 位`;
  }
  return "";
}

function payloadFromForm(): StaffPayload {
  const payload: StaffPayload = {
    name: form.name.trim(),
    gender: form.gender as "MALE" | "FEMALE",
    institution_id: form.institution_id,
    department_name: form.department_name.trim() || null,
    supervisor_user_id: form.supervisor_user_id,
    position_keys: form.position_keys,
    employment_status: form.employment_status,
    is_active: form.is_active
  };
  if (!isEditing.value) {
    payload.login_account = form.login_account.trim();
    payload.phone = form.phone.trim();
    payload.replace_phone = true;
    payload.responsibility_org_unit_id = form.responsibility_org_unit_id;
    payload.responsibility_scope_type = form.responsibility_scope_type;
    payload.temporary_password = form.custom_password ? form.temporary_password : null;
  } else {
    if (form.login_account.trim()) payload.login_account = form.login_account.trim();
    if (form.phone.trim()) {
      payload.phone = form.phone.trim();
      payload.replace_phone = true;
    }
    const currentSignature = authorizationSignature(
      form.position_keys,
      form.responsibility_org_unit_id,
      form.responsibility_scope_type
    );
    if (currentSignature !== initialAuthorization.value) {
      payload.responsibility_org_unit_id = form.responsibility_org_unit_id;
      payload.responsibility_scope_type = form.responsibility_scope_type;
    }
  }
  return payload;
}

async function confirmDisablingChange() {
  const current = currentRecord.value;
  if (!current) return;
  const messages: string[] = [];
  if (current.employment_status === "ACTIVE" && form.employment_status === "LEAVE") {
    messages.push(`确认将${form.name}设为离职吗？其工作人员权限将立即停止。`);
  }
  if (current.is_active && !form.is_active) {
    messages.push(`确认停用${form.name}的登录账号吗？其现有登录会话将立即失效。`);
  }
  if (!messages.length) return;
  await ElMessageBox.confirm(messages.join("\n"), "确认状态变更", {
    confirmButtonText: "确认",
    cancelButtonText: "取消",
    type: "warning"
  });
}

async function save() {
  const validationMessage = validateForm();
  if (validationMessage) {
    ElMessage.warning(validationMessage);
    return;
  }
  try {
    saving.value = true;
    const payload = payloadFromForm();
    if (isEditing.value && form.id) {
      await confirmDisablingChange();
      const result = await updateStaff(form.id, payload);
      drawerVisible.value = false;
      ElMessage.success(
        result.data.sessions_revoked
          ? "已保存，账号原有登录会话已失效"
          : "专职人员信息已保存"
      );
    } else {
      const result = await createStaff(payload as StaffPayload & { login_account: string });
      drawerVisible.value = false;
      const temporaryPassword = result.data.temporary_password;
      await ElMessageBox.alert(
        temporaryPassword
          ? `登录账号：${payload.login_account}\n临时密码：${temporaryPassword}\n\n请通过安全渠道交给本人；关闭后不会再次显示。`
          : `登录账号：${payload.login_account}\n\n已使用你填写的临时密码创建账号。`,
        "创建成功",
        { confirmButtonText: "我已保存", type: "success" }
      );
    }
    await loadStaff();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error, "保存失败，请稍后重试"));
  } finally {
    saving.value = false;
  }
}

async function resetPassword(row: StaffRecord) {
  if (!writesEnabled.value) return;
  try {
    const password = await ElMessageBox.prompt(
      `输入新密码（至少 ${PASSWORD_MIN_LENGTH} 位）。保存后旧登录会话会立即失效。`,
      `重置密码 · ${row.name}`,
      {
        inputType: "password",
        inputPlaceholder: `至少 ${PASSWORD_MIN_LENGTH} 位`,
        inputValidator: value =>
          value.length >= PASSWORD_MIN_LENGTH || `密码至少需要 ${PASSWORD_MIN_LENGTH} 位`,
        confirmButtonText: "确认重置",
        cancelButtonText: "取消"
      }
    );
    await resetStaffPassword(row.id, { password: password.value });
    ElMessage.success("密码已重置，旧登录会话已失效");
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error, "密码重置失败，请稍后重试"));
  }
}

function scopeLabel(row: StaffRecord) {
  if (!row.scopes.length) return "待维护";
  return row.scopes
    .map(
      scope =>
        `${scope.org_name || scope.org_unit_id}（${
          scope.scope_type === "SUBTREE" ? "含下级" : "仅本级"
        }）`
    )
    .join("、");
}

function showPermissions(row: StaffRecord) {
  permissionRecord.value = row;
  permissionVisible.value = true;
}

onMounted(() => {
  void Promise.all([loadCatalog(), loadStaff()]);
});
</script>

<template>
  <div class="staff-page">
    <section class="page-head">
      <div>
        <p>一分钟完成人员建档</p>
        <h1>专职人员管理</h1>
        <span>填写人员、岗位和负责范围，系统自动完成账号、权限与审计。</span>
      </div>
      <el-button type="primary" :disabled="!writesEnabled" @click="openCreate">新增专职人员</el-button>
    </section>

    <el-alert v-if="catalog && !writesEnabled" title="身份写入门禁当前关闭：可查看人员，暂不能新增、修改或重置密码。" type="warning" :closable="false" show-icon />

    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="负责范围">
          <el-tree-select v-model="filters.org_unit_id" :data="orgTree" :props="{ label: 'label', children: 'children' }" node-key="id" check-strictly clearable filterable placeholder="全部组织" style="width: 190px" />
        </el-form-item>
        <el-form-item label="岗位">
          <el-select v-model="filters.position_key" clearable placeholder="全部岗位" style="width: 170px"><el-option v-for="position in positions" :key="position.position_key" :label="position.position_name" :value="position.position_key" /></el-select>
        </el-form-item>
        <el-form-item label="状态"><el-select v-model="filters.is_active" clearable placeholder="全部" style="width: 104px"><el-option label="启用" value="true" /><el-option label="停用" value="false" /></el-select></el-form-item>
        <el-form-item><el-input v-model="filters.query" clearable placeholder="姓名、账号或手机号" style="width: 220px" @keyup.enter="loadStaff" /></el-form-item>
        <el-form-item><el-button type="primary" @click="loadStaff">查询</el-button><el-button @click="resetFilters">重置</el-button></el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading || catalogLoading" :data="rows" stripe empty-text="暂无专职人员">
        <el-table-column prop="name" label="姓名" min-width="110" fixed="left" />
        <el-table-column prop="login_account" label="登录账号" min-width="145" />
        <el-table-column label="手机号" min-width="120"><template #default="{ row }">{{ row.phone_masked || "待维护" }}</template></el-table-column>
        <el-table-column prop="institution_name" label="所属机构" min-width="150" />
        <el-table-column label="岗位" min-width="170"><template #default="{ row }">{{ row.positions.map(item => item.position_name).join("、") || "待维护" }}</template></el-table-column>
        <el-table-column label="负责范围" min-width="210"><template #default="{ row }">{{ scopeLabel(row as StaffRecord) }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.is_active && row.employment_status === 'ACTIVE' ? 'success' : 'info'">{{ row.employment_status === "LEAVE" ? "离职" : row.is_active ? "在职" : "账号停用" }}</el-tag></template></el-table-column>
        <el-table-column label="最近登录" min-width="150"><template #default="{ row }">{{ row.last_login_at ? dayjs(row.last_login_at).format("YYYY-MM-DD HH:mm") : "尚未登录" }}</template></el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="!writesEnabled" @click="openEdit(row as StaffRecord)">编辑</el-button>
            <el-button link type="primary" @click="showPermissions(row as StaffRecord)">查看系统权限</el-button>
            <el-button link type="primary" :disabled="!writesEnabled || !row.is_active" @click="resetPassword(row as StaffRecord)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drawerVisible" :title="isEditing ? '编辑专职人员' : '新增专职人员'" size="min(650px, 96vw)" destroy-on-close>
      <el-form label-position="top" class="staff-form" size="large">
        <div class="form-grid">
          <el-form-item label="姓名" required><el-input v-model="form.name" maxlength="255" /></el-form-item>
          <el-form-item label="性别" required><el-select v-model="form.gender" placeholder="请选择"><el-option label="男" value="MALE" /><el-option label="女" value="FEMALE" /></el-select></el-form-item>
          <el-form-item label="手机号" required><el-input v-model="form.phone" maxlength="32" :placeholder="isEditing && form.phone_hint ? `当前 ${form.phone_hint}；填写新号码即可修改` : '请输入手机号'" /></el-form-item>
          <el-form-item label="登录账号" required><el-input v-model="form.login_account" maxlength="128" :placeholder="isEditing ? `留空保持 ${form.account_hint}` : '独立账号，不必等于手机号'" /></el-form-item>
          <el-form-item label="所属机构" required><el-select v-model="form.institution_id" filterable placeholder="请选择" @change="handleInstitutionChange"><el-option v-for="item in institutions" :key="item.id" :label="item.name" :value="item.id"><span>{{ item.name }}</span><small v-if="!item.scope_available" class="option-note">组织树待配置</small></el-option></el-select></el-form-item>
          <el-form-item label="岗位" required><el-select v-model="form.position_keys" multiple collapse-tags filterable placeholder="请选择岗位"><el-option v-for="position in positions" :key="position.position_key" :label="position.position_name" :value="position.position_key"><div class="position-option"><span>{{ position.position_name }}</span><small>{{ position.duty_description }}</small></div></el-option></el-select></el-form-item>
          <el-form-item label="负责范围" required class="wide"><el-alert v-if="form.institution_id && selectedInstitution && !selectedInstitution.scope_available" title="该机构的组织根节点尚未配置，暂不能建立负责范围。请先补齐组织主数据。" type="warning" :closable="false" show-icon /><div v-else class="scope-fields"><el-tree-select v-model="form.responsibility_org_unit_id" :data="scopeOrgTree" :props="{ label: 'label', children: 'children' }" node-key="id" check-strictly filterable placeholder="选择负责组织" /><el-select v-model="form.responsibility_scope_type"><el-option label="含下级" value="SUBTREE" /><el-option label="仅本级" value="UNIT" /></el-select></div></el-form-item>
        </div>

        <el-collapse v-model="moreSections">
          <el-collapse-item title="更多信息（可选）" name="more">
            <div class="form-grid more-grid">
              <el-form-item label="部门"><el-autocomplete v-model="form.department_name" :fetch-suggestions="(query: string, callback: (items: Array<{ value: string }>) => void) => callback(departments.filter(item => item.includes(query)).map(value => ({ value })))" maxlength="255" /></el-form-item>
              <el-form-item label="上级负责人"><el-select v-model="form.supervisor_user_id" clearable filterable placeholder="可不选"><el-option v-for="person in supervisors" :key="person.id" :label="`${person.name} · ${person.institution_name}`" :value="person.id" /></el-select></el-form-item>
              <el-form-item v-if="!isEditing" label="临时密码" class="wide"><el-switch v-model="form.custom_password" active-text="自定义临时密码" inactive-text="系统自动生成" /><el-input v-if="form.custom_password" v-model="form.temporary_password" type="password" show-password maxlength="256" autocomplete="new-password" :placeholder="`至少 ${PASSWORD_MIN_LENGTH} 位`" /></el-form-item>
              <template v-if="isEditing"><el-form-item label="在职状态"><el-select v-model="form.employment_status"><el-option label="在职" value="ACTIVE" /><el-option label="离职" value="LEAVE" /></el-select></el-form-item><el-form-item label="登录账号状态"><el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" /></el-form-item></template>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <template #footer><el-button @click="drawerVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存</el-button></template>
    </el-drawer>

    <el-dialog v-model="permissionVisible" :title="`${permissionRecord?.name || ''} · 系统权限`" width="min(760px, 94vw)">
      <el-alert title="系统权限由当前岗位和负责范围自动生成，仅供查看。" type="info" :closable="false" show-icon />
      <el-table :data="permissionRecord?.authorization_grants || []" max-height="420" empty-text="暂无当前系统权限"><el-table-column prop="role_name" label="能力" min-width="180" /><el-table-column label="负责范围" min-width="240"><template #default="{ row }">{{ row.org_name || row.org_unit_id }}（{{ row.scope_type === "SUBTREE" ? "含下级" : "仅本级" }}）</template></el-table-column></el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.staff-page { display: grid; gap: 18px; padding: 20px; }
.page-head { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 28px; color: #f7fbff; background: linear-gradient(125deg, #17324d, #2b6d83); border-radius: 18px; }
.page-head p { margin: 0 0 8px; color: #a9dbe5; letter-spacing: 0.13em; }
.page-head h1 { margin: 0 0 10px; font-size: 28px; }
.page-head span { color: #d3eaf0; }
.filter-card :deep(.el-form-item) { margin-bottom: 0; }
.staff-form { padding-right: 6px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.form-grid .wide { grid-column: 1 / -1; }
.scope-fields { display: grid; grid-template-columns: minmax(0, 1fr) 120px; gap: 10px; width: 100%; }
.position-option { display: flex; flex-direction: column; gap: 2px; line-height: 1.25; }
.position-option small, .option-note { color: #86909c; font-size: 12px; }
.option-note { float: right; margin-left: 12px; }
.more-grid { padding-top: 18px; }
@media (max-width: 720px) { .page-head { align-items: flex-start; flex-direction: column; } .form-grid, .scope-fields { grid-template-columns: 1fr; } .form-grid .wide { grid-column: auto; } }
</style>
