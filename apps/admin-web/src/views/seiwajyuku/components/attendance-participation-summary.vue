<script setup lang="ts">
import { computed } from "vue";
import {
  participationTableRows,
  participationRateLabel
} from "@/utils/attendanceManagement.mjs";

const props = defineProps<{ result?: Record<string, any> | null }>();
const rows = computed(() => participationTableRows(props.result || {}));
const rowClass = ({ row }: { row: { isTotal?: boolean } }) =>
  row.isTotal ? "class-total-row" : "";
</script>

<template>
  <section class="participation-summary">
    <h3>本次班会参会统计</h3>
    <p class="basis">
      上午、下午合并去重。上方六项指标为当前场次，下表与
      Excel「小组参会统计」一致。
    </p>
    <div class="participation-rates">
      <div>
        <span>总小组参会率</span
        ><strong>{{
          participationRateLabel(result?.group_totals?.participation_rate)
        }}</strong
        ><small
          >{{ result?.group_totals?.present_count ?? "—" }} /
          {{ result?.group_totals?.roster_count ?? "—" }} 人</small
        >
      </div>
      <div>
        <span>整体参会率（含本班师资）</span
        ><strong>{{
          participationRateLabel(
            result?.class_statistics?.participation_rate,
            1
          )
        }}</strong
        ><small
          >{{ result?.class_statistics?.present_count ?? "—" }} /
          {{ result?.class_statistics?.roster_count ?? "—" }} 人</small
        >
      </div>
    </div>
    <p
      v-if="result?.class_statistics?.includes_formal_class_staff"
      class="basis"
    >
      本班在册学长到场
      {{
        result.class_statistics.member_present_count
      }}
      人；额外计入本班正式师资
      {{
        result.class_statistics.staff_present_count
      }}
      人。小组率仅统计在册学长，整体参会率沿用活动签到的师资例外口径。
    </p>
    <el-alert
      v-if="result?.group_statistics_message"
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
        result?.group_statistics_basis ||
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
