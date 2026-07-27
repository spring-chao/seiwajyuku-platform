<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, type UploadFile, type UploadFiles } from "element-plus";
import {
  getRenewalOverview,
  previewRenewalImport,
  type RenewalImportSample,
  type RenewalImportSummary,
  type RenewalOverviewRow
} from "@/api/seiwajyuku";

defineOptions({ name: "RenewalOperations" });

const year = ref(2026);
const loading = ref(false);
const importing = ref(false);
const rows = ref<RenewalOverviewRow[]>([]);
const renewalFile = ref<File>();
const masterFile = ref<File>();
const batchId = ref<number>();
const importSummary = ref<RenewalImportSummary>();
const samples = ref<RenewalImportSample[]>([]);

const centerNames = computed(() => [
  ...new Set(rows.value.map(item => item.org_name))
]);
const total = computed(() =>
  rows.value.reduce((sum, item) => sum + Number(item.count), 0)
);
const renewed = computed(() =>
  rows.value
    .filter(item => item.status === "RENEWED")
    .reduce((sum, item) => sum + Number(item.count), 0)
);
const needsAttention = computed(() =>
  rows.value
    .filter(item =>
      [
        "PENDING_FIRST_CONTACT",
        "CONTACTED_WAITING_REPLY",
        "IN_COMMUNICATION"
      ].includes(item.status)
    )
    .reduce((sum, item) => sum + Number(item.count), 0)
);
const monthlyRows = computed(() =>
  Array.from({ length: 12 }, (_, index) => {
    const month = index + 1;
    const monthRows = rows.value.filter(item => item.due_month === month);
    return {
      month,
      total: monthRows.reduce((sum, item) => sum + Number(item.count), 0),
      renewed: monthRows
        .filter(item => item.status === "RENEWED")
        .reduce((sum, item) => sum + Number(item.count), 0),
      attention: monthRows
        .filter(item => item.status !== "RENEWED")
        .reduce((sum, item) => sum + Number(item.count), 0)
    };
  })
);

const statusLabel = (status: string) =>
  ({
    PENDING_FIRST_CONTACT: "待首次联系",
    CONTACTED_WAITING_REPLY: "已联系待回复",
    IN_COMMUNICATION: "沟通中",
    RENEWED: "已续费",
    NOT_RENEWING: "明确不续费",
    DEFERRED: "延期/暂停",
    EXITED: "已退出"
  })[status] ?? status;

const matchLabel = (status: string) =>
  ({
    MASTER_PHONE_EXACT: "手机号匹配",
    MASTER_NAME_CENTER_EXACT: "姓名+中心匹配",
    MATCHED: "系统主档匹配",
    NEEDS_REVIEW: "待人工确认",
    INVALID: "数据不完整"
  })[status] ?? status;

async function load() {
  loading.value = true;
  try {
    const response = await getRenewalOverview(year.value);
    rows.value = response.data.rows;
  } finally {
    loading.value = false;
  }
}

function pickFile(
  uploadFile: UploadFile,
  _uploadFiles: UploadFiles,
  target: "renewal" | "master"
) {
  if (!uploadFile.raw) return;
  if (!uploadFile.name.toLowerCase().endsWith(".xlsx")) {
    ElMessage.warning("请选择 .xlsx 格式的工作簿");
    return;
  }
  if (target === "renewal") renewalFile.value = uploadFile.raw;
  else masterFile.value = uploadFile.raw;
}

