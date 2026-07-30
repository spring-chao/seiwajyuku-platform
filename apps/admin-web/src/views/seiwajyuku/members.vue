<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  ElMessage,
  ElMessageBox,
  type FormInstance,
  type FormRules,
  type UploadFile,
  type UploadUserFile
} from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import {
  createMember,
  getMembers,
  getOrgUnits,
  applyDirectClassWorkbook,
  applyFullClassRosterOrganization,
  previewDirectClassWorkbook,
  previewFullClassRosterWorkbook,
  type DirectClassPreflight,
  type FullClassRosterPreflight,
  type Member,
  type OrgUnit
} from "@/api/seiwajyuku";

defineOptions({ name: "MemberManagement" });

const loading = ref(false);
const saving = ref(false);
const dialogVisible = ref(false);
const preflightVisible = ref(false);
const preflightLoading = ref(false);
const preflightFiles = ref<UploadUserFile[]>([]);
const preflightResult = ref<DirectClassPreflight>();
const fullPreflightVisible = ref(false);
const fullPreflightLoading = ref(false);
const fullOrgImportLoading = ref(false);
const fullPreflightFiles = ref<UploadUserFile[]>([]);
const fullPreflightResult = ref<FullClassRosterPreflight>();
const selectedOrg = ref("");
const keyword = ref("");
const rows = ref<Member[]>([]);
const fullOrgConfirmationText = "确认创建20个普通班和112个普通班小组";
const canApplyFullOrgImport = computed(() => {
  const result = fullPreflightResult.value;
  if (!result) return false;
  const classActions = Object.fromEntries(
    result.organization.class_action_summary.map(item => [
      item.action,
      item.count
    ])
  );
  const groupActions = Object.fromEntries(
    result.organization.group_action_summary.map(item => [
      item.action,
      item.count
    ])
  );
  const matching = Object.fromEntries(
    result.matching.summary.map(item => [item.status, item.count])
  );
  const issues = Object.fromEntries(
    result.issues.map(item => [item.code, item.count])
  );
  return (
    result.source.active_member_count === 834 &&
    result.source.ordinary_class_count === 20 &&
    result.source.ordinary_group_pair_count === 112 &&
    classActions.CREATE_OR_RESOLVE === 20 &&
    classActions.REUSE === 4 &&
    groupActions.REVIEW === 112 &&
    groupActions.REUSE === 11 &&
    matching.UNIQUE_ACTIVE_MATCH === 722 &&
    matching.NO_PRODUCTION_MATCH === 84 &&
    matching.MANUAL_REVIEW === 28 &&
    issues.DUPLICATE_SOURCE_PHONE === 8 &&
    issues.INVALID_PHONE === 9 &&
    issues.MISSING_PHONE === 11 &&
    issues.MISSING_CLASS === 18
  );
});
const orgs = ref<OrgUnit[]>([]);
const formRef = ref<FormInstance>();
const canManage = computed(() =>
  useUserStoreHook().permissions.includes("members:manage")
);
const centerOrgs = computed(() =>
  orgs.value.filter(item => item.unit_type === "REGIONAL_CENTER")
);
const filteredRows = computed(() => {
  const term = keyword.value.trim().toLowerCase();
  if (!term) return rows.value;
  return rows.value.filter(item =>
    [item.name, item.member_code, item.company_name, item.phone_last4]
      .filter(Boolean)
      .some(value => String(value).toLowerCase().includes(term))
  );
});

const form = reactive({
  name: "",
  org_unit_id: "",
  phone: "",
  company_name: "",
  gender: "",
  district: "",
  company_address: "",
  class_name: "",
  group_name: "",
  birthday: "",
  join_date: "",
  study_start_date: "",
  membership_years: undefined as number | undefined,
  renewal_month: "",
  status: "ACTIVE",
  position: "",
  referrer: "",
  referrer_center: "",
  industry_category: "",
  industry: "",
  company_products: "",
  annual_sales: "",
  company_size: "",
  profit_margin: "",
  notes: ""
});
const rules: FormRules = {
  name: [{ required: true, message: "请输入姓名", trigger: "blur" }],
  org_unit_id: [{ required: true, message: "请选择分中心", trigger: "change" }],
  phone: [
    { required: true, message: "请输入手机号", trigger: "blur" },
    {
      pattern: /^1\d{10}$/,
      message: "请输入 11 位手机号",
      trigger: "blur"
    }
  ]
};

