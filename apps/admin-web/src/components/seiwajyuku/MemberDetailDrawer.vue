<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  getMemberDetail,
  getMemberTimeline,
  getMemberVolunteerAppointments,
  type MemberCareAction,
  type MemberCarePerson,
  type MemberTimeline,
  type MemberVolunteerAppointments
} from "@/api/seiwajyuku";

defineOptions({ name: "MemberDetailDrawer" });

type ProfileRecord = Record<string, string | number | null | undefined>;

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    memberId?: number;
    carePerson?: MemberCarePerson;
  }>(),
  {
    memberId: undefined,
    carePerson: undefined
  }
);

const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
  (event: "action", action: MemberCareAction): void;
}>();

const visible = computed({
  get: () => props.modelValue,
  set: value => emit("update:modelValue", value)
});
const loading = ref(false);
const detail = ref<ProfileRecord>();
const timeline = ref<MemberTimeline>();
const appointments = ref<MemberVolunteerAppointments>();
const errorMessage = ref("");
let loadToken = 0;

const fallbackProfile = computed<ProfileRecord | undefined>(() => {
  if (!props.carePerson) return undefined;
  return {
    id: props.carePerson.member_id,
    name: props.carePerson.member_name,
    org_name: props.carePerson.org_name,
    class_name: props.carePerson.class_name,
    group_name: props.carePerson.group_name
  };
});

const profile = computed<ProfileRecord | undefined>(
  () =>
    detail.value ||
    (timeline.value?.member as ProfileRecord) ||
    fallbackProfile.value
);
const profileName = computed(() => String(profile.value?.name || "学员"));
const profileLocation = computed(
  () =>
    [
      profile.value?.org_name,
      profile.value?.class_name,
      profile.value?.group_name
    ]
      .filter(
        value => value !== null && value !== undefined && String(value).trim()
      )
      .map(value => String(value))
      .join(" · ") || "归属信息待维护"
);
const careActions = computed(() => props.carePerson?.actions || []);
const events = computed(() => timeline.value?.events || []);
const activeAppointments = computed(() =>
  (appointments.value?.appointments || []).filter(item => {
    const status = String(item.status || "").toUpperCase();
    return status === "ACTIVE" || status === "CURRENT" || !item.ends_at;
  })
);
const summaryCards = computed(() => [
  {
    label: "续费",
    value: countEvents(["RENEWAL_CYCLE", "RENEWAL_FOLLOWUP"]),
    note: "时间线记录"
  },
  {
    label: "关爱",
    value: countEvents([
      "FOLLOWUP_TASK",
      "FOLLOWUP_RECORD",
      "ENTERPRISE_VISIT"
    ]),
    note: "跟进与走访"
  },
  {
    label: "学习",
    value: countEvents(["ATTENDANCE", "LEARNING_ACTIVITY"]),
    note: "学习记录"
  },
  {
    label: "志工",
    value: activeAppointments.value.length,
    note: "当前有效任职"
  }
]);
const recentEvents = computed(() => events.value.slice(0, 8));

function countEvents(types: string[]) {
  return events.value.filter(event => types.includes(event.event_type)).length;
}

function displayValue(value: unknown, fallback = "—") {
  if (value === null || value === undefined || String(value).trim() === "") {
    return fallback;
  }
  return String(value);
}

function profileValue(key: string, fallback = "—") {
  return displayValue(profile.value?.[key], fallback);
}

function memberStatusLabel(status: unknown) {
  return (
    {
      ACTIVE: "在册",
      INACTIVE: "已退出",
      SUSPENDED: "暂缓",
      PENDING: "待确认"
    }[String(status || "").toUpperCase()] || displayValue(status, "在册")
  );
}

function formatDate(value: string | number | null | undefined) {
  if (!value) return "—";
  return String(value)
    .replace("T", " ")
    .replace(/\.\d+Z$/, "")
    .replace(/Z$/, "");
}

function eventTypeLabel(type: string) {
  return (
    {
      RENEWAL_CYCLE: "续费周期",
      RENEWAL_FOLLOWUP: "续费跟进",
      FOLLOWUP_TASK: "关爱任务",
      FOLLOWUP_RECORD: "关爱记录",
      ENTERPRISE_VISIT: "企业走访",
      ATTENDANCE: "出席记录",
      LEARNING_ACTIVITY: "学习活动",
      PROFILE_CHANGE: "资料变更"
    }[type] ||
    type ||
    "运营记录"
  );
}

function eventTypeTag(type: string) {
  if (type.includes("RENEWAL")) return "warning";
  if (type.includes("FOLLOWUP") || type === "ENTERPRISE_VISIT") {
    return "success";
  }
  if (type.includes("LEARNING") || type === "ATTENDANCE") return "info";
  return "primary";
}

