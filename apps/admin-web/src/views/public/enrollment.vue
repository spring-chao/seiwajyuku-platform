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
  gender: "" as "" | "MALE" | "FEMALE" | "OTHER",
  birthday: "",
  district: "",
  company_name: "",
  position: "",
  referrer: "",
  industry_category: "",
  industry: "",
  company_products: "",
  annual_sales: "",
  profit_margin: "",
  notes: "",
  privacy_consent: false
});

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
  privacy_consent: [
    {
      validator: (_rule, value, callback) => {
        if (value === true) callback();
        else callback(new Error("请阅读并同意资料使用说明"));
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
  submitting.value = true;
  errorMessage.value = "";
  const payload: PublicEnrollmentPayload = {
    name: form.name.trim(),
    phone: form.phone.trim(),
    privacy_consent: true,
    gender: form.gender || undefined,
    birthday: optional(form.birthday),
    district: optional(form.district),
    company_name: optional(form.company_name),
    position: optional(form.position),
    referrer: optional(form.referrer),
    industry_category: optional(form.industry_category),
    industry: optional(form.industry),
    company_products: optional(form.company_products),
    annual_sales: optional(form.annual_sales),
    profit_margin: optional(form.profit_margin),
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

          <el-form-item label="姓名" prop="name">
            <el-input
              v-model="form.name"
              maxlength="255"
              autocomplete="name"
              placeholder="请填写真实姓名"
            />
          </el-form-item>

          <el-form-item label="手机号" prop="phone">
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
              <el-radio value="OTHER">其他/不便填写</el-radio>
            </el-radio-group>
          </el-form-item>

          <div class="two-column">
            <el-form-item label="生日">
              <el-date-picker
                v-model="form.birthday"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                :teleported="false"
              />
            </el-form-item>
            <el-form-item label="所在地区">
              <el-input v-model="form.district" placeholder="如：昆山市" />
            </el-form-item>
          </div>

          <div class="section-heading spaced">
            <span>02</span>
            <div>
              <h2>企业与经营信息</h2>
              <p>用于了解经营情况与后续学习服务</p>
            </div>
          </div>

          <el-form-item label="企业名称">
            <el-input
              v-model="form.company_name"
              maxlength="500"
              placeholder="请填写企业全称"
            />
          </el-form-item>
          <el-form-item label="职务">
            <el-input
              v-model="form.position"
              maxlength="255"
              placeholder="如：董事长、总经理"
            />
          </el-form-item>
          <el-form-item label="推荐人">
            <el-input
              v-model="form.referrer"
              maxlength="255"
              placeholder="如有推荐人可填写"
            />
          </el-form-item>

          <div class="two-column">
            <el-form-item label="行业大类">
              <el-input
                v-model="form.industry_category"
                placeholder="如：制造业"
              />
            </el-form-item>
            <el-form-item label="细分行业">
              <el-input v-model="form.industry" placeholder="如：汽车零部件" />
            </el-form-item>
          </div>

          <el-form-item label="主要产品或服务">
            <el-input
              v-model="form.company_products"
              type="textarea"
              :rows="3"
              maxlength="4000"
              show-word-limit
              placeholder="简要介绍主要产品、服务或客户"
            />
          </el-form-item>

          <div class="financial-panel">
            <div class="privacy-badge">企业敏感资料 · 加密保存</div>
            <div class="two-column">
              <el-form-item label="年销售额">
                <el-input
                  v-model="form.annual_sales"
                  maxlength="255"
                  placeholder="可填写金额或销售额区间"
                />
              </el-form-item>
              <el-form-item label="利润率">
                <el-input
                  v-model="form.profit_margin"
                  maxlength="64"
                  placeholder="如：15%"
                />
              </el-form-item>
            </div>
            <p>
              年销售额、利润率仅供获授权人员在入塾审核和后续经营学习服务中使用。
            </p>
          </div>

          <el-form-item label="其他补充">
            <el-input
              v-model="form.notes"
              type="textarea"
              :rows="3"
              maxlength="1000"
              show-word-limit
              placeholder="请勿填写身份证、银行卡等无关敏感信息"
            />
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
