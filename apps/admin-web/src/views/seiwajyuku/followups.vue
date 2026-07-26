<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  closeFollowupTask,
  createFollowupRecord,
  createFollowupTask,
  createVisitRecord,
  getFollowupAssignees,
  getFollowupTasks,
  getMemberDetail,
  getMembers,
  revealMemberContact,
  type FollowupAssignee,
  type FollowupTask,
  type Member
} from "@/api/seiwajyuku";
import { useUserStoreHook } from "@/store/modules/user";

defineOptions({ name: "FollowupTasks" });

type DialogMode = "create" | "record" | "visit";

const RECORD_DRAFT_KEY = "seiwajyuku-followup-record-draft-v1";
const VISIT_DRAFT_KEY = "seiwajyuku-followup-visit-draft-v1";

const loading = ref(false);
const saving = ref(false);
const status = ref<string>();
const rows = ref<FollowupTask[]>([]);
const members = ref<Member[]>([]);
const assignees = ref<FollowupAssignee[]>([]);
const dialogVisible = ref(false);
const dialogMode = ref<DialogMode>("create");
const activeTask = ref<FollowupTask>();

const openCount = computed(
  () => rows.value.filter(item => item.status !== "CLOSED").length
);
const canViewFullProfile = computed(() =>
  useUserStoreHook().roles.some(role =>
    ["system_admin", "operations_admin", "regional_manager"].includes(role)
  )
);
const dialogTitle = computed(() => {
  if (dialogMode.value === "create") return "创建关怀任务";
  if (dialogMode.value === "record") return `记录跟进 · ${activeTask.value?.member_name}`;
  return `记录企业走访 · ${activeTask.value?.member_name}`;
});

const statusText: Record<string, string> = {
  OPEN: "待执行",
  IN_PROGRESS: "跟进中",
  CLOSED: "已关闭"
};
const channelOptions = [
  ["PHONE", "电话"],
  ["WECHAT", "微信"],
  ["MEETING", "面谈"],
  ["COURSE", "课程"],
  ["OTHER", "其他"]
];
const outcomeOptions = [
  ["CONNECTED", "已联系"],
  ["NO_ANSWER", "未接通"],
  ["DECLINED", "暂不接受"],
  ["RESCHEDULED", "另约时间"],
  ["COMPLETED", "已完成"],
  ["OTHER", "其他"]
];

const taskForm = reactive({
  member_id: undefined as number | undefined,
  task_type: "CARE",
  service_purpose: "",
  assigned_user_id: undefined as number | undefined,
  due_at: "",
  confidentiality_level: "ASSIGNEE"
});
const servicePurposeLength = computed(
  () => taskForm.service_purpose.trim().length
);
const servicePurposeError = computed(() => {
  if (!taskForm.service_purpose.trim()) return "";
  return servicePurposeLength.value < 4
    ? `服务目的至少填写 4 个字符，当前 ${servicePurposeLength.value} 个`
    : "";
});
const recordForm = reactive({
  channel: "PHONE",
  contacted_at: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
  outcome_code: "CONNECTED",
  subject_statement: "",
  objective_facts: "",
  staff_judgment: "",
  next_action: "",
  next_followup_at: ""
});
const visitForm = reactive({
  appointment_at: "",
  visited_at: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
  purpose: "",
  participants_text: "",
  location_type: "ENTERPRISE",
  objective_facts: "",
  expressed_needs: "",
  support_provided: "",
  staff_judgment: "",
  next_action: "",
  next_followup_at: ""
});

function readDraft(key: string, taskId: number) {
  try {
    const draft = JSON.parse(sessionStorage.getItem(key) || "null") as {
      task_id?: number;
      values?: Record<string, string>;
    } | null;
    return draft?.task_id === taskId ? draft.values : undefined;
  } catch {
    sessionStorage.removeItem(key);
    return undefined;
  }
}

function saveDraft(
  key: string,
  taskId: number,
  values: Record<string, string>
) {
  sessionStorage.setItem(
    key,
    JSON.stringify({ task_id: taskId, values, saved_at: Date.now() })
  );
}

