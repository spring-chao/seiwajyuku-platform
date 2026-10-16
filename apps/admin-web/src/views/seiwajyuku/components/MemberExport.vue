<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { http } from "@/utils/http";
import type { MemberListParams } from "@/api/seiwajyuku";

const props = defineProps<{ filters: MemberListParams; total: number }>();
const visible = ref(false);
const busy = ref(false);
const layout = ref("member");
const selected = ref(["member_code", "name", "management_org_name", "class_name", "group_name", "status"]);
const fields = [
  { key: "member_code", label: "学员编号", personal: true },
  { key: "name", label: "姓名" }, { key: "shuku_name", label: "塾" },
  { key: "management_org_name", label: "分中心/指导团" },
  { key: "class_name", label: "班级" }, { key: "group_name", label: "小组" },
  { key: "phone_masked", label: "手机号（脱敏）", personal: true },
  { key: "status", label: "状态", personal: true }
];
const available = computed(() => fields.filter(field => layout.value === "member" || !field.personal));
watch(layout, () => {
  selected.value = layout.value === "group"
    ? ["management_org_name", "class_name", "group_name", "name"]
    : ["member_code", "name", "management_org_name", "class_name", "group_name", "status"];
});
async function download() {
  if (busy.value || !selected.value.length) return;
  busy.value = true;
  try {
    const blob = await http.request<Blob>("get", "/api/v1/exports/members/workbook", {
      params: { ...props.filters, layout: layout.value, fields: selected.value.join(",") },
      responseType: "blob", timeout: 60000
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = layout.value === "group" ? "学员列表-小组汇总.xlsx" : "学员列表.xlsx";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    visible.value = false;
  } catch (error: any) {
    let message = error?.response?.data?.detail;
    if (error?.response?.data instanceof Blob) {
      try { message = JSON.parse(await error.response.data.text()).detail; } catch { /* use fallback */ }
    }
    ElMessage.error(message || "学员导出失败，请稍后重试");
  } finally { busy.value = false; }
}
</script>

<template>
  <el-button size="large" @click="visible = true">导出学员列表</el-button>
  <el-dialog v-model="visible" title="导出学员列表" width="min(620px, 95vw)"
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
    <p>导出当前筛选条件下的全部结果（当前列表共 {{ total }} 人），不限于本页；最多 5000 人，以实际导出授权范围为准。</p>
    <el-form label-position="top">
      <el-form-item label="表格格式">
        <el-radio-group v-model="layout" :disabled="busy">
          <el-radio value="member">一人一行</el-radio>
          <el-radio value="group">一组一行，姓名放在同一格</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="选择需要下载的字段">
        <el-checkbox-group v-model="selected" :disabled="busy">
          <el-checkbox v-for="field in available" :key="field.key" :value="field.key">{{ field.label }}</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <p v-if="layout === 'group'">自动附带小组人数；未分组学员单独成行。</p>
    </el-form>
    <template #footer>
      <el-button :disabled="busy" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!selected.length || total === 0" @click="download">下载 Excel</el-button>
    </template>
  </el-dialog>
</template>
