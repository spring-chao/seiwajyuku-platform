<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import { creditSettlementErrorMessage } from "@/utils/creditSettlementError";
import {
  downloadOpeningTemplate,
  getOpeningWorkbench,
  inspectOpeningFile,
  submitOpeningFile,
  prepareOpeningStorage,
  processOpeningImport,
  type OpeningImport,
  type OpeningPreview,
  type OpeningWorkbench
} from "@/api/credit-opening-balances";

const emit = defineEmits<{ posted: [] }>();
const state = ref<OpeningWorkbench | null>(null);
const loading = ref(false);
const busy = ref(false);
const error = ref("");
const file = ref<File | null>(null);
const cutoff = ref("");
const preview = ref<OpeningPreview | null>(null);
const detail = ref<OpeningImport | null>(null);
const checkedVersion = ref(0);
const userId = computed(() => state.value?.actor_user_id);
const canExport = computed(() =>
  (useUserStoreHook().permissions || []).some(p =>
    ["exports:normal", "plans:credit_opening_manage"].includes(p)
  )
);
const canSubmit = computed(
  () =>
    !!preview.value?.fingerprint &&
    preview.value.error_count === 0 &&
    state.value?.storage_available &&
    state.value?.enabled &&
    state.value?.can_approve
);
const statuses: Record<string, string> = {
  PENDING_APPROVAL: "待复核",
  APPROVED: "已复核，待入账",
  POSTED: "已正式入账",
  CANCELLED: "已取消，来源记录保留"
};
watch([file, cutoff], () => {
  preview.value = null;
  checkedVersion.value += 1;
});
const message = (err: unknown) =>
  creditSettlementErrorMessage(err, "操作未完成，请刷新核对状态");
const reload = async () => {
  loading.value = true;
  try {
    state.value = (await getOpeningWorkbench()).data;
  } catch (err) {
    error.value = message(err);
  } finally {
    loading.value = false;
  }
};
const chooseFile = (event: Event) => {
  file.value = (event.target as HTMLInputElement).files?.[0] || null;
};
const download = async () => {
  busy.value = true;
  error.value = "";
  try {
    const blob = await downloadOpeningTemplate();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "学长期初学分模板.xlsx";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    error.value = message(err);
  } finally {
    busy.value = false;
  }
};
const check = async () => {
  if (!file.value || !cutoff.value) return;
  const version = checkedVersion.value;
  busy.value = true;
  error.value = "";
  preview.value = null;
  try {
    const result = await inspectOpeningFile(file.value, cutoff.value);
    if (version === checkedVersion.value) preview.value = result.data;
  } catch (err) {
    error.value = message(err);
  } finally {
    busy.value = false;
  }
};
const submit = async () => {
  if (!canSubmit.value || !file.value || !preview.value?.fingerprint) return;
  busy.value = true;
  error.value = "";
  try {
    await submitOpeningFile(
      file.value,
      cutoff.value,
      preview.value.fingerprint
    );
    preview.value = null;
    file.value = null;
    ElMessage.success("已提交复核，尚未加分。请在下方查看并核对导入记录。");
  } catch (err) {
    error.value = message(err);
  } finally {
    await reload();
    busy.value = false;
  }
};
const setup = async () => {
  if (!state.value?.setup_allowed) return;
  busy.value = true;
  error.value = "";
  try {
    await prepareOpeningStorage(state.value);
    ElMessage.success("期初余额存储已准备完成，可以上传检查并提交复核");
  } catch (err) {
    error.value = message(err);
  } finally {
    await reload();
    busy.value = false;
  }
};
const process = async (
  row: OpeningImport,
  operation: "approve" | "post" | "cancel"
) => {
  try {
    await ElMessageBox.confirm(
      `${row.row_count}位学长，共${row.total_points}分，统计截止日${String(row.cutoff_date).slice(0, 10)}。${operation === "cancel" ? "取消后不会入账。原文件和操作历史保留，修正分值后的文件可重新上传。" : operation === "approve" ? "请确认已核对每位学长的累计分值。" : "确认后将计入学长累计学分，每位学长只录入一次。"}`,
      operation === "cancel"
        ? "取消本次导入"
        : operation === "approve"
          ? "复核期初余额"
          : "确认正式入账",
      {
        confirmButtonText:
          operation === "cancel"
            ? "确认取消导入"
            : operation === "approve"
              ? "确认复核"
              : "确认入账",
        cancelButtonText: "返回检查",
        type: "warning"
      }
    );
  } catch {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await processOpeningImport(row, operation);
    ElMessage.success(
      operation === "cancel"
        ? "已取消本次导入，原来源记录保留"
        : operation === "approve"
          ? "复核完成，等待入账人员处理"
          : "期初余额已正式入账"
    );
    detail.value = null;
    if (operation === "post") emit("posted");
  } catch (err) {
    error.value = message(err);
  } finally {
    await reload();
    busy.value = false;
  }
};
onMounted(reload);
</script>