function saveActiveDraft() {
  if (!activeTask.value || !dialogVisible.value) return;
  if (dialogMode.value === "record") {
    saveDraft(RECORD_DRAFT_KEY, activeTask.value.id, { ...recordForm });
  }
  if (dialogMode.value === "visit") {
    saveDraft(VISIT_DRAFT_KEY, activeTask.value.id, { ...visitForm });
  }
}

watch(recordForm, saveActiveDraft, { deep: true });
watch(visitForm, saveActiveDraft, { deep: true });

function errorText(error: any, fallback = "操作失败") {
  const detail = error?.response?.data?.detail;
  if (detail) return detail;
  if (error?.code === "ECONNABORTED") {
    return "填写超时，结果可能已保存。请刷新任务列表确认后再重试，避免重复提交";
  }
  if (error?.message === "Network Error") {
    return "网络连接中断，操作结果暂时无法确认。请刷新任务列表后再决定是否重试";
  }
  return error?.message || fallback;
}

async function load() {
  loading.value = true;
  try {
    const [tasks, memberResult] = await Promise.all([
      getFollowupTasks(status.value),
      getMembers()
    ]);
    rows.value = tasks.data;
    members.value = memberResult.data;
  } catch (error) {
    ElMessage.error(errorText(error, "加载关怀数据失败，请刷新页面后重试"));
  } finally {
    loading.value = false;
  }
}

async function openCreate() {
  Object.assign(taskForm, {
    member_id: undefined,
    task_type: "CARE",
    service_purpose: "",
    assigned_user_id: undefined,
    due_at: "",
    confidentiality_level: "ASSIGNEE"
  });
  assignees.value = [];
  dialogMode.value = "create";
  dialogVisible.value = true;
}

async function memberChanged(memberId?: number) {
  taskForm.assigned_user_id = undefined;
  const member = members.value.find(item => item.id === memberId);
  if (!member) {
    assignees.value = [];
    return;
  }
  try {
    const response = await getFollowupAssignees(member.org_unit_id);
    assignees.value = response.data;
  } catch (error) {
    ElMessage.error(errorText(error));
  }
}

function openRecord(task: any) {
  activeTask.value = task as FollowupTask;
  Object.assign(recordForm, {
    channel: "PHONE",
    contacted_at: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
    outcome_code: "CONNECTED",
    subject_statement: "",
    objective_facts: "",
    staff_judgment: "",
    next_action: "",
    next_followup_at: ""
  });
  dialogMode.value = "record";
  dialogVisible.value = true;
  const draft = readDraft(RECORD_DRAFT_KEY, activeTask.value.id);
  if (draft) {
    Object.assign(recordForm, draft);
    ElMessage.info("已恢复本次跟进的未确认填写内容");
  }
}

function openVisit(task: any) {
  activeTask.value = task as FollowupTask;
  Object.assign(visitForm, {
    appointment_at: "",
    visited_at: dayjs().format("YYYY-MM-DDTHH:mm:ss"),
    purpose: "",
    participants_text: "",
    location_type: "ENTERPRISE",
    objective_facts: "",
    expressed_needs: "",
    support_provided: "",
    staff_judgment: "",
    next_action: "",
    next_followup_at: ""
  });
  dialogMode.value = "visit";
  dialogVisible.value = true;
  const draft = readDraft(VISIT_DRAFT_KEY, activeTask.value.id);
  if (draft) {
    Object.assign(visitForm, draft);
    ElMessage.info("已恢复本次走访的未确认填写内容");
  }
}

