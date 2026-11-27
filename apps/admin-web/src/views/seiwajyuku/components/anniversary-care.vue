<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { http } from "@/utils/http";

type Bucket = "pending" | "upcoming" | "completed";
type Action = "regenerate" | "sent";

type Anniversary = {
  member_id: number;
  member_name: string;
  org_name: string;
  class_name: string | null;
  years: number;
  due_date: string;
  review_status: "PENDING" | "APPROVED";
  send_status: "PENDING" | "SENT";
  sent_date: string | null;
};

type Detail = Pick<Anniversary, "review_status" | "send_status" | "sent_date"> & {
  join_date: string;
  class_name: string | null;
  draft: string;
  memories: { id: string; title: string; occurred_on: string }[];
  selected_ids: string[];
  can_manage: boolean;
  can_mark_sent: boolean;
};

const items = ref<Anniversary[]>([]);
const asOf = ref("");
const loading = ref(false);
const listError = ref(false);
const tab = ref<Bucket>("pending");
const selected = ref<Anniversary>();
const visible = ref(false);
const busy = ref(false);
const result = ref<Detail>();
const selectedIds = ref<string[]>([]);
const detailError = ref("");
let detailVersion = 0;

const selectionChanged = computed(() => {
  const saved = result.value?.selected_ids ?? [];
  return selectedIds.value.length !== saved.length || selectedIds.value.some(id => !saved.includes(id));
});

const buckets = computed(() => ({
  pending: items.value.filter(item => item.send_status === "PENDING" && item.due_date <= asOf.value),
  upcoming: items.value.filter(item => item.send_status === "PENDING" && item.due_date > asOf.value),
  completed: items.value.filter(item => item.sent_date === asOf.value)
}));

const rowKey = (row: Anniversary) => `${row.member_id}:${row.due_date}`;
const memberPath = (row: Anniversary) => `/api/v1/members/${row.member_id}/anniversary-care`;
const statusLabel = (sent: Anniversary["send_status"], review: Anniversary["review_status"]) => sent === "SENT"
  ? "已发送" : review === "APPROVED" ? "待发送" : "待生成";

async function load() {
  if (loading.value) return;
  loading.value = true;
  listError.value = false;
  try {
    const response = await http.request<{ data: { as_of: string; items: Anniversary[] } }>(
      "get",
      "/api/v1/members/anniversary-care"
    );
    asOf.value = response.data.as_of;
    items.value = response.data.items;
  } catch {
    listError.value = true;
  } finally {
    loading.value = false;
  }
}

function showDetail(detail: Detail) {
  result.value = detail;
  selectedIds.value = detail.selected_ids;
}

async function open(memberId: number, dueDate: string) {
  const member = items.value.find(item => item.member_id === memberId && item.due_date === dueDate);
  if (!member) return;
  selected.value = member;
  visible.value = true;
  result.value = undefined;
  detailError.value = "";
  busy.value = true;
  const version = ++detailVersion;
  try {
    const response = await http.request<{ data: Detail }>("get", memberPath(member), {
      params: { due_date: member.due_date }
    });
    if (version === detailVersion) showDetail(response.data);
  } catch {
    if (version === detailVersion) detailError.value = "暂时无法读取周年关怀，请重试。";
  } finally {
    if (version === detailVersion) busy.value = false;
  }
}

async function update(action: Action, memoryIds: string[] = []) {
  const member = selected.value;
  if (!member || busy.value || !result.value) return;
  const version = ++detailVersion;
  busy.value = true;
  detailError.value = "";
  try {
    const response = await http.request<{ data: Detail }>("post", `${memberPath(member)}/${action}`, {
      data: { due_date: member.due_date, memory_ids: memoryIds }
    });
    if (version === detailVersion) showDetail(response.data);
    const row = items.value.find(item => rowKey(item) === rowKey(member));
    if (row) {
      row.review_status = response.data.review_status;
      row.send_status = response.data.send_status;
      row.sent_date = response.data.sent_date;
    }
    if (action === "sent") await load();
  } catch {
    if (version === detailVersion) detailError.value = "暂时无法完成操作，请刷新后重试。";
  } finally {
    if (version === detailVersion) busy.value = false;
  }
}

function changeMemories(values: (string | number | boolean)[]) {
  const ids = values.filter((value): value is string => typeof value === "string");
  if (ids.length <= 3) selectedIds.value = ids;
}

async function copy() {
  const detail = result.value;
  if (busy.value || selectionChanged.value || !detail?.draft || detail.review_status !== "APPROVED" || detail.send_status === "SENT") return;
  try {
    await navigator.clipboard.writeText(detail.draft);
    ElMessage.success("已复制周年关怀文案");
  } catch {
    ElMessage.warning("复制失败，可选中文案手动复制。");
  }
}

async function markSent() {
  const member = selected.value;
  if (!member || !result.value?.can_mark_sent || busy.value || selectionChanged.value) return;
  try {
    await ElMessageBox.confirm("仅在祝福已经发送到微信群后登记。", "登记已发送", {
      type: "info",
      confirmButtonText: "登记发送",
      cancelButtonText: "取消"
    });
  } catch {
    return;
  }
  if (visible.value && selected.value === member && !selectionChanged.value) await update("sent");
}

