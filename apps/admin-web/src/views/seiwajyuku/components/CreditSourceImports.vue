<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import { getMembers, type Member } from "@/api/seiwajyuku";
import { creditSettlementErrorMessage } from "@/utils/creditSettlementError";
import {
  sourceWorkbench,
  sourceGet,
  sourceExportOptions,
  sourceTemplate,
  sourceExport,
  sourcePreview,
  sourceRegister,
  sourceCreateBatches,
  sourceAction,
  sourceReconcile,
  sourceSetup,
  sourcePrepareCalendar,
  type SourceKind,
  type SourceWorkbench,
  type SourcePreview,
  type SourceImport,
  type SourceForm
} from "@/api/credit-source-imports";
const emit = defineEmits<{ posted: [] }>();
const permissions = computed(() => useUserStoreHook().permissions || []);
const canManage = computed(() =>
  [
    "members:read",
    "plans:credit_settlement_manage",
    "plans:credit_settlement_preview"
  ].every(p => permissions.value.includes(p))
);
const canExport = computed(() =>
  ["exports:normal", "members:read", "plans:credit_settlement_preview"].every(
    p => permissions.value.includes(p)
  )
);
const state = ref<SourceWorkbench>();
const preview = ref<SourcePreview>();
const detail = ref<SourceImport>();
const file = ref<File>();
const busy = ref(false);
const posting = ref(false);
const stopRequested = ref(false);
const canReconcile = computed(() => permissions.value.includes("plans:credit_settlement_reconcile"));
const error = ref("");
const exportClasses = ref<{ id: string; name: string; center_name?: string }[]>([]);
const form = reactive({
  kind: "CLASS_MEETING" as SourceKind,
  class_id: "",
  cycle_id: undefined as number | undefined,
  actual_start: "",
  source_note: ""
});
const exportForm = reactive({ class_id: "", from: "2026-10-01", to: "", member_id: undefined as number | undefined });
const exportMembers = ref<Member[]>([]);
const searchingMembers = ref(false);
let memberSearch = 0;
async function searchExportMembers(keyword: string) {
  const generation = ++memberSearch;
  searchingMembers.value = true;
  try {
    const response = await getMembers({ keyword: keyword.trim() || undefined,
      class_org_unit_id: exportForm.class_id || undefined, status: "ALL", page_size: 20 });
    if (generation === memberSearch) exportMembers.value = response.data.items;
  } catch { if (generation === memberSearch) ElMessage.error("学员检索失败，请重试"); }
  finally { if (generation === memberSearch) searchingMembers.value = false; }
}
watch(() => exportForm.class_id, () => {
  memberSearch++;
  exportForm.member_id = undefined;
  exportMembers.value = [];
});
const slotLabel = (value: string) =>
  (
    ({ MORNING: "上午", AFTERNOON: "下午", KONPA: "空巴" }) as Record<
      string,
      string
    >
  )[value] || value;
const batchSummary = (value: unknown) => {
  const r = resolveRow(value);
  if (r?.source_only) return "原表已保存，未完成录音，无需入账";
  if (!r?.batches.length) return "来源已保存，待生成结算";
  const states = [...new Set(r.batches.filter(b => b.proposed_entry_count > 0).map(b => statuses[b.status] || b.status))];
  return (states.join("、") || "无新增学分") +
    (r.settlement_summary ? ` · 预计${r.settlement_summary.proposed_points}分 / 已入账${r.settlement_summary.posted_points}分` : "");
};
const choose = (event: Event) => {
  file.value = (event.target as HTMLInputElement).files?.[0];
};
const prepareCalendar = () =>
  perform(async () => {
    await sourcePrepareCalendar();
    await reload();
    ElMessage.success("读书工作日日历已准备，现有发布版本保留");
  });
