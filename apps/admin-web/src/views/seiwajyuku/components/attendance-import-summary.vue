<script setup lang="ts">
defineProps<{
  headerRow: number;
  columns: Record<string, number>;
  quality: {
    member_count: number;
    valid_phone_count: number;
    missing_phone_count: number;
    invalid_phone_count: number;
    repeated_slots: number;
  };
}>();
const fields = [
  { key: "name", label: "姓名（必填）" },
  { key: "phone", label: "手机号（选填）" },
  { key: "company", label: "公司" },
  { key: "center", label: "分中心" },
  { key: "class_name", label: "班级" },
  { key: "group_name", label: "小组" },
  { key: "group_num", label: "组号" },
  { key: "dinner_table_num", label: "桌号" }
];
</script>

<template>
  <section class="import-summary" aria-label="报名表识别结果">
    <p class="header-row">自动识别表头：第 {{ headerRow }} 行</p>
    <div class="column-status-list" aria-label="列识别">
      <el-checkbox
        v-for="field in fields"
        :key="field.key"
        class="column-status"
        :class="columns[field.key] >= 0 ? 'recognized' : 'unrecognized'"
        :model-value="columns[field.key] >= 0"
        disabled
      >
        {{ field.label }}：{{
          columns[field.key] >= 0 ? `第 ${columns[field.key] + 1} 列` : "未识别"
        }}
      </el-checkbox>
    </div>
    <div class="read-status" role="status">
      <div class="read-title">✓ 活动名单已读取</div>
      <p>
        报名名单共 {{ quality.member_count }} 条；姓名为必填，手机号正常
        {{ quality.valid_phone_count }} 条。
      </p>
      <p v-if="quality.missing_phone_count || quality.invalid_phone_count">
        手机号缺失 {{ quality.missing_phone_count }} 条，格式异常
        {{ quality.invalid_phone_count }} 条；手机号问题不阻断导入。
      </p>
      <p v-if="quality.repeated_slots">
        重复报名名额 {{ quality.repeated_slots }} 个，分别保留为独立报名位。
      </p>
    </div>
    <p class="summary-hint">勾选表示已识别该列；可选列未识别不影响导入。</p>
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
  height: auto;
  margin-right: 0;
  align-items: flex-start;
}
.column-status :deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.5;
}
.column-status :deep(.el-checkbox__input) {
  margin-top: 3px;
}
.column-status.recognized
  :deep(.el-checkbox__input.is-disabled + .el-checkbox__label) {
  color: var(--el-color-success-dark-2);
}
.recognized
  :deep(.el-checkbox__input.is-disabled.is-checked .el-checkbox__inner) {
  background: var(--el-color-success);
  border-color: var(--el-color-success);
}
.recognized
  :deep(.el-checkbox__input.is-disabled.is-checked .el-checkbox__inner::after) {
  border-color: white;
}
.column-status.unrecognized
  :deep(.el-checkbox__input.is-disabled + .el-checkbox__label) {
  color: var(--el-color-warning-dark-2);
}
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
