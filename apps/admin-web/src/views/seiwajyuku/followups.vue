<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from "vue";
import dayjs from "dayjs";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import {
  acceptFollowupInvitation,
  closeFollowupTask,
  createFollowupInvitation,
  createFollowupRecord,
  createFollowupTask,
  createVisitRecord,
  getFollowupAssignees,
  getFollowupCapabilities,
  getFollowupTasks,
  getMemberDetail,
  getMembers,
  getMyFollowupInvitations,
  markFollowupInvitationUnavailable,
  revealMemberContact,
  requestFollowupInvitationAdjustment,
  type FollowupAssignee,
  type FollowupInvitation,
  type FollowupTask,
  type Member
} from "@/api/seiwajyuku";
import { useUserStoreHook } from "@/store/modules/user";
import {
  adaptVolunteerMessage,
  productCopy,
  productLanguageContext
} from "@/utils/productLanguage";

defineOptions({ name: "FollowupTasks" });

type DialogMode = "create" | "record" | "visit";

const RECORD_DRAFT_KEY = "seiwajyuku-followup-record-draft-v1";
const VISIT_DRAFT_KEY = "seiwajyuku-followup-visit-draft-v1";

const loading = ref(false);
const saving = ref(false);
const status = ref<string>();
const rows = ref<FollowupTask[]>([]);
const invitations = ref<FollowupInvitation[]>([]);
const invitationEnabled = ref(false);
const members = ref<Member[]>([]);
const assignees = ref<FollowupAssignee[]>([]);
const dialogVisible = ref(false);
const dialogMode = ref<DialogMode>("create");
const activeTask = ref<FollowupTask>();
const route = useRoute();
const router = useRouter();
const companionDialogVisible = ref(false);
const companionInvitation = ref<FollowupInvitation>();

const openCount = computed(
  () => rows.value.filter(item => item.status !== "CLOSED").length
);
const actionableInvitations = computed(() =>
  invitations.value.filter(item =>
    ["PENDING", "ADJUSTMENT_REQUESTED", "ACCEPTED"].includes(item.status)
  )
);
const languageContext = computed(() =>
  productLanguageContext(useUserStoreHook().roles)
);
const copy = computed(() => productCopy(languageContext.value));
const canViewFullProfile = computed(() =>
  useUserStoreHook().permissions.includes("members:enterprise_view")
);
const dialogTitle = computed(() => {
  if (dialogMode.value === "create") return copy.value.createTitle;
  if (dialogMode.value === "record")
    return `记录跟进 · ${activeTask.value?.member_name}`;
  return `记录企业走访 · ${activeTask.value?.member_name}`;
});

async function returnToDashboardIfRequested() {
  if (String(route.query.return_to || "") !== "/operations/dashboard") {
    return;
  }
  await router.push({ path: "/operations/dashboard" });
}

