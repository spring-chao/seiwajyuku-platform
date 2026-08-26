<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";
import type { FormInstance, FormRules } from "element-plus";
import {
  getPublicEnrollmentForm,
  submitPublicEnrollment,
  type PublicEnrollmentForm,
  type PublicEnrollmentPayload
} from "@/api/enrollment";

const route = useRoute();
const token = computed(() => String(route.params.token || ""));
const formRef = ref<FormInstance>();
const pageState = ref<"loading" | "ready" | "invalid" | "success">("loading");
const submitting = ref(false);
const errorMessage = ref("");
const config = ref<PublicEnrollmentForm>();

const form = reactive({
  name: "",
  phone: "",
  gender: "" as "" | "MALE" | "FEMALE",
  birthday: "",
  political_status: "",
  company_name: "",
  company_tax_id: "",
  company_address: "",
  email: "",
  position: "",
  referrer: "",
  invoice_type: "" as "" | "NORMAL" | "SPECIAL" | "NONE",
  invoice_title: "",
  invoice_tax_id: "",
  invoice_registered_address: "",
  invoice_phone: "",
  invoice_bank: "",
  invoice_account: "",
  industry_category: "",
  industry_other: "",
  company_products: "",
  employee_count: "",
  annual_sales: "",
  profit_margin: "",
  goal_years: "",
  goal_years_other: "",
  revenue_growth_target: "",
  revenue_growth_other: "",
  profit_growth_target: "",
  profit_growth_other: "",
  notes: "",
  rules_acknowledged: false,
  privacy_consent: false
});

const requiredText = (label: string, max: number) => [
  { required: true, message: `请填写${label}`, trigger: "blur" },
  { max, message: `${label}过长`, trigger: "blur" }
];

const rules: FormRules = {
  name: [
    { required: true, message: "请填写姓名", trigger: "blur" },
    { max: 255, message: "姓名过长", trigger: "blur" }
  ],
  phone: [
    { required: true, message: "请填写手机号", trigger: "blur" },
    {
      pattern: /^1\d{10}$/,
      message: "请填写11位中国大陆手机号",
      trigger: "blur"
    }
  ],
  birthday: [{ required: true, message: "请选择出生日期", trigger: "change" }],
  referrer: requiredText("推荐人", 255),
  company_name: requiredText("公司名称", 500),
  company_address: requiredText("公司地址", 1000),
  position: requiredText("职务", 255),
  invoice_type: requiredText("发票类型", 64),
  industry_category: requiredText("所属行业", 255),
  company_products: requiredText("主要产品", 4000),
  employee_count: [
    { required: true, message: "请填写员工人数", trigger: "blur" },
    {
      pattern: /^\d+$/,
      message: "员工人数请输入整数",
      trigger: "blur"
    }
  ],
  annual_sales: requiredText("年销售额", 255),
  email: [
    { type: "email", message: "邮箱格式不正确", trigger: "blur" },
    { max: 255, message: "邮箱过长", trigger: "blur" }
  ],
  privacy_consent: [
    {
      validator: (_rule, value, callback) => {
        if (value === true) callback();
        else callback(new Error("请阅读并同意资料使用说明"));
      },
      trigger: "change"
    }
  ],
  rules_acknowledged: [
    {
      validator: (_rule, value, callback) => {
        if (value === true) callback();
        else callback(new Error("请阅读并确认加入守则与缴费说明"));
      },
      trigger: "change"
    }
  ]
};

function optional(value: string): string | undefined {
  return value.trim() || undefined;
}

async function loadForm() {
  pageState.value = "loading";
  errorMessage.value = "";
  try {
    const response = await getPublicEnrollmentForm(token.value);
    config.value = response.data;
    pageState.value = "ready";
  } catch {
    pageState.value = "invalid";
  }
}