function errorText(error: any) {
  return error?.response?.data?.detail || error?.message || "操作失败";
}

async function load() {
  loading.value = true;
  try {
    const [members, organizations] = await Promise.all([
      getMembers(selectedOrg.value || undefined),
      getOrgUnits()
    ]);
    rows.value = members.data;
    orgs.value = organizations.data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  Object.assign(form, {
    name: "",
    org_unit_id: selectedOrg.value,
    phone: "",
    company_name: "",
    gender: "",
    district: "",
    company_address: "",
    class_name: "",
    group_name: "",
    birthday: "",
    join_date: "",
    study_start_date: "",
    membership_years: undefined,
    renewal_month: "",
    status: "ACTIVE",
    position: "",
    referrer: "",
    referrer_center: "",
    industry_category: "",
    industry: "",
    company_products: "",
    annual_sales: "",
    company_size: "",
    profit_margin: "",
    notes: ""
  });
  dialogVisible.value = true;
}

async function submit() {
  if (!(await formRef.value?.validate())) return;
  saving.value = true;
  try {
    await createMember({
      name: form.name.trim(),
      org_unit_id: form.org_unit_id,
      phone: form.phone.trim(),
      company_name: form.company_name.trim() || undefined,
      gender: form.gender || undefined,
      district: form.district.trim() || undefined,
      company_address: form.company_address.trim() || undefined,
      class_name: form.class_name.trim() || undefined,
      group_name: form.group_name.trim() || undefined,
      birthday: form.birthday || undefined,
      join_date: form.join_date || undefined,
      study_start_date: form.study_start_date || undefined,
      membership_years: form.membership_years,
      renewal_month: form.renewal_month || undefined,
      status: form.status,
      position: form.position.trim() || undefined,
      referrer: form.referrer.trim() || undefined,
      referrer_center: form.referrer_center.trim() || undefined,
      industry_category: form.industry_category.trim() || undefined,
      industry: form.industry.trim() || undefined,
      company_products: form.company_products.trim() || undefined,
      annual_sales: form.annual_sales.trim() || undefined,
      company_size: form.company_size.trim() || undefined,
      profit_margin: form.profit_margin.trim() || undefined,
      notes: form.notes.trim() || undefined
    });
    ElMessage.success("学员已创建，手机号已加密保存");
    dialogVisible.value = false;
    await load();
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    saving.value = false;
  }
}

function selectPreflightFile(file: UploadFile) {
  preflightFiles.value = [file];
  preflightResult.value = undefined;
  return false;
}

function selectFullPreflightFile(file: UploadFile) {
  fullPreflightFiles.value = [file];
  fullPreflightResult.value = undefined;
  return false;
}

async function runFullClassPreflight() {
  const workbook = fullPreflightFiles.value[0]?.raw;
  if (!workbook) {
    ElMessage.warning("请先选择最新学员表 .xlsx 文件");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "文件只在服务器内存中用于受保护匹配，结果只返回班级、小组和匹配汇总；不会创建、修改或停用任何生产数据。",
      "确认进行全量班级只读预检",
      {
        confirmButtonText: "开始只读预检",
        cancelButtonText: "取消",
        type: "warning"
      }
    );
  } catch {
    return;
  }
  fullPreflightLoading.value = true;
  try {
    const result = await previewFullClassRosterWorkbook(workbook);
    fullPreflightResult.value = result.data;
    ElMessage.success("全量班级只读预检已完成，未写入生产数据");
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    fullPreflightLoading.value = false;
  }
}

async function applyFullOrgImport() {
  const workbook = fullPreflightFiles.value[0]?.raw;
  if (!workbook || !canApplyFullOrgImport.value) return;
  let confirmationText = "";
  try {
    const prompt = await ElMessageBox.prompt(
      `本阶段仅创建20个普通班和112个普通班小组，不修改任何学员或签到数据。请输入：${fullOrgConfirmationText}`,
      "第一阶段组织节点生产写入确认",
      {
        confirmButtonText: "执行第一阶段",
        cancelButtonText: "取消",
        type: "warning",
        inputValidator: value =>
          value === fullOrgConfirmationText || "确认文字不完整，已禁止写入"
      }
    );
    confirmationText = prompt.value;
  } catch {
    return;
  }
  fullOrgImportLoading.value = true;
  try {
    const result = await applyFullClassRosterOrganization(
      workbook,
      confirmationText
    );
    ElMessage.success(
      `第一阶段完成：创建班级 ${result.data.created_classes} 个、小组 ${result.data.created_groups} 个；学员变更 ${result.data.members_changed} 人`
    );
    const refreshed = await previewFullClassRosterWorkbook(workbook);
    fullPreflightResult.value = refreshed.data;
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    fullOrgImportLoading.value = false;
  }
}

