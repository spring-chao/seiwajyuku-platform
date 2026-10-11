<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { creditSettlementErrorMessage } from "@/utils/creditSettlementError";
import {
  getYearWorkbench,
  previewYearFile,
  submitYearFile,
  processYearImport,
  setupYearStorage,
  setupReconciliationStorage,
  type YearWorkbench,
  type YearPreview,
  type YearImport,
  type YearForm
} from "@/api/credit-year-allocations";
const emit = defineEmits<{ posted: [] }>();
const state = ref<YearWorkbench>();
const file = ref<File>();
const preview = ref<YearPreview>();
const detail = ref<YearImport>();
const busy = ref(false);
const error = ref("");
const aliases = ref("");
const statuses: Record<string, string> = {
  PENDING_APPROVAL: "待复核",
  APPROVED: "待确认归属",
  POSTED: "年度归属已完成",
  CANCELLED: "已取消"
};
let generation = 0;
const form = reactive<YearForm>({
  binding_id: undefined,
  year_index: 2,
  cutoff_date: "",
  source_note: "",
  mode: "CLASSIFY_ONLY",
  name_overrides_json: "{}"
});
watch(
  [
    file,
    () => form.binding_id,
    () => form.year_index,
    () => form.cutoff_date,
    () => form.source_note,
    () => form.mode,
    aliases
  ],
  () => {
    generation++;
    preview.value = undefined;
  }
);
const reload = async () => {
  try {
    state.value = (await getYearWorkbench()).data;
  } catch (err) {
    error.value = creditSettlementErrorMessage(err, "年度归属读取失败");
  }
};
const perform = async (action: () => Promise<void>) => {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (err) {
    error.value = creditSettlementErrorMessage(
      err,
      "操作未完成，请刷新核对实际状态"
    );
  } finally {
    busy.value = false;
  }
};
const choose = (event: Event) => {
  file.value = (event.target as HTMLInputElement).files?.[0];
};
const uploadForm = () => {
  const mappings: Record<string,string> = {};
  for (const line of aliases.value.split(/\r?\n/).filter(line => line.trim())) {
    const parts = line.split("=").map(v => v.trim());
    if (parts.length !== 2 || !parts[0] || !parts[1] || parts[0] in mappings) throw new Error("对应关系每行填写：原表姓名=学长编号，姓名不可重复");
    mappings[parts[0]] = parts[1];
  }
  return { ...form, name_overrides_json: form.mode === "RECONCILE_TO_SOURCE" ? JSON.stringify(mappings) : "{}" };
};
const storageReady = () => !!state.value?.storage_available && (form.mode !== "RECONCILE_TO_SOURCE" || !!state.value?.reconciliation_available);
const check = () =>
  perform(async () => {
    if (
      !file.value ||
      !form.binding_id ||
      !form.cutoff_date ||
      form.source_note.trim().length < 8
    )
      return;
    const current = generation;
    const result = await previewYearFile(file.value, uploadForm());
    if (current === generation) preview.value = result.data;
  });
const submit = () =>
  perform(async () => {
    if (
      !file.value ||
      !preview.value?.fingerprint ||
      !storageReady()
    )
      return;
    await submitYearFile(file.value, uploadForm(), preview.value.fingerprint);
    preview.value = undefined;
    ElMessage.success("来源已提交，请在下方核对年度原分值和本次补入分数，再确认入账。");
    await reload();
  });
const setup = () =>
  perform(async () => {
    if (!state.value?.setup_allowed) return;
    // Persist before dispatch so a lost response/reload never repeats DDL.
    const key = "credit-year-storage-0070";
    if (localStorage.getItem(key))
      throw new Error("存储准备已提交，请刷新核验实际结果，不重复执行");
    localStorage.setItem(
      key,
      JSON.stringify({
        commit: state.value.release_commit,
        at: new Date().toISOString()
      })
    );
    await setupYearStorage(state.value);
    await reload();
    ElMessage.success("年度归属存储已准备完成");
  });
