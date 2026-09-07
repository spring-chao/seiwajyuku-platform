<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  createStaff,
  getAuthorizationMigrationPreview,
  getStaff,
  getStaffCatalog,
  getStaffList,
  previewStaffChange,
  resetStaffPassword,
  updateStaff,
  type AuthorizationMigrationPreview,
  type StaffAuthorizationGrant,
  type StaffCatalog,
  type StaffGrantInput,
  type StaffPayload,
  type StaffRecord,
  type StaffRole,
  type StaffScopeType
} from "@/api/staffManagement";

defineOptions({ name: "StaffManagement" });

type GrantDraft = {
  role_key: string;
  org_unit_id: string;
  scope_type: StaffScopeType;
  valid_from: string;
  valid_until: string;
};

type StaffForm = {
  id: number | null;
  name: string;
  login_account: string;
  account_hint: string;
  is_active: boolean;
  phone: string;
  replace_phone: boolean;
  gender: string;
  institution_id: string;
  department_name: string;
  supervisor_user_id: number | null;
  position_keys: string[];
  started_on: string;
  ended_on: string;
  grants: GrantDraft[];
  authorization_basis: string;
  authorization_reason: string;
  auto_password: boolean;
  temporary_password: string;
  authorization_mode: StaffRecord["authorization_mode"];
};

const loading = ref(false);
const saving = ref(false);
const catalogLoading = ref(false);
const catalog = ref<StaffCatalog>();
const rows = ref<StaffRecord[]>([]);
const drawerVisible = ref(false);
const migrationVisible = ref(false);
const migrationLoading = ref(false);
const migrationRows = ref<AuthorizationMigrationPreview[]>([]);
const permissionRole = ref<StaffRole>();
const permissionVisible = ref(false);
const currentRecord = ref<StaffRecord>();

const filters = reactive({
  org_unit_id: "",
  department_name: "",
  position_key: "",
  role_key: "",
  is_active: "",
  query: ""
});

function initialDate() {
  return dayjs().format("YYYY-MM-DDTHH:mm:ss");
}

function asInputDate(value?: string | null) {
  return value ? dayjs(value).format("YYYY-MM-DDTHH:mm:ss") : "";
}

function cleanDate(value: string) {
  return value.trim() || null;
}

function emptyForm(): StaffForm {
  return {
    id: null,
    name: "",
    login_account: "",
    account_hint: "",
    is_active: true,
    phone: "",
    replace_phone: false,
    gender: "UNSPECIFIED",
    institution_id: "",
    department_name: "",
    supervisor_user_id: null,
    position_keys: [],
    started_on: initialDate(),
    ended_on: "",
    grants: [],
    authorization_basis: "",
    authorization_reason: "",
    auto_password: true,
    temporary_password: "",
    authorization_mode: "UNCONFIGURED"
  };
}

const form = reactive<StaffForm>(emptyForm());

const isEditing = computed(() => form.id !== null);
const writesEnabled = computed(() => Boolean(catalog.value?.writes_enabled));
const roleOptions = computed(() => catalog.value?.roles || []);
const positions = computed(() => catalog.value?.positions || []);
const institutions = computed(() => catalog.value?.institutions || []);
const departments = computed(() => catalog.value?.departments || []);
const supervisors = computed(() => catalog.value?.supervisors || []);
const roleByKey = computed<Record<string, StaffRole>>(() =>
  Object.fromEntries(roleOptions.value.map(role => [role.role_key, role]))
);