function appointmentStatusLabel(status: string) {
  return (
    {
      ACTIVE: "当前有效",
      CURRENT: "当前有效",
      ENDED: "已结束",
      EXPIRED: "已结束",
      CANCELLED: "已取消"
    }[String(status || "").toUpperCase()] ||
    status ||
    "状态待维护"
  );
}

function eventStatusLabel(status?: string) {
  return (
    {
      OPEN: "待处理",
      IN_PROGRESS: "处理中",
      CLOSED: "已完成",
      COMPLETED: "已完成",
      RENEWED: "已续费",
      PENDING: "待确认"
    }[String(status || "").toUpperCase()] || ""
  );
}

function actionButtonLabel(action: MemberCareAction) {
  if (action.navigation_type === "RENEWAL") return "去处理续费";
  if (action.navigation_type === "BIRTHDAY") return "去做生日关爱";
  return "去记录关爱";
}

function selectAction(action: MemberCareAction) {
  emit("action", action);
}

async function load() {
  if (!props.modelValue || !props.memberId) return;
  const token = ++loadToken;
  loading.value = true;
  errorMessage.value = "";
  detail.value = undefined;
  timeline.value = undefined;
  appointments.value = undefined;
  const [detailResult, timelineResult, appointmentResult] =
    await Promise.allSettled([
      getMemberDetail(props.memberId),
      getMemberTimeline(props.memberId, 20),
      getMemberVolunteerAppointments(props.memberId)
    ]);
  if (token !== loadToken) return;
  if (detailResult.status === "fulfilled")
    detail.value = detailResult.value.data;
  if (timelineResult.status === "fulfilled") {
    timeline.value = timelineResult.value.data;
  }
  if (appointmentResult.status === "fulfilled") {
    appointments.value = appointmentResult.value.data;
  }
  if (
    detailResult.status === "rejected" &&
    timelineResult.status === "rejected"
  ) {
    errorMessage.value = "学员资料暂时加载失败，请稍后重试";
  } else if (appointmentResult.status === "rejected") {
    ElMessage.info("当前账号暂不可查看志工任职，其他学员摘要仍可查看");
  }
  loading.value = false;
}

watch(
  () => [props.modelValue, props.memberId],
  () => {
    void load();
  },
  { immediate: true }
);
</script>

<template>
  <el-drawer
    v-model="visible"
    :title="`${profileName} · 学员详情`"
    size="min(680px, 96vw)"
    destroy-on-close
  >
    <div v-loading="loading" class="member-detail-drawer">
      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="false"
        show-icon
      />

      <template v-if="profile">
        <section class="member-detail-hero">
          <div>
            <span class="member-detail-kicker">学员档案</span>
            <h2>{{ profileName }}学长</h2>
            <p>{{ profileLocation }}</p>
          </div>
          <el-tag type="success" effect="light">
            {{ memberStatusLabel(profile?.status) }}
          </el-tag>
        </section>

        <section class="member-detail-section">
          <div class="member-detail-section-title">
            <h3>基本资料</h3>
            <span>只展示当前账号可见信息</span>
          </div>
          <div class="member-detail-facts">
            <div>
              <span>手机号</span
              ><strong>{{
                profileValue("phone_masked", profileValue("phone"))
              }}</strong>
            </div>
            <div>
              <span>入塾时间</span
              ><strong>{{ profileValue("join_date") }}</strong>
            </div>
            <div>
              <span>续费月份</span
              ><strong>{{ profileValue("renewal_month") }}</strong>
            </div>
            <div>
              <span>同行年数</span
              ><strong>{{ profileValue("membership_years") }}</strong>
            </div>
          </div>
        </section>

        <section class="member-detail-section">
          <div class="member-detail-section-title">
            <h3>运营摘要</h3>
            <span>从现有服务记录汇总</span>
          </div>
          <div class="member-detail-summary-grid">
            <article v-for="card in summaryCards" :key="card.label">
              <span>{{ card.label }}</span>
              <strong>{{ card.value }}</strong>
              <small>{{ card.note }}</small>
            </article>
          </div>
        </section>

        <section v-if="careActions.length" class="member-detail-section">
          <div class="member-detail-section-title">
            <h3>本次需要做</h3>
            <span>完成后返回今日行动即可刷新</span>
          </div>
          <div class="member-detail-actions">
            <article
              v-for="action in careActions"
              :key="`${action.source}-${action.source_id}`"
            >
              <div>
                <div class="member-detail-action-title">
                  <el-tag
                    :type="action.urgency === 'OVERDUE' ? 'danger' : 'warning'"
                  >
                    {{ action.urgency === "OVERDUE" ? "已逾期" : "待处理" }}
                  </el-tag>
                  <strong>{{ action.label }}</strong>
                </div>
                <p>{{ action.reason }}</p>
                <small>截止：{{ action.due_date || "按现有流程" }}</small>
              </div>
              <el-button type="primary" plain @click="selectAction(action)">
                {{ actionButtonLabel(action) }}
              </el-button>
            </article>
          </div>
        </section>
        <el-empty v-else description="当前没有待处理行动" :image-size="64" />

        <section class="member-detail-section">
          <div class="member-detail-section-title">
            <h3>志工任职</h3>
            <span>当前有效岗位</span>
          </div>
          <div
            v-if="activeAppointments.length"
            class="member-detail-appointments"
          >
            <div v-for="item in activeAppointments" :key="item.id">
              <div>
                <strong>{{ item.position_name || "志工岗位" }}</strong>
                <span>{{ item.org_name }}</span>
              </div>
              <el-tag type="success" effect="plain">{{
                appointmentStatusLabel(item.status)
              }}</el-tag>
            </div>
          </div>
          <p v-else class="member-detail-muted">当前没有有效志工任职</p>
        </section>

        <section class="member-detail-section">
          <div class="member-detail-section-title">
            <h3>最近记录</h3>
            <span>最多显示 8 条</span>
          </div>
          <div v-if="recentEvents.length" class="member-detail-events">
            <article v-for="event in recentEvents" :key="event.id">
              <div class="member-detail-event-meta">
                <el-tag :type="eventTypeTag(event.event_type)" effect="plain">
                  {{ eventTypeLabel(event.event_type) }}
                </el-tag>
                <span>{{
                  formatDate(event.occurred_at || event.updated_at)
                }}</span>
              </div>
              <strong>{{ event.title }}</strong>
              <small v-if="eventStatusLabel(event.status)">
                {{ eventStatusLabel(event.status) }}
              </small>
            </article>
          </div>
          <p v-else class="member-detail-muted">暂无可展示的学习或服务记录</p>
        </section>
      </template>
    </div>
  </el-drawer>