<template>
  <el-card shadow="never" class="opening-card" v-loading="loading">
    <template #header
      ><div class="heading">
        <strong>用 Excel 录入已确认的累计学分</strong
        ><el-button :disabled="busy" @click="reload">刷新导入记录</el-button>
      </div></template
    >
    <p class="intro">
      适合把现有累计学分作为期初余额。先检查，再核对每位学长的分值，最后正式入账。之后的小程序直接展示累计学分。
    </p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <div v-if="state && !state.storage_available" class="setup">
      <p v-if="state.setup_incomplete">
        存储准备曾中断，系统已保留执行记录。请由技术人员核对实际存储后修复；刷新本页可查看结果。
      </p>
      <template v-else>
        <p>
          首次使用需要准备期初余额存储。管理员完成后，运营人员即可按下面三步导入。
        </p>
        <el-button
          v-if="state.setup_allowed"
          type="primary"
          :disabled="busy"
          :loading="busy"
          @click="setup"
          >准备期初余额存储</el-button
        >
        <span v-else
          >请由系统管理员完成本次发布设置；现在仍可下载模板和检查 Excel。</span
        >
      </template>
    </div>
    <p v-if="state?.storage_available && !state.enabled" class="notice">
      存储已准备完成。管理员需要启用期初余额导入；您现在可以下载模板并检查文件。
    </p>
    <div class="steps">
      <section>
        <h3>1. 下载模板，填写累计学分</h3>
        <p>
          系统预填编号、姓名和班级，您只填最后一列。空白表示暂不导入，已确认没有学分请填
          0。
        </p>
        <el-button
          type="primary"
          plain
          :disabled="busy || !canExport"
          @click="download"
          >下载带学长名册的 Excel 模板</el-button
        >
        <p v-if="!canExport">
          当前账号没有名册导出权限，请由有权限的运营人员下载模板。
        </p>
      </section>
      <section>
        <h3>2. 选择截止日期，上传检查</h3>
        <p>
          截止日期是这份累计分统计到哪一天。全表使用同一天，不能与已有正式学分重叠。
        </p>
        <el-date-picker
          v-model="cutoff"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="选择统计截止日期"
          :disabled="busy"
        />
        <label class="file-label"
          >选择填写后的 Excel<input
            type="file"
            accept=".xlsx"
            :disabled="busy"
            @change="chooseFile"
        /></label>
        <el-button
          :disabled="busy || !file || !cutoff"
          :loading="busy"
          @click="check"
          >检查 Excel</el-button
        >
      </section>
      <section>
        <h3>3. 提交复核，确认入账</h3>
        <p>
          检查无误后提交。学习条线专职人员核对分值，通过后确认入账；可以由上传人完成。上传本身不会加分。
        </p>
        <el-button type="primary" :disabled="busy || !canSubmit" @click="submit"
          >提交复核</el-button
        >
        <p v-if="!preview">先上传文件，查看检查结果。</p>
      </section>
    </div>
    <div v-if="preview" class="preview">
      <el-alert
        :title="
          preview.error_count
            ? `有${preview.error_count}行需要修改。按下表提示修改后重新检查。`
            : `检查通过：${preview.row_count}位学长，共${preview.total_points}分。空白跳过${preview.skipped_count}行。`
        "
        :type="preview.error_count ? 'error' : 'success'"
        :closable="false"
        show-icon
      />
      <el-table :data="preview.rows" max-height="420" row-key="excel_row">
        <el-table-column prop="excel_row" label="Excel 行" width="90" />
        <el-table-column prop="member_name" label="学长" width="120" />
        <el-table-column
          prop="class_label"
          label="班级/分中心"
          min-width="160"
        />
        <el-table-column prop="points" label="累计学分" width="110" />
        <el-table-column label="检查结果 / 解决方法" min-width="280"
          ><template #default="{ row }"
            ><span :class="row.errors?.length ? 'error-text' : ''">{{
              row.errors?.join("；") || "通过"
            }}</span></template
          ></el-table-column
        >
      </el-table>
    </div>
    <div v-if="state" class="records">
      <div class="heading">
        <h3>期初余额导入记录</h3>
        <span
          >已入账 {{ state.summary.entry_count }} 位学长 ·
          {{ state.summary.total_points }} 分</span
        >
      </div>
      <el-table
        :data="state.imports"
        row-key="id"
        empty-text="还没有提交文件，请先按上面三步操作。"
      >
        <el-table-column
          prop="original_filename"
          label="来源文件"
          min-width="180"
        />
        <el-table-column label="截止日期" width="120"
          ><template #default="{ row }">{{
            String(row.cutoff_date).slice(0, 10)
          }}</template></el-table-column
        >
        <el-table-column label="学长 / 累计分" width="160"
          ><template #default="{ row }"
            >{{ row.row_count }} 人 · {{ row.total_points }} 分</template
          ></el-table-column
        >
        <el-table-column label="进度" min-width="180"
          ><template #default="{ row }">{{
            statuses[row.status]
          }}</template></el-table-column
        >
        <el-table-column label="下一步" min-width="240"
          ><template #default="{ row }">
            <el-button
              link
              type="primary"
              @click="detail = row as OpeningImport"
              >查看每位学长</el-button
            >
            <el-button
              v-if="row.status === 'PENDING_APPROVAL' && state.can_approve"
              :disabled="busy"
              @click="process(row as OpeningImport, 'approve')"
              >复核通过</el-button
            >
            <el-button
              v-else-if="row.status === 'APPROVED' && state.can_post"
              type="primary"
              :disabled="busy"
              @click="process(row as OpeningImport, 'post')"
              >正式入账</el-button
            >
            <span v-else-if="row.status === 'PENDING_APPROVAL'"
              >请由学习条线专职人员复核</span
            >
            <span v-else-if="row.status === 'APPROVED'"
              >请由已授权入账人员确认</span
            >
            <el-button
              v-if="
                !['POSTED', 'CANCELLED'].includes(row.status) &&
                (row.created_by === userId || state.can_approve)
              "
              link
              type="danger"
              :disabled="busy"
              @click="process(row as OpeningImport, 'cancel')"
              >取消此导入</el-button
            >
          </template></el-table-column
        >
      </el-table>
    </div>
    <el-dialog
      :model-value="!!detail"
      title="核对每位学长的期初学分"
      width="850px"
      @close="detail = null"
    >
      <p v-if="detail">
        统计截止日 {{ String(detail.cutoff_date).slice(0, 10) }}，共
        {{ detail.total_points }} 分。
      </p>
      <el-table :data="detail?.rows || []" max-height="480"
        ><el-table-column
          prop="excel_row"
          label="Excel 行"
          width="90" /><el-table-column
          prop="member_code"
          label="学长编号"
          min-width="160" /><el-table-column
          prop="member_name"
          label="姓名"
          width="120" /><el-table-column
          prop="class_label"
          label="班级/分中心"
          min-width="160" /><el-table-column
          prop="points"
          label="累计学分"
          width="110"
      /></el-table>
      <template #footer>
        <el-button @click="detail = null">返回</el-button>
        <el-button
          v-if="
            detail?.status === 'PENDING_APPROVAL' &&
            state?.can_approve &&
            detail.created_by !== userId
          "
          type="primary"
          :disabled="busy"
          @click="process(detail, 'approve')"
          >已核对，复核通过</el-button
        >
        <el-button
          v-if="detail?.status === 'APPROVED' && state?.can_post"
          type="primary"
          :disabled="busy"
          @click="process(detail, 'post')"
          >确认正式入账</el-button
        >
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.opening-card {
  margin-bottom: 20px;
}
.heading {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
.intro,
.steps p {
  color: #606266;
  line-height: 1.7;
}
.setup,
.notice {
  padding: 12px 16px;
  background: #f0f7f4;
  border-radius: 6px;
  margin: 16px 0;
}
.steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  margin: 20px 0;
}
.steps section {
  padding: 16px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}
h3 {
  font-size: 16px;
  margin: 0 0 12px;
}
.file-label {
  display: block;
  margin: 16px 0;
  line-height: 1.8;
}
.file-label input {
  display: block;
  max-width: 100%;
}
.preview,
.records {
  margin-top: 24px;
}
.error-text {
  color: #b42318;
}
@media (max-width: 1100px) {
  .steps {
    grid-template-columns: 1fr;
  }
}
</style>