const orgTree = computed(() => {
  const byId = new Map(
    (catalog.value?.org_units || []).map(unit => [
      unit.id,
      { id: unit.id, label: unit.name, children: [] as any[] }
    ])
  );
  const roots: Array<{ id: string; label: string; children: any[] }> = [];
  for (const unit of catalog.value?.org_units || []) {
    const node = byId.get(unit.id);
    if (!node) continue;
    const parent = unit.parent_id ? byId.get(unit.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
});

function errorText(error: any, fallback = "操作失败") {
  return error?.response?.data?.detail || error?.message || fallback;
}

function resetForm(record?: StaffRecord) {
  const next = emptyForm();
  if (record) {
    next.id = record.id;
    next.name = record.name;
    // Phone-shaped account identifiers are masked in every read response.
    // Keep their real value server-side unless an operator deliberately edits it.
    next.account_hint = record.login_account;
    next.is_active = record.is_active;
    next.gender = record.gender || "UNSPECIFIED";
    next.institution_id = record.institution_id;
    next.department_name = record.department_name || "";
    next.supervisor_user_id = record.supervisor_user_id || null;
    next.position_keys = record.positions.map(item => item.position_key);
    next.started_on = asInputDate(record.started_on) || initialDate();
    next.ended_on = asInputDate(record.ended_on);
    next.authorization_mode = record.authorization_mode;
    next.grants =
      record.authorization_mode === "EXPLICIT"
        ? record.authorization_grants.map(grant => ({
            role_key: grant.role_key,
            org_unit_id: grant.org_unit_id,
            scope_type: grant.scope_type,
            valid_from: asInputDate(grant.valid_from),
            valid_until: asInputDate(grant.valid_until)
          }))
        : [];
  }
  Object.assign(form, next);
}

function addGrant() {
  form.grants.push({
    role_key: roleOptions.value[0]?.role_key || "",
    org_unit_id: "",
    scope_type: "UNIT",
    valid_from: "",
    valid_until: ""
  });
}

function removeGrant(index: number) {
  form.grants.splice(index, 1);
}

function showPermission(roleKey: string) {
  const role = roleByKey.value[roleKey];
  if (!role) return;
  permissionRole.value = role;
  permissionVisible.value = true;
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
        department_name: filters.department_name || undefined,
        position_key: filters.position_key || undefined,
        role_key: filters.role_key || undefined,
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
    department_name: "",
    position_key: "",
    role_key: "",
    is_active: "",
    query: ""
  });
  void loadStaff();
}

function openCreate() {
  if (!writesEnabled.value) {
    ElMessage.warning("身份写入门禁当前关闭，暂不能创建或调整专职人员");
    return;
  }
  resetForm();
  drawerVisible.value = true;
}

async function openEdit(row: StaffRecord) {
  if (!writesEnabled.value) {
    ElMessage.warning("身份写入门禁当前关闭，暂不能调整专职人员");
    return;
  }
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
  if (!isEditing.value && form.login_account.trim().length < 3) {
    return "登录账号至少填写 3 个字符";
  }
  if (!form.institution_id) return "请选择所属机构";
  if (!form.position_keys.length) return "至少选择一个岗位";
  if (!form.started_on) return "请填写任职开始时间";
  if (!isEditing.value && !form.grants.length) {
    return "新建专职人员至少配置一条角色与管辖范围授权";
  }
  if (
    form.grants.some(
      grant => !grant.role_key || !grant.org_unit_id || !grant.scope_type
    )
  ) {
    return "每条角色授权均需选择角色、组织和范围";
  }
  if (form.authorization_basis.trim().length < 4) {
    return "请填写至少 4 个字符的授权依据";
  }
  if (!form.auto_password && !isEditing.value && form.temporary_password.length < 10) {
    return "临时密码至少需要 10 位";
  }
  return "";
}

function payloadFromForm(): StaffPayload {
  const grants: StaffGrantInput[] = form.grants.map(grant => ({
    role_key: grant.role_key,
    org_unit_id: grant.org_unit_id,
    scope_type: grant.scope_type,
    valid_from: cleanDate(grant.valid_from),
    valid_until: cleanDate(grant.valid_until)
  }));
  return {
    name: form.name.trim(),
    login_account: form.login_account.trim() || null,
    temporary_password:
      !isEditing.value && !form.auto_password
        ? form.temporary_password
        : null,
    is_active: form.is_active,
    phone: form.replace_phone ? form.phone.trim() || null : null,
    replace_phone: form.replace_phone,
    gender: form.gender || null,
    institution_id: form.institution_id,
    department_name: form.department_name.trim() || null,
    supervisor_user_id: form.supervisor_user_id,
    position_keys: form.position_keys,
    started_on: form.started_on,
    ended_on: cleanDate(form.ended_on),
    grants,
    authorization_basis: form.authorization_basis.trim(),
    authorization_reason: form.authorization_reason.trim()
  };
}

