<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import dayjs from "dayjs";
import QRCode from "qrcode";
import { ElMessage, ElMessageBox } from "element-plus";
import { useUserStoreHook } from "@/store/modules/user";
import { getOrgUnits, type OrgUnit } from "@/api/seiwajyuku";
import {
  confirmEnrollmentPayment,
  createEnrollmentLink,
  disableEnrollmentLink,
  enrollApplication,
  generateEnrollmentMiniProgramCode,
  getActiveEnrollmentLink,
  getEnrollmentApplication,
  getEnrollmentApplications,
  rejectEnrollmentApplication,
  reviewEnrollmentApplication,
  rotateEnrollmentLink,
  type EnrollmentApplicationDetail,
  type EnrollmentApplicationListItem,
  type EnrollmentComputedStatus,
  type EnrollmentLink,
  type EnrollmentReviewPayload
} from "@/api/enrollment";

type StatusFilter = "ALL" | EnrollmentComputedStatus;

const loading = ref(false);
const detailLoading = ref(false);
const actionLoading = ref(false);
const rows = ref<EnrollmentApplicationListItem[]>([]);
const query = ref("");
const statusFilter = ref<StatusFilter>("ALL");
const detailVisible = ref(false);
const detail = ref<EnrollmentApplicationDetail>();
const centers = ref<OrgUnit[]>([]);
const activeLink = ref<EnrollmentLink | null>(null);
const linkLoading = ref(false);
const rawPublicUrl = ref("");
const rawToken = ref("");
const qrDataUrl = ref("");
const miniProgramQrDataUrl = ref("");
const miniProgramQrLoading = ref(false);

const permissions = computed(() => useUserStoreHook().permissions);
const canReview = computed(() =>
  permissions.value.includes("enrollment:review")
);
const canConfirmPayment = computed(() =>
  permissions.value.includes("enrollment:payment_confirm")
);
const canEnroll = computed(() =>
  permissions.value.includes("enrollment:enroll")
);
const canManageLink = computed(() =>
  permissions.value.includes("enrollment:manage_link")
);

const industryOptions = [
  { value: "制造业", label: "制造业" },
  { value: "纺织 / 服装", label: "纺织 / 服装" },
  { value: "商贸 / 零售", label: "商贸 / 零售" },
  { value: "服务业", label: "服务业" },
  { value: "建筑 / 工程", label: "建筑 / 工程" },
  { value: "信息技术 / 软件", label: "信息技术 / 软件" },
  { value: "餐饮 / 文旅", label: "餐饮 / 文旅" },
  { value: "医疗 / 健康", label: "医疗 / 健康" },
  { value: "教育", label: "教育" },
  { value: "金融 / 投资", label: "金融 / 投资" },
  { value: "房地产", label: "房地产" },
  { value: "其他", label: "其他" }
];
const invoiceOptions = [
  { value: "NORMAL", label: "普票" },
  { value: "SPECIAL", label: "专票" },
  { value: "NONE", label: "无需开票" }
];
const profitMarginOptions = [
  { value: "GE_10_PERCENT", label: "10%及以上" },
  { value: "LT_10_PERCENT", label: "0%～10%以下" },
  { value: "LOSS", label: "亏损" }
];
const goalYearOptions = [
  { value: "1", label: "1年" },
  { value: "2", label: "2年" },
  { value: "3", label: "3年" },
  { value: "5", label: "5年" }
];
const growthTargetOptions = [
  { value: "UNSET", label: "暂不设定" },
  { value: "1.5", label: "1.5倍" },
  { value: "2", label: "2倍" },
  { value: "3", label: "3倍" },
  { value: "5", label: "5倍" }
];

const editForm = reactive({
  name: "",
  gender: "" as "" | "MALE" | "FEMALE" | "OTHER",
  birthday: "",
  district: "",
  political_status: "",
  company_name: "",
  company_tax_id: "",
  company_address: "",
  email: "",
  position: "",
  referrer: "",
  invoice_info: "",
  invoice_type: "",
  invoice_title: "",
  invoice_tax_id: "",
  invoice_registered_address: "",
  invoice_phone: "",
  invoice_bank: "",
  invoice_account: "",
  industry_category: "",
  industry_other: "",
  industry: "",
  company_products: "",
  employee_count: null as number | null,
  books_read: "",
  enrollment_reason_philosophy: "",
  enrollment_reason_change: "",
  enrollment_reason_other: "",
  learning_years_goal: "",
  learning_participation_goal: "",
  business_goal: "",
  other_goal: "",
  goal_years: "",
  revenue_growth_target: "",
  profit_growth_target: "",
  annual_sales: "",
  profit_margin: "",
  notes: "",
  org_unit_id: "",
  join_date: "",
  review_note: ""
});

const statusTabs: Array<{ value: StatusFilter; label: string }> = [
  { value: "ALL", label: "全部" },
  { value: "PENDING_REVIEW", label: "待审核" },
  { value: "PENDING_PAYMENT", label: "待收款" },
  { value: "PENDING_CENTER", label: "待分中心" },
  { value: "PENDING_ENROLLMENT", label: "待正式入塾" },
  { value: "ENROLLED", label: "已入塾" },
  { value: "REJECTED", label: "已驳回" }
];

const filteredRows = computed(() =>
  statusFilter.value === "ALL"
    ? rows.value
    : rows.value.filter(row => row.computed_status === statusFilter.value)
);

const hasLegacyLearningFields = computed(() => {
  const value = detail.value;
  if (!value) return false;
  return [
    value.books_read,
    value.enrollment_reason_philosophy,
    value.enrollment_reason_change,
    value.enrollment_reason_other,
    value.learning_years_goal,
    value.learning_participation_goal,
    value.business_goal,
    value.other_goal
  ].some(item => Boolean(item && String(item).trim()));
});