const sourceDetails = (value: unknown) => {
  if (!value || typeof value !== "object") return "";
  const row = value as { record_json?: string; raw?: Record<string, unknown> };
  let record = row.raw;
  if (!record && row.record_json) {
    try {
      record = JSON.parse(row.record_json);
    } catch {
      return "来源内容待核对";
    }
  }
  if (!record || typeof record !== "object") return "";
  if (record.state) return String(record.state);
  return [
    "上午状态",
    "下午状态",
    "空巴状态",
    "已完成录音",
    "分享编号",
    "分享标题",
    "已评定优秀"
  ]
    .filter(k => record[k] != null)
    .map(k => `${k}：${record[k]}`)
    .join("；");
};
const labels: Record<string, string> = {
  CLASS_MEETING: "班会线下签到",
  DAILY_READING: "读书打卡",
  EXCELLENT_SHARE: "优秀分享"
};
const statuses: Record<string, string> = {
  DRY_RUN: "待确认入账",
  PENDING_APPROVAL: "待确认入账",
  APPROVED: "待入账",
  POSTING: "入账中",
  POSTED: "已入账",
  PARTIAL_FAILED: "部分失败，请核对",
  CLOSED: "已封账",
  CANCELLED: "已取消"
};
const rowStatuses: Record<string, string> = {
  READY: "可计分",
  NO_CREDIT: "不计分",
  DUPLICATE: "已有学分，跳过",
  SKIPPED_DUPLICATE: "已有学分，跳过",
  BLOCKED: "需修正"
};
const reasonLabels: Record<string, string> = {
  NOT_BUSINESS_DAY: "非工作日",
  NOT_BUSINESS_WORKDAY: "非工作日",
  FACT_NOT_CREDITABLE: "未完成录音或未确认完成",
  RECORDING_NOT_CONFIRMED: "未完成录音",
  PARTICIPATION_NOT_CONFIRMED: "未确认完成",
  MONTHLY_CAP_REACHED: "已达当月上限",
  DAILY_ALREADY_POSTED: "当天已入账",
  ALREADY_POSTED: "已入账",
  ZERO_POINTS: "本次无学分",
  FACT_CANCELLED: "未确认完成"
};
const cycles = computed(
  () =>
    state.value?.cycles.filter(c => c.class_org_unit_id === form.class_id) || []
);
const canImport = computed(() =>
  form.kind === "CLASS_MEETING"
    ? state.value?.can_import_attendance
    : state.value?.can_import_activity
);
let generation = 0;
watch(
  [
    file,
    () => form.kind,
    () => form.class_id,
    () => form.cycle_id,
    () => form.actual_start
  ],
  () => {
    generation++;
    preview.value = undefined;
  }
);
watch(
  () => form.class_id,
  () => {
    form.cycle_id = undefined;
    form.actual_start = "";
  }
);
const perform = async (action: () => Promise<void>) => {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await action();
  } catch (err) {
    error.value = creditSettlementErrorMessage(
      err,
      "操作未完成，请刷新查看实际结果；保存来源可用同一检查结果重试，不会重复保存"
    );
    ElMessage.error(error.value);
  } finally {
    busy.value = false;
  }
};
const reload = async () => {
  const tasks: { label: string; run: () => Promise<void> }[] = [];
  if (canManage.value)
    tasks.push({
      label: "来源记录读取失败",
      run: async () => {
        state.value = (await sourceWorkbench()).data;
      }
    });
  if (canExport.value)
    tasks.push({
      label: "导出班级读取失败",
      run: async () => {
        exportClasses.value = (await sourceExportOptions()).data.classes;
      }
    });
  const results = await Promise.allSettled(tasks.map(task => task.run()));
  const failures = results.flatMap((result, index) =>
    result.status === "rejected"
      ? [creditSettlementErrorMessage(result.reason, tasks[index].label)]
      : []
  );
  if (failures.length) error.value = failures.join("；");
};
const download = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
const getTemplate = () =>
  perform(async () => {
    if (!form.class_id) throw new Error("请先选择班级");
    download(
      await sourceTemplate(form.kind, form.class_id),
      labels[form.kind] + "模板.xlsx"
    );
  });
const uploadForm = (): SourceForm => {
  if (!form.class_id) throw new Error("请先选择班级");
  if (form.kind === "CLASS_MEETING" && (!form.cycle_id || !form.actual_start))
    throw new Error("请选择本次学习日并填写实际班会开始时间");
  return {
    kind: form.kind,
    class_id: form.class_id,
    source_note: form.source_note,
    options_json:
      form.kind === "CLASS_MEETING"
        ? JSON.stringify({
            cycle_id: form.cycle_id,
            actual_start: form.actual_start.replace(" ", "T") + "+08:00"
          })
        : "{}"
  };
};
const check = () =>
  perform(async () => {
    if (!file.value) throw new Error("请先选择填写后的.xlsx表格");
    const current = generation;
    const result = await sourcePreview(file.value, uploadForm());
    if (current === generation) preview.value = result.data;
  });