</template>

<style scoped>
.member-detail-drawer {
  min-height: 320px;
  color: #345247;
}
.member-detail-drawer > .el-alert {
  margin-bottom: 16px;
}
.member-detail-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px;
  margin-bottom: 18px;
  background: linear-gradient(135deg, #f0f8f3, #fbfdfc);
  border: 1px solid #d8e9df;
  border-radius: 14px;
}
.member-detail-kicker,
.member-detail-section-title span,
.member-detail-facts span,
.member-detail-summary-grid span,
.member-detail-summary-grid small,
.member-detail-appointments span,
.member-detail-events small,
.member-detail-event-meta span,
.member-detail-muted {
  color: #81968c;
  font-size: 12px;
}
.member-detail-hero h2 {
  margin: 5px 0;
  color: #173f33;
  font-size: 24px;
}
.member-detail-hero p {
  margin: 0;
  color: #60756c;
}
.member-detail-section {
  padding: 16px 0;
  border-bottom: 1px solid #edf2ef;
}
.member-detail-section:last-child {
  border-bottom: 0;
}
.member-detail-section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.member-detail-section-title h3 {
  margin: 0;
  color: #244f40;
  font-size: 16px;
}
.member-detail-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.member-detail-facts div {
  display: grid;
  gap: 4px;
  padding: 11px 12px;
  background: #f8fbf9;
  border-radius: 9px;
}
.member-detail-facts strong {
  color: #345247;
  font-size: 14px;
  font-weight: 500;
}
.member-detail-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.member-detail-summary-grid article {
  display: grid;
  gap: 4px;
  padding: 12px;
  background: #f5f9f7;
  border-radius: 9px;
}
.member-detail-summary-grid strong {
  color: #173f33;
  font-size: 22px;
}
.member-detail-actions,
.member-detail-events,
.member-detail-appointments {
  display: grid;
  gap: 9px;
}
.member-detail-actions article,
.member-detail-appointments > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px;
  background: #fbfdfc;
  border: 1px solid #e1ebe6;
  border-radius: 10px;
}
.member-detail-action-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.member-detail-actions p {
  margin: 7px 0 3px;
  color: #60756c;
  line-height: 1.5;
}
.member-detail-actions small {
  color: #81968c;
  font-size: 12px;
}
.member-detail-appointments > div > div {
  display: grid;
  gap: 4px;
}
.member-detail-events article {
  display: grid;
  gap: 6px;
  padding: 10px 12px;
  border-left: 3px solid #c6ded0;
  background: #fbfdfc;
}
.member-detail-event-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.member-detail-events strong {
  color: #345247;
  font-size: 14px;
  font-weight: 500;
}
@media (max-width: 560px) {
  .member-detail-facts,
  .member-detail-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .member-detail-actions article,
  .member-detail-appointments > div {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