const statusLabels: Record<EnrollmentComputedStatus, string> = {
  PENDING_REVIEW: "待审核",
  PENDING_PAYMENT: "待收款",
  PENDING_CENTER: "待分中心",
  PENDING_ENROLLMENT: "待正式入塾",
  ENROLLED: "已入塾",
  REJECTED: "已驳回",
  CANCELLED: "已取消"
};

function statusType(status: EnrollmentComputedStatus) {
  if (status === "ENROLLED") return "success";
  if (status === "REJECTED" || status === "CANCELLED") return "info";
  if (status === "PENDING_ENROLLMENT") return "primary";
  return "warning";
}

function formatTime(value?: string | null) {
  return value ? dayjs(value).format("YYYY-MM-DD HH:mm") : "—";
}

function phoneHref(value?: string | null) {
  return value ? `tel:${value}` : undefined;
}

function errorText(error: any, fallback = "操作失败") {
  return error?.response?.data?.detail || fallback;
}

async function loadRows() {
  loading.value = true;
  try {
    const response = await getEnrollmentApplications({
      query: query.value.trim() || undefined,
      limit: 200
    });
    rows.value = response.data;
  } catch (error: any) {
    ElMessage.error(errorText(error, "申请列表加载失败"));
  } finally {
    loading.value = false;
  }
}

async function loadCenters() {
  try {
    const response = await getOrgUnits();
    centers.value = response.data.filter(
      item => item.unit_type === "REGIONAL_CENTER"
    );
  } catch (error: any) {
    ElMessage.error(errorText(error, "分中心选项加载失败"));
  }
}

function syncEditForm(value: EnrollmentApplicationDetail) {
  Object.assign(editForm, {
    name: value.name || "",
    gender: value.gender || "",
    birthday: value.birthday || "",
    district: value.district || "",
    political_status: value.political_status || "",
    company_name: value.company_name || "",
    company_tax_id: value.company_tax_id || "",
    company_address: value.company_address || "",
    email: value.email || "",
    position: value.position || "",
    referrer: value.referrer || "",
    invoice_info: value.invoice_info || "",
    invoice_type: value.invoice_type || "",
    invoice_title: value.invoice_title || "",
    invoice_tax_id: value.invoice_tax_id || "",
    invoice_registered_address: value.invoice_registered_address || "",
    invoice_phone: value.invoice_phone || "",
    invoice_bank: value.invoice_bank || "",
    invoice_account: value.invoice_account || "",
    industry_category: value.industry_category || "",
    industry_other: value.industry_other || "",
    industry: value.industry || "",
    company_products: value.company_products || "",
    employee_count: value.employee_count ?? null,
    books_read: value.books_read || "",
    enrollment_reason_philosophy: value.enrollment_reason_philosophy || "",
    enrollment_reason_change: value.enrollment_reason_change || "",
    enrollment_reason_other: value.enrollment_reason_other || "",
    learning_years_goal: value.learning_years_goal || "",
    learning_participation_goal: value.learning_participation_goal || "",
    business_goal: value.business_goal || "",
    other_goal: value.other_goal || "",
    goal_years: value.goal_years || "",
    revenue_growth_target: value.revenue_growth_target || "",
    profit_growth_target: value.profit_growth_target || "",
    annual_sales: value.annual_sales || "",
    profit_margin: value.profit_margin || "",
    notes: value.notes || "",
    org_unit_id: value.org_unit_id || "",
    join_date: value.join_date || "",
    review_note: value.review_note || ""
  });
}

async function openDetail(row: unknown) {
  const application = row as EnrollmentApplicationListItem;
  detailVisible.value = true;
  detailLoading.value = true;
  try {
    const response = await getEnrollmentApplication(application.id);
    detail.value = response.data;
    syncEditForm(response.data);
  } catch (error: any) {
    detailVisible.value = false;
    ElMessage.error(errorText(error, "申请详情加载失败"));
  } finally {
    detailLoading.value = false;
  }
}