async function previewImport() {
  if (!renewalFile.value || !masterFile.value) {
    ElMessage.warning("请先分别选择续费名单和最新学员主档案");
    return;
  }
  importing.value = true;
  try {
    const response = await previewRenewalImport(
      renewalFile.value,
      masterFile.value
    );
    batchId.value = response.data.batch_id;
    importSummary.value = response.data.summary;
    samples.value = response.data.samples;
    ElMessage.success("匹配预检完成，数据尚未写入正式续费周期");
  } catch (error: any) {
    ElMessage.error(
      error?.response?.data?.detail ?? "导入预检失败，请检查工作簿格式"
    );
  } finally {
    importing.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="renewal-page" v-loading="loading">
    <section class="hero">
      <div>
        <p class="eyebrow">年度续费运营中心</p>
        <h1>按续费周期，把名单变成可跟进的行动</h1>
        <p class="subtitle">
          学习班级与发展归属分别保留；先锋班、黄埔班按直属班级学习，续费责任仍归六大分中心。
        </p>
      </div>
      <el-select v-model="year" class="year-select" @change="load">
        <el-option :value="2026" label="2026年度" />
        <el-option :value="2027" label="2027年度" />
      </el-select>
    </section>

    <section class="summary-grid">
      <article>
        <span>年度续费对象</span>
        <strong>{{ total }}</strong>
        <small>已进入正式续费周期的学员</small>
      </article>
      <article>
        <span>覆盖分中心</span>
        <strong>{{ centerNames.length }} / 6</strong>
        <small>续费发展归属按六大分中心统计</small>
      </article>
      <article>
        <span>已完成续费</span>
        <strong>{{ renewed }}</strong>
        <small>状态已确认完成</small>
      </article>
      <article class="attention">
        <span>当前需跟进</span>
        <strong>{{ needsAttention }}</strong>
        <small>待联系、待回复或沟通中的学员</small>
      </article>
    </section>

    <section class="content-grid">
      <el-card shadow="never" class="timeline-card">
        <template #header>
          <div class="card-title">
            <div>
              <h2>年度续费节奏</h2>
              <p>按到期月份查看全年工作量与完成情况</p>
            </div>
          </div>
        </template>
        <el-table :data="monthlyRows" stripe>
          <el-table-column prop="month" label="到期月份" min-width="100">
            <template #default="{ row }">{{ row.month }}月</template>
          </el-table-column>
          <el-table-column prop="total" label="续费对象" min-width="100" align="right" />
          <el-table-column prop="renewed" label="已续费" min-width="100" align="right" />
          <el-table-column prop="attention" label="待推进" min-width="100" align="right" />
          <el-table-column label="完成率" min-width="130" align="right">
            <template #default="{ row }">
              {{ row.total ? Math.round((row.renewed / row.total) * 100) : 0 }}%
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never" class="import-card">
        <template #header>
          <div class="card-title">
            <div>
              <h2>名单融合预检</h2>
              <p>先匹配主档案并识别疑点，不会直接覆盖正式数据</p>
            </div>
            <el-tag type="success" effect="plain">安全预览</el-tag>
          </div>
        </template>

        <div class="upload-list">
          <div class="upload-row">
            <div>
              <b>1. 待续费名单</b>
              <span>{{ renewalFile?.name ?? "请选择“待续费名单（更新）.xlsx”" }}</span>
            </div>
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              accept=".xlsx"
              :on-change="(file, files) => pickFile(file, files, 'renewal')"
            >
              <el-button>选择文件</el-button>
            </el-upload>
          </div>
          <div class="upload-row">
            <div>
              <b>2. 最新学员主档案</b>
              <span>{{ masterFile?.name ?? "请选择2026年最新学员表" }}</span>
            </div>
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              accept=".xlsx"
              :on-change="(file, files) => pickFile(file, files, 'master')"
            >
              <el-button>选择文件</el-button>
            </el-upload>
          </div>
        </div>
        <el-button
          type="primary"
          class="preview-button"
          :loading="importing"
          @click="previewImport"
        >
          开始匹配预检
        </el-button>
        <p class="import-note">
          匹配顺序：唯一手机号 → 姓名+六大分中心 → 人工核对。直属学习班级不会改变续费发展归属。
        </p>
      </el-card>
    </section>

    <el-card v-if="importSummary" shadow="never" class="result-card">
      <template #header>
        <div class="card-title">
          <div>
            <h2>预检结果 · 批次 #{{ batchId }}</h2>
            <p>以下结果仅用于核对，确认正式导入功能将在下一步启用</p>
          </div>
          <el-tag type="warning">待业务确认</el-tag>
        </div>
      </template>
      <div class="result-summary">
        <span>总计 <b>{{ importSummary.total }}</b></span>
        <span>自动匹配 <b>{{ importSummary.matched }}</b></span>
        <span>待确认 <b>{{ importSummary.needs_review }}</b></span>
        <span>无效数据 <b>{{ importSummary.invalid }}</b></span>
        <span>需要协助 <b>{{ importSummary.assistance_review }}</b></span>
      </div>
      <el-table :data="samples" stripe max-height="430">
        <el-table-column prop="row_no" label="Excel行" width="86" />
        <el-table-column prop="name" label="学员" min-width="100" />
        <el-table-column prop="center_name" label="续费归属" min-width="135" />
        <el-table-column prop="class_name" label="学习班级" min-width="120" />
        <el-table-column prop="due_month" label="到期月" width="90">
          <template #default="{ row }">{{ row.due_month ? `${row.due_month}月` : "—" }}</template>
        </el-table-column>
        <el-table-column label="匹配结果" min-width="130">
          <template #default="{ row }">{{ matchLabel(row.match_status) }}</template>
        </el-table-column>
        <el-table-column label="建议状态" min-width="130">
          <template #default="{ row }">{{ statusLabel(row.proposed_status) }}</template>
        </el-table-column>
        <el-table-column prop="assistance_note" label="需要协助" min-width="180" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.renewal-page {
  display: grid;
  gap: 18px;
  padding: 20px;
  color: #163d32;
}
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 30px 34px;
  color: #f7fffc;
  background:
    radial-gradient(circle at 85% 15%, rgb(91 183 142 / 28%), transparent 34%),
    linear-gradient(125deg, #0e4435, #217153);
  border-radius: 20px;
}
.eyebrow {
  margin: 0 0 10px;
  color: #9ee2c6;
  letter-spacing: 0.18em;
}
.hero h1 {
  margin: 0 0 12px;
  font-size: clamp(26px, 3vw, 38px);
  line-height: 1.25;
}
.subtitle {
  max-width: 760px;
  margin: 0;
  color: #d3eee3;
  line-height: 1.8;
}
.year-select {
  width: 140px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}
.summary-grid article {
  display: grid;
  gap: 8px;
  padding: 20px 22px;
  background: var(--el-bg-color);
  border: 1px solid #dce9e3;
  border-radius: 16px;
}
.summary-grid span {
  color: #6d8179;
}
.summary-grid strong {
  font-size: 30px;
  color: #123f32;
}
.summary-grid small {
  color: #879991;
}
.summary-grid .attention {
  background: #fff9ee;
  border-color: #f1d9ad;
}
.content-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.75fr);
  gap: 18px;
}
.timeline-card,
.import-card,
.result-card {
  border-color: #dce9e3;
  border-radius: 16px;
}
.card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.card-title h2 {
  margin: 0 0 6px;
  font-size: 20px;
}
.card-title p {
  margin: 0;
  color: #82958d;
}
.upload-list {
  display: grid;
  gap: 12px;
}
.upload-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 15px;
  background: #f5f9f7;
  border: 1px solid #e2ece7;
  border-radius: 12px;
}
.upload-row div {
  display: grid;
  min-width: 0;
  gap: 5px;
}
.upload-row span {
  overflow: hidden;
  color: #82958d;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-button {
  width: 100%;
  margin-top: 16px;
}
.import-note {
  margin: 14px 0 0;
  color: #82958d;
  font-size: 13px;
  line-height: 1.7;
}
.result-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.result-summary span {
  padding: 9px 13px;
  color: #657a71;
  background: #f2f7f5;
  border-radius: 10px;
}
.result-summary b {
  margin-left: 5px;
  color: #174d3c;
  font-size: 17px;
}
@media (max-width: 1100px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .content-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 680px) {
  .renewal-page {
    padding: 12px;
  }
  .hero {
    align-items: stretch;
    flex-direction: column;
    padding: 24px;
  }
  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
