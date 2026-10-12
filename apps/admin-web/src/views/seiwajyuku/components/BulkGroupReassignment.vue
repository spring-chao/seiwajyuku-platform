<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { getGroupReassignmentRoster, previewGroupReassignment, applyGroupReassignment,
  type GroupRoster, type GroupReassignmentPreview } from "@/api/member-group-reassignment";
import { studyMeetingErrorMessage } from "@/utils/studyMeetingError";

const props = defineProps<{ classes: { id: string; name: string }[]; initialClass?: string }>();
const emit = defineEmits<{ saved: [] }>();
const visible = ref(false);
const classId = ref("");
const roster = ref<GroupRoster>();
const loading = ref(false);
const saving = ref(false);
const targets = ref<Record<number, string>>({});
const selected = ref<GroupRoster["members"]>([]);
const unifiedTarget = ref("");
const reason = ref("");
const preview = ref<GroupReassignmentPreview>();
const assignments = computed(() => (roster.value?.members || [])
  .filter(item => targets.value[item.id] && targets.value[item.id] !== item.group_org_unit_id)
  .map(item => ({ member_id: item.id, group_org_unit_id: targets.value[item.id] })));
watch(targets, () => { preview.value = undefined; }, { deep: true });
let generation = 0;
const loadRoster = async () => {
  const request = ++generation;
  roster.value = undefined;
  targets.value = {};
  preview.value = undefined;
  selected.value = [];
  unifiedTarget.value = "";
  if (!classId.value) { loading.value = false; return; }
  loading.value = true;
  try {
    const response = await getGroupReassignmentRoster(classId.value);
    if (request === generation && visible.value) roster.value = response.data;
  } catch (error) {
    if (request === generation) ElMessage.error(studyMeetingErrorMessage(error, "班级名单加载"));
  } finally { if (request === generation) loading.value = false; }
};
const open = () => {
  visible.value = true;
  reason.value = "";
  classId.value = props.initialClass || "";
  void loadRoster();
};
const assignSelected = () => {
  if (!unifiedTarget.value || !selected.value.length) return;
  selected.value.forEach(item => { if (!item.unavailable) targets.value[item.id] = unifiedTarget.value; });
};
const runPreview = async () => {
  if (!assignments.value.length) { ElMessage.warning("请先为需要调组的学员选择新小组"); return; }
  loading.value = true;
  const request = generation;
  try {
    const response = await previewGroupReassignment({ class_org_unit_id: classId.value, assignments: assignments.value });
    if (request === generation && visible.value) preview.value = response.data;
  } catch (error) { if (request === generation) ElMessage.error(studyMeetingErrorMessage(error, "调组预览")); }
  finally { if (request === generation) loading.value = false; }
};
const save = async () => {
  if (!preview.value || saving.value) return;
  if (!reason.value.trim()) { ElMessage.warning("请填写调组原因"); return; }
  saving.value = true;
  try {
    const response = await applyGroupReassignment({ class_org_unit_id: classId.value, assignments: assignments.value,
      preview_fingerprint: preview.value.preview_fingerprint, reason: reason.value.trim() });
    ElMessage.success(`已调整 ${response.data.changed_count} 名学员的小组，变更已记录`);
    visible.value = false;
    emit("saved");
  } catch (error) {
    preview.value = undefined;
    ElMessage.error(studyMeetingErrorMessage(error, "批量调组"));
  } finally { saving.value = false; }
};
</script>

<template>
  <el-button size="large" @click="open">批量调整小组</el-button>
  <el-dialog v-model="visible" class="group-reassignment-dialog" title="批量调整小组" width="min(900px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="generation++">
    <el-alert title="适用于班级重新开班、打乱重组。选择本班新小组并预览后保存，历史学习会和学分保留原记录。新小组请先在班级与小组管理中创建。" type="info" :closable="false" />
    <div class="group-toolbar">
      <el-select v-model="classId" filterable placeholder="选择需要重组的班级" :disabled="loading || saving" @change="loadRoster">
        <el-option v-for="item in props.classes" :key="item.id" :value="item.id" :label="item.name" />
      </el-select>
      <span v-if="roster">在册 {{ roster.members.length }} 人 · 已安排变更 {{ assignments.length }} 人</span>
    </div>
    <template v-if="roster">
      <div class="group-toolbar">
        <el-select v-model="unifiedTarget" placeholder="勾选人员统一调入" :disabled="loading || saving">
          <el-option v-for="item in roster.groups" :key="item.id" :value="item.id" :label="item.name" />
        </el-select>
        <el-button :disabled="!selected.length || !unifiedTarget || loading || saving" @click="assignSelected">应用到勾选 {{ selected.length }} 人</el-button>
      </div>
      <el-table :data="roster.members" :max-height="360" row-key="id" v-loading="loading" @selection-change="selected = $event">
        <el-table-column type="selection" :selectable="(row: GroupRoster['members'][number]) => !row.unavailable && !loading && !saving" width="45" />
        <el-table-column prop="name" label="姓名" width="120" />
        <el-table-column prop="member_code" label="学员编号" width="160" />
        <el-table-column prop="group_name" label="原小组" />
        <el-table-column label="新小组" min-width="220">
          <template #default="{ row }">
            <el-select v-model="targets[row.id]" clearable placeholder="保持原小组" :disabled="row.unavailable || loading || saving">
              <el-option v-for="item in roster.groups" :key="item.id" :value="item.id" :label="item.name" />
            </el-select>
          </template>
        </el-table-column>
      </el-table>
    </template>
    <div v-if="preview" class="group-preview">
      <h3>确认以下 {{ preview.changed_count }} 人的调组安排</h3>
      <el-table :data="preview.rows" max-height="240">
        <el-table-column prop="name" label="姓名" />
        <el-table-column prop="old_group_name" label="原小组" />
        <el-table-column prop="new_group_name" label="新小组" />
      </el-table>
      <el-input v-model="reason" placeholder="调组原因，例如：班级重新开班，小组打乱重组" maxlength="1000" :disabled="saving" />
    </div>
    <template #footer>
      <el-button :disabled="saving" @click="visible = false">取消</el-button>
      <el-button :loading="loading" :disabled="saving || !assignments.length || assignments.length > 500" @click="runPreview">预览调组</el-button>
      <el-button type="primary" :loading="saving" :disabled="!preview || loading" @click="save">确认保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.group-toolbar { display: flex; align-items: center; gap: 12px; margin: 16px 0; }
.group-toolbar .el-select { width: 280px; }
.group-preview { margin-top: 20px; }
.group-preview .el-input { margin-top: 12px; }
</style>

<style>
.group-reassignment-dialog { max-height: 90vh; margin-top: 5vh; display: flex; flex-direction: column; }
.group-reassignment-dialog .el-dialog__body { overflow: auto; }
</style>