const register = () =>
  perform(async () => {
    if (
      !file.value ||
      !preview.value?.fingerprint ||
      !state.value?.storage_available ||
      !canImport.value
    )
      return;
    const saved = await sourceRegister(file.value, uploadForm(), preview.value.fingerprint);
    preview.value = undefined;
    await reload();
    ElMessage.success(saved?.data?.idempotent ? "这份来源已保存，已打开原记录；本次没有新增学分" : "来源已保存，请生成结算并核对每人分数后确认入账");
    if (saved?.data?.id) detail.value = (await sourceGet(saved.data.id)).data;
  });
const resolveRow = (value: unknown) => {
  if (!value || typeof value !== "object" || !("id" in value)) return;
  if (detail.value?.id === value.id) return detail.value;
  return state.value?.imports.find(r => r.id === value.id);
};
const updateReceipt = (receipt: SourceImport) => {
  detail.value = receipt;
  const index = state.value?.imports.findIndex(r => r.id === receipt.id) ?? -1;
  if (state.value && index >= 0) state.value.imports[index] = receipt;
};
const refreshDetail = () => perform(async () => {
  if (detail.value) updateReceipt((await sourceGet(detail.value.id)).data);
  await reload();
});
const recover = () => perform(async () => {
  if (!detail.value || !canReconcile.value) return;
  await ElMessageBox.confirm("只核对中断状态，不新增学分。系统会检查已有账本；最近30分钟仍有处理进度时不会恢复。", "核对中断状态");
  updateReceipt((await sourceReconcile(detail.value.id)).data);
  await reload();
  ElMessage.success("中断状态已核对，请检查剩余分数后继续入账");
});
const show = (value: unknown) =>
  perform(async () => {
    const row = resolveRow(value);
    if (row) detail.value = (await sourceGet(row.id)).data;
  });
const eligible = (value: unknown) => {
  const r = resolveRow(value);
  const active = r?.batches.filter(b => b.proposed_entry_count > 0) || [];
  return !!r && permissions.value.includes("plans:credit_settlement_post") &&
    active.some(b => ["DRY_RUN", "PENDING_APPROVAL", "APPROVED", "PARTIAL_FAILED"].includes(b.status)) &&
    active.every(b => ["DRY_RUN", "PENDING_APPROVAL", "APPROVED", "POSTED", "PARTIAL_FAILED"].includes(b.status));
};
const periodLabel = (row: {period_start?: string; period_end?: string}) =>
  row.period_start === row.period_end ? row.period_start : `${row.period_start || ""} 至 ${row.period_end || ""}`;