async function submit() {
  saving.value = true;
  try {
    if (dialogMode.value === "create") {
      if (!taskForm.member_id) throw new Error("请选择学员");
      if (servicePurposeLength.value < 4) {
        throw new Error(
          `服务目的至少填写 4 个字符，当前 ${servicePurposeLength.value} 个`
        );
      }
      if (!taskForm.assigned_user_id) throw new Error("请选择责任人");
      await createFollowupTask({
        member_id: taskForm.member_id,
        task_type: taskForm.task_type,
        service_purpose: taskForm.service_purpose.trim(),
        assigned_user_id: taskForm.assigned_user_id,
        due_at: taskForm.due_at || undefined,
        confidentiality_level: taskForm.confidentiality_level
      });
      ElMessage.success("关怀任务已创建");
    } else if (dialogMode.value === "record" && activeTask.value) {
      if (
        !recordForm.contacted_at ||
        ![
          recordForm.subject_statement,
          recordForm.objective_facts,
          recordForm.staff_judgment
        ].some(value => value.trim())
      ) {
        throw new Error("请填写联系时间，并至少填写一项联系内容");
      }
      saveActiveDraft();
      await createFollowupRecord(activeTask.value.id, {
        channel: recordForm.channel,
        contacted_at: recordForm.contacted_at,
        outcome_code: recordForm.outcome_code,
        subject_statement: recordForm.subject_statement.trim() || undefined,
        objective_facts: recordForm.objective_facts.trim() || undefined,
        staff_judgment: recordForm.staff_judgment.trim() || undefined,
        next_action: recordForm.next_action.trim() || undefined,
        next_followup_at: recordForm.next_followup_at || undefined
      });
      sessionStorage.removeItem(RECORD_DRAFT_KEY);
      ElMessage.success("跟进记录已保存");
    } else if (activeTask.value) {
      if (
        !visitForm.visited_at ||
        !visitForm.purpose.trim() ||
        !visitForm.objective_facts.trim()
      ) {
        throw new Error("请填写走访时间、目的和客观事实");
      }
      saveActiveDraft();
      await createVisitRecord(activeTask.value.id, {
        appointment_at: visitForm.appointment_at || undefined,
        visited_at: visitForm.visited_at,
        purpose: visitForm.purpose.trim(),
        participants: visitForm.participants_text
          .split(/[、,，]/)
          .map(item => item.trim())
          .filter(Boolean),
        location_type: visitForm.location_type,
        objective_facts: visitForm.objective_facts.trim(),
        expressed_needs: visitForm.expressed_needs.trim() || undefined,
        support_provided: visitForm.support_provided.trim() || undefined,
        staff_judgment: visitForm.staff_judgment.trim() || undefined,
        next_action: visitForm.next_action.trim() || undefined,
        next_followup_at: visitForm.next_followup_at || undefined
      });
      sessionStorage.removeItem(VISIT_DRAFT_KEY);
      ElMessage.success("走访记录已保存");
    }
    dialogVisible.value = false;
    await load();
  } catch (error) {
    ElMessage.error(errorText(error, "保存失败，请稍后重试"));
  } finally {
    saving.value = false;
  }
}