async function submit() {
  if (!formRef.value || !(await formRef.value.validate().catch(() => false))) {
    return;
  }
  if (
    form.invoice_type !== "NONE" &&
    (!form.invoice_title.trim() || !form.invoice_tax_id.trim())
  ) {
    errorMessage.value = "请填写发票抬头和税号。";
    return;
  }
  if (
    form.invoice_type === "SPECIAL" &&
    [
      form.invoice_registered_address,
      form.invoice_phone,
      form.invoice_bank,
      form.invoice_account
    ].some(value => !value.trim())
  ) {
    errorMessage.value = "专票资料请填写完整。";
    return;
  }
  if (form.industry_category === "其他" && !form.industry_other.trim()) {
    errorMessage.value = "请填写其他行业。";
    return;
  }
  if (form.goal_years === "OTHER" && !/^\d+$/.test(form.goal_years_other.trim())) {
    errorMessage.value = "请填写计划学习年限。";
    return;
  }
  if (
    (form.revenue_growth_target === "CUSTOM" &&
      !/^\d+(\.\d+)?$/.test(form.revenue_growth_other.trim())) ||
    (form.profit_growth_target === "CUSTOM" &&
      !/^\d+(\.\d+)?$/.test(form.profit_growth_other.trim()))
  ) {
    errorMessage.value = "自定义目标请输入数字。";
    return;
  }
  submitting.value = true;
  errorMessage.value = "";
  const payload: PublicEnrollmentPayload = {
    name: form.name.trim(),
    phone: form.phone.trim(),
    privacy_consent: true,
    rules_acknowledged: true,
    gender: form.gender || undefined,
    birthday: form.birthday,
    political_status: optional(form.political_status),
    company_name: form.company_name.trim(),
    company_tax_id: optional(form.company_tax_id),
    company_address: form.company_address.trim(),
    email: optional(form.email),
    position: form.position.trim(),
    referrer: form.referrer.trim(),
    invoice_type: form.invoice_type as "NORMAL" | "SPECIAL" | "NONE",
    invoice_title: optional(form.invoice_title),
    invoice_tax_id: optional(form.invoice_tax_id),
    invoice_registered_address: optional(form.invoice_registered_address),
    invoice_phone: optional(form.invoice_phone),
    invoice_bank: optional(form.invoice_bank),
    invoice_account: optional(form.invoice_account),
    industry_category: form.industry_category.trim(),
    industry_other: optional(form.industry_other),
    company_products: form.company_products.trim(),
    employee_count: Number(form.employee_count),
    annual_sales: form.annual_sales.trim(),
    profit_margin: optional(form.profit_margin),
    goal_years: optional(
      form.goal_years === "OTHER" ? form.goal_years_other : form.goal_years
    ),
    revenue_growth_target: optional(
      form.revenue_growth_target === "CUSTOM"
        ? form.revenue_growth_other
        : form.revenue_growth_target
    ),
    profit_growth_target: optional(
      form.profit_growth_target === "CUSTOM"
        ? form.profit_growth_other
        : form.profit_growth_target
    ),
    notes: optional(form.notes)
  };
  try {
    await submitPublicEnrollment(token.value, payload);
    pageState.value = "success";
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error: any) {
    const status = error?.response?.status;
    errorMessage.value =
      status === 429
        ? "提交较频繁，请稍后再试。"
        : "暂时无法提交，请检查网络后重试。";
  } finally {
    submitting.value = false;
  }
}

onMounted(loadForm);
</script>