function buildReviewPayload(
  decision: "SAVE" | "APPROVE"
): EnrollmentReviewPayload {
  const payload: EnrollmentReviewPayload = {
    decision,
    review_note: editForm.review_note.trim() || undefined,
    name: editForm.name.trim(),
    gender: editForm.gender === "OTHER" ? undefined : editForm.gender || null,
    birthday: editForm.birthday || null,
    district: editForm.district.trim() || null,
    political_status: editForm.political_status.trim() || null,
    company_name: editForm.company_name.trim() || null,
    company_address: editForm.company_address.trim() || null,
    email: editForm.email.trim() || null,
    position: editForm.position.trim() || null,
    referrer: editForm.referrer.trim() || null,
    company_products: editForm.company_products.trim() || null,
    employee_count: editForm.employee_count,
    notes: editForm.notes.trim() || null,
    org_unit_id: editForm.org_unit_id || null,
    join_date: editForm.join_date || null
  };
  if (editForm.industry_category.trim() || editForm.industry.trim()) {
    payload.industry_category = editForm.industry_category.trim() || null;
    payload.industry_other = editForm.industry_other.trim() || null;
    payload.industry = editForm.industry.trim() || null;
  }
  if (hasLegacyLearningFields.value) {
    payload.books_read = editForm.books_read.trim() || null;
    payload.enrollment_reason_philosophy =
      editForm.enrollment_reason_philosophy.trim() || null;
    payload.enrollment_reason_change = editForm.enrollment_reason_change.trim() || null;
    payload.enrollment_reason_other = editForm.enrollment_reason_other.trim() || null;
    payload.learning_years_goal = editForm.learning_years_goal.trim() || null;
    payload.learning_participation_goal =
      editForm.learning_participation_goal.trim() || null;
    payload.business_goal = editForm.business_goal.trim() || null;
    payload.other_goal = editForm.other_goal.trim() || null;
  }
  if (editForm.goal_years.trim()) payload.goal_years = editForm.goal_years.trim();
  if (editForm.revenue_growth_target.trim()) {
    payload.revenue_growth_target = editForm.revenue_growth_target.trim();
  }
  if (editForm.profit_growth_target.trim()) {
    payload.profit_growth_target = editForm.profit_growth_target.trim();
  }
  if (detail.value?.financial_fields_visible) {
    payload.annual_sales = editForm.annual_sales.trim() || null;
    payload.profit_margin = editForm.profit_margin.trim() || null;
    const invoiceHasValue = [
      editForm.company_tax_id,
      editForm.invoice_type,
      editForm.invoice_info,
      editForm.invoice_title,
      editForm.invoice_tax_id,
      editForm.invoice_registered_address,
      editForm.invoice_phone,
      editForm.invoice_bank,
      editForm.invoice_account
    ].some(value => value.trim());
    if (invoiceHasValue) {
      payload.company_tax_id = editForm.company_tax_id.trim() || null;
      payload.invoice_type = editForm.invoice_type.trim() || null;
      payload.invoice_info = editForm.invoice_info.trim() || null;
      payload.invoice_title = editForm.invoice_title.trim() || null;
      payload.invoice_tax_id = editForm.invoice_tax_id.trim() || null;
      payload.invoice_registered_address =
        editForm.invoice_registered_address.trim() || null;
      payload.invoice_phone = editForm.invoice_phone.trim() || null;
      payload.invoice_bank = editForm.invoice_bank.trim() || null;
      payload.invoice_account = editForm.invoice_account.trim() || null;
    }
  }
  return payload;
}

async function saveReview(decision: "SAVE" | "APPROVE") {
  if (!detail.value) return;
  if (!editForm.name.trim()) {
    ElMessage.warning("姓名不能为空");
    return;
  }
  if (decision === "APPROVE") {
    await ElMessageBox.confirm(
      "审核通过只代表资料复核完成，仍需收款、正式分中心和最终入塾确认。",
      "确认审核通过",
      { type: "warning", confirmButtonText: "确认通过" }
    );
  }
  actionLoading.value = true;
  try {
    const response = await reviewEnrollmentApplication(
      detail.value.id,
      buildReviewPayload(decision)
    );
    detail.value = response.data;
    syncEditForm(response.data);
    ElMessage.success(decision === "APPROVE" ? "审核已通过" : "申请资料已保存");
    await loadRows();
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error));
    }
  } finally {
    actionLoading.value = false;
  }
}

async function confirmPayment() {
  if (!detail.value) return;
  try {
    const { value } = await ElMessageBox.prompt(
      "填写本次已确认收款金额（可留空），V1 仅开放“已收款”状态。",
      "确认收款",
      {
        confirmButtonText: "确认已收款",
        inputPlaceholder: "可留空或填写实际收款金额",
        inputValidator: value =>
          !value || /^\d+(\.\d{1,2})?$/.test(value) || "请输入有效金额"
      }
    );
    actionLoading.value = true;
    const response = await confirmEnrollmentPayment(detail.value.id, {
      payment_status: "PAID",
      amount: value || undefined
    });
    detail.value = response.data;
    syncEditForm(response.data);
    ElMessage.success("收款状态已确认");
    await loadRows();
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error, "收款确认失败"));
    }
  } finally {
    actionLoading.value = false;
  }
}

async function rejectApplication() {
  if (!detail.value) return;
  try {
    const { value } = await ElMessageBox.prompt(
      "请填写驳回原因。原因会进入审计记录，但不会向公开提交页泄露。",
      "驳回申请",
      {
        type: "warning",
        confirmButtonText: "确认驳回",
        inputType: "textarea",
        inputValidator: value =>
          (value || "").trim().length >= 4 || "驳回原因至少4个字符"
      }
    );
    actionLoading.value = true;
    const response = await rejectEnrollmentApplication(
      detail.value.id,
      value.trim()
    );
    detail.value = response.data;
    syncEditForm(response.data);
    ElMessage.success("申请已驳回");
    await loadRows();
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error, "驳回失败"));
    }
  } finally {
    actionLoading.value = false;
  }
}

async function completeEnrollment() {
  if (!detail.value) return;
  try {
    await ElMessageBox.confirm(
      "系统将以当前申请资料建立正式学长档案，并写入所选分中心关系。该动作可幂等重试，但不能绕过缺失门槛。",
      "确认正式入塾",
      { type: "warning", confirmButtonText: "确认建立正式档案" }
    );
    actionLoading.value = true;
    const response = await enrollApplication(detail.value.id);
    ElMessage.success(
      response.data.idempotent
        ? "该申请已完成入塾，未重复建档"
        : `正式学长档案已建立（ID ${response.data.member_id}）`
    );
    const refreshed = await getEnrollmentApplication(detail.value.id);
    detail.value = refreshed.data;
    syncEditForm(refreshed.data);
    await loadRows();
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error, "正式入塾失败"));
    }
  } finally {
    actionLoading.value = false;
  }
}

function publicUrlForToken(rawToken: string) {
  const basePath = (import.meta.env.BASE_URL || "/").replace(/\/$/, "");
  return `${window.location.origin}${basePath}/#/enroll/${encodeURIComponent(rawToken)}`;
}

