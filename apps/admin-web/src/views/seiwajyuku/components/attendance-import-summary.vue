<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  headerRow: number;
  columns: Record<string, number>;
  activityType?: string;
  quality: {
    member_count: number;
    valid_phone_count: number;
    missing_phone_count: number;
    invalid_phone_count: number;
    repeated_slots: number;
    valid_dinner_table_count: number;
    invalid_checkin_badge_count: number;
  };
}>();
const fields = [
  { key: "name", label: "姓名（必填）" },
  { key: "phone", label: "手机号（选填）" },
  { key: "company", label: "公司" },
  { key: "center", label: "分中心" },
  { key: "class_name", label: "班级" },
  { key: "group", label: "现场分组" },
  { key: "dinner_table_num", label: "餐桌号" },
  { key: "checkin_badge", label: "签到标识" }
];
const displayedFields = computed(() =>
  fields.map(field => {
    const keys =
      field.key === "group" ? ["group_name", "group_num"] : [field.key];
    const positions = [
      ...new Set(
        keys.map(key => props.columns[key]).filter(index => index >= 0)
      )
    ].sort((a, b) => a - b);
    const emptyDinnerTable =
      field.key === "dinner_table_num" && props.quality.valid_dinner_table_count === 0;
    const recognized = positions.length > 0 && !emptyDinnerTable;
    return {
      ...field,
      label:
        field.key === "group" && props.activityType === "class_meeting"
          ? "小组"
          : field.label,
      recognized,
      state: recognized ? "已识别" : emptyDinnerTable && positions.length > 0 ? "无有效数据" : field.key === "name" ? "缺少必填列" : "未提供",
      position: recognized
        ? positions.map(index => `第 ${index + 1} 列`).join(" / ")
        : ""
    };
  })
);
</script>

<template>
  <section class="import-summary" aria-label="报名表识别结果">
    <p class="header-row">自动识别表头：第 {{ headerRow }} 行</p>
    <div class="column-status-list" aria-label="列识别">
      <div
        v-for="field in displayedFields"
        :key="field.key"
        class="column-status"
      >
        <span>{{ field.label }}</span>
        <el-tag size="small" :type="field.recognized ? 'success' : field.key === 'name' ? 'danger' : field.state === '无有效数据' ? 'warning' : 'info'">
          {{ field.recognized ? `已识别 · ${field.position}` : field.state }}
        </el-tag>
      </div>
    </div>
    <div class="read-status" role="status">
      <div class="read-title">✓ 活动名单已读取</div>
      <p>
        报名名单共 {{ quality.member_count }} 条；姓名为必填，手机号正常
        {{ quality.valid_phone_count }} 条。
      </p>
      <p v-if="quality.missing_phone_count || quality.invalid_phone_count" class="quality-warning">
        手机号缺失 {{ quality.missing_phone_count }} 条，格式异常
        {{ quality.invalid_phone_count }} 条；手机号问题不阻断导入。
      </p>
      <p v-if="quality.repeated_slots">
        重复报名名额 {{ quality.repeated_slots }} 个，分别保留为独立报名位。
      </p>
      <p v-if="quality.invalid_checkin_badge_count">
        签到标识有 {{ quality.invalid_checkin_badge_count }} 条无法识别，导入时按空白处理。
      </p>
    </div>
    <p class="summary-hint">可选列留空可以正常导入。</p>
    <p class="summary-hint">在 Excel 添加「签到标识」列，每人填写一项：熟生、非熟生、特邀嘉宾、桌长。</p>
  </section>
</template>

<style scoped>
.import-summary {
  margin: 16px 0;
  padding: 18px 20px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  background: var(--el-fill-color-extra-light);
}
.header-row {
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 600;
}
.column-status-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 8px 16px;
}
.column-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
}
.quality-warning { color: #795217; }
.read-status {
  margin-top: 18px;
  padding: 14px 16px;
  border-radius: 10px;
  background: var(--el-color-success-light-9);
  color: var(--el-text-color-regular);
  font-size: 14px;
  line-height: 1.7;
}
.read-title {
  margin-bottom: 5px;
  font-weight: 600;
  color: var(--el-color-success-dark-2);
}
.summary-hint {
  margin-top: 12px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