<template>
  <main class="enrollment-page">
    <section class="hero">
      <div class="brand-mark">和</div>
      <div>
        <p class="eyebrow">盛和塾运营平台</p>
        <h1>{{ config?.title || "新学长入塾申请" }}</h1>
        <p class="hero-subtitle">{{ config?.subtitle || "欢迎您填写入塾申请资料" }}</p>
      </div>
    </section>

    <section v-if="pageState === 'loading'" class="state-card">
      <el-skeleton :rows="6" animated />
    </section>

    <section v-else-if="pageState === 'invalid'" class="state-card centered">
      <div class="state-icon muted">!</div>
      <h2>申请链接已失效</h2>
      <p>请联系工作人员获取最新的入塾申请二维码。</p>
    </section>

    <section v-else-if="pageState === 'success'" class="state-card centered">
      <div class="state-icon success">✓</div>
      <h2>申请已提交</h2>
      <p>感谢填写，工作人员会在审核后与你联系。</p>
      <div class="success-notice">
        提交申请不代表已正式入塾。完成审核、收款确认和分中心归属后，系统才会建立正式学长档案。
      </div>
    </section>

    <template v-else>
      <section class="notice-card">
        <strong>填写前请知悉</strong>
        <p>{{ config?.notice }}</p>
      </section>

      <section class="form-card">
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          size="large"
          @submit.prevent="submit"
        >
          <div class="section-heading">
            <span>01</span>
            <div>
              <h2>基本信息</h2>
              <p>带 * 的项目为必填</p>
            </div>
          </div>

          <el-form-item label="姓名" prop="name" required>
            <el-input
              v-model="form.name"
              maxlength="255"
              autocomplete="name"
              placeholder="请填写真实姓名"
            />
          </el-form-item>

          <el-form-item label="手机号" prop="phone" required>
            <el-input
              v-model="form.phone"
              maxlength="11"
              inputmode="numeric"
              autocomplete="tel"
              placeholder="用于审核联系，将加密保存"
            />
          </el-form-item>

          <el-form-item label="性别">
            <el-radio-group v-model="form.gender">
              <el-radio value="MALE">男</el-radio>
              <el-radio value="FEMALE">女</el-radio>
            </el-radio-group>
          </el-form-item>

          <div class="two-column">
            <el-form-item label="出生日期" prop="birthday" required>
              <el-date-picker
                v-model="form.birthday"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                :teleported="false"
              />
            </el-form-item>
            <el-form-item label="政治面貌">
              <el-input
                v-model="form.political_status"
                maxlength="255"
                placeholder="如：群众、中共党员"
              />
            </el-form-item>
          </div>

          <div class="two-column">
            <el-form-item label="推荐人" prop="referrer" required>
              <el-input
                v-model="form.referrer"
                maxlength="255"
                placeholder="请填写推荐学长姓名"
              />
            </el-form-item>
            <el-form-item label="邮箱">
              <el-input
                v-model="form.email"
                maxlength="255"
                type="email"
                autocomplete="email"
                placeholder="用于接收电子发票（可选）"
              />
            </el-form-item>
          </div>

          <div class="section-heading spaced">
            <span>02</span>
            <div>
              <h2>企业与开票资料</h2>
              <p>请按申请书填写企业基本经营信息</p>
            </div>
          </div>

          <el-form-item label="公司名称" prop="company_name" required>
            <el-input
              v-model="form.company_name"
              maxlength="500"
              placeholder="请填写企业全称"
            />
          </el-form-item>

          <el-form-item label="统一社会信用代码 / 税号">
            <el-input
              v-model="form.company_tax_id"
              maxlength="64"
              placeholder="手工填写，可选"
            />
          </el-form-item>

          <el-form-item label="公司地址" prop="company_address" required>
            <el-input
              v-model="form.company_address"
              maxlength="1000"
              placeholder="请填写公司完整地址"
            />
          </el-form-item>

          <div class="two-column">
            <el-form-item label="职务" prop="position" required>
              <el-input
                v-model="form.position"
                maxlength="255"
                placeholder="如：董事长、总经理"
              />
            </el-form-item>
            <el-form-item label="发票类型" prop="invoice_type" required>
              <el-select
                v-model="form.invoice_type"
                placeholder="请选择发票类型"
                style="width: 100%"
              >
                <el-option
                  v-for="option in config?.invoice_types || []"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </el-form-item>
          </div>

          <template v-if="form.invoice_type !== 'NONE'">
            <div class="two-column">
              <el-form-item label="发票抬头" required>
                <el-input v-model="form.invoice_title" maxlength="500" placeholder="请填写发票抬头" />
              </el-form-item>
              <el-form-item label="发票税号" required>
                <el-input v-model="form.invoice_tax_id" maxlength="64" placeholder="请填写税号" />
              </el-form-item>
            </div>
            <div v-if="form.invoice_type === 'SPECIAL'" class="two-column">
              <el-form-item label="注册地址" required>
                <el-input v-model="form.invoice_registered_address" maxlength="1000" />
              </el-form-item>
              <el-form-item label="注册电话" required>
                <el-input v-model="form.invoice_phone" maxlength="64" />
              </el-form-item>
              <el-form-item label="开户银行" required>
                <el-input v-model="form.invoice_bank" maxlength="255" />
              </el-form-item>
              <el-form-item label="银行账号" required>
                <el-input v-model="form.invoice_account" maxlength="128" />
              </el-form-item>
            </div>
          </template>

          <div class="two-column">
            <el-form-item label="所属行业" prop="industry_category" required>
              <el-select v-model="form.industry_category" placeholder="请选择所属行业" style="width: 100%">
                <el-option
                  v-for="option in config?.industry_options || []"
                  :key="option"
                  :label="option"
                  :value="option"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="员工人数" prop="employee_count" required>
              <el-input
                v-model="form.employee_count"
                maxlength="8"
                inputmode="numeric"
                placeholder="请输入人数"
              />
            </el-form-item>
          </div>

          <el-form-item v-if="form.industry_category === '其他'" label="其他行业" required>
            <el-input v-model="form.industry_other" maxlength="255" placeholder="请填写行业名称" />
          </el-form-item>

          <el-form-item label="主要产品" prop="company_products" required>
            <el-input
              v-model="form.company_products"
              type="textarea"
              :rows="3"
              maxlength="4000"
              show-word-limit
              placeholder="请填写主要产品或服务"
            />
          </el-form-item>

          <div class="financial-panel">
            <div class="privacy-badge">企业敏感资料 · 加密保存</div>
            <el-form-item label="年销售额" prop="annual_sales" required>
              <el-input
                v-model="form.annual_sales"
                maxlength="255"
                placeholder="如：5000万元，或填写销售额区间"
              />
            </el-form-item>
            <el-form-item label="利润率">
              <el-select v-model="form.profit_margin" placeholder="请选择（可选）" style="width: 100%">
                <el-option
                  v-for="option in config?.profit_margin_options || []"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
            </el-form-item>
            <p>年销售额仅供获授权人员在入塾审核和后续经营学习服务中使用。</p>
          </div>

          <div class="section-heading spaced">
            <span>03</span>
            <div>
              <h2>你在盛和塾想获得什么？</h2>
              <p>可按目前计划选择，提交后仍可由工作人员联系确认</p>
            </div>
          </div>

          <div class="two-column">
            <el-form-item label="计划学习年限">
              <el-select v-model="form.goal_years" placeholder="请选择" style="width: 100%">
                <el-option label="1年" value="1" />
                <el-option label="2年" value="2" />
                <el-option label="3年" value="3" />
                <el-option label="5年" value="5" />
                <el-option label="其他" value="OTHER" />
              </el-select>
            </el-form-item>
            <el-form-item label="业绩提升目标">
              <el-select v-model="form.revenue_growth_target" placeholder="请选择" style="width: 100%">
                <el-option label="暂不设定" value="UNSET" />
                <el-option label="1.5倍" value="1.5" />
                <el-option label="2倍" value="2" />
                <el-option label="3倍" value="3" />
                <el-option label="5倍" value="5" />
                <el-option label="自定义" value="CUSTOM" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item v-if="form.goal_years === 'OTHER'" label="自定义学习年限" required>
            <el-input v-model="form.goal_years_other" maxlength="3" inputmode="numeric" placeholder="请输入年数" />
          </el-form-item>
          <el-form-item label="利润提升目标">
            <el-select v-model="form.profit_growth_target" placeholder="请选择" style="width: 100%">
              <el-option label="暂不设定" value="UNSET" />
              <el-option label="1.5倍" value="1.5" />
              <el-option label="2倍" value="2" />
              <el-option label="3倍" value="3" />
              <el-option label="5倍" value="5" />
              <el-option label="自定义" value="CUSTOM" />
            </el-select>
          </el-form-item>

          <el-form-item v-if="form.revenue_growth_target === 'CUSTOM'" label="自定义业绩目标" required>
            <el-input v-model="form.revenue_growth_other" maxlength="8" inputmode="decimal" placeholder="请输入倍数" />
          </el-form-item>
          <el-form-item v-if="form.profit_growth_target === 'CUSTOM'" label="自定义利润目标" required>
            <el-input v-model="form.profit_growth_other" maxlength="8" inputmode="decimal" placeholder="请输入倍数" />
          </el-form-item>

          <el-form-item label="补充说明">
            <el-input v-model="form.notes" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="可选填写" />
          </el-form-item>

          <el-form-item prop="rules_acknowledged" class="consent-item">
            <el-checkbox v-model="form.rules_acknowledged">
              我已阅读并确认加入守则与缴费说明
            </el-checkbox>
            <div class="rules-copy">
              <p>盛和塾是认同稻盛哲学、经营学理念的企业经营者相互学习、相互交流和共同成长的学习平台，致力于让幸福企业遍布苏城。</p>
              <p>每一位准备加入盛和塾苏州分中心的学员，均需认真阅读并遵守以下加入守则。</p>
              <p><strong>（一）加入条件</strong></p>
              <p>1. 依法开展经营的企业经营者，包括企业股东、总经理、合伙人等；</p>
              <p>2. 认同稻盛哲学与经营学，并自愿加入的企业经营者配偶、二代接班人；</p>
              <p>3. 加入后按照学员守则要求，坚持线上读书打卡、分享学习感悟，并积极参加线下学习活动。不得利用盛和塾资源平台发布与稻盛经营学无关的企业咨询、企业培训等商业广告，或从事其他商业推广活动；不得以个人商业推广为目的，在相关群内主动添加非本班陌生学长微信（线下活动正常认识除外）。违反相关约定者，自愿接受退出处理。</p>
              <p><strong>（二）缴费说明</strong></p>
              <p>盛和塾学员费：4800元/人/年。</p>
              <p>汇款账户：无锡稻合企业管理顾问有限公司<br />账号：512914112210201<br />开户行：招商银行苏州新区支行</p>
              <p>转款后请将转款截图提交工作人员。收到会费后，工作人员将开具电子发票，并通过微信或邮箱提供。</p>
              <p><strong>如有疑问请联系</strong><br />盛和塾苏州分中心事务所<br />张玲嫣：199-8486-4833<br />胡延辉：137-7605-2728<br />苏州市高新区竹园路189号2幢102室2楼</p>
            </div>
          </el-form-item>

          <el-form-item prop="privacy_consent" class="consent-item">
            <el-checkbox v-model="form.privacy_consent">
              我已阅读并同意资料使用说明
            </el-checkbox>
            <p>{{ config?.privacy_notice }}</p>
          </el-form-item>

          <el-alert
            v-if="errorMessage"
            :title="errorMessage"
            type="error"
            :closable="false"
            show-icon
          />

          <el-button
            class="submit-button"
            type="primary"
            native-type="submit"
            :loading="submitting"
          >
            提交入塾申请
          </el-button>
        </el-form>
      </section>

      <footer>盛和塾运营平台 · 资料仅用于入塾审核与学长服务</footer>
    </template>
  </main>