async function rememberRawToken(link: EnrollmentLink) {
  if (!link.raw_token) return;
  rawToken.value = link.raw_token;
  rawPublicUrl.value = publicUrlForToken(link.raw_token);
  sessionStorage.setItem(
    `enrollment-public-url-${link.id}`,
    rawPublicUrl.value
  );
  sessionStorage.setItem(`enrollment-raw-token-${link.id}`, link.raw_token);
  qrDataUrl.value = await QRCode.toDataURL(rawPublicUrl.value, {
    width: 320,
    margin: 2,
    color: { dark: "#173f2f", light: "#ffffff" }
  });
  await generateMiniProgramCode(link.raw_token, false);
}

async function restoreRawUrl(link: EnrollmentLink | null) {
  rawPublicUrl.value = "";
  rawToken.value = "";
  qrDataUrl.value = "";
  miniProgramQrDataUrl.value = "";
  if (!link) return;
  const saved = sessionStorage.getItem(`enrollment-public-url-${link.id}`);
  const savedToken = sessionStorage.getItem(`enrollment-raw-token-${link.id}`);
  if (!saved && !savedToken) return;
  rawToken.value =
    savedToken ||
    (saved ? decodeURIComponent(saved.split("/enroll/").pop() || "") : "");
  rawPublicUrl.value =
    saved || (rawToken.value ? publicUrlForToken(rawToken.value) : "");
  if (saved) {
    qrDataUrl.value = await QRCode.toDataURL(saved, {
      width: 320,
      margin: 2,
      color: { dark: "#173f2f", light: "#ffffff" }
    });
  }
  if (rawToken.value) await generateMiniProgramCode(rawToken.value, false);
}

async function generateMiniProgramCode(token = rawToken.value, notify = true) {
  if (!activeLink.value || !token) return false;
  miniProgramQrLoading.value = true;
  try {
    const response = await generateEnrollmentMiniProgramCode(
      activeLink.value.id,
      token
    );
    miniProgramQrDataUrl.value = response.data.image_data_url;
    return true;
  } catch (error: any) {
    miniProgramQrDataUrl.value = "";
    if (notify) {
      ElMessage.warning(
        errorText(error, "小程序码生成失败，请检查微信小程序配置")
      );
    }
    return false;
  } finally {
    miniProgramQrLoading.value = false;
  }
}

async function loadActiveLink() {
  if (!canManageLink.value) return;
  linkLoading.value = true;
  try {
    const response = await getActiveEnrollmentLink();
    activeLink.value = response.data;
    await restoreRawUrl(response.data);
  } catch (error: any) {
    ElMessage.error(errorText(error, "二维码状态加载失败"));
  } finally {
    linkLoading.value = false;
  }
}

async function createLink() {
  try {
    const { value } = await ElMessageBox.prompt(
      "系统全局只保留一个有效小程序码入口。创建新码会停用旧入口。",
      "创建小程序码入口",
      {
        inputValue: "学长服务助手-新学长信息登记",
        inputValidator: value => !!value.trim() || "请填写入口名称"
      }
    );
    linkLoading.value = true;
    const response = await createEnrollmentLink(value.trim());
    activeLink.value = response.data;
    await rememberRawToken(response.data);
    ElMessage.success("小程序码入口已创建，请立即下载并保存小程序码");
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error, "二维码创建失败"));
    }
  } finally {
    linkLoading.value = false;
  }
}

async function rotateLink() {
  if (!activeLink.value) return;
  try {
    await ElMessageBox.confirm(
      "轮换后旧小程序码和 H5 入口都会立即失效。新入口只在本次生成后可见，请及时保存。",
      "轮换小程序码入口",
      { type: "warning", confirmButtonText: "确认轮换" }
    );
    linkLoading.value = true;
    sessionStorage.removeItem(`enrollment-public-url-${activeLink.value.id}`);
    const response = await rotateEnrollmentLink(activeLink.value.id);
    activeLink.value = response.data;
    await rememberRawToken(response.data);
    ElMessage.success("小程序码入口已轮换，旧入口已失效");
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error, "二维码轮换失败"));
    }
  } finally {
    linkLoading.value = false;
  }
}

async function disableLink() {
  if (!activeLink.value) return;
  try {
    await ElMessageBox.confirm(
      "停用后公开申请页将立即不可提交。如需恢复，必须创建新的二维码。",
      "停用入塾二维码",
      { type: "warning", confirmButtonText: "确认停用" }
    );
    linkLoading.value = true;
    await disableEnrollmentLink(activeLink.value.id);
    sessionStorage.removeItem(`enrollment-public-url-${activeLink.value.id}`);
    activeLink.value = null;
    rawPublicUrl.value = "";
    rawToken.value = "";
    qrDataUrl.value = "";
    miniProgramQrDataUrl.value = "";
    ElMessage.success("入塾二维码已停用");
  } catch (error: any) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(errorText(error, "二维码停用失败"));
    }
  } finally {
    linkLoading.value = false;
  }
}

async function copyPublicUrl() {
  try {
    await navigator.clipboard.writeText(rawPublicUrl.value);
    ElMessage.success("公开申请链接已复制");
  } catch {
    ElMessage.warning("浏览器未允许复制，请手动选择链接复制");
  }
}

function downloadMiniProgramCode() {
  if (!miniProgramQrDataUrl.value) return;
  const anchor = document.createElement("a");
  anchor.href = miniProgramQrDataUrl.value;
  anchor.download = "学长服务助手-新学长信息登记-小程序码.png";
  anchor.click();
}