const act = async (value: unknown, operation: "generate" | "confirm") => {
  const r = resolveRow(value);
  if (!r || busy.value) return;
  await perform(async () => {
    if (operation === "generate") {
      detail.value = (await sourceCreateBatches(r.id)).data;
      await reload();
      ElMessage.success("结算已生成，请核对每人分数后确认入账");
      return;
    }
    const current = (await sourceGet(r.id)).data;
    detail.value = current;
    const resume = current.batches.some(b => b.status === "PARTIAL_FAILED");
    try {
      await ElMessageBox.confirm(
        `${current.class_name} · ${labels[current.source_kind]} · ${periodLabel(current)}，共${current.member_count}人、${current.settlement_summary.entry_count}笔，预计${current.settlement_summary.proposed_points}分，已入账${current.settlement_summary.posted_points}分。确认将其余有效学分入账？已入账条目不会重复计分。`,
        resume ? "确认继续入账未完成条目" : "确认学分入账", { confirmButtonText: resume ? "继续入账" : "确认入账", cancelButtonText: "返回检查" }
      );
    } catch { return; }
    posting.value = true;
    stopRequested.value = false;
    let latest = current;
    try {
      do {
        const previousCount = latest.posting_progress?.posted_entries ?? -1;
        latest = (await sourceAction(r.id, "confirm", latest.settlement_fingerprint, resume)).data;
        updateReceipt(latest);
        if (!latest.posting_progress || latest.posting_progress.completed) break;
        if (latest.posting_progress.state === "POSTING") throw new Error("后台仍在处理，请点击刷新核对进度；请勿重新上传或反复提交");
        if (latest.posting_progress.state === "PARTIAL_FAILED") throw new Error("部分条目未完成，请核对后继续入账；已有学分不会重复计入");
        if (latest.posting_progress.posted_entries <= previousCount) throw new Error("入账进度未增加，请刷新核对实际结果后继续");
      } while (!stopRequested.value);
    } catch (err) {
      try { updateReceipt((await sourceGet(r.id)).data); } catch { /* Preserve the last durable progress receipt. */ }
      await reload();
      if (!detail.value?.posting_progress?.completed) {
        const progress = detail.value?.posting_progress;
        throw new Error(`${creditSettlementErrorMessage(err, "连接中断，结果待核对")}。${progress ? `已入账${progress.posted_entries}/${progress.total_entries}笔。` : ""}请刷新查看实际进度，再继续入账；无需重新上传，已有学分不会重复计入。`);
      }
    } finally {
      posting.value = false;
    }
    await reload();
    emit("posted");
    ElMessage.success(detail.value?.posting_progress && !detail.value.posting_progress.completed ? "已暂停处理，已入账学分已保留，可继续入账" : "本次处理已完成，请核对已入账分数");
  });
};
const setup = () =>
  perform(async () => {
    if (!state.value?.setup_allowed) return;
    const key = "credit-source-storage-0072";
    if (localStorage.getItem(key))
      throw new Error("准备已提交，请刷新核验实际结构，不重复执行");
    localStorage.setItem(
      key,
      JSON.stringify({
        commit: state.value.release_commit,
        at: new Date().toISOString()
      })
    );
    await sourceSetup(state.value);
    await reload();
  });
const exportFile = () =>
  perform(async () => {
    if (!exportForm.from) throw new Error("请选择新增明细开始日期");
    download(
      await sourceExport(exportForm.class_id, exportForm.from, exportForm.to, exportForm.member_id),
      "学分汇总及新增来源.xlsx"
    );
  });
const reasons = (values?: string[]) =>
  (values || []).map(v => reasonLabels[v] || v).join("；");