</template>

<style scoped lang="scss">
.enrollment-page {
  min-height: 100vh;
  padding: 28px 16px 44px;
  color: #24332d;
  background:
    radial-gradient(circle at 12% 0%, rgb(151 191 169 / 22%), transparent 34%),
    linear-gradient(180deg, #f2f7f3 0%, #f7f5ef 54%, #f3efe5 100%);
}

.hero,
.notice-card,
.form-card,
.state-card,
footer {
  width: min(720px, 100%);
  margin-right: auto;
  margin-left: auto;
}

.hero {
  display: flex;
  gap: 14px;
  align-items: center;
  margin-bottom: 20px;
}

.brand-mark {
  display: grid;
  width: 54px;
  height: 54px;
  color: #fff;
  font-family: serif;
  font-size: 29px;
  font-weight: 700;
  background: #285b45;
  border-radius: 17px 17px 17px 5px;
  box-shadow: 0 10px 24px rgb(40 91 69 / 24%);
  place-items: center;
}

.eyebrow {
  margin: 0 0 3px;
  color: #6f8178;
  font-size: 13px;
  letter-spacing: 0.12em;
}

.hero-subtitle {
  margin: 6px 0 0;
  color: #6f8178;
  font-size: 14px;
}

h1,
h2,
p {
  margin-top: 0;
}

h1 {
  margin-bottom: 0;
  font-size: clamp(25px, 6vw, 34px);
  letter-spacing: 0.04em;
}

.notice-card {
  padding: 18px 20px;
  margin-bottom: 14px;
  color: #574a2c;
  background: #fff8e7;
  border: 1px solid #efdfb8;
  border-radius: 16px;
}

.notice-card p {
  margin: 6px 0 0;
  line-height: 1.65;
}

.form-card,
.state-card {
  padding: clamp(20px, 5vw, 36px);
  background: rgb(255 255 255 / 94%);
  border: 1px solid rgb(43 91 70 / 10%);
  border-radius: 22px;
  box-shadow: 0 18px 50px rgb(51 68 58 / 10%);
}

.section-heading {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 22px;
}

.section-heading.spaced {
  margin-top: 32px;
}

.section-heading > span {
  display: grid;
  width: 38px;
  height: 38px;
  color: #fff;
  font-weight: 700;
  background: #3b765d;
  border-radius: 50%;
  place-items: center;
}

.section-heading h2 {
  margin-bottom: 2px;
  font-size: 19px;
}

.section-heading p {
  margin-bottom: 0;
  color: #87928c;
  font-size: 13px;
}

.two-column {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.financial-panel {
  padding: 16px 16px 2px;
  margin-bottom: 18px;
  background: #f1f7f3;
  border: 1px solid #d7e8dd;
  border-radius: 14px;
}

.financial-panel > p {
  margin-bottom: 14px;
  color: #64776e;
  font-size: 12px;
  line-height: 1.6;
}

.privacy-badge {
  display: inline-flex;
  padding: 4px 9px;
  margin-bottom: 12px;
  color: #286047;
  font-size: 12px;
  font-weight: 600;
  background: #dcece2;
  border-radius: 999px;
}

.consent-item :deep(.el-form-item__content) {
  display: block;
}

.consent-item p {
  margin: 6px 0 0 24px;
  color: #77837d;
  font-size: 12px;
  line-height: 1.6;
}

.rules-copy {
  padding: 12px 14px;
  margin: 10px 0 0 24px;
  color: #6c6254;
  font-size: 12px;
  line-height: 1.7;
  background: #fffaf0;
  border-radius: 10px;
}

.rules-copy p {
  margin: 0 0 8px;
}

.rules-copy p:last-child {
  margin-bottom: 0;
}

.submit-button {
  width: 100%;
  height: 50px;
  margin-top: 18px;
  font-size: 16px;
  font-weight: 600;
  background: #2e6b50;
  border-color: #2e6b50;
  border-radius: 12px;
}

.centered {
  margin-top: 36px;
  text-align: center;
}

.state-icon {
  display: grid;
  width: 66px;
  height: 66px;
  margin: 0 auto 18px;
  color: #fff;
  font-size: 34px;
  font-weight: 700;
  border-radius: 50%;
  place-items: center;
}

.state-icon.success {
  background: #3f815f;
}

.state-icon.muted {
  background: #9b978c;
}

.success-notice {
  padding: 14px;
  margin-top: 20px;
  color: #5c624e;
  line-height: 1.65;
  background: #f7f5ea;
  border-radius: 12px;
}

footer {
  padding-top: 24px;
  color: #8a918b;
  font-size: 12px;
  text-align: center;
}

:deep(.el-input__wrapper),
:deep(.el-textarea__inner),
:deep(.el-date-editor.el-input__wrapper) {
  border-radius: 10px;
}

:deep(.el-date-editor) {
  width: 100%;
}

@media (max-width: 560px) {
  .enrollment-page {
    padding-top: 20px;
  }

  .two-column {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .form-card,
  .state-card {
    border-radius: 18px;
  }
}
</style>
