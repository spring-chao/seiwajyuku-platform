const app = getApp();

const REQUIRED_FIELDS = [
  ["name", "姓名"],
  ["phone", "手机号"],
  ["birthday", "出生日期"],
  ["referrer", "推荐人"],
  ["company_name", "公司名称"],
  ["company_address", "公司地址"],
  ["position", "职务"],
  ["invoice_type", "发票类型"],
  ["industry_category", "所属行业"],
  ["employee_count", "员工人数"],
  ["company_products", "主要产品或服务"],
  ["annual_sales", "年销售额"]
];

const INDUSTRY_OPTIONS = [
  "制造业",
  "纺织 / 服装",
  "商贸 / 零售",
  "服务业",
  "建筑 / 工程",
  "信息技术 / 软件",
  "餐饮 / 文旅",
  "医疗 / 健康",
  "教育",
  "金融 / 投资",
  "房地产",
  "其他"
];
const GENDER_OPTIONS = ["男", "女"];
const GENDER_VALUES = ["MALE", "FEMALE"];
const POLITICAL_OPTIONS = ["群众", "党员"];
const INVOICE_OPTIONS = ["普票", "专票", "无需开票"];
const INVOICE_VALUES = ["NORMAL", "SPECIAL", "NONE"];
const PROFIT_OPTIONS = ["10%及以上", "0%～10%以下", "亏损"];
const PROFIT_VALUES = ["GE_10_PERCENT", "LT_10_PERCENT", "LOSS"];
const GROWTH_OPTIONS = ["暂不设定", "1.5倍", "2倍", "3倍", "5倍", "自定义"];
const GROWTH_VALUES = ["UNSET", "1.5", "2", "3", "5", "CUSTOM"];
const GOAL_YEAR_OPTIONS = ["1年", "2年", "3年", "5年", "自定义"];
const GOAL_YEAR_VALUES = ["1", "2", "3", "5", "OTHER"];

function decodeScene(value) {
  if (!value) return "";
  try {
    return decodeURIComponent(value);
  } catch (error) {
    return value;
  }
}

function cleanPayload(payload) {
  Object.keys(payload).forEach(field => {
    if (payload[field] === "" || payload[field] === undefined) delete payload[field];
  });
  return payload;
}