async function applyDirectClassImport() {
  const workbook = preflightFiles.value[0]?.raw;
  if (!workbook || !preflightResult.value) return;
  try { await ElMessageBox.confirm("将按已确认工作簿写入直属四班：8 名新建、115 名更新、430 条组织关系和 4 条备注。指纹或实时预检不符将自动停止并回滚。", "执行直属四班生产导入", { confirmButtonText: "确认执行", cancelButtonText: "取消", type: "warning" }); } catch { return; }
  preflightLoading.value = true;
  try { const result = await applyDirectClassWorkbook(workbook); ElMessage.success(`导入完成：新建 ${result.data.created}，更新 ${result.data.updated}，关系 ${result.data.relations}`); await load(); }
  catch (error) { ElMessage.error(errorText(error)); }
  finally { preflightLoading.value = false; }
}

async function runDirectClassPreflight() {
  const workbook = preflightFiles.value[0]?.raw;
  if (!workbook) {
    ElMessage.warning("请先选择直属班级名单 .xlsx 文件");
    return;
  }
  try {
    await ElMessageBox.confirm(
      "文件只在服务器内存中用于受保护匹配，结果只返回汇总数量；不会创建、修改或停用任何学员、组织或关系。",
      "确认进行直属四班只读预检",
      { confirmButtonText: "开始只读预检", cancelButtonText: "取消", type: "warning" }
    );
  } catch {
    return;
  }
  preflightLoading.value = true;
  try {
    const result = await previewDirectClassWorkbook(workbook);
    preflightResult.value = result.data;
    ElMessage.success("只读预检已完成，未写入生产数据");
  } catch (error) {
    ElMessage.error(errorText(error));
  } finally {
    preflightLoading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="member-page" v-loading="loading">
    <section class="page-head">
      <div>
        <p>关怀试点 · 主数据</p>
        <h1>学员管理</h1>
        <span>手机号加密保存；列表、普通查询和后续任务默认只显示脱敏号码。</span>
      </div>
      <div class="head-actions" v-if="canManage">
        <el-button size="large" @click="fullPreflightVisible = true">
          全量班级预检
        </el-button>
        <el-button size="large" @click="preflightVisible = true">
          直属四班预检
        </el-button>
        <el-button type="primary" size="large" @click="openCreate">
          新增学员
        </el-button>
      </div>
    </section>

    <el-alert
      title="先选择一个分中心开展小范围试点；正式批量导入前需提供已填写的学员基本信息表。"
      type="warning"
      :closable="false"
      show-icon
    />

    <el-card shadow="never">
      <div class="toolbar">
        <el-select
          v-model="selectedOrg"
          clearable
          placeholder="全部分中心"
          @change="load"
        >
          <el-option
            v-for="org in centerOrgs"
            :key="org.id"
            :label="org.name"
            :value="org.id"
          />
        </el-select>
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索姓名、编号、企业或手机后四位"
        />
        <span class="result-count">共 {{ filteredRows.length }} 人</span>
      </div>

      <el-table :data="filteredRows" stripe empty-text="暂无学员数据">
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column prop="org_name" label="所属分中心" min-width="140" />
        <el-table-column prop="class_name" label="班级" min-width="120">
          <template #default="{ row }">{{ row.class_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="group_name" label="组名" min-width="110">
          <template #default="{ row }">{{ row.group_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="company_name" label="企业" min-width="180">
          <template #default="{ row }">{{ row.company_name || "—" }}</template>
        </el-table-column>
        <el-table-column prop="phone_masked" label="手机号（脱敏）" min-width="150" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'info'">
              {{ row.status === "ACTIVE" ? "在册" : row.status }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="preflightVisible"
      title="直属四班生产前只读预检"
      width="860px"
      class="preflight-dialog"
    >
      <el-alert
        title="本操作不创建或修改任何生产数据"
        description="工作簿仅在服务器内存中解析；手机号仅用于受保护的匹配，界面只显示汇总数量。"
        type="success"
        :closable="false"
        show-icon
      />
      <el-upload
        class="preflight-upload"
        accept=".xlsx"
        :auto-upload="false"
        :limit="1"
        :file-list="preflightFiles"
        :on-change="selectPreflightFile"
      >
        <el-button>选择直属班级名单 .xlsx</el-button>
      </el-upload>
      <el-button
        type="primary"
        :loading="preflightLoading"
        @click="runDirectClassPreflight"
      >
        生成只读预检报告
      </el-button>

      <div v-if="preflightResult" class="preflight-result">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="在册直属学员">
            {{ preflightResult.source.active_direct_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="生产写入">
            已禁止
          </el-descriptions-item>
          <el-descriptions-item label="工作簿班级分布" :span="2">
            <el-tag
              v-for="item in preflightResult.source.by_class"
              :key="item.class_name"
              class="result-tag"
              type="success"
            >
              {{ item.class_name }} {{ item.count }} 人
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="生产现有直属班记录" :span="2">
            <el-tag
              v-for="item in preflightResult.production_existing_direct_class_records"
              :key="item.class_name"
              class="result-tag"
            >
              {{ item.class_name }} {{ item.count }} 人
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="匹配结果" :span="2">
            <el-tag
              v-for="item in preflightResult.matching.summary"
              :key="item.status"
              class="result-tag"
              type="info"
            >
              {{ item.status }}：{{ item.count }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="未匹配生产主档" :span="2">
            <span v-if="!preflightResult.matching.no_production_match_by_class.length">
              无
            </span>
            <template v-else>
              <el-tag
                v-for="item in preflightResult.matching.no_production_match_by_class"
                :key="item.class_name"
                class="result-tag"
                type="warning"
              >
                {{ item.class_name }}：{{ item.count }} 人
              </el-tag>
            </template>
          </el-descriptions-item>
          <el-descriptions-item label="已匹配但待校正字段" :span="2">
            <span v-if="!preflightResult.matching.matched_profile_fields_needing_reconciliation.length">
              无
            </span>
            <template v-else>
              <el-tag
                v-for="item in preflightResult.matching.matched_profile_fields_needing_reconciliation"
                :key="item.field"
                class="result-tag"
                type="info"
              >
                {{ item.field }}：{{ item.count }}
              </el-tag>
            </template>
          </el-descriptions-item>
          <el-descriptions-item label="直属班组织解析" :span="2">
            <el-tag
              v-for="item in preflightResult.organization.direct_class_status"
              :key="item.class_name"
              class="result-tag"
              :type="item.action === 'REUSE' ? 'success' : 'warning'"
            >
              {{ item.class_name }}：{{ item.action }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="preflightResult.issues.length"
          class="result-alert"
          title="存在需人工复核的汇总项；系统不会自动写入"
          type="warning"
          :closable="false"
          show-icon
        />
        <el-alert
          v-if="preflightResult.issues.length"
          class="result-alert"
          :title="`复核原因：${preflightResult.issues.map(item => `${item.code} ${item.count}`).join('；')}`"
          type="warning"
          :closable="false"
          show-icon
        />
        <p class="form-hint">{{ preflightResult.write_gates[0] }}</p>
      </div>
      <template #footer>
        <el-button v-if="preflightResult && !preflightResult.issues.length" type="danger" :loading="preflightLoading" @click="applyDirectClassImport">执行确认导入</el-button>
        <el-button @click="preflightVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="fullPreflightVisible"
      title="全量班级与小组生产前只读预检"
      width="1080px"
      class="preflight-dialog"
    >
      <el-alert
        title="本操作只生成聚合报告，不创建或修改任何生产数据"
        description="工作簿只在服务器内存中解析；手机号仅转换为受保护的匹配摘要，界面不显示姓名、手机号、成员编号或组织 ID。"
        type="success"
        :closable="false"
        show-icon
      />
      <el-upload
        class="preflight-upload"
        accept=".xlsx"
        :auto-upload="false"
        :limit="1"
        :file-list="fullPreflightFiles"
        :on-change="selectFullPreflightFile"
      >
        <el-button>选择最新学员表 .xlsx</el-button>
      </el-upload>
      <el-button
        type="primary"
        :loading="fullPreflightLoading"
        @click="runFullClassPreflight"
      >
        生成全量只读预检报告
      </el-button>

      <div v-if="fullPreflightResult" class="preflight-result">
        <el-descriptions :column="4" border>
          <el-descriptions-item label="在册学员">
            {{ fullPreflightResult.source.active_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="已有班级">
            {{ fullPreflightResult.source.with_class_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="未分班">
            {{ fullPreflightResult.source.missing_class_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="生产写入">
            已禁止
          </el-descriptions-item>
          <el-descriptions-item label="普通班">
            {{ fullPreflightResult.source.ordinary_class_count }} 个／
            {{ fullPreflightResult.source.ordinary_class_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="直属班">
            {{ fullPreflightResult.source.direct_class_count }} 个／
            {{ fullPreflightResult.source.direct_class_member_count }} 人
          </el-descriptions-item>
          <el-descriptions-item label="普通班小组">
            {{ fullPreflightResult.source.ordinary_group_pair_count }} 个
          </el-descriptions-item>
          <el-descriptions-item label="直属班小组">
            {{ fullPreflightResult.source.direct_group_pair_count }} 个
          </el-descriptions-item>
          <el-descriptions-item label="生产匹配" :span="4">
            <el-tag
              v-for="item in fullPreflightResult.matching.summary"
              :key="item.status"
              class="result-tag"
              type="info"
            >
              {{ item.status }}：{{ item.count }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="班级组织解析" :span="4">
            <el-tag
              v-for="item in fullPreflightResult.organization.class_status"
              :key="item.class_name"
              class="result-tag"
              :type="item.action === 'REUSE' ? 'success' : item.action === 'REVIEW' ? 'danger' : 'warning'"
            >
              {{ item.class_name }}（{{ item.expected_parent }}）：{{ item.action }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="小组组织处理" :span="4">
            <el-tag
              v-for="item in fullPreflightResult.organization.group_action_summary"
              :key="item.action"
              class="result-tag"
              :type="item.action === 'REUSE' ? 'success' : 'warning'"
            >
              {{ item.action }}：{{ item.count }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="待校正字段或关系" :span="4">
            <span
              v-if="!fullPreflightResult.matching.fields_or_relations_needing_reconciliation.length"
            >
              无
            </span>
            <template v-else>
              <el-tag
                v-for="item in fullPreflightResult.matching.fields_or_relations_needing_reconciliation"
                :key="item.field"
                class="result-tag"
                type="warning"
              >
                {{ item.field }}：{{ item.count }}
              </el-tag>
            </template>
          </el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="fullPreflightResult.issues.length"
          class="result-alert"
          :title="`需人工复核：${fullPreflightResult.issues.map(item => `${item.code} ${item.count}`).join('；')}`"
          type="warning"
          :closable="false"
          show-icon
        />
        <p class="form-hint">
          {{ fullPreflightResult.write_gates.join(" ") }}
        </p>
        <el-alert
          v-if="canApplyFullOrgImport"
          class="result-alert"
          title="第一阶段只创建组织节点：20个普通班、112个普通班小组；不修改任何学员或签到数据。"
          type="error"
          :closable="false"
          show-icon
        />
        <el-button
          v-if="canApplyFullOrgImport"
          type="danger"
          :loading="fullOrgImportLoading"
          @click="applyFullOrgImport"
        >
          执行第一阶段组织创建
        </el-button>
      </div>
      <template #footer>
        <el-button @click="fullPreflightVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="dialogVisible"
      title="新增学员"
      width="1180px"
      class="member-dialog"
    >
      <p class="form-hint">
        姓名、分中心和手机号为必填项；年销售额与利润率按敏感信息加密保存。
      </p>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
      >
        <div class="form-grid">
          <el-form-item label="姓名" prop="name">
            <el-input v-model="form.name" />
          </el-form-item>
          <el-form-item label="分中心" prop="org_unit_id">
            <el-select v-model="form.org_unit_id" placeholder="请选择">
              <el-option
                v-for="org in centerOrgs"
                :key="org.id"
                :label="org.name"
                :value="org.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="公司名称" prop="company_name">
            <el-input v-model="form.company_name" />
          </el-form-item>
          <el-form-item label="手机号" prop="phone">
            <el-input v-model="form.phone" maxlength="11" />
          </el-form-item>
          <el-form-item label="隶属区">
            <el-input v-model="form.district" />
          </el-form-item>
          <el-form-item label="公司地址">
            <el-input v-model="form.company_address" />
          </el-form-item>
          <el-form-item label="性别">
            <el-select v-model="form.gender" clearable placeholder="请选择">
              <el-option label="男" value="MALE" />
              <el-option label="女" value="FEMALE" />
              <el-option label="其他/未说明" value="UNSPECIFIED" />
            </el-select>
          </el-form-item>
          <el-form-item label="班级">
            <el-input v-model="form.class_name" />
          </el-form-item>
          <el-form-item label="行业分类">
            <el-input v-model="form.industry_category" />
          </el-form-item>
          <el-form-item label="生日">
            <el-date-picker
              v-model="form.birthday"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="组名">
            <el-input v-model="form.group_name" />
          </el-form-item>
          <el-form-item label="行业">
            <el-input v-model="form.industry" />
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="form.status">
              <el-option label="在册" value="ACTIVE" />
              <el-option label="停用" value="INACTIVE" />
              <el-option label="暂停" value="SUSPENDED" />
            </el-select>
          </el-form-item>
          <el-form-item label="职务">
            <el-input v-model="form.position" />
          </el-form-item>
          <el-form-item label="公司产品">
            <el-input v-model="form.company_products" />
          </el-form-item>
          <el-form-item label="入塾日期">
            <el-date-picker
              v-model="form.join_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="续费月份">
            <el-date-picker
              v-model="form.renewal_month"
              type="month"
              value-format="YYYY-MM"
              placeholder="YYYY-MM"
            />
          </el-form-item>
          <el-form-item label="年销售额">
            <el-input v-model="form.annual_sales" placeholder="按原系统口径填写" />
          </el-form-item>
          <el-form-item label="开始学习时间">
            <el-date-picker
              v-model="form.study_start_date"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="YYYY-MM-DD"
            />
          </el-form-item>
          <el-form-item label="推荐人">
            <el-input v-model="form.referrer" />
          </el-form-item>
          <el-form-item label="公司规模">
            <el-input v-model="form.company_size" />
          </el-form-item>
          <el-form-item label="入塾年限">
            <el-input-number
              v-model="form.membership_years"
              :min="0"
              :max="100"
              :precision="1"
              controls-position="right"
            />
          </el-form-item>
          <el-form-item label="推荐人所属分中心">
            <el-input v-model="form.referrer_center" />
          </el-form-item>
          <el-form-item label="利润率">
            <el-input v-model="form.profit_margin" placeholder="例如 12%" />
          </el-form-item>
          <el-form-item class="full" label="备注">
            <el-input v-model="form.notes" type="textarea" :rows="3" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">
          加密保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.member-page {
  display: grid;
  gap: 18px;
  padding: 20px;
}
.page-head {
  display: flex;
  align-items: end;
  justify-content: space-between;
  padding: 28px;
  color: #f6fff9;
  background: linear-gradient(125deg, #123c2e, #25704e);
  border-radius: 18px;
}
.page-head p {
  margin: 0 0 8px;
  color: #9fe0bd;
  letter-spacing: 0.14em;
}
.page-head h1 {
  margin: 0 0 10px;
  font-size: 30px;
}
.page-head span {
  color: #cbe9d8;
}
.head-actions {
  display: flex;
  gap: 12px;
}
.toolbar {
  display: grid;
  grid-template-columns: 220px minmax(260px, 1fr) auto;
  gap: 14px;
  align-items: center;
  margin-bottom: 18px;
}
.result-count {
  color: var(--el-text-color-secondary);
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0 18px;
}
.form-grid .full {
  grid-column: 1 / -1;
}
.form-grid :deep(.el-select) {
  width: 100%;
}
.form-grid :deep(.el-date-editor),
.form-grid :deep(.el-input-number) {
  width: 100%;
}
.form-hint {
  margin: -4px 0 20px;
  color: var(--el-text-color-secondary);
}
.preflight-upload {
  margin: 18px 0 12px;
}
.preflight-result {
  display: grid;
  gap: 14px;
  margin-top: 20px;
}
.result-tag {
  margin: 0 8px 6px 0;
}
.result-alert {
  margin-top: 4px;
}
:global(.member-dialog) {
  max-width: calc(100vw - 40px);
}
@media (max-width: 760px) {
  .page-head {
    align-items: flex-start;
    gap: 20px;
  }
  .head-actions {
    flex-wrap: wrap;
  }
  .toolbar,
  .form-grid {
    grid-template-columns: 1fr;
  }
  .form-grid .full {
    grid-column: auto;
  }
}
</style>