function previewMessage(preview: Awaited<ReturnType<typeof previewStaffChange>>["data"]) {
  const addedRoles = preview.diff.added_grants.map(item => item.role_name).join("、") || "无";
  const removedRoles = preview.diff.removed_grants.map(item => item.role_name).join("、") || "无";
  const positionDelta = [
    preview.diff.added_position_keys.length
      ? `新增岗位 ${preview.diff.added_position_keys.length} 项`
      : "",
    preview.diff.removed_position_keys.length
      ? `结束岗位 ${preview.diff.removed_position_keys.length} 项`
      : ""
  ]
    .filter(Boolean)
    .join("；") || "岗位不变";
  return [
    `状态：${preview.before.is_active ? "启用" : "停用"} → ${preview.after.is_active ? "启用" : "停用"}`,
    `新增角色范围：${addedRoles}`,
    `移除角色范围：${removedRoles}`,
    `调整角色范围有效期或状态：${preview.diff.changed_grants.length} 项`,
    positionDelta,
    preview.requires_business_reason
      ? "本次包含敏感权限扩大，已要求填写业务原因。"
      : "本次不包含新增敏感权限。"
  ].join("\n");
}

async function save() {
  const validationMessage = validateForm();
  if (validationMessage) {
    ElMessage.warning(validationMessage);
    return;
  }
  const payload = payloadFromForm();
  if (isEditing.value && !payload.login_account) {
    // An omitted account means "keep the existing protected identifier".
    delete payload.login_account;
  }
  try {
    if (isEditing.value && form.id) {
      const preview = await previewStaffChange(form.id, payload);
      if (
        preview.data.requires_business_reason &&
        payload.authorization_reason.trim().length < 8
      ) {
        ElMessage.warning("扩大敏感权限时必须填写至少 8 个字符的业务原因");
        return;
      }
      await ElMessageBox.confirm(previewMessage(preview.data), "请确认人员与授权变更", {
        confirmButtonText: "确认保存",
        cancelButtonText: "返回修改",
        type: preview.data.sensitive_expansion ? "warning" : "info"
      });
      saving.value = true;
      const result = await updateStaff(form.id, payload);
      drawerVisible.value = false;
      ElMessage.success(
        result.data.sessions_revoked
          ? "已保存，账号现有登录会话已全部失效"
          : "专职人员信息已保存"
      );
    } else {
      const createPayload = payload as StaffPayload & { login_account: string };
      if (!createPayload.login_account) return;
      await ElMessageBox.confirm(
        "将同时建立人员、账号、任职、岗位与逐条角色范围授权。系统会保留任职和授权历史。",
        "确认创建专职人员",
        {
          confirmButtonText: "确认创建",
          cancelButtonText: "返回修改",
          type: "warning"
        }
      );
      saving.value = true;
      const result = await createStaff(createPayload);
      drawerVisible.value = false;
      const temporaryPassword = result.data.temporary_password;
      if (temporaryPassword) {
        await ElMessageBox.alert(
          `登录账号：${createPayload.login_account}\n临时密码：${temporaryPassword}\n\n请仅通过安全渠道交付本人；关闭此窗口后系统不会再次展示该密码。`,
          "专职人员创建成功",
          { confirmButtonText: "我已安全保存", type: "success" }
        );
      } else {
        ElMessage.success("专职人员创建成功");
      }
    }
    await loadStaff();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error, "保存失败"));
  } finally {
    saving.value = false;
  }
}

async function resetPassword(row: StaffRecord) {
  if (!writesEnabled.value) return;
  try {
    const password = await ElMessageBox.prompt(
      "新密码至少 10 位；保存后该人员的全部旧登录会话会失效。",
      `重置密码 · ${row.name}`,
      {
        inputType: "password",
        inputValidator: value => value.length >= 10 || "密码至少需要 10 位",
        confirmButtonText: "下一步",
        cancelButtonText: "取消"
      }
    );
    const reason = await ElMessageBox.prompt("请填写本次重置的业务原因或批准依据。", "重置依据", {
      inputValidator: value => value.trim().length >= 6 || "至少填写 6 个字符",
      confirmButtonText: "确认重置",
      cancelButtonText: "取消",
      type: "warning"
    });
    await resetStaffPassword(row.id, {
      password: password.value,
      reason: reason.value.trim()
    });
    ElMessage.success("密码已重置，旧登录会话已失效");
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error, "密码重置失败"));
  }
}

