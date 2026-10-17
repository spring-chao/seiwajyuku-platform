<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { http } from "@/utils/http";
import type { MemberListParams } from "@/api/seiwajyuku";

const props = defineProps<{ filters: MemberListParams; total: number }>();
const visible = ref(false);
const busy = ref(false);
const loading = ref(false);
const loadError = ref("");
const purpose = ref("");
const layout = ref("member");
const selected = ref(["member_code", "name", "management_org_name", "class_name", "group_name", "status"]);
interface ExportField {
  key: string;
  label: string;
  category: string;
  available: boolean;
  reason: string;
  personal: boolean;
  requires_purpose: boolean;
}
const fields = ref<ExportField[]>([]);
const available = computed(() => fields.value.filter(field => field.available && (layout.value === "member" || !field.personal)));
const categories = computed(() => [...new Set(fields.value.map(field => field.category))]);
const needsPurpose = computed(() => fields.value.some(field => selected.value.includes(field.key) && field.requires_purpose));
const allSelected = computed(() => available.value.length > 0 && available.value.every(field => selected.value.includes(field.key)));
function resetSelection() {
  const defaults = layout.value === "group"
    ? ["management_org_name", "class_name", "group_name", "name"]
    : ["member_code", "name", "management_org_name", "class_name", "group_name", "status"];
  selected.value = defaults.filter(key => available.value.some(field => field.key === key));
}
watch(layout, resetSelection);
function toggleAll() {
  selected.value = allSelected.value ? [] : available.value.map(field => field.key);
}
function disabledReason(field: ExportField) {
  return !field.available ? field.reason : layout.value === "group" && field.personal ? "个人字段请切换为一人一行" : "";
}
async function open() {
  visible.value = true;
  loading.value = true;
  loadError.value = "";
  fields.value = [];
  try {
    const result = await http.request<{ data: ExportField[] }>("get", "/api/v1/exports/members/fields");
    fields.value = result.data;
    resetSelection();
  } catch {
    selected.value = [];
    loadError.value = "字段加载失败，请重试";
  } finally { loading.value = false; }
}
async function download() {
  if (busy.value || loading.value || loadError.value || !selected.value.length) return;
  if (needsPurpose.value && purpose.value.trim().length < 4) {
    ElMessage.error("导出企业资料请填写用途（至少4个字符）");
    return;
  }
  busy.value = true;
  try {
    const blob = await http.request<Blob>("get", "/api/v1/exports/members/workbook", {
      params: { ...props.filters, layout: layout.value, fields: selected.value.join(","),
        ...(needsPurpose.value ? { purpose: purpose.value.trim() } : {}) },
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
  <el-button size="large" @click="open">导出学员列表</el-button>
  <el-dialog v-model="visible" title="导出学员列表" width="min(840px, 95vw)" top="5vh"
    :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
    <p>导出当前筛选条件下的全部结果（当前列表共 {{ total }} 人），不限于本页；最多 5000 人，以实际导出授权范围为准。</p>
    <el-form label-position="top">
      <el-form-item label="表格格式">
        <el-radio-group v-model="layout" :disabled="busy || loading">
          <el-radio value="member">一人一行</el-radio>
          <el-radio value="group">一组一行，姓名放在同一格</el-radio>
        </el-radio-group>
      </el-form-item>
      <p v-if="layout === 'group'">自动附带小组人数；未分组学员单独成行。个人字段请切换为“一人一行”选择。</p>
      <div class="field-toolbar">
        <strong>选择需要下载的字段</strong>
        <el-button link type="primary" :disabled="busy || loading || !!loadError" @click="toggleAll">{{ allSelected ? '取消全选' : '全选可选字段' }}</el-button>
        <span>已选 {{ selected.length }} 项</span>
      </div>
      <p v-if="loading" role="status">正在加载全部字段…</p>
      <p v-if="loadError" role="alert">{{ loadError }} <el-button link type="primary" @click="open">重新加载</el-button></p>
      <div v-else class="field-scroll">
        <el-checkbox-group v-model="selected" :disabled="busy || loading">
          <section v-for="category in categories" :key="category" class="field-category">
            <h4>{{ category }}</h4>
            <div class="field-grid">
              <div v-for="field in fields.filter(item => item.category === category)" :key="field.key" class="field-item">
                <el-checkbox :value="field.key" :disabled="!!disabledReason(field)" :title="disabledReason(field)">{{ field.label }}</el-checkbox>
                <small v-if="disabledReason(field)">{{ disabledReason(field) }}</small>
              </div>
            </div>
          </section>
        </el-checkbox-group>
      </div>
      <el-form-item v-if="needsPurpose" label="企业资料导出用途（至少4个字符）">
        <el-input v-model="purpose" maxlength="500" :disabled="busy" placeholder="请说明本次资料使用用途" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="busy" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="loading || !!loadError || !selected.length || total === 0" @click="download">下载 Excel</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.field-toolbar { display: flex; align-items: center; gap: 16px; margin: 12px 0; }
.field-toolbar span { margin-left: auto; color: var(--el-text-color-secondary); }
.field-scroll { max-height: 48vh; overflow-y: auto; padding-right: 12px; margin-bottom: 16px; }
.field-category { padding: 12px 0; border-top: 1px solid var(--el-border-color-lighter); }
.field-category h4 { margin: 0 0 8px; color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; }
.field-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px 12px; }
.field-item small { display: block; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.5; }
.field-item :deep(.el-checkbox) { max-width: 100%; height: auto; min-height: 32px; margin-right: 0; }
.field-item :deep(.el-checkbox__label) { white-space: normal; }
@media (max-width: 600px) { .field-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .field-toolbar { gap: 8px; flex-wrap: wrap; } }
</style>