Page({
  data: {
    config: app.globalData,
    token: "",
    state: "loading",
    errorMessage: "",
    submitting: false,
    submitted: false,
    formMeta: {
      title: "新学长信息登记",
      subtitle: "欢迎您填写入塾申请资料",
      notice: "提交资料不代表已经正式入塾。工作人员审核资料、确认所属分中心及会费后，才会建立正式学员档案。",
      privacy_notice: "所填资料仅用于入塾审核与后续服务。手机号、税号、银行账号和企业财务资料将按权限使用。"
    },
    purposeItems: [
      {
        title: "建立学员档案与开通账号",
        description: "用于建立您的学员档案，并开通后续学习所需的学员账号。"
      },
      {
        title: "更快协助办理入塾",
        description: "便于工作人员及时联系您，更快完成入塾审核与办理。"
      },
      {
        title: "提供后续学习服务",
        description: "为后续课程、活动及学习服务提供准确的基础信息。"
      }
    ],
    industryOptions: INDUSTRY_OPTIONS,
    politicalOptions: POLITICAL_OPTIONS,
    invoiceOptions: INVOICE_OPTIONS,
    genderOptions: GENDER_OPTIONS,
    profitOptions: PROFIT_OPTIONS,
    growthOptions: GROWTH_OPTIONS,
    goalYearOptions: GOAL_YEAR_OPTIONS,
    form: {
      name: "",
      phone: "",
      birthday: "",
      gender: "",
      political_status: "",
      social_role: "",
      referrer: "",
      email: "",
      company_name: "",
      company_address: "",
      position: "",
      invoice_type: "",
      invoice_title: "",
      invoice_tax_id: "",
      invoice_registered_address: "",
      invoice_phone: "",
      invoice_bank: "",
      invoice_account: "",
      industry_category: "",
      industry_other: "",
      employee_count: "",
      company_products: "",
      annual_sales: "",
      profit_margin: "",
      goal_years: "",
      goal_years_other: "",
      revenue_growth_target: "",
      revenue_growth_other: "",
      profit_growth_target: "",
      profit_growth_other: "",
      notes: ""
    },
    isIndustryOther: false,
    isInvoiceDetailsVisible: false,
    isInvoiceSpecial: false,
    isGoalYearsOther: false,
    isRevenueCustom: false,
    isProfitCustom: false,
    rulesAcknowledged: false,
    privacyConsent: false
  },

  onLoad(options) {
    const token = decodeScene((options && (options.scene || options.token)) || "");
    this.setData({ token });
    if (!token) {
      this.setData({ state: "error", errorMessage: "请使用工作人员提供的有效小程序码进入。" });
      return;
    }
    this.loadForm();
  },

  request(path, options) {
    const baseUrl = (this.data.config.apiBaseUrl || "").replace(/\/$/, "");
    return new Promise((resolve, reject) => {
      wx.request({
        url: `${baseUrl}${path}`,
        timeout: 20000,
        ...options,
        success: response => {
          if (response.statusCode >= 200 && response.statusCode < 300) {
            resolve(response.data);
            return;
          }
          reject(new Error(this.errorFromResponse(response.data)));
        },
        fail: error => reject(new Error(error.errMsg || "网络请求失败"))
      });
    });
  },

  errorFromResponse(data) {
    if (!data) return "服务暂时不可用，请稍后重试。";
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail.message === "string") return data.detail.message;
    return "服务暂时不可用，请稍后重试。";
  },

  async loadForm() {
    this.setData({ state: "loading" });
    try {
      const response = await this.request(`/api/v1/public/enrollment/${encodeURIComponent(this.data.token)}`, { method: "GET" });
      const metadata = response.data || {};
      this.setData({
        state: "ready",
        formMeta: { ...this.data.formMeta, ...metadata },
        industryOptions: metadata.industry_options || INDUSTRY_OPTIONS,
        politicalOptions: metadata.political_status_options || POLITICAL_OPTIONS,
        invoiceOptions: metadata.invoice_types ? metadata.invoice_types.map(item => item.label) : INVOICE_OPTIONS,
        profitOptions: metadata.profit_margin_options ? metadata.profit_margin_options.map(item => item.label) : PROFIT_OPTIONS,
        growthOptions: metadata.growth_target_options ? metadata.growth_target_options.map(item => item.label) : GROWTH_OPTIONS,
        goalYearOptions: metadata.goal_year_options ? metadata.goal_year_options.map(item => item === "OTHER" ? "自定义" : `${item}年`) : GOAL_YEAR_OPTIONS
      });
    } catch (error) {
      this.setData({ state: "error", errorMessage: error.message || "申请入口加载失败，请联系工作人员。" });
    }
  },

  handleInput(event) {
    const field = event.currentTarget.dataset.field;
    this.setData({ [`form.${field}`]: event.detail.value });
  },

  handleBirthdayChange(event) {
    this.setData({ "form.birthday": event.detail.value });
  },

  handleGenderChange(event) {
    this.setData({ "form.gender": GENDER_VALUES[Number(event.detail.value)] || "" });
  },

  handlePoliticalStatusChange(event) {
    const value = this.data.politicalOptions[Number(event.detail.value)] || "";
    this.setData({
      "form.political_status": value,
      "form.social_role": value === "党员" ? this.data.form.social_role : ""
    });
  },

  handleIndustryChange(event) {
    const value = this.data.industryOptions[Number(event.detail.value)] || "";
    this.setData({ "form.industry_category": value, isIndustryOther: value === "其他" });
  },

  handleInvoiceChange(event) {
    const value = INVOICE_VALUES[Number(event.detail.value)] || "";
    this.setData({
      "form.invoice_type": value,
      isInvoiceDetailsVisible: value !== "NONE",
      isInvoiceSpecial: value === "SPECIAL"
    });
  },

  handleProfitChange(event) {
    this.setData({ "form.profit_margin": PROFIT_VALUES[Number(event.detail.value)] || "" });
  },

  handleGoalYearsChange(event) {
    const value = GOAL_YEAR_VALUES[Number(event.detail.value)] || "";
    this.setData({ "form.goal_years": value, isGoalYearsOther: value === "OTHER" });
  },

  handleRevenueGrowthChange(event) {
    const value = GROWTH_VALUES[Number(event.detail.value)] || "";
    this.setData({ "form.revenue_growth_target": value, isRevenueCustom: value === "CUSTOM" });
  },

  handleProfitGrowthChange(event) {
    const value = GROWTH_VALUES[Number(event.detail.value)] || "";
    this.setData({ "form.profit_growth_target": value, isProfitCustom: value === "CUSTOM" });
  },

  toggleRules() {
    this.setData({ rulesAcknowledged: !this.data.rulesAcknowledged });
  },

  togglePrivacy() {
    this.setData({ privacyConsent: !this.data.privacyConsent });
  },

  openPrivacyContract() {
    if (typeof wx.openPrivacyContract === "function") {
      wx.openPrivacyContract({ fail: () => this.showPrivacyFallback() });
      return;
    }
    this.showPrivacyFallback();
  },

  showPrivacyFallback() {
    wx.showModal({
      title: this.data.config.privacyContractTitle,
      content: this.data.formMeta.privacy_notice,
      showCancel: false,
      confirmText: "我已了解"
    });
  },

  validate() {
    const form = this.data.form;
    for (const [field, label] of REQUIRED_FIELDS) {
      if (String(form[field] || "").trim() === "") {
        wx.showToast({ title: `请填写${label}`, icon: "none" });
        return false;
      }
    }
    if (!/^1\d{10}$/.test(String(form.phone).trim())) {
      wx.showToast({ title: "请输入正确的11位手机号", icon: "none" });
      return false;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.birthday)) {
      wx.showToast({ title: "请选择出生日期", icon: "none" });
      return false;
    }
    if (!/^\d+$/.test(String(form.employee_count).trim())) {
      wx.showToast({ title: "员工人数请输入数字", icon: "none" });
      return false;
    }
    if (form.industry_category === "其他" && !form.industry_other.trim()) {
      wx.showToast({ title: "请填写其他行业", icon: "none" });
      return false;
    }
    if (form.invoice_type !== "NONE") {
      if (!form.invoice_title.trim() || !form.invoice_tax_id.trim()) {
        wx.showToast({ title: "请填写发票抬头和税号", icon: "none" });
        return false;
      }
      if (form.invoice_type === "SPECIAL" && ["invoice_registered_address", "invoice_phone", "invoice_bank", "invoice_account"].some(field => !form[field].trim())) {
        wx.showToast({ title: "专票资料请填写完整", icon: "none" });
        return false;
      }
    }
    if (this.data.isGoalYearsOther && !/^\d+$/.test(form.goal_years_other.trim())) {
      wx.showToast({ title: "请输入计划学习年限", icon: "none" });
      return false;
    }
    if ((this.data.isRevenueCustom && !/^\d+(\.\d+)?$/.test(form.revenue_growth_other.trim())) || (this.data.isProfitCustom && !/^\d+(\.\d+)?$/.test(form.profit_growth_other.trim()))) {
      wx.showToast({ title: "自定义目标请输入数字", icon: "none" });
      return false;
    }
    if (!this.data.rulesAcknowledged) {
      wx.showToast({ title: "请先阅读并确认加入守则", icon: "none" });
      return false;
    }
    if (!this.data.privacyConsent) {
      wx.showToast({ title: "请先同意隐私保护指引", icon: "none" });
      return false;
    }
    return true;
  },

  async submit() {
    if (this.data.submitting || !this.validate()) return;
    const form = this.data.form;
    const revenueTarget = this.data.isRevenueCustom ? form.revenue_growth_other.trim() : form.revenue_growth_target;
    const profitTarget = this.data.isProfitCustom ? form.profit_growth_other.trim() : form.profit_growth_target;
    const goalYears = this.data.isGoalYearsOther ? form.goal_years_other.trim() : form.goal_years;
    const payload = cleanPayload({
      name: form.name.trim(),
      phone: form.phone.trim(),
      privacy_consent: true,
      rules_acknowledged: true,
      gender: form.gender,
      birthday: form.birthday,
      political_status: form.political_status.trim(),
      social_role: form.social_role.trim(),
      referrer: form.referrer.trim(),
      email: form.email.trim(),
      company_name: form.company_name.trim(),
      company_address: form.company_address.trim(),
      position: form.position.trim(),
      invoice_type: form.invoice_type,
      invoice_title: form.invoice_title.trim(),
      invoice_tax_id: form.invoice_tax_id.trim(),
      invoice_registered_address: form.invoice_registered_address.trim(),
      invoice_phone: form.invoice_phone.trim(),
      invoice_bank: form.invoice_bank.trim(),
      invoice_account: form.invoice_account.trim(),
      industry_category: form.industry_category,
      industry_other: form.industry_category === "其他" ? form.industry_other.trim() : undefined,
      company_products: form.company_products.trim(),
      employee_count: Number(form.employee_count),
      annual_sales: form.annual_sales.trim(),
      profit_margin: form.profit_margin,
      goal_years: goalYears,
      revenue_growth_target: revenueTarget,
      profit_growth_target: profitTarget,
      notes: form.notes.trim()
    });
    if (payload.invoice_type === "NONE") {
      ["invoice_title", "invoice_tax_id", "invoice_registered_address", "invoice_phone", "invoice_bank", "invoice_account"].forEach(field => delete payload[field]);
    }
    this.setData({ submitting: true });
    wx.showLoading({ title: "正在提交" });
    try {
      await this.request(`/api/v1/public/enrollment/${encodeURIComponent(this.data.token)}`, { method: "POST", data: payload });
      this.setData({ submitted: true });
    } catch (error) {
      wx.showModal({ title: "提交未完成", content: error.message || "请稍后重试，或联系邀请您的学长。", showCancel: false });
    } finally {
      wx.hideLoading();
      this.setData({ submitting: false });
    }
  },

  retry() {
    this.loadForm();
  },

  onShareAppMessage() {
    return { title: "新学长信息登记", path: `/pages/enrollment/index?token=${encodeURIComponent(this.data.token)}` };
  }
});