async function openMigrationPreview() {
  migrationVisible.value = true;
  migrationLoading.value = true;
  try {
    migrationRows.value = (await getAuthorizationMigrationPreview()).data;
  } catch (error) {
    ElMessage.error(errorText(error, "无法生成迁移预览"));
  } finally {
    migrationLoading.value = false;
  }
}

function roleSummary(grants: StaffAuthorizationGrant[]) {
  return grants.slice(0, 2);
}

function scopeSummary(row: StaffRecord) {
  return row.scopes.slice(0, 2);
}

function grantLabel(grant: StaffAuthorizationGrant | GrantDraft) {
  const name = "role_name" in grant ? grant.role_name : roleByKey.value[grant.role_key]?.role_name || grant.role_key;
  const org = "org_name" in grant ? grant.org_name || grant.org_unit_id : grant.org_unit_id;
  return `${name} · ${org} · ${grant.scope_type === "SUBTREE" ? "含下级" : "仅本级"}`;
}

onMounted(() => {
  void Promise.all([loadCatalog(), loadStaff()]);
});
</script>

<template>
  <div class="staff-page">
    <section class="page-head">
      <div>
        <p>人员、账号、任职与授权一体化</p>
        <h1>专职人员管理</h1>
        <span>岗位记录职业分工；角色与管辖范围逐条绑定决定系统权限，二者不会相互替代。</span>
      </div>
      <div class="head-actions">
        <el-button plain @click="openMigrationPreview">兼容授权迁移预览</el-button>
        <el-button type="primary" :disabled="!writesEnabled" @click="openCreate">
          新增专职人员
        </el-button>
      </div>
    </section>

    <el-alert
      v-if="catalog && !writesEnabled"
      title="身份写入门禁当前关闭：可查看人员和兼容预览，不能新建、修改、停用或重置密码。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      title="敏感权限扩大须填写授权依据和业务原因；停用账号会废止全部现有登录会话，不会删除任职和授权历史。"
      type="info"
      :closable="false"
      show-icon
    />

    <el-card shadow="never" class="filter-card">
      <el-form inline>
        <el-form-item label="管辖组织">
          <el-tree-select
            v-model="filters.org_unit_id"
            :data="orgTree"
            :props="{ label: 'label', children: 'children' }"
            node-key="id"
            check-strictly
            clearable
            filterable
            placeholder="全部组织"
            style="width: 190px"
          />
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="filters.department_name" clearable filterable placeholder="全部部门" style="width: 150px">
            <el-option v-for="department in departments" :key="department" :label="department" :value="department" />
          </el-select>
        </el-form-item>
        <el-form-item label="岗位">
          <el-select v-model="filters.position_key" clearable placeholder="全部岗位" style="width: 160px">
            <el-option v-for="position in positions" :key="position.position_key" :label="position.position_name" :value="position.position_key" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="filters.role_key" clearable placeholder="全部角色" style="width: 165px">
            <el-option v-for="role in roleOptions" :key="role.role_key" :label="role.role_name" :value="role.role_key" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.is_active" clearable placeholder="全部" style="width: 104px">
            <el-option label="启用" value="true" />
            <el-option label="停用" value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-input v-model="filters.query" clearable placeholder="姓名、账号或工作手机号" style="width: 230px" @keyup.enter="loadStaff" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="loadStaff">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table v-loading="loading || catalogLoading" :data="rows" stripe empty-text="暂无专职人员">
        <el-table-column prop="name" label="姓名" min-width="118" fixed="left" />
        <el-table-column prop="login_account" label="登录账号" min-width="160" />
        <el-table-column label="工作手机号" min-width="122">
          <template #default="{ row }">{{ row.phone_masked || "未登记" }}</template>
        </el-table-column>
        <el-table-column label="机构 / 部门" min-width="175">
          <template #default="{ row }">
            <div>{{ row.institution_name }}</div>
            <span class="muted">{{ row.department_name || "未设置部门" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="岗位" min-width="160">
          <template #default="{ row }">
            <el-tag v-for="position in row.positions" :key="position.position_key" size="small" effect="plain" class="tag-gap">
              {{ position.position_name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="角色" min-width="190">
          <template #default="{ row }">
            <el-tag v-for="grant in roleSummary(row.authorization_grants)" :key="`${grant.role_key}-${grant.org_unit_id}-${grant.scope_type}`" size="small" type="success" effect="plain" class="tag-gap">
              {{ grant.role_name }}
            </el-tag>
            <el-popover v-if="row.authorization_grants.length > 2" placement="top" width="300" trigger="hover">
              <template #reference><el-tag size="small">+{{ row.authorization_grants.length - 2 }}</el-tag></template>
              <p v-for="grant in row.authorization_grants" :key="`${grant.role_key}-${grant.org_unit_id}-${grant.scope_type}`" class="popover-line">{{ grantLabel(grant) }}</p>
            </el-popover>
            <span v-if="!row.authorization_grants.length" class="muted">待配置</span>
            <el-tag v-if="row.authorization_mode === 'LEGACY_COMPATIBILITY'" size="small" type="warning" class="tag-gap">兼容解析</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="管辖范围" min-width="190">
          <template #default="{ row }">
            <el-tag v-for="scope in scopeSummary(row as StaffRecord)" :key="`${scope.org_unit_id}-${scope.scope_type}`" size="small" effect="plain" class="tag-gap">
              {{ scope.org_name || scope.org_unit_id }} · {{ scope.scope_type === "SUBTREE" ? "含下级" : "本级" }}
            </el-tag>
            <el-popover v-if="row.scopes.length > 2" placement="top" width="280" trigger="hover">
              <template #reference><el-tag size="small">+{{ row.scopes.length - 2 }}</el-tag></template>
              <p v-for="scope in row.scopes" :key="`${scope.org_unit_id}-${scope.scope_type}`" class="popover-line">{{ scope.org_name || scope.org_unit_id }} · {{ scope.scope_type === "SUBTREE" ? "含下级" : "仅本级" }}</p>
            </el-popover>
          </template>
        </el-table-column>
        <el-table-column label="账号状态" width="108">
          <template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? "启用" : "停用" }}</el-tag></template>
        </el-table-column>
        <el-table-column label="最近登录" min-width="158">
          <template #default="{ row }">{{ row.last_login_at ? dayjs(row.last_login_at).format("YYYY-MM-DD HH:mm") : "尚未登录" }}</template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="!writesEnabled" @click="openEdit(row as StaffRecord)">编辑</el-button>
            <el-button link type="primary" :disabled="!writesEnabled || !row.is_active" @click="resetPassword(row as StaffRecord)">重置密码</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drawerVisible" :title="isEditing ? '编辑专职人员' : '新增专职人员'" size="min(820px, 96vw)" destroy-on-close>
      <el-alert
        v-if="form.authorization_mode === 'LEGACY_COMPATIBILITY'"
        title="此人员当前仍按旧岗位与服务责任范围兼容解析。保持角色范围为空不会写入迁移；添加逐条角色授权后才会切换到显式授权。"
        type="warning"
        :closable="false"
        show-icon
        class="drawer-alert"
      />
      <el-form label-position="top" class="staff-form">
        <h3>基本信息</h3>
        <div class="form-grid">
          <el-form-item label="姓名" required><el-input v-model="form.name" maxlength="255" /></el-form-item>
          <el-form-item label="性别"><el-select v-model="form.gender"><el-option label="未说明" value="UNSPECIFIED" /><el-option label="男" value="MALE" /><el-option label="女" value="FEMALE" /></el-select></el-form-item>
          <el-form-item label="工作手机号（可选）" class="wide">
            <el-checkbox v-model="form.replace_phone">修改或清空工作手机号</el-checkbox>
            <el-input v-if="form.replace_phone" v-model="form.phone" maxlength="32" placeholder="留空表示清空；仅保存加密值，列表只显示脱敏号码" />
          </el-form-item>
        </div>

        <el-divider />
        <h3>登录信息</h3>
        <div class="form-grid">
          <el-form-item label="登录账号" required class="wide">
            <el-input v-model="form.login_account" :placeholder="isEditing ? `留空保持现有账号（${form.account_hint}）` : '至少 3 个字符；账号不必等于手机号'" maxlength="128" />
          </el-form-item>
          <el-form-item label="账号状态"><el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" /></el-form-item>
        </div>
        <el-form-item v-if="!isEditing" label="临时密码">
          <el-checkbox v-model="form.auto_password">由系统自动生成并且仅展示一次</el-checkbox>
          <el-input v-if="!form.auto_password" v-model="form.temporary_password" type="password" show-password maxlength="256" autocomplete="new-password" placeholder="至少 10 位" />
          <p class="form-hint">系统不会在接口返回、列表或审计中保存明文密码。</p>
        </el-form-item>

        <el-divider />
        <h3>任职信息</h3>
        <div class="form-grid">
          <el-form-item label="所属机构" required><el-select v-model="form.institution_id" filterable><el-option v-for="item in institutions" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item>
          <el-form-item label="部门"><el-input v-model="form.department_name" maxlength="255" placeholder="可选" /></el-form-item>
          <el-form-item label="岗位" required class="wide"><el-select v-model="form.position_keys" multiple filterable><el-option v-for="position in positions" :key="position.position_key" :label="position.position_name" :value="position.position_key" /></el-select><p class="form-hint">岗位用于任职与历史记录，不直接等同系统权限角色。</p></el-form-item>
          <el-form-item label="任职开始" required><el-date-picker v-model="form.started_on" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" /></el-form-item>
          <el-form-item label="任职结束（可选）"><el-date-picker v-model="form.ended_on" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" clearable /></el-form-item>
          <el-form-item label="上级负责人（可选）" class="wide"><el-select v-model="form.supervisor_user_id" clearable filterable><el-option v-for="person in supervisors" :key="person.id" :label="`${person.name} · ${person.institution_name}`" :value="person.id" /></el-select></el-form-item>
        </div>

        <el-divider />
        <div class="section-title"><h3>角色与管辖范围</h3><el-button type="primary" plain @click="addGrant">添加授权</el-button></div>
        <p class="form-hint">每行是一条独立授权：角色只在本行组织范围内生效。系统管理员、技术管理员及受限角色不在此处提供。</p>
        <div v-if="!form.grants.length" class="empty-grants">尚未配置显式角色范围授权。</div>
        <div v-for="(grant, index) in form.grants" :key="index" class="grant-row">
          <el-select v-model="grant.role_key" filterable placeholder="选择角色"><el-option v-for="role in roleOptions" :key="role.role_key" :label="role.role_name" :value="role.role_key" /></el-select>
          <el-tree-select v-model="grant.org_unit_id" :data="orgTree" :props="{ label: 'label', children: 'children' }" node-key="id" check-strictly filterable placeholder="选择组织" />
          <el-select v-model="grant.scope_type"><el-option label="仅本级" value="UNIT" /><el-option label="本级及下级" value="SUBTREE" /></el-select>
          <el-button text type="primary" :disabled="!grant.role_key" @click="showPermission(grant.role_key)">权限预览</el-button>
          <el-button text type="danger" @click="removeGrant(index)">移除</el-button>
          <el-date-picker v-model="grant.valid_from" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="授权开始（默认任职开始）" />
          <el-date-picker v-model="grant.valid_until" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="授权结束（默认任职结束）" clearable />
        </div>

        <el-divider />
        <h3>确认与审计</h3>
        <el-form-item label="授权依据" required><el-input v-model="form.authorization_basis" maxlength="500" placeholder="例如：岗位任命、分中心授权或审批编号" /></el-form-item>
        <el-form-item label="敏感权限扩大业务原因"><el-input v-model="form.authorization_reason" type="textarea" :rows="3" maxlength="1000" placeholder="新增敏感权限时必填，至少 8 个字符；不会写入密码等敏感原文" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="drawerVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">先预览并确认保存</el-button></template>
    </el-drawer>

    <el-dialog v-model="permissionVisible" :title="`${permissionRole?.role_name || '角色'} · 权限预览`" width="min(680px, 94vw)">
      <el-alert title="此处仅供预览；权限由角色与本页逐条管辖范围共同决定。" type="info" :closable="false" show-icon />
      <el-table :data="permissionRole?.permissions || []" max-height="420">
        <el-table-column prop="permission_name" label="权限" min-width="220" />
        <el-table-column prop="permission_key" label="权限标识" min-width="220" />
        <el-table-column label="敏感级别" width="110"><template #default="{ row }"><el-tag :type="row.sensitive_level === 'RESTRICTED' ? 'danger' : row.sensitive_level === 'SENSITIVE' ? 'warning' : 'info'">{{ row.sensitive_level }}</el-tag></template></el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="migrationVisible" title="兼容授权迁移预览" width="min(1040px, 96vw)">
      <el-alert title="本功能只计算旧岗位与服务责任范围到显式角色范围授权的等价结果；不会写入、不会自动迁移。请逐项复核后另行受控执行。" type="warning" :closable="false" show-icon />
      <el-table v-loading="migrationLoading" :data="migrationRows" max-height="560" empty-text="没有待评估的旧兼容任职记录">
        <el-table-column prop="name" label="人员" min-width="140" />
        <el-table-column label="当前岗位" min-width="180"><template #default="{ row }">{{ row.current_positions.map(item => item.position_name).join('、') || '无' }}</template></el-table-column>
        <el-table-column label="拟授权（已去重）" min-width="260"><template #default="{ row }">{{ row.proposed_authorization_grants.map((item: StaffAuthorizationGrant) => grantLabel(item)).join('；') || '无' }}</template></el-table-column>
        <el-table-column label="权限差异" min-width="180"><template #default="{ row }">{{ row.permission_diff.added.length || row.permission_diff.removed.length ? `新增 ${row.permission_diff.added.length}，移除 ${row.permission_diff.removed.length}` : '等价' }}</template></el-table-column>
        <el-table-column label="结论" width="140"><template #default="{ row }"><el-tag :type="row.status === 'SAFE_TO_MIGRATE' ? 'success' : 'warning'">{{ row.status === 'SAFE_TO_MIGRATE' ? '可受控迁移' : '需人工复核' }}</el-tag></template></el-table-column>
        <el-table-column label="说明" min-width="190"><template #default="{ row }">{{ row.blockers.join('；') || (row.deduplicated_grant_count ? `已自动去重 ${row.deduplicated_grant_count} 条重复组合` : '权限集合等价') }}</template></el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.staff-page { display: grid; gap: 18px; padding: 20px; }
.page-head { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding: 28px; color: #f7fbff; background: linear-gradient(125deg, #17324d, #2b6d83); border-radius: 18px; }
.page-head p { margin: 0 0 8px; color: #a9dbe5; letter-spacing: 0.13em; }
.page-head h1 { margin: 0 0 10px; font-size: 28px; }
.page-head span { color: #d3eaf0; }
.head-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.filter-card :deep(.el-form-item) { margin-bottom: 0; }
.tag-gap { margin: 0 4px 4px 0; }
.muted, .form-hint { color: var(--el-text-color-secondary); font-size: 12px; }
.form-hint { margin: 6px 0 0; line-height: 1.55; }
.popover-line { margin: 0 0 8px; line-height: 1.45; }
.drawer-alert { margin-bottom: 18px; }
.staff-form h3 { margin: 0 0 14px; color: var(--el-text-color-primary); font-size: 16px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.form-grid .wide { grid-column: 1 / -1; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.empty-grants { padding: 16px; color: var(--el-text-color-secondary); background: var(--el-fill-color-lighter); border-radius: 8px; }
.grant-row { display: grid; grid-template-columns: minmax(140px, 1fr) minmax(150px, 1fr) 120px auto auto; gap: 10px; align-items: center; padding: 12px; margin-bottom: 10px; background: var(--el-fill-color-lighter); border-radius: 8px; }
.grant-row :deep(.el-date-editor) { width: 100%; }
@media (max-width: 840px) { .page-head { align-items: flex-start; flex-direction: column; } .form-grid, .grant-row { grid-template-columns: 1fr; } .form-grid .wide { grid-column: auto; } }
</style>