function downloadFallbackQr() {
  if (!qrDataUrl.value) return;
  const anchor = document.createElement("a");
  anchor.href = qrDataUrl.value;
  anchor.download = "新学长信息登记-H5备用二维码.png";
  anchor.click();
}

onMounted(async () => {
  await Promise.all([loadRows(), loadCenters(), loadActiveLink()]);
});
</script>

<template>
  <div class="enrollment-admin">
    <div class="page-heading">
      <div>
        <h2>新学长入塾申请</h2>
        <p>
          公开提交仅进入申请池；完成审核、收款、正式分中心和最终确认后才建立学长档案。
        </p>
      </div>
      <el-button :loading="loading" @click="loadRows">刷新列表</el-button>
    </div>

    <el-card
      v-if="canManageLink"
      v-loading="linkLoading"
      class="link-card"
      shadow="never"
    >
      <template #header>
        <div class="card-header">
          <div>
            <strong>微信小程序主入口</strong>
            <span>日常使用小程序码；H5 仅保留为备用入口</span>
          </div>
          <el-tag :type="activeLink ? 'success' : 'info'">
            {{ activeLink ? "启用中" : "未创建" }}
          </el-tag>
        </div>
      </template>

      <div v-if="activeLink" class="link-layout">
        <div class="link-info">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="名称">{{
              activeLink.name
            }}</el-descriptions-item>
            <el-descriptions-item label="最近轮换">
              {{
                formatTime(activeLink.last_rotated_at || activeLink.created_at)
              }}
            </el-descriptions-item>
          </el-descriptions>
          <template v-if="rawPublicUrl">
            <p class="secret-note">
              原始入口只保存在当前浏览器会话中，服务端仅保存摘要。请及时下载小程序码；H5
              链接仅作备用。
            </p>
            <el-input v-model="rawPublicUrl" readonly>
              <template #append>
                <el-button @click="copyPublicUrl">复制 H5 备用链接</el-button>
              </template>
            </el-input>
          </template>
          <el-alert
            v-else
            title="服务端不保存原始入口；如之前未保存，请轮换小程序码入口生成新码。"
            type="warning"
            :closable="false"
            show-icon
          />
          <div class="link-actions">
            <el-button
              v-if="miniProgramQrDataUrl"
              type="primary"
              @click="downloadMiniProgramCode"
            >
              下载小程序码
            </el-button>
            <el-button
              v-if="rawToken"
              :loading="miniProgramQrLoading"
              @click="generateMiniProgramCode()"
            >
              生成小程序码
            </el-button>
            <el-button v-if="qrDataUrl" @click="downloadFallbackQr">
              下载 H5 备用码
            </el-button>
            <el-button @click="rotateLink">轮换入口</el-button>
            <el-button type="danger" plain @click="disableLink">停用</el-button>
          </div>
        </div>
        <div v-if="miniProgramQrDataUrl" class="qr-preview">
          <img
            :src="miniProgramQrDataUrl"
            alt="学长服务助手新学长信息登记小程序码"
          />
        </div>
        <div v-else class="qr-preview empty-qr-preview">
          <span>配置小程序 AppID 后生成主入口小程序码</span>
        </div>
      </div>
      <el-empty v-else description="当前没有有效的入塾二维码">
        <el-button type="primary" @click="createLink"
          >创建小程序码入口</el-button
        >
      </el-empty>
    </el-card>

    <el-card shadow="never" class="list-card">
      <div class="filters">
        <el-input
          v-model="query"
          clearable
          placeholder="搜索申请编号、姓名或手机号后四位"
          @keyup.enter="loadRows"
          @clear="loadRows"
        >
          <template #append>
            <el-button @click="loadRows">搜索</el-button>
          </template>
        </el-input>
      </div>

      <el-tabs v-model="statusFilter">
        <el-tab-pane
          v-for="tab in statusTabs"
          :key="tab.value"
          :name="tab.value"
        >
          <template #label>
            {{ tab.label }}
            <span class="tab-count">
              {{
                tab.value === "ALL"
                  ? rows.length
                  : rows.filter(item => item.computed_status === tab.value)
                      .length
              }}
            </span>
          </template>
        </el-tab-pane>
      </el-tabs>

      <el-table
        v-loading="loading"
        :data="filteredRows"
        row-key="id"
        @row-click="openDetail"
      >
        <el-table-column
          prop="application_no"
          label="申请编号"
          min-width="170"
        />
        <el-table-column prop="name" label="姓名" min-width="110" />
        <el-table-column label="手机号" min-width="150">
          <template #default="scope">
            <el-link
              v-if="scope.row.phone"
              :href="phoneHref(scope.row.phone)"
              type="primary"
              @click.stop
            >
              {{ scope.row.phone }}
            </el-link>
            <span v-else>{{ scope.row.phone_masked }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="company_name"
          label="企业"
          min-width="170"
          show-overflow-tooltip
        />
        <el-table-column label="处理状态" min-width="130">
          <template #default="scope">
            <el-tag :type="statusType(scope.row.computed_status)">
              {{ statusLabels[scope.row.computed_status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="正式分中心" min-width="150">
          <template #default="scope">{{
            scope.row.org_unit_name || "待分配"
          }}</template>
        </el-table-column>
        <el-table-column label="风险" min-width="130">
          <template #default="scope">
            <el-tag
              v-if="scope.row.duplicate_member_risk"
              type="danger"
              effect="plain"
            >
              疑似已有档案
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="提交时间" min-width="160">
          <template #default="scope">{{
            formatTime(scope.row.created_at)
          }}</template>
        </el-table-column>
        <el-table-column label="操作" fixed="right" width="90">
          <template #default="scope">
            <el-button link type="primary" @click.stop="openDetail(scope.row)">
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer
      v-model="detailVisible"
      title="入塾申请详情"
      size="min(760px, 96vw)"
      destroy-on-close
    >
      <div v-loading="detailLoading" class="detail-body">
        <template v-if="detail">
          <div class="detail-title">
            <div>
              <h3>{{ detail.name }}</h3>
              <p>
                {{ detail.application_no }} ·
                <el-link
                  :href="phoneHref(detail.phone)"
                  type="primary"
                  :underline="false"
                >
                  {{ detail.phone || detail.phone_masked }}
                </el-link>
              </p>
            </div>
            <el-tag :type="statusType(detail.computed_status)" size="large">
              {{ statusLabels[detail.computed_status] }}
            </el-tag>
          </div>

          <el-alert
            v-if="detail.duplicate_member_risk"
            title="该手机号疑似已有正式学员档案。请先人工核对或处理重复档案，服务端不会允许再次建档。"
            type="error"
            :closable="false"
            show-icon
          />

          <el-form label-position="top" class="review-form">
            <div class="form-grid">
              <el-form-item label="姓名">
                <el-input v-model="editForm.name" :disabled="!canReview" />
              </el-form-item>
              <el-form-item label="手机号">
                <el-link
                  :href="phoneHref(detail.phone)"
                  type="primary"
                  :underline="false"
                >
                  {{ detail.phone || detail.phone_masked }}
                </el-link>
              </el-form-item>
              <el-form-item label="性别">
                <el-select
                  v-if="editForm.gender !== 'OTHER'"
                  v-model="editForm.gender"
                  clearable
                  :disabled="!canReview"
                >
                  <el-option label="男" value="MALE" />
                  <el-option label="女" value="FEMALE" />
                </el-select>
                <el-alert
                  v-else
                  title="其他（历史值）"
                  type="info"
                  :closable="false"
                />
              </el-form-item>
              <el-form-item label="生日">
                <el-date-picker
                  v-model="editForm.birthday"
                  type="date"
                  value-format="YYYY-MM-DD"
                  :disabled="!canReview"
                />
              </el-form-item>
              <el-form-item label="所在地区">
                <el-input v-model="editForm.district" :disabled="!canReview" />
              </el-form-item>
              <el-form-item label="政治面貌">
                <el-input
                  v-model="editForm.political_status"
                  :disabled="!canReview"
                />
              </el-form-item>
              <el-form-item label="企业名称">
                <el-input
                  v-model="editForm.company_name"
                  :disabled="!canReview"
                />
              </el-form-item>
              <el-form-item label="统一社会信用代码 / 税号">
                <el-input
                  v-model="editForm.company_tax_id"
                  :disabled="!canReview || !detail.financial_fields_visible"
                  placeholder="敏感资料权限可见"
                />
              </el-form-item>
              <el-form-item label="职务">
                <el-input v-model="editForm.position" :disabled="!canReview" />
              </el-form-item>
              <el-form-item label="邮箱">
                <el-input v-model="editForm.email" :disabled="!canReview" />
              </el-form-item>
              <el-form-item label="推荐人">
                <el-input v-model="editForm.referrer" :disabled="!canReview" />
              </el-form-item>
              <el-form-item label="公司地址">
                <el-input
                  v-model="editForm.company_address"
                  :disabled="!canReview"
                />
              </el-form-item>
              <el-form-item label="发票类型">
                <el-select
                  v-model="editForm.invoice_type"
                  clearable
                  :disabled="!canReview || !detail.invoice_fields_visible"
                >
                  <el-option
                    v-for="option in invoiceOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="行业大类">
                <el-select
                  v-model="editForm.industry_category"
                  :disabled="!canReview"
                  clearable
                >
                  <el-option
                    v-for="option in industryOptions"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </el-form-item>
              <el-form-item
                v-if="editForm.industry_category === '其他'"
                label="其他行业"
              >
                <el-input
                  v-model="editForm.industry_other"
                  :disabled="!canReview"
                />
              </el-form-item>
              <el-form-item label="正式归属分中心">
                <el-select
                  v-model="editForm.org_unit_id"
                  clearable
                  filterable
                  :disabled="!canReview"
                  placeholder="正式入塾前必须选择"
                >
                  <el-option
                    v-for="center in centers"
                    :key="center.id"
                    :label="center.name"
                    :value="center.id"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="员工人数">
                <el-input-number
                  v-model="editForm.employee_count"
                  :min="0"
                  :max="10000000"
                  :disabled="!canReview"
                  controls-position="right"
                />
              </el-form-item>
              <el-form-item label="入塾日期">
                <el-date-picker
                  v-model="editForm.join_date"
                  type="date"
                  value-format="YYYY-MM-DD"
                  :disabled="!canReview"
                  placeholder="可在正式入塾前补充"
                />
              </el-form-item>
            </div>

            <el-form-item label="主要产品或服务">
              <el-input
                v-model="editForm.company_products"
                type="textarea"
                :rows="2"
                :disabled="!canReview"
              />
            </el-form-item>

            <div class="invoice-detail">
              <div class="section-label">开票资料</div>
              <el-alert
                v-if="!detail.invoice_fields_visible"
                title="开票抬头、税号及银行资料属于敏感信息，当前账号无查看权限。"
                type="info"
                :closable="false"
                show-icon
              />
              <template v-else-if="editForm.invoice_type !== 'NONE'">
                <el-alert
                  v-if="editForm.invoice_info && !editForm.invoice_title"
                  title="这是历史申请的开票资料原文；保存前请按下方结构化字段核对。"
                  type="warning"
                  :closable="false"
                />
                <el-input
                  v-if="editForm.invoice_info && !editForm.invoice_title"
                  v-model="editForm.invoice_info"
                  type="textarea"
                  :rows="2"
                  disabled
                  class="legacy-invoice"
                />
                <div class="form-grid">
                  <el-form-item label="发票抬头">
                    <el-input v-model="editForm.invoice_title" :disabled="!canReview" />
                  </el-form-item>
                  <el-form-item label="发票税号">
                    <el-input v-model="editForm.invoice_tax_id" :disabled="!canReview" />
                  </el-form-item>
                </div>
                <div v-if="editForm.invoice_type === 'SPECIAL'" class="form-grid">
                  <el-form-item label="注册地址">
                    <el-input v-model="editForm.invoice_registered_address" :disabled="!canReview" />
                  </el-form-item>
                  <el-form-item label="注册电话">
                    <el-input v-model="editForm.invoice_phone" :disabled="!canReview" />
                  </el-form-item>
                  <el-form-item label="开户银行">
                    <el-input v-model="editForm.invoice_bank" :disabled="!canReview" />
                  </el-form-item>
                  <el-form-item label="银行账号">
                    <el-input v-model="editForm.invoice_account" :disabled="!canReview" />
                  </el-form-item>
                </div>
              </template>
              <p v-else class="muted">申请人选择无需开票。</p>
            </div>

            <div v-if="hasLegacyLearningFields" class="legacy-learning-panel">
              <div class="section-label">历史申请字段（仅历史有值时显示）</div>
              <el-form-item label="所读稻盛和夫著作">
              <el-input
                v-model="editForm.books_read"
                type="textarea"
                :rows="2"
                :disabled="!canReview"
              />
              </el-form-item>

              <div class="form-grid">
                <el-form-item label="认同的哲学理念">
                  <el-input
                    v-model="editForm.enrollment_reason_philosophy"
                    type="textarea"
                    :rows="3"
                    :disabled="!canReview"
                  />
                </el-form-item>
                <el-form-item label="期望改变或努力方向">
                  <el-input
                    v-model="editForm.enrollment_reason_change"
                    type="textarea"
                    :rows="3"
                    :disabled="!canReview"
                  />
                </el-form-item>
              </div>
              <el-form-item label="入塾初心的其他内容">
                <el-input
                  v-model="editForm.enrollment_reason_other"
                  type="textarea"
                  :rows="3"
                  :disabled="!canReview"
                />
              </el-form-item>

              <div class="goal-panel">
                <div class="section-label">历史入塾后目标</div>
                <el-form-item label="学习年限目标">
                  <el-input
                    v-model="editForm.learning_years_goal"
                    :disabled="!canReview"
                  />
                </el-form-item>
                <el-form-item label="学习与活动参与目标">
                  <el-input
                    v-model="editForm.learning_participation_goal"
                    type="textarea"
                    :rows="2"
                    :disabled="!canReview"
                  />
                </el-form-item>
                <el-form-item label="公司业绩目标">
                  <el-input
                    v-model="editForm.business_goal"
                    type="textarea"
                    :rows="2"
                    :disabled="!canReview"
                  />
                </el-form-item>
                <el-form-item label="其他目标">
                  <el-input
                    v-model="editForm.other_goal"
                    type="textarea"
                    :rows="2"
                    :disabled="!canReview"
                  />
                </el-form-item>
              </div>
            </div>

            <div class="goal-panel">
              <div class="section-label">V1.1.1 结构化目标</div>
              <div class="form-grid">
                <el-form-item label="计划学习年限">
                  <el-select v-model="editForm.goal_years" :disabled="!canReview" clearable placeholder="可选">
                    <el-option
                      v-for="option in goalYearOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="业绩提升目标">
                  <el-select v-model="editForm.revenue_growth_target" :disabled="!canReview" clearable placeholder="可选">
                    <el-option
                      v-for="option in growthTargetOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="利润提升目标">
                  <el-select v-model="editForm.profit_growth_target" :disabled="!canReview" clearable placeholder="可选">
                    <el-option
                      v-for="option in growthTargetOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
              </div>
            </div>

            <div class="financial-detail">
              <div class="section-label">企业敏感财务资料</div>
              <div v-if="detail.financial_fields_visible" class="form-grid">
                <el-form-item label="年销售额">
                  <el-input
                    v-model="editForm.annual_sales"
                    :disabled="!canReview"
                  />
                </el-form-item>
                <el-form-item label="利润率">
                  <el-select
                    v-model="editForm.profit_margin"
                    :disabled="!canReview"
                    clearable
                  >
                    <el-option
                      v-for="option in profitMarginOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
              </div>
              <el-alert
                v-else-if="detail.has_enterprise_financial_data"
                title="申请人已填写财务资料；当前账号无企业敏感资料查看权限，精确值保持隐藏。"
                type="info"
                :closable="false"
                show-icon
              />
              <p v-else class="muted">申请人未填写年销售额或利润率。</p>
            </div>

            <el-form-item label="其他补充">
              <el-input
                v-model="editForm.notes"
                type="textarea"
                :rows="3"
                :disabled="!canReview"
              />
            </el-form-item>
            <el-form-item label="审核备注">
              <el-input
                v-model="editForm.review_note"
                type="textarea"
                :rows="2"
                :disabled="!canReview"
                placeholder="记录本次复核依据，不填写敏感正文"
              />
            </el-form-item>
          </el-form>

          <el-alert
            :title="detail.rules_acknowledged ? '申请人已确认加入守则与缴费说明' : '申请人尚未确认加入守则与缴费说明（历史记录）'"
            :type="detail.rules_acknowledged ? 'success' : 'warning'"
            :closable="false"
            show-icon
          />

          <div class="gate-panel">
            <div class="section-label">正式入塾门槛</div>
            <el-result
              v-if="detail.application_status === 'ENROLLED'"
              icon="success"
              title="已建立正式学长档案"
              :sub-title="`学长档案 ID：${detail.converted_member_id}`"
            />
            <template v-else-if="detail.missing_gates.length">
              <p
                v-for="item in detail.missing_gates"
                :key="item"
                class="gate-item"
              >
                <span>!</span>{{ item }}
              </p>
            </template>
            <p v-else class="gate-ready">
              ✓ 所有服务端门槛已满足，可以正式入塾
            </p>
          </div>

          <el-descriptions class="audit-summary" :column="2" border>
            <el-descriptions-item label="提交时间">{{
              formatTime(detail.created_at)
            }}</el-descriptions-item>
            <el-descriptions-item label="审核人">{{
              detail.reviewer_name || "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="审核时间">{{
              formatTime(detail.reviewed_at)
            }}</el-descriptions-item>
            <el-descriptions-item label="收款确认人">{{
              detail.payment_confirmer_name || "—"
            }}</el-descriptions-item>
            <el-descriptions-item label="收款确认时间">{{
              formatTime(detail.payment_confirmed_at)
            }}</el-descriptions-item>
            <el-descriptions-item label="正式入塾时间">{{
              formatTime(detail.converted_at)
            }}</el-descriptions-item>
          </el-descriptions>

          <div class="drawer-actions">
            <el-button
              v-if="
                canReview &&
                ['SUBMITTED', 'APPROVED'].includes(detail.application_status)
              "
              :loading="actionLoading"
              @click="saveReview('SAVE')"
            >
              保存资料
            </el-button>
            <el-button
              v-if="
                canReview &&
                ['SUBMITTED', 'APPROVED'].includes(detail.application_status)
              "
              type="primary"
              :loading="actionLoading"
              @click="saveReview('APPROVE')"
            >
              审核通过
            </el-button>
            <el-button
              v-if="
                canConfirmPayment &&
                ['SUBMITTED', 'APPROVED'].includes(detail.application_status)
              "
              type="success"
              plain
              :loading="actionLoading"
              @click="confirmPayment"
            >
              确认收款
            </el-button>
            <el-button
              v-if="canEnroll && detail.application_status !== 'ENROLLED'"
              type="success"
              :disabled="!detail.can_enroll"
              :loading="actionLoading"
              @click="completeEnrollment"
            >
              正式入塾
            </el-button>
            <el-button
              v-if="
                canReview &&
                !['REJECTED', 'ENROLLED', 'CANCELLED'].includes(
                  detail.application_status
                )
              "
              type="danger"
              plain
              :loading="actionLoading"
              @click="rejectApplication"
            >
              驳回申请
            </el-button>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped lang="scss">