const statusText = computed<Record<string, string>>(() => ({
  OPEN: copy.value.open,
  IN_PROGRESS: copy.value.inProgress,
  CLOSED: copy.value.closed
}));
const invitationStatusText: Record<string, string> = {
  PENDING: "等待您的回应",
  ACCEPTED: "已温暖接受",
  ADJUSTMENT_REQUESTED: "已建议调整时间",
  UNAVAILABLE: "本次暂时无法参与",
  CANCELLED: "邀请已撤回",
  EXPIRED: "邀请已过有效期"
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
  confidentiality_level: "ASSIGNEE",
  invitation_mode: false,
  invitation_message: "",
  invitation_valid_until: ""
});
const companionForm = reactive({
  invited_user_id: undefined as number | undefined,
  invitation_message: "",
  valid_until: ""
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
  if (detail) {
    return languageContext.value === "VOLUNTEER"
      ? adaptVolunteerMessage(detail)
      : detail;
  }
  if (error?.code === "ECONNABORTED") {
    return "填写超时，结果可能已保存。请刷新任务列表确认后再重试，避免重复提交";
  }
  if (error?.message === "Network Error") {
    return "网络连接中断，操作结果暂时无法确认。请刷新任务列表后再决定是否重试";
  }
  const message = error?.message || fallback;
  return languageContext.value === "VOLUNTEER"
    ? adaptVolunteerMessage(message)
    : message;
}

async function load() {
  loading.value = true;
  try {
    const [tasks, memberResult, capabilities, invitationResult] =
      await Promise.all([
        getFollowupTasks(status.value),
        getMembers(),
        getFollowupCapabilities(),
        getMyFollowupInvitations()
      ]);
    rows.value = tasks.data;
    members.value = memberResult.data;
    invitationEnabled.value = capabilities.data.enabled;
    invitations.value = invitationResult.data;
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
    confidentiality_level: "ASSIGNEE",
    invitation_mode: false,
    invitation_message: "",
    invitation_valid_until: ""
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
      if (!taskForm.assigned_user_id)
        throw new Error(`请选择${copy.value.assignee}`);
      await createFollowupTask({
        member_id: taskForm.member_id,
        task_type: taskForm.task_type,
        service_purpose: taskForm.service_purpose.trim(),
        assigned_user_id: taskForm.assigned_user_id,
        due_at: taskForm.due_at || undefined,
        confidentiality_level: taskForm.confidentiality_level,
        invitation_mode: taskForm.invitation_mode,
        invitation_message: taskForm.invitation_message.trim() || undefined,
        invitation_valid_until: taskForm.invitation_valid_until || undefined
      });
      ElMessage.success(copy.value.created);
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
    await returnToDashboardIfRequested();
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
        [
          "班级 / 小组",
          [profile.class_name, profile.group_name].filter(Boolean).join(" / ")
        ],
        ["职务", profile.position],
        ["公司名称", profile.company_name],
        ["公司地址", profile.company_address],
        [
          "行业",
          [profile.industry_category, profile.industry]
            .filter(Boolean)
            .join(" / ")
        ],
        ["公司产品", profile.company_products],
        [
          "员工人数",
          profile.employee_count ? `${profile.employee_count}人` : null
        ],
        [
          "公司销售额",
          profile.annual_sales ? `${profile.annual_sales}万元` : null
        ],
        ["利润率", profile.profit_margin],
        ["备注", profile.notes]
      ].filter(
        ([, value]) => value !== null && value !== undefined && value !== ""
      );
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
        inputValidator: value =>
          value.trim().length >= 4 || "用途至少填写 4 个字",
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
      `${copy.value.closeTitle} · ${task.member_name}`,
      {
        inputPlaceholder: `填写${copy.value.closeNote}（至少 4 个字）`,
        inputValidator: value =>
          value.trim().length >= 4 || "说明至少填写 4 个字",
        confirmButtonText: copy.value.close,
        cancelButtonText: "取消"
      }
    );
    await closeFollowupTask(task.id, value.trim());
    ElMessage.success(copy.value.closeSuccess);
    await load();
    await returnToDashboardIfRequested();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

async function acceptInvitation(invitation: FollowupInvitation) {
  try {
    await ElMessageBox.confirm(
      "感谢您的回应。接受后，这项服务会进入您的待办，并按有效任职范围开放必要信息。",
      `接受担当 · ${invitation.member_name}`,
      { confirmButtonText: "愿意担当", cancelButtonText: "再想一想" }
    );
    await acceptFollowupInvitation(invitation.id);
    ElMessage.success("感谢担当，服务事项已加入您的待办");
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

async function suggestAnotherTime(invitation: FollowupInvitation) {
  try {
    const { value: time } = await ElMessageBox.prompt(
      "请输入您更从容、合适的时间，例如 2026-08-05 18:00。",
      "建议调整完成时间",
      {
        inputPlaceholder: "YYYY-MM-DD HH:mm",
        inputValidator: value => dayjs(value).isValid() || "请输入有效时间",
        confirmButtonText: "下一步",
        cancelButtonText: "取消"
      }
    );
    const { value: note } = await ElMessageBox.prompt(
      "简单说明即可，方便发起人理解和协调。",
      "补充说明",
      {
        inputPlaceholder: "例如：本周已有服务安排",
        inputValidator: value =>
          value.trim().length >= 2 || "请至少填写 2 个字",
        confirmButtonText: "发送建议",
        cancelButtonText: "取消"
      }
    );
    await requestFollowupInvitationAdjustment(
      invitation.id,
      dayjs(time).format("YYYY-MM-DDTHH:mm:ss"),
      note.trim()
    );
    ElMessage.success("时间建议已温暖地送达发起人");
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

async function temporarilyUnavailable(invitation: FollowupInvitation) {
  try {
    const { value } = await ElMessageBox.prompt(
      "每个人都有需要留白的时候。简单说明即可，不会展示参与率或排名。",
      "本次暂时无法参与",
      {
        inputPlaceholder: "例如：本周时间暂时无法妥善安排",
        inputValidator: value =>
          value.trim().length >= 2 || "请至少填写 2 个字",
        confirmButtonText: "温暖回应",
        cancelButtonText: "取消"
      }
    );
    await markFollowupInvitationUnavailable(invitation.id, value.trim());
    ElMessage.success("回应已送达，感谢您的坦诚");
    await load();
  } catch (error: any) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error));
  }
}

async function openCompanionInvitation(invitation: FollowupInvitation) {
  companionInvitation.value = invitation;
  const task = rows.value.find(item => item.id === invitation.task_id);
  if (!task) {
    ElMessage.warning("请先刷新服务事项列表");
    return;
  }
  try {
    const response = await getFollowupAssignees(task.org_unit_id);
    assignees.value = response.data.filter(
      item => item.username !== useUserStoreHook().username
    );
    Object.assign(companionForm, {
      invited_user_id: undefined,
      invitation_message: "想邀请您与我同行协力，一起温暖地完成这项服务",
      valid_until: dayjs().add(2, "day").format("YYYY-MM-DDTHH:mm:ss")
    });
    companionDialogVisible.value = true;
  } catch (error) {
    ElMessage.error(errorText(error));
  }
}

async function submitCompanionInvitation() {
  if (!companionInvitation.value || !companionForm.invited_user_id) {
    ElMessage.warning("请选择同行伙伴");
    return;
  }
  saving.value = true;
  try {
    await createFollowupInvitation(companionInvitation.value.task_id, {
      invited_user_id: companionForm.invited_user_id,
      invitation_type: "COMPANION",
      invitation_message: companionForm.invitation_message.trim() || undefined,
      valid_until: companionForm.valid_until
    });
    companionDialogVisible.value = false;
    ElMessage.success("同行邀请已送达");
    await load();
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

async function openTaskFromRoute() {
  const memberId = Number(route.query.member_id || 0);
  if (memberId) {
    await openCreate();
    taskForm.member_id = memberId;
    await memberChanged(memberId);
    return;
  }
  const taskId = Number(route.query.task_id || 0);
  if (!taskId) return;
  const task = rows.value.find(item => item.id === taskId);
  if (!task) return;
  if (String(task.task_type).toUpperCase() === "VISIT") {
    openVisit(task);
  } else {
    openRecord(task);
  }
}

onMounted(async () => {
  await load();
  await openTaskFromRoute();
});
</script>

<template>
  <div v-loading="loading" class="followup-page">
    <section class="page-head">
      <div>
        <p>{{ copy.pageKicker }}</p>
        <h1>电话跟进与企业走访</h1>
        <span>{{ copy.accessHint }}</span>
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
          {{ copy.create }}
        </el-button>
      </div>
    </section>

    <el-alert
      :title="`当前显示 ${rows.length} 项，其中 ${openCount} 项尚未完成`"
      type="info"
      :closable="false"
      show-icon
    />

    <el-card
      v-if="invitationEnabled && actionableInvitations.length"
      class="invitation-card"
      shadow="never"
    >
      <template #header>
        <div>
          <strong>服务邀请</strong>
          <p>请按自己的时间与状态安心回应，每一种回应都值得尊重。</p>
        </div>
      </template>
      <div class="invitation-list">
        <article
          v-for="invitation in actionableInvitations"
          :key="invitation.id"
          class="invitation-item"
        >
          <div>
            <el-tag type="success" effect="plain">
              {{
                invitation.invitation_type === "ASSIGNEE"
                  ? "邀请担当"
                  : "同行邀请"
              }}
            </el-tag>
            <h3>{{ invitation.member_name }} · {{ invitation.org_name }}</h3>
            <p>{{ invitation.service_purpose }}</p>
            <blockquote v-if="invitation.invitation_message">
              {{ invitation.inviter_name }}：{{ invitation.invitation_message }}
            </blockquote>
            <small>
              {{ invitationStatusText[invitation.status] }} · 邀请有效至
              {{ dayjs(invitation.valid_until).format("YYYY-MM-DD HH:mm") }}
            </small>
          </div>
          <div class="invitation-actions">
            <template v-if="invitation.status === 'PENDING'">
              <el-button type="primary" @click="acceptInvitation(invitation)">
                愿意担当
              </el-button>
              <el-button @click="suggestAnotherTime(invitation)">
                建议调整时间
              </el-button>
              <el-button text @click="temporarilyUnavailable(invitation)">
                本次暂时无法参与
              </el-button>
            </template>
            <el-button
              v-if="
                invitation.status === 'ACCEPTED' &&
                invitation.invitation_type === 'ASSIGNEE'
              "
              type="primary"
              plain
              @click="openCompanionInvitation(invitation)"
            >
              邀请同行协力
            </el-button>
          </div>
        </article>
      </div>
    </el-card>

    <el-card shadow="never">
      <el-table :data="rows" stripe :empty-text="copy.empty">
        <el-table-column
          prop="member_name"
          label="学员"
          min-width="105"
          fixed
        />
        <el-table-column prop="phone_masked" label="联系方式" min-width="135" />
        <el-table-column prop="company_name" label="企业" min-width="150">
          <template #default="{ row }">{{ row.company_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="org_name" label="所属中心" min-width="135" />
        <el-table-column
          prop="service_purpose"
          label="服务目的"
          min-width="220"
          show-overflow-tooltip
        />
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
        <el-table-column
          prop="next_followup_at"
          label="下次跟进"
          min-width="170"
        >
          <template #default="{ row }">
            {{
              row.next_followup_at
                ? dayjs(row.next_followup_at).format("YYYY-MM-DD HH:mm")
                : "—"
            }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="315" fixed="right">
          <template #default="{ row }">
            <div
              v-if="row.status !== 'CLOSED' && row.can_record"
              class="row-actions"
            >
              <el-button
                v-if="row.can_close"
                link
                type="primary"
                @click="reveal(row)"
              >
                {{ canViewFullProfile ? "查看资料" : "联系" }}
              </el-button>
              <el-button link type="primary" @click="openRecord(row)"
                >记跟进</el-button
              >
              <el-button link type="primary" @click="openVisit(row)"
                >记走访</el-button
              >
              <el-button
                v-if="row.can_close"
                link
                type="danger"
                @click="closeTask(row)"
              >
                {{ copy.close }}
              </el-button>
            </div>
            <span v-else class="muted">
              {{ row.status === "CLOSED" ? copy.closed : copy.onlyAssignee }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="720px">
      <el-form
        v-if="dialogMode === 'create'"
        :model="taskForm"
        label-position="top"
      >
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
          <el-form-item :label="copy.itemType">
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
          <el-form-item :label="copy.assignee">
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
          <el-form-item :label="copy.deadline">
            <el-date-picker
              v-model="taskForm.due_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="可选"
            />
          </el-form-item>
          <el-form-item label="保密范围">
            <el-select v-model="taskForm.confidentiality_level">
              <el-option :label="copy.privateLabel" value="ASSIGNEE" />
              <el-option :label="copy.managerLabel" value="ORG_MANAGERS" />
            </el-select>
          </el-form-item>
          <template v-if="invitationEnabled">
            <el-form-item class="full" label="协同方式">
              <el-switch
                v-model="taskForm.invitation_mode"
                active-text="先邀请担当，对方接受后再开放服务事项"
                :inactive-text="
                  languageContext === 'VOLUNTEER'
                    ? '直接发起服务事项'
                    : '沿用直接指派方式'
                "
              />
            </el-form-item>
            <el-form-item
              v-if="taskForm.invitation_mode"
              class="full"
              label="邀请寄语"
            >
              <el-input
                v-model="taskForm.invitation_message"
                type="textarea"
                :rows="2"
                placeholder="说明为什么想到邀请对方，以及期待获得的支持"
              />
            </el-form-item>
            <el-form-item v-if="taskForm.invitation_mode" label="邀请有效期">
              <el-date-picker
                v-model="taskForm.invitation_valid_until"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm:ss"
                placeholder="请选择回应期限"
              />
            </el-form-item>
          </template>
        </div>
      </el-form>

      <el-form
        v-else-if="dialogMode === 'record'"
        :model="recordForm"
        label-position="top"
      >
        <div class="form-grid">
          <el-form-item label="联系渠道">
            <el-select v-model="recordForm.channel">
              <el-option
                v-for="[value, label] in channelOptions"
                :key="value"
                :label="label"
                :value="value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="联系时间">
            <el-date-picker
              v-model="recordForm.contacted_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
            />
          </el-form-item>
          <el-form-item label="联系结果">
            <el-select v-model="recordForm.outcome_code">
              <el-option
                v-for="[value, label] in outcomeOptions"
                :key="value"
                :label="label"
                :value="value"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="下次跟进">
            <el-date-picker
              v-model="recordForm.next_followup_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="可选"
            />
          </el-form-item>
          <el-form-item class="full" label="学员陈述">
            <el-input
              v-model="recordForm.subject_statement"
              type="textarea"
              :rows="2"
              placeholder="学员本人表达的需求或情况"
            />
          </el-form-item>
          <el-form-item class="full" label="客观事实">
            <el-input
              v-model="recordForm.objective_facts"
              type="textarea"
              :rows="2"
              placeholder="已经确认的客观信息"
            />
          </el-form-item>
          <el-form-item class="full" label="工作人员判断">
            <el-input
              v-model="recordForm.staff_judgment"
              type="textarea"
              :rows="2"
              placeholder="与客观事实分开填写"
            />
          </el-form-item>
          <el-form-item class="full" label="下一步行动">
            <el-input v-model="recordForm.next_action" />
          </el-form-item>
        </div>
      </el-form>

      <el-form v-else :model="visitForm" label-position="top">
        <div class="form-grid">
          <el-form-item label="预约时间">
            <el-date-picker
              v-model="visitForm.appointment_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="可选"
            />
          </el-form-item>
          <el-form-item label="实际走访时间">
            <el-date-picker
              v-model="visitForm.visited_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
            />
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
            <el-input
              v-model="visitForm.participants_text"
              placeholder="用逗号分隔"
            />
          </el-form-item>
          <el-form-item class="full" label="走访目的">
            <el-input v-model="visitForm.purpose" />
          </el-form-item>
          <el-form-item class="full" label="客观事实">
            <el-input
              v-model="visitForm.objective_facts"
              type="textarea"
              :rows="3"
            />
          </el-form-item>
          <el-form-item label="表达需求">
            <el-input
              v-model="visitForm.expressed_needs"
              type="textarea"
              :rows="2"
            />
          </el-form-item>
          <el-form-item label="已提供支持">
            <el-input
              v-model="visitForm.support_provided"
              type="textarea"
              :rows="2"
            />
          </el-form-item>
          <el-form-item label="工作人员判断">
            <el-input
              v-model="visitForm.staff_judgment"
              type="textarea"
              :rows="2"
            />
          </el-form-item>
          <el-form-item label="下一步行动">
            <el-input
              v-model="visitForm.next_action"
              type="textarea"
              :rows="2"
            />
          </el-form-item>
          <el-form-item label="下次跟进">
            <el-date-picker
              v-model="visitForm.next_followup_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="可选"
            />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit"
          >保存</el-button
        >
      </template>
    </el-dialog>

    <el-dialog
      v-model="companionDialogVisible"
      title="邀请同行协力"
      width="560px"
    >
      <el-alert
        title="同行伙伴可以共同记录服务过程，但不会获得完整联系方式，也不能结束服务事项。"
        type="info"
        :closable="false"
      />
      <el-form
        :model="companionForm"
        label-position="top"
        class="companion-form"
      >
        <el-form-item label="同行伙伴">
          <el-select
            v-model="companionForm.invited_user_id"
            filterable
            placeholder="选择一位有效任职范围内的志工"
          >
            <el-option
              v-for="user in assignees"
              :key="user.id"
              :label="user.display_name"
              :value="user.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="邀请寄语">
          <el-input
            v-model="companionForm.invitation_message"
            type="textarea"
            :rows="3"
          />
        </el-form-item>
        <el-form-item label="邀请有效期">
          <el-date-picker
            v-model="companionForm.valid_until"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="companionDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="submitCompanionInvitation"
        >
          送出同行邀请
        </el-button>
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
.invitation-card :deep(.el-card__header) p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
}
.invitation-list {
  display: grid;
  gap: 14px;
}
.invitation-item {
  display: flex;
  gap: 24px;
  align-items: center;
  justify-content: space-between;
  padding: 18px;
  background: #f5fbf7;
  border: 1px solid #d9eee0;
  border-radius: 12px;
}
.invitation-item h3 {
  margin: 10px 0 6px;
}
.invitation-item p,
.invitation-item blockquote {
  margin: 6px 0;
}
.invitation-item blockquote {
  padding-left: 12px;
  color: #35634c;
  border-left: 3px solid #8cc9a7;
}
.invitation-item small {
  color: var(--el-text-color-secondary);
}
.invitation-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  min-width: 280px;
}
.companion-form {
  margin-top: 18px;
}
.companion-form :deep(.el-select),
.companion-form :deep(.el-date-editor) {
  width: 100%;
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
  .invitation-item {
    align-items: stretch;
    flex-direction: column;
  }
  .invitation-actions {
    justify-content: flex-start;
    min-width: 0;
  }
}
</style>
