<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  participationTableRows,
  participationRateLabel,
  participationScene
} from "@/utils/attendanceManagement.mjs";

const props = defineProps<{ result?: Record<string, any> | null }>();
const emit = defineEmits<{ "select-scene": [eventId: string] }>();
const sceneId = ref("overall");
let lastGroup = "", lastEvent = "";
watch(() => props.result, result => {
  const event = result?.event;
  if (!event?.event_id) return;
  const group = event.event_group_id || event.event_id;
  if (lastGroup && lastGroup === group && lastEvent !== event.event_id) sceneId.value = event.event_id;
  else if (group !== lastGroup) sceneId.value = "overall";
  lastGroup = group; lastEvent = event.event_id;
}, { immediate: true });
const scenes = computed(() => (props.result?.class_session_statistics || []).map((row: any) => ({
  id: row.event_id, name: row.session_name || ({ MORNING: "上午", AM: "上午", AFTERNOON: "下午", PM: "下午", KONPA: "空巴", EVENING: "空巴" } as Record<string,string>)[row.session_code] || "场次"
})));
const view = computed(() => participationScene(props.result || {}, sceneId.value));
const rows = computed(() => participationTableRows(view.value));
const scopeName = computed(() => sceneId.value === "overall" ? "整体" : scenes.value.find((row: any) => row.id === sceneId.value)?.name || "当前场次");
function selectScene() {
  if (sceneId.value !== "overall" && sceneId.value !== props.result?.event?.event_id) emit("select-scene", sceneId.value);
}
const rowClass = ({ row }: { row: { isTotal?: boolean } }) =>
  row.isTotal ? "class-total-row" : "";
</script>

<template>
  <section class="participation-summary">
    <h3>本次班会参会统计 · {{ scopeName }}</h3>
    <el-radio-group v-model="sceneId" @change="selectScene"><el-radio-button value="overall">整体</el-radio-button><el-radio-button v-for="scene in scenes" :key="scene.id" :value="scene.id">{{ scene.name }}</el-radio-button></el-radio-group>
    <p class="basis">
      {{ sceneId === "overall" ? "上午、下午合并去重，空巴另计；整体统计与 Excel 小组参会统计一致。" : "仅展示所选场次的参会率，不合并其他场次。" }}
    </p>
    <div class="participation-rates">
      <div>
        <span>总小组参会率</span
        ><strong>{{
          participationRateLabel(view?.group_totals?.participation_rate)
        }}</strong
        ><small
          >{{ view?.group_totals?.present_count ?? "—" }} /
          {{ view?.group_totals?.roster_count ?? "—" }} 人</small
        >
      </div>
      <div>
        <span>{{ scopeName }}参会率（含本班师资）</span
        ><strong>{{
          participationRateLabel(
            view?.class_statistics?.participation_rate,
            1
          )
        }}</strong
        ><small
          >{{ view?.class_statistics?.present_count ?? "—" }} /
          {{ view?.class_statistics?.roster_count ?? "—" }} 人</small
        >
      </div>
    </div>
    <p
      v-if="view?.class_statistics?.includes_formal_class_staff"
      class="basis"
    >
      本班在册学长到场
      {{
        view.class_statistics.member_present_count
      }}
      人；额外计入本班正式师资
      {{
        view.class_statistics.staff_present_count
      }}
      人。小组率仅统计在册学长，整体参会率沿用活动签到的师资例外口径。
    </p>
    <el-alert
      v-if="view?.group_statistics_message"
      :title="result.group_statistics_message"
      type="warning"
      :closable="false"
    />
    <el-table
      :data="rows"
      border
      :row-class-name="rowClass"
      empty-text="班会统计暂不可用，请刷新重试"
    >
      <el-table-column prop="group_name" label="小组" min-width="180" />
      <el-table-column
        prop="roster_count"
        label="本组在册人数"
        min-width="140"
        align="right"
      />
      <el-table-column
        prop="present_count"
        label="本次班会去重到场人数"
        min-width="190"
        align="right"
      />
      <el-table-column label="班会小组参会率" min-width="160" align="right"
        ><template #default="{ row }">{{
          participationRateLabel(row.participation_rate, row.isTotal ? 1 : 0)
        }}</template></el-table-column
      >
    </el-table>
    <p class="basis">
      {{
        view?.group_statistics_basis ||
        "当前正式在册人数为分母；班级包含未分组学长。总小组参会率按已分组人数汇总计算。"
      }}
    </p>
  </section>
</template>

<style scoped>
.participation-summary {
  margin-top: 24px;
  padding: 20px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
}
h3 {
  margin: 0 0 8px;
  font-size: 17px;
}
.basis {
  margin: 8px 0 16px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.participation-rates {
  display: flex;
  flex-wrap: wrap;
  gap: 48px;
  margin: 20px 0;
}
.participation-rates > div {
  display: grid;
  gap: 8px;
  min-width: 180px;
}
.participation-rates span,
.participation-rates small {
  color: var(--el-text-color-secondary);
}
.participation-rates strong {
  font-size: 28px;
  font-weight: 600;
}
:deep(.class-total-row) {
  --el-table-tr-bg-color: var(--el-fill-color-light);
  font-weight: 600;
}
</style>