const setupReconciliation = () => perform(async () => {
  if (!state.value?.reconciliation_setup_allowed) return;
  const key = "credit-year-storage-0071";
  if (localStorage.getItem(key)) throw new Error("補差存储准备已提交，请刷新核验实际结果，不重复执行");
  localStorage.setItem(key, JSON.stringify({ commit: state.value.release_commit, at: new Date().toISOString() }));
  await setupReconciliationStorage(state.value);
  await reload();
  ElMessage.success("年度补差存储已准备完成");
});
const resolveRow = (value: unknown) => {
  if (!value || typeof value !== 'object' || !('id' in value)) return;
  return state.value?.imports.find(item => item.id === value.id);
};
const showDetail = (value: unknown) => { detail.value = resolveRow(value); };
const process = async (
  value: unknown,
  operation: "approve" | "post" | "cancel"
) => {
  const row = resolveRow(value);
  if (!row) return;
  try {
    await ElMessageBox.confirm(
      `${row.class_name}，第${row.year_index}学年，${row.row_count}人，共${row.total_points}分。${operation === "post" ? `确认这一学年12次学习日已完成，截止${String(row.cutoff_date).slice(0, 10)}。下一学年从0分开始。${row.mode === "RECONCILE_TO_SOURCE" ? `本次仅补入${row.supplement_points}分，原余额和来源保留。` : "累计余额不增加。"}` : operation === "approve" ? "请核对原始分值、截止日与月份更正说明。" : "取消后来源记录保留。"}`,
      operation === "post"
        ? "确认年度归属"
        : operation === "approve"
          ? "复核年度来源"
          : "取消年度来源",
      {
        confirmButtonText:
          operation === "post"
            ? "确认归属"
            : operation === "approve"
              ? "确认复核"
              : "确认取消",
        cancelButtonText: "返回检查",
        type: "warning"
      }
    );
  } catch {
    return;
  }
  await perform(async () => {
    await processYearImport(row, operation);
    await reload();
    detail.value = undefined;
    if (operation === "post") emit("posted");
    ElMessage.success(
      operation === "post"
        ? (row.mode === "RECONCILE_TO_SOURCE" ? `年度归属已完成，本次补入${row.supplement_points}分，原记录保留` : "年度归属已完成，累计余额保持原值")
        : "来源状态已更新"
    );
  });
};
onMounted(reload);
</script>
<template>
  <el-card shadow="never" class="year-card">
    <template #header
      ><div class="heading">
        <strong>导入历史年度学分</strong
        ><el-button :disabled="busy" @click="reload">刷新年度记录</el-button>
      </div></template
    >
    <p>
      上传班级年度统计原表，核对姓名、截止日和总分。已有余额可以只标注年度；以年度原表为准时，只补不足的分数，原记录保留。每学年12次学习日完成后，下一学年从0分开始。
    </p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <div v-if="state && !state.storage_available">
      <p v-if="state.setup_incomplete">
        存储准备曾中断，请由技术人员核对实际结构并修复。
      </p>
      <el-button v-else-if="state.setup_allowed" :disabled="busy" @click="setup"
        >准备年度归属存储</el-button
      >
      <p v-else>年度归属存储待管理员准备。现在可以上传检查原表。</p>
    </div>
    <div v-if="state?.storage_available && !state.reconciliation_available">
      <p v-if="state.reconciliation_setup_incomplete">补差存储准备曾中断，请核对实际结构。</p>
      <el-button v-else-if="state.reconciliation_setup_allowed" :disabled="busy" @click="setupReconciliation">准备年度补差存储</el-button>
    </div>
    <el-form label-position="top" class="year-form">
      <el-form-item label="导入方式"><el-select v-model="form.mode" aria-label="年度导入方式" :disabled="busy">
        <el-option label="只标注已有余额的年度" value="CLASSIFY_ONLY" />
        <el-option label="以年度原表为准补齐缺额" value="RECONCILE_TO_SOURCE" />
      </el-select></el-form-item>
      <el-form-item v-if="form.mode === 'RECONCILE_TO_SOURCE'" label="原表姓名与后台编号对应（有差异时填写）">
        <el-input v-model="aliases" type="textarea" :rows="4" aria-label="年度姓名对应关系" placeholder="每行：原表姓名=学长编号" :disabled="busy" />
      </el-form-item>
      <el-form-item label="班级"
        ><el-select
          v-model="form.binding_id"
          aria-label="历史年度班级"
          placeholder="选择班级"
          :disabled="busy"
          ><el-option
            v-for="b in state?.bindings || []"
            :key="b.id"
            :label="b.class_name"
            :value="b.id" /></el-select
      ></el-form-item>
      <el-form-item label="原表所属学习年度"
        ><el-select
          v-model="form.year_index"
          aria-label="历史学习年度"
          :disabled="busy"
          ><el-option
            v-for="i in [1, 2, 3]"
            :key="i"
            :label="`第${i}学年`"
            :value="i" /></el-select
      ></el-form-item>
      <el-form-item label="统计截止日期"
        ><el-date-picker
          v-model="form.cutoff_date"
          type="date"
          value-format="YYYY-MM-DD"
          aria-label="年度统计截止日期"
          placeholder="选择统计截止日"
          :disabled="busy"
      /></el-form-item>
      <el-form-item label="年度和月份确认说明"
        ><el-input
          v-model="form.source_note"
          aria-label="年度归属确认说明"
          placeholder="例如：原表8月实际为9月，第二学年12次学习日已完成"
          :disabled="busy"
          maxlength="1000"
      /></el-form-item>
      <el-form-item label="班级年度统计原表"
        ><input
          type="file"
          aria-label="选择历史年度Excel"
          accept=".xlsx"
          :disabled="busy"
          @change="choose"
      /></el-form-item>
    </el-form>
    <p>
      原表工作表名为“第1学年学分统计”“第2学年学分统计”或“第3学年学分统计”，保留姓名和总分值。此入口适用于已完成该学年12次学习日的班级。未提供的更早年度显示“待补充”。
    </p>
    <el-button
      type="primary"
      plain
      :disabled="
        busy ||
        !file ||
        !form.binding_id ||
        !form.cutoff_date ||
        form.source_note.trim().length < 8
      "
      @click="check"
      >检查年度 Excel</el-button
    >
    <el-button
      type="primary"
      :disabled="busy || !preview?.fingerprint || !storageReady()"
      @click="submit"
      >提交年度复核</el-button
    >
    <div v-if="preview">
      <p>
        {{ preview.row_count }}人 · {{ preview.total_points }}分 ·
        {{ preview.error_count }}人待核对。归属第{{
          preview.year_index
        }}学年，已完成{{ preview.completed_days }}次学习日，进入第{{
          preview.current_year_after_import
        }}学年。{{ preview.mode === "RECONCILE_TO_SOURCE" ? `本次补入${preview.supplement_points}分，原记录保留。` : "累计分不增加。" }}
      </p>
      <el-table :data="preview.rows" max-height="330"
        ><el-table-column
          prop="excel_row"
          label="原表行号"
          width="100"
        /><el-table-column
          prop="source_name"
          label="原表姓名"
        /><el-table-column
          prop="member_code"
          label="匹配学长编号"
        /><el-table-column prop="previous_points" label="已有余额" /><el-table-column prop="points" label="原年度总分" /><el-table-column prop="supplement_points" label="本次补入" /><el-table-column
          label="需要处理"
          ><template #default="{ row }">{{
            (row.errors || []).join("；") || "核对通过"
          }}</template></el-table-column
        ></el-table
      >
    </div>
    <h3>年度归属记录</h3>
    <el-table :data="state?.imports || []">
      <el-table-column
        prop="original_filename"
        label="原文件"
      /><el-table-column prop="class_name" label="班级" /><el-table-column
        label="学习年度"
        ><template #default="{ row }"
          >第{{ row.year_index }}学年</template
        ></el-table-column
      >
      <el-table-column label="人数 / 学分"
        ><template #default="{ row }"
          >{{ row.row_count }}人 · {{ row.total_points }}分<span v-if="row.mode === 'RECONCILE_TO_SOURCE'">；补入{{ row.supplement_points }}分</span></template
        ></el-table-column
      >
      <el-table-column label="进度"
        ><template #default="{ row }">{{
          statuses[row.status] || row.status
        }}</template></el-table-column
      >
      <el-table-column label="操作" width="260"
        ><template #default="{ row }"
          ><el-button link @click="showDetail(row)">查看来源</el-button
          ><el-button
            v-if="row.status === 'PENDING_APPROVAL'"
            link
            :disabled="busy"
            @click="process(row, 'approve')"
            >复核</el-button
          ><el-button
            v-if="row.status === 'APPROVED'"
            link
            type="primary"
            :disabled="busy"
            @click="process(row, 'post')"
            >确认年度归属</el-button
          ><el-button
            v-if="['PENDING_APPROVAL', 'APPROVED'].includes(row.status)"
            link
            :disabled="busy"
            @click="process(row, 'cancel')"
            >取消</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-dialog
      :model-value="!!detail"
      title="历史年度来源"
      width="85%"
      @close="detail = undefined"
      ><p>{{ detail?.source_note }}</p>
      <el-table :data="detail?.rows || []" max-height="450"
        ><el-table-column prop="excel_row" label="原表行号" /><el-table-column
          prop="member_code"
          label="学长编号" /><el-table-column
          prop="member_name"
          label="姓名" /><el-table-column
          prop="points"
          label="原年度学分" /><el-table-column prop="source_name" label="原表姓名" /><el-table-column prop="previous_points" label="原余额" /><el-table-column prop="supplement_points" label="本次补入" /></el-table
    ></el-dialog>
  </el-card>
</template>
<style scoped>
.year-card {
  margin-top: 16px;
}
.heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.year-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 16px;
  margin-top: 16px;
}
.year-form :deep(.el-select) {
  width: 100%;
}
@media (max-width: 900px) {
  .year-form {
    grid-template-columns: 1fr;
  }
}
</style>