onMounted(() => perform(reload));
</script>
<template>
  <el-card v-if="canManage || canExport" shadow="never" class="source-card">
    <template #header
      ><div class="heading">
        <strong>表格计学分与导出</strong
        ><el-button :disabled="busy" @click="perform(reload)"
          >刷新来源记录</el-button
        >
      </div></template
    >
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <section v-if="canExport" class="section">
      <strong>导出学分</strong>
      <p>
        可按班级或指定个人导出。文件包含每人学分汇总、所选日期的逐笔明细及来源、已导入的历史年度。
        汇总含小组、当月满分和学分率；当月指截止日期所在月，学分率按月初至截止日已获学分除以整月计划满分计算，配置不全时显示“待配置”。
      </p>
      <el-form inline
        ><el-form-item label="班级"
          ><el-select
            v-model="exportForm.class_id"
            clearable
            placeholder="全部授权学长（含未分班）"
            filterable
            ><el-option
              v-for="c in exportClasses"
              :key="c.id"
              :label="c.center_name ? `${c.name} · ${c.center_name}` : c.name"
              :value="c.id" /></el-select></el-form-item
        ><el-form-item label="学员">
          <el-select v-model="exportForm.member_id" filterable remote clearable
            :remote-method="searchExportMembers" :loading="searchingMembers" placeholder="全部学员；输入姓名或编号搜索"
            style="width: 290px" @visible-change="value => value && searchExportMembers('')">
            <el-option v-for="member in exportMembers" :key="member.id" :value="member.id"
              :label="`${member.name} · ${member.member_code} · ${member.class_name || '未分班'}`" />
          </el-select>
        </el-form-item><el-form-item label="新增日期"
          ><el-date-picker
            v-model="exportForm.from"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="开始日期" /><span>至</span
          ><el-date-picker
            v-model="exportForm.to"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="今天" /></el-form-item
        ><el-button type="primary" :loading="busy" @click="exportFile"
          >导出学分（含新增来源）</el-button
        ></el-form
      >
    </section>
    <section v-if="canManage" class="section">
      <strong>上传10月以后的学习记录</strong>
      <p v-if="form.kind === 'DAILY_READING'">
        ①选择班级 → ②上传该班原始读书打卡表 → ③检查预计学分并保存来源 →
        ④生成结算、核对每人分数、确认入账。
      </p>
      <p v-else>
        ①选择班级下载预填名册的模板 → ②填写实际签到或已评定优秀的分享 →
        ③上传检查并保存来源 → ④生成结算、核对每人分数、确认入账。
      </p>
      <el-alert
        v-if="state && !state.storage_available"
        :title="
          state.setup_incomplete
            ? '存储准备曾中断，请由技术人员核对结构后修复。'
            : '管理员准备来源存储后，即可保存上传记录。'
        "
        type="warning"
        :closable="false"
      />
      <el-button v-if="state?.setup_allowed" :disabled="busy" @click="setup"
        >准备来源存储</el-button
      >
      <el-form label-width="120px" class="upload-form">
        <el-form-item label="班级"
          ><el-select
            v-model="form.class_id"
            filterable
            :disabled="busy"
            placeholder="选择班级"
            ><el-option
              v-for="c in state?.classes"
              :key="c.id"
              :label="c.center_name ? `${c.name} · ${c.center_name}` : c.name"
              :value="c.id" /></el-select
        ></el-form-item>
        <el-form-item label="记录类型"
          ><el-radio-group v-model="form.kind" :disabled="busy"
            ><el-radio-button value="CLASS_MEETING"
              >班会线下签到</el-radio-button
            ><el-radio-button value="DAILY_READING">读书打卡</el-radio-button
            ><el-radio-button value="EXCELLENT_SHARE"
              >优秀分享</el-radio-button
            ></el-radio-group
          ><el-button
            v-if="form.kind !== 'DAILY_READING'"
            :disabled="busy || !form.class_id"
            @click="getTemplate"
            >下载本班模板</el-button
          ></el-form-item
        >
        <template v-if="form.kind === 'CLASS_MEETING'"
          ><el-form-item label="本次学习日"
            ><el-select
              v-model="form.cycle_id"
              :disabled="busy"
              placeholder="选择本班学习日"
              ><el-option
                v-for="c in cycles"
                :key="c.id"
                :value="c.id"
                :label="`第${c.learning_cycle_index}期${c.actual_class_meeting_at ? '（已举办）' : ''}`" /></el-select></el-form-item
          ><el-form-item label="实际开始时间"
            ><el-date-picker
              v-model="form.actual_start"
              :disabled="busy"
              type="datetime"
              value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="实际举办时间（北京时间）"
          /></el-form-item>
          <p>
            上午、下午、空巴分别填写到场、迟到、早退、请假或缺席。正式结算成功后，本期班会计入本学年学习日，本班自动进入下一周期；历史周期补登不推进当前周期。原扫码记录与表格不一致时，先到签到管理核对。
          </p></template
        >
        <p v-else-if="form.kind === 'DAILY_READING'">
          直接上传“读书会打卡统计列表.xlsx”，无需改成新模板。系统按姓名和脱敏账户核对学长；按原表提供的日期，录音为按时完成、提前完成或过期完成时每人每天计1分。未提交只保存原表，之后完成可重新上传补录。
        </p>
        <p v-else>
          仅填写已经评定优秀的分享；每次分享填写一个编号，例如20261009-01，重复上传沿用原编号。系统保留月度计分上限。
        </p>
        <div v-if="form.kind === 'DAILY_READING'" class="calendar-status">
          <p v-if="state?.reading_calendar.status === 'PUBLISHED'">
            2026年读书日期日历已发布，覆盖{{
              state.reading_calendar.day_count
            }}天。
          </p>
          <template v-else
            ><p>
              2026年日期日历尚未准备，管理员可使用系统已核准的年度日历补齐，再检查读书表格。
            </p>
            <el-button
              v-if="permissions.includes('plans:business_calendar_manage')"
              :disabled="busy"
              @click="prepareCalendar"
              >准备2026年日期日历</el-button
            ></template
          >
        </div>
        <el-form-item label="来源备注（选填）"
          ><el-input
            v-model="form.source_note"
            :disabled="busy"
            type="textarea"
            maxlength="1000"
            placeholder="可留空；如有特殊情况，可补充说明"
        /></el-form-item>
        <el-form-item label="填写后的表格"
          ><input
            type="file"
            accept=".xlsx"
            :disabled="busy"
            aria-label="选择学分来源表格"
            @change="choose"
        /></el-form-item>
        <el-form-item
          ><el-button
            type="primary"
            :loading="busy"
            :disabled="!file || !form.class_id"
            @click="check"
            >检查表格与预计学分</el-button
          ><el-button
            :disabled="
              busy ||
              !preview?.fingerprint ||
              !state?.storage_available ||
              !canImport
            "
            @click="register"
            >保存来源</el-button
          ></el-form-item
        >
      </el-form>
      <template v-if="preview"
        ><p>
          共{{ preview.row_count }}行，{{
            preview.error_count
          }}行需修正{{ form.kind === 'DAILY_READING' ? '（保存时跳过，不参与结算）' : '' }}，预计新增{{
            preview.proposed_points
          }}分。保存来源后再生成正式结算预览，规则与账本会重新核对。
        </p>
        <el-table :data="preview.rows" max-height="420"
          ><el-table-column
            prop="excel_row"
            label="Excel行"
            width="85"
          /><el-table-column
            prop="member_code"
            label="学长编号"
            width="145"
          /><el-table-column
            prop="member_name"
            label="姓名"
            width="100"
          /><el-table-column
            prop="occurred_on"
            label="日期"
            width="120"
          /><el-table-column
            prop="proposed_points"
            label="预计学分"
            width="100"
          /><el-table-column label="原表记录" min-width="200"
            ><template #default="{ row }">{{
              sourceDetails(row)
            }}</template></el-table-column
          ><el-table-column label="结果" width="160"
            ><template #default="{ row }">{{
              row.errors?.length
                ? (form.kind === 'DAILY_READING' ? "将跳过 · 需修正" : "需修正")
                : rowStatuses[row.credit_status] || "待核对"
            }}</template></el-table-column
          ><el-table-column label="原因"
            ><template #default="{ row }">{{
              row.errors?.join("；") ||
              reasons(row.credit_reasons) ||
              "核对通过"
            }}</template></el-table-column
          ></el-table
        ></template
      >
      <p>
        保存来源 → 生成结算 → 核对每位学长的学分 → 确认入账。无效记录与已计分来源会跳过，只有确认入账才会增加正式学分。
      </p>
      <el-table
        :data="state?.imports || []"
        row-key="id"
        empty-text="尚未上传学习记录"
        ><el-table-column
          prop="class_name"
          label="班级"
          width="160"
        /><el-table-column label="来源" min-width="230"
          ><template #default="{ row }"
            >{{ labels[row.source_kind] }}<br />{{ row.original_filename }}（{{
              row.row_count
            }}行）<small v-if="row.duplicate_upload_count > 1"> · 重复来源{{ row.duplicate_upload_count }}次，合并展示，不重复计分</small></template
          ></el-table-column
        ><el-table-column label="结算状态" min-width="170"
          ><template #default="{ row }">{{
            batchSummary(row)
          }}</template></el-table-column
        ><el-table-column label="操作" min-width="340"
          ><template #default="{ row }"
            ><el-button link @click="show(row)">查看来源</el-button
            ><el-button
              link
              :disabled="busy || row.source_only"
              @click="act(row, 'generate')"
              >生成结算</el-button
            ><el-button
              link
              type="primary"
              :disabled="busy || !eligible(row)"
              @click="act(row, 'confirm')"
              >确认入账</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      :model-value="!!detail"
      title="学习记录与结算结果"
      width="85%"
      :close-on-click-modal="!posting"
      :close-on-press-escape="!posting"
      :show-close="!posting"
      @update:model-value="detail = undefined"
      ><template v-if="detail"
        ><p>
          {{ detail.class_name }} · {{ detail.original_filename }}<span v-if="detail.source_note"> · {{ detail.source_note }}</span>
        </p>
        <p>
          {{ periodLabel(detail) }} · 结算涉及{{ detail.member_count }}人 · {{ detail.row_count }}行原表记录。
          预计新增 {{ detail.settlement_summary.proposed_points }} 分，已入账 {{ detail.settlement_summary.posted_points }} 分。
          <el-button v-if="!detail.batches.length && !detail.source_only" :disabled="busy" @click="act(detail, 'generate')">生成结算</el-button>
          <el-button v-if="detail.batches.length" type="primary" :loading="posting" :disabled="busy || !eligible(detail)" @click="act(detail, 'confirm')">{{ posting ? "正在入账" : detail.posting_progress?.completed ? "已完成入账" : Number(detail.settlement_summary.posted_points) > 0 ? "继续入账" : "确认入账" }}</el-button>
          <el-button :disabled="busy" @click="refreshDetail">刷新进度</el-button>
          <el-button v-if="posting" :disabled="stopRequested" @click="stopRequested = true">{{ stopRequested ? "当前小段完成后暂停" : "暂停处理" }}</el-button>
          <el-button v-if="detail.posting_progress?.state === 'POSTING' && canReconcile" :disabled="busy" @click="recover">核对中断状态</el-button>
        </p>
        <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
        <el-alert v-if="detail.posting_progress?.state === 'POSTING' && !posting" title="后台仍在入账，请刷新查看进度，不需要重新上传。若超过30分钟没有新进度，可由有对账权限的人员点击“核对中断状态”。" type="warning" :closable="false" show-icon />
        <p v-if="detail.posting_progress">已入账 {{ detail.posting_progress.posted_entries }} / {{ detail.posting_progress.total_entries }} 笔，剩余 {{ detail.posting_progress.remaining_entries }} 笔。{{ posting ? "正在分段处理，每段成功后立即保存进度。" : "" }}</p>
        <el-table v-if="detail.batches.length" :data="detail.member_results || []" max-height="420" empty-text="请先生成结算，即可查看每位学长的学分">
          <el-table-column prop="member_name" min-width="120" label="姓名" />
          <el-table-column prop="member_code" min-width="145" label="学长编号" />
          <el-table-column prop="credit_count" min-width="100" label="计分笔数" />
          <el-table-column prop="proposed_points" min-width="140" label="预计新增学分" />
          <el-table-column prop="posted_points" min-width="140" label="已入账学分" />
          <el-table-column prop="blocked_count" min-width="110" label="跳过待核对" />
        </el-table>
        <el-collapse>
          <el-collapse-item title="按日期查看结算与原表记录" name="dates">
        <el-table :data="detail.batches">
          <el-table-column label="学习日期"><template #default="{ row }">{{ periodLabel(row) }}</template></el-table-column>
          <el-table-column prop="member_count" label="计分人数" />
          <el-table-column prop="proposed_entry_count" label="计分笔数" />
          <el-table-column label="状态"><template #default="{ row }">{{ row.proposed_entry_count ? (statuses[row.status] || "待核对") : "无新增学分" }}</template></el-table-column>
          <el-table-column prop="proposed_points" min-width="140" label="预计学分" />
          <el-table-column prop="posted_points" min-width="140" label="已入账" />
          <el-table-column prop="blocked_count" min-width="110" label="待核对" />
        </el-table
        ><el-table :data="detail.rows" max-height="420"
          ><el-table-column
            prop="excel_row"
            label="Excel行"
            width="85"
          /><el-table-column
            prop="member_code"
            label="学长编号"
            width="145"
          /><el-table-column
            prop="member_name"
            label="姓名"
            width="100"
          /><el-table-column
            prop="occurred_on"
            label="日期"
            width="160"
          /><el-table-column label="原表记录" min-width="200"
            ><template #default="{ row }">{{
              sourceDetails(row)
            }}</template></el-table-column
          ><el-table-column label="来源编号／场次"
            ><template #default="{ row }">{{
              slotLabel(row.source_reference)
            }}</template></el-table-column
          ></el-table
        >
        <p>
          已有学分不会重复计入；系统保留来源和内部结算编号供追溯。部分失败请核对后再次点击“确认入账”，将继续处理未入账条目。
        </p></el-collapse-item></el-collapse></template
      ></el-dialog
    >
  </el-card>
</template>
<style scoped>
.source-card {
  margin-bottom: 16px;
}
.heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.section + .section {
  border-top: 1px solid var(--el-border-color);
  margin-top: 20px;
  padding-top: 20px;
}
p {
  color: var(--el-text-color-regular);
  line-height: 1.7;
}
.upload-form {
  margin-top: 16px;
  max-width: 980px;
}
.el-select {
  min-width: 220px;
}
.el-button {
  margin-left: 8px;
}
.el-date-editor {
  margin: 0 8px;
}
</style>
