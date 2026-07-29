<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  changeIdentityAssignmentStatus,
  createAccountEmployment,
  createAccountTechnicalAssignment,
  createAccountVolunteerAppointment,
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
const dialogMode = ref<DialogMode>("initialize");
const activeAccount = ref<IdentityAccount>();

const form = reactive({
  source_reference: "",
  confirmation_note: "",
  position_key: "",
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
const writesEnabled = computed(() => catalog.value?.writes_enabled === true);
const dialogTitle = computed(() => {
  const name = activeAccount.value?.display_name || "";
  return {
    initialize: `确认自然人关联 · ${name}`,
    employment: `建立运营中心雇佣 · ${name}`,
    volunteer: `建立志工任职 · ${name}`,
    technical: `建立技术管理员任期 · ${name}`
  }[dialogMode.value];
});

function errorText(error: any, fallback = "操作失败") {
  return error?.response?.data?.detail || error?.message || fallback;
}

function resetForm() {
  Object.assign(form, {
    source_reference: "",
    confirmation_note: "",
    position_key: "",
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

async function load() {
  loading.value = true;
  unavailableMessage.value = "";
  try {
    const [catalogResult, accountResult, orgResult] = await Promise.all([
      getIdentityCatalog(),
      getIdentityAccounts(),
      getIdentityOrgOptions()
    ]);
    catalog.value = catalogResult.data;
    rows.value = accountResult.data;
    orgs.value = orgResult.data;
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
        position_key: form.position_key,
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
        <span>自然人、专职雇佣、服务责任、志工任职和技术职责分别记录。</span>
      </div>
      <el-input v-model="keyword" clearable placeholder="搜索账号或姓名" />
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
        <el-table-column label="自然人关联" min-width="185">
          <template #default="{ row }">
            <el-tag :type="row.person_id ? 'success' : 'warning'">
              {{ row.person_id ? "已确认关联" : "待确认" }}
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
              v-if="!row.person_id"
              link
              type="primary"
              :disabled="!writesEnabled"
              @click="openDialog('initialize', row)"
            >
              确认自然人
            </el-button>
            <template v-else>
              <el-button link :disabled="!writesEnabled" @click="openDialog('employment', row)">
                建立雇佣
              </el-button>
              <el-button link :disabled="!writesEnabled" @click="openDialog('volunteer', row)">
                建立志工任职
              </el-button>
              <el-button link :disabled="!writesEnabled" @click="openDialog('technical', row)">
                建立技术任期
              </el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="680px">
      <el-form :model="form" label-position="top">
        <template v-if="dialogMode === 'employment'">
          <el-form-item label="运营中心岗位">
            <el-select v-model="form.position_key" placeholder="请选择已确认岗位">
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
@media (max-width: 900px) {
  .page-head,
  .assignment-grid {
    grid-template-columns: 1fr;
  }
  .page-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .assignment-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