watch(visible, openNow => {
  if (!openNow) {
    detailVersion++;
    busy.value = false;
    result.value = undefined;
  }
});
onMounted(load);
</script>

<template>
  <section v-loading="loading" aria-label="入塾周年关怀">
    <div class="anniversary-heading">
      <h2>入塾周年关怀</h2>
      <el-button link type="primary" :disabled="loading" @click="load">刷新</el-button>
    </div>
    <el-alert v-if="listError" title="暂时无法读取周年名单" type="warning" :closable="false" />
    <template v-else>
      <el-tabs v-model="tab">
        <el-tab-pane name="pending" :label="`待处理 ${buckets.pending.length}`" />
        <el-tab-pane name="upcoming" :label="`未来 7 天 ${buckets.upcoming.length}`" />
        <el-tab-pane name="completed" :label="`今日已完成 ${buckets.completed.length}`" />
      </el-tabs>
      <el-table :data="buckets[tab]" :row-key="rowKey" empty-text="暂无周年关怀记录">
        <el-table-column prop="member_name" label="学长" min-width="115" />
        <el-table-column prop="org_name" label="所属组织" min-width="130" />
        <el-table-column prop="class_name" label="班级" min-width="110" />
        <el-table-column label="入塾周年" width="105">
          <template #default="{ row }">{{ row.years }} 周年</template>
        </el-table-column>
        <el-table-column v-if="tab === 'completed'" prop="sent_date" label="发送日期" width="115" />
        <el-table-column v-else prop="due_date" label="周年日期" width="115" />
        <el-table-column label="状态" width="105">
          <template #default="{ row }"><el-tag :type="row.send_status === 'SENT' ? 'success' : row.review_status === 'APPROVED' ? 'warning' : 'info'" effect="light">{{ statusLabel(row.send_status, row.review_status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="关怀" width="115">
          <template #default="{ row }">
            <el-button link type="primary" @click="open(row.member_id, row.due_date)">{{ row.send_status === "SENT" ? "查看记录" : "准备祝福" }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
    <el-drawer
      v-model="visible"
      :title="`${selected?.member_name || ''}学长 · 入塾${selected?.years || ''}周年`"
      size="min(94vw, 560px)"
    >
      <div v-loading="busy" class="anniversary-detail">
        <el-alert v-if="detailError" :title="detailError" type="warning" :closable="false" />
        <el-button v-if="detailError && !result && selected" :disabled="busy" @click="open(selected.member_id, selected.due_date)">重新加载</el-button>
        <template v-if="result">
          <p class="anniversary-dates">{{ result.class_name }}<span v-if="result.class_name"> · </span>入塾 {{ result.join_date }} · 周年 {{ selected?.due_date }}</p>
          <div v-if="result.send_status !== 'SENT'">
            <h3>学习回忆</h3>
            <p v-if="!result.memories.length" class="anniversary-dates">当前没有可加入祝福的已核实经历。</p>
            <el-checkbox-group
              v-else
              :model-value="selectedIds"
              :max="3"
              :disabled="busy || !result.can_manage"
              class="anniversary-memories"
              @change="changeMemories"
            >
              <el-checkbox v-for="memory in result.memories" :key="memory.id" :value="memory.id">
                {{ memory.occurred_on }} · {{ memory.title }}
              </el-checkbox>
            </el-checkbox-group>
          </div>
          <div>
            <h3>微信群祝福</h3>
            <el-input v-if="result.draft" :model-value="result.draft" aria-label="周年关怀文案" type="textarea" :rows="10" readonly />
            <p v-else class="anniversary-dates">当前权限无法查看祝福文案中的学习经历。</p>
          </div>
          <p v-if="result.send_status === 'SENT'" class="anniversary-receipt">已登记发送 · {{ result.sent_date }}</p>
          <div v-else class="anniversary-actions">
            <span v-if="selectionChanged" class="anniversary-dates">重新生成后更新祝福文案。</span>
            <el-button v-if="result.can_manage && result.draft" type="primary" :disabled="busy" @click="update('regenerate', selectedIds)">重新生成</el-button>
            <template v-if="result.review_status === 'APPROVED' && result.draft && !selectionChanged">
              <el-button type="primary" :disabled="busy" @click="copy">复制文案</el-button>
              <el-button v-if="result.can_mark_sent" :disabled="busy" @click="markSent">登记已发送</el-button>
              <span v-else-if="result.can_manage" class="anniversary-dates">周年当天起可登记发送。</span>
            </template>
          </div>
        </template>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.anniversary-heading,
.anniversary-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.anniversary-heading { justify-content: space-between; }
.anniversary-detail { min-height: 90px; display: grid; gap: 16px; }
.anniversary-dates { color: var(--el-text-color-secondary); }
.anniversary-receipt { color: var(--el-color-success-dark-2); }
.anniversary-memories { display: grid; gap: 6px; margin-top: 8px; }
.anniversary-memories :deep(.el-checkbox) { height: auto; align-items: flex-start; margin: 0; }
.anniversary-memories :deep(.el-checkbox__label) { white-space: normal; line-height: 1.45; }
.anniversary-detail h3 { margin-bottom: 8px; }
.anniversary-actions :deep(.el-button + .el-button) { margin-left: 0; }
</style>