.enrollment-admin {
  padding: 20px;
}

.page-heading,
.card-header,
.detail-title,
.drawer-actions {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
}

.page-heading {
  margin-bottom: 18px;
}

.page-heading h2,
.detail-title h3 {
  margin: 0 0 6px;
}

.page-heading p,
.detail-title p {
  margin: 0;
  color: var(--el-text-color-secondary);
}

.link-card,
.list-card {
  margin-bottom: 18px;
  border-radius: 14px;
}

.card-header > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.card-header span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.link-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 28px;
  align-items: center;
}

.secret-note {
  margin: 18px 0 8px;
  color: #8b6428;
  font-size: 13px;
}

.link-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.qr-preview {
  padding: 10px;
  text-align: center;
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  border-radius: 12px;
}

.empty-qr-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
  padding: 24px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.qr-preview img {
  width: 100%;
  max-width: 200px;
}

.filters {
  display: flex;
  width: min(460px, 100%);
  margin-bottom: 4px;
}

.tab-count {
  padding: 1px 6px;
  margin-left: 4px;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  background: var(--el-fill-color-light);
  border-radius: 999px;
}

.detail-body {
  min-height: 300px;
}

.detail-title {
  margin-bottom: 18px;
}

.review-form {
  margin-top: 22px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 14px;
}