async function reveal(task: any) {
  if (canViewFullProfile.value) {
    try {
      const response = await getMemberDetail(task.member_id);
      const profile = response.data;
      const fields = [
        ["姓名", profile.name],
        ["手机号", profile.phone],
        ["所属中心", profile.org_name],
        ["班级 / 小组", [profile.class_name, profile.group_name].filter(Boolean).join(" / ")],
        ["职务", profile.position],
        ["公司名称", profile.company_name],
        ["公司地址", profile.company_address],
        ["行业", [profile.industry_category, profile.industry].filter(Boolean).join(" / ")],
        ["公司产品", profile.company_products],
        ["公司规模", profile.company_size],
        ["年销售额", profile.annual_sales],
        ["利润率", profile.profit_margin],
        ["备注", profile.notes]
      ].filter(([, value]) => value !== null && value !== undefined && value !== "");
      await ElMessageBox.alert(
        h(
          "div",
          { class: "profile-detail" },
          fields.map(([label, value]) =>
            h("p", [h("strong", `${label}：`), String(value)])
          )
        ),
        "学员完整资料",
        { confirmButtonText: "关闭" }
      );
    } catch (error) {
      ElMessage.error(errorText(error, "读取完整资料失败，请稍后重试"));
    }
    return;
  }
  try {
    const { value } = await ElMessageBox.prompt(
      "完整手机号只用于当前任务联系，并将记录访问用途和审计日志。",
      `查看 ${task.member_name} 的联系方式`,
      {
        inputPlaceholder: "填写具体联系用途（至少 4 个字）",
        inputValidator: value => value.trim().length >= 4 || "用途至少填写 4 个字",
        confirmButtonText: "确认查看",
        cancelButtonText: "取消"
      }
    );
    const response = await revealMemberContact(task.member_id, {
      task_id: task.id,
      purpose: value.trim(),
      client_reference: "admin-web"
    });
    await ElMessageBox.alert(
      `${response.data.name}：${response.data.phone}`,
      "联系方式（60 秒内使用）",
      { confirmButtonText: "已记录" }
    );
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

async function closeTask(task: any) {
  try {
    const { value } = await ElMessageBox.prompt(
      "至少保存过一次电话、面谈或企业走访结果后才能关闭。",
      `关闭任务 · ${task.member_name}`,
      {
        inputPlaceholder: "填写关闭说明（至少 4 个字）",
        inputValidator: value => value.trim().length >= 4 || "说明至少填写 4 个字",
        confirmButtonText: "确认关闭",
        cancelButtonText: "取消"
      }
    );
    await closeFollowupTask(task.id, value.trim());
    ElMessage.success("任务已关闭");
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
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
      <div class="head-actions">
        <el-select
          v-model="status"
          clearable
          placeholder="全部状态"
          @change="load"
        >
          <el-option label="待执行" value="OPEN" />
          <el-option label="跟进中" value="IN_PROGRESS" />
          <el-option label="已关闭" value="CLOSED" />
        </el-select>
        <el-button type="primary" size="large" @click="openCreate">
          创建任务
        </el-button>
      </div>
    </section>

    <el-alert
      :title="`当前显示 ${rows.length} 项，其中 ${openCount} 项尚未关闭`"
      type="info"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <el-table :data="rows" stripe empty-text="暂无关怀任务，请先在学员管理中建立试点学员">
        <el-table-column prop="member_name" label="学员" min-width="105" fixed />
        <el-table-column prop="phone_masked" label="联系方式" min-width="135" />
        <el-table-column prop="company_name" label="企业" min-width="150">
          <template #default="{ row }">{{ row.company_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="org_name" label="所属中心" min-width="135" />
        <el-table-column prop="service_purpose" label="服务目的" min-width="220" show-overflow-tooltip />
        <el-table-column prop="assignee_name" label="责任人" min-width="105" />
        <el-table-column label="状态" width="95">
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
        <el-table-column prop="next_followup_at" label="下次跟进" min-width="170">
          <template #default="{ row }">
            {{ row.next_followup_at ? dayjs(row.next_followup_at).format("YYYY-MM-DD HH:mm") : "—" }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="315" fixed="right">
          <template #default="{ row }">
            <div v-if="row.status !== 'CLOSED' && row.can_record" class="row-actions">
              <el-button link type="primary" @click="reveal(row)">
                {{ canViewFullProfile ? "查看资料" : "联系" }}
              </el-button>
              <el-button link type="primary" @click="openRecord(row)">记跟进</el-button>
              <el-button link type="primary" @click="openVisit(row)">记走访</el-button>
              <el-button link type="danger" @click="closeTask(row)">关闭</el-button>
            </div>
            <span v-else class="muted">
              {{ row.status === "CLOSED" ? "已完成" : "仅责任人可操作" }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="720px">
      <el-form v-if="dialogMode === 'create'" :model="taskForm" label-position="top">
        <div class="form-grid">
          <el-form-item label="学员">
            <el-select
              v-model="taskForm.member_id"
              filterable
              placeholder="选择试点学员"
              @change="memberChanged"
            >
              <el-option
                v-for="member in members"
                :key="member.id"
                :label="`${member.name} · ${member.org_name} · ${member.phone_masked}`"
                :value="member.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="任务类型">
            <el-select v-model="taskForm.task_type">
              <el-option label="日常关怀" value="CARE" />
              <el-option label="电话回访" value="PHONE" />
              <el-option label="企业走访" value="VISIT" />
            </el-select>
          </el-form-item>
          <el-form-item
            class="full"
            label="服务目的（至少 4 个字符）"
            :error="servicePurposeError || undefined"
          >
            <el-input
              v-model="taskForm.service_purpose"
              type="textarea"
              :rows="3"
              maxlength="1000"
              show-word-limit
              placeholder="至少填写 4 个字符，说明本次联系要解决或了解的事项"
            />
          </el-form-item>
          <el-form-item label="责任人">
            <el-select
              v-model="taskForm.assigned_user_id"
              placeholder="先选择学员"
              :disabled="!taskForm.member_id"
            >
              <el-option
                v-for="user in assignees"
                :key="user.id"
                :label="user.display_name"
                :value="user.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="截止时间">
            <el-date-picker
              v-model="taskForm.due_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="可选"
            />
          </el-form-item>
          <el-form-item label="保密范围">
            <el-select v-model="taskForm.confidentiality_level">
              <el-option label="仅责任人可见" value="ASSIGNEE" />
              <el-option label="组织管理人员可见" value="ORG_MANAGERS" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>

      <el-form v-else-if="dialogMode === 'record'" :model="recordForm" label-position="top">
        <div class="form-grid">
          <el-form-item label="联系渠道">
            <el-select v-model="recordForm.channel">
              <el-option v-for="[value, label] in channelOptions" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="联系时间">
            <el-date-picker v-model="recordForm.contacted_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
          </el-form-item>
          <el-form-item label="联系结果">
            <el-select v-model="recordForm.outcome_code">
              <el-option v-for="[value, label] in outcomeOptions" :key="value" :label="label" :value="value" />
            </el-select>
          </el-form-item>
          <el-form-item label="下次跟进">
            <el-date-picker v-model="recordForm.next_followup_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="可选" />
          </el-form-item>
          <el-form-item class="full" label="学员陈述">
            <el-input v-model="recordForm.subject_statement" type="textarea" :rows="2" placeholder="学员本人表达的需求或情况" />
          </el-form-item>
          <el-form-item class="full" label="客观事实">
            <el-input v-model="recordForm.objective_facts" type="textarea" :rows="2" placeholder="已经确认的客观信息" />
          </el-form-item>
          <el-form-item class="full" label="工作人员判断">
            <el-input v-model="recordForm.staff_judgment" type="textarea" :rows="2" placeholder="与客观事实分开填写" />
          </el-form-item>
          <el-form-item class="full" label="下一步行动">
            <el-input v-model="recordForm.next_action" />
          </el-form-item>
        </div>
      </el-form>

      <el-form v-else :model="visitForm" label-position="top">
        <div class="form-grid">
          <el-form-item label="预约时间">
            <el-date-picker v-model="visitForm.appointment_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="可选" />
          </el-form-item>
          <el-form-item label="实际走访时间">
            <el-date-picker v-model="visitForm.visited_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
          </el-form-item>
          <el-form-item label="地点类型">
            <el-select v-model="visitForm.location_type">
              <el-option label="学员企业" value="ENTERPRISE" />
              <el-option label="盛和塾场地" value="SEIWAJYUKU" />
              <el-option label="线上会议" value="ONLINE" />
              <el-option label="其他" value="OTHER" />
            </el-select>
          </el-form-item>
          <el-form-item label="参与人员">
            <el-input v-model="visitForm.participants_text" placeholder="用逗号分隔" />
          </el-form-item>
          <el-form-item class="full" label="走访目的">
            <el-input v-model="visitForm.purpose" />
          </el-form-item>
          <el-form-item class="full" label="客观事实">
            <el-input v-model="visitForm.objective_facts" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item label="表达需求">
            <el-input v-model="visitForm.expressed_needs" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="已提供支持">
            <el-input v-model="visitForm.support_provided" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="工作人员判断">
            <el-input v-model="visitForm.staff_judgment" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="下一步行动">
            <el-input v-model="visitForm.next_action" type="textarea" :rows="2" />
          </el-form-item>
          <el-form-item label="下次跟进">
            <el-date-picker v-model="visitForm.next_followup_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="可选" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
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
.head-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}
.head-actions :deep(.el-select) {
  width: 150px;
}
.row-actions {
  white-space: nowrap;
}
.muted {
  color: var(--el-text-color-secondary);
}
.profile-detail {
  max-height: 55vh;
  overflow-y: auto;
  line-height: 1.7;
}
.profile-detail p {
  margin: 0 0 8px;
  overflow-wrap: anywhere;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}
.form-grid .full {
  grid-column: 1 / -1;
}
.form-grid :deep(.el-select),
.form-grid :deep(.el-date-editor) {
  width: 100%;
}
@media (max-width: 760px) {
  .page-head {
    align-items: flex-start;
    gap: 20px;
  }
  .head-actions {
    flex-direction: column;
    align-items: stretch;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
  .form-grid .full {
    grid-column: auto;
  }
}
</style>