.form-grid :deep(.el-select),
.form-grid :deep(.el-date-editor) {
  width: 100%;
}

.financial-detail,
.goal-panel,
.invoice-detail,
.legacy-learning-panel,
.gate-panel {
  padding: 16px;
  margin-bottom: 20px;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
}

.section-label {
  margin-bottom: 12px;
  font-weight: 600;
}

.muted {
  margin: 0;
  color: var(--el-text-color-secondary);
}

.gate-item {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 8px 0;
  color: #9a5a19;
}

.gate-item span {
  display: grid;
  width: 20px;
  height: 20px;
  color: #fff;
  font-weight: 700;
  background: #d58a37;
  border-radius: 50%;
  place-items: center;
}

.gate-ready {
  margin: 0;
  color: var(--el-color-success);
  font-weight: 600;
}

.audit-summary {
  margin-bottom: 22px;
}

.drawer-actions {
  position: sticky;
  bottom: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
  padding: 14px 0;
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color-lighter);
}

@media (max-width: 720px) {
  .enrollment-admin {
    padding: 12px;
  }

  .page-heading,
  .detail-title {
    align-items: flex-start;
  }

  .link-layout,
  .form-grid {
    grid-template-columns: 1fr;
  }

  .qr-preview {
    width: min(220px, 100%);
    margin: 0 auto;
  }
}
</style>
