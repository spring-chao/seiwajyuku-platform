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
  ["invoice_info", "开票资料"],
  ["industry", "所属行业"],
  ["employee_count", "员工人数"],
  ["company_products", "主要产品或服务"],
  ["annual_sales", "年销售额"],
  ["books_read", "所读稻盛和夫著作"],
  ["enrollment_reason_philosophy", "认同的哲学理念"],
  ["enrollment_reason_change", "希望改变或努力的方向"],
  ["enrollment_reason_other", "其他入塾原因"]
];

const OPTIONAL_FIELDS = [
  "gender",
  "political_status",
  "email",
  "district",
  "industry_category",
  "profit_margin",
  "learning_years_goal",
  "learning_participation_goal",
  "business_goal",
  "other_goal",
  "notes"
];

function decodeScene(value) {
  if (!value) return "";
  try {
    return decodeURIComponent(value);
  } catch (error) {
    return value;
  }
}

function trimOptional(payload) {
  OPTIONAL_FIELDS.forEach(field => {
    if (payload[field] === "" || payload[field] === undefined) {
      delete payload[field];
    }
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
      notice:
        "提交资料不代表正式入册，工作人员审核、确认相关信息后会与您联系。",
      privacy_notice:
        "您填写的信息仅用于学习服务、身份核验及入册审核。手机号和企业资料将加密保存并按权限使用。"
    },
    genderOptions: ["男", "女", "其他"],
    form: {
      name: "",
      phone: "",
      birthday: "",
      gender: "",
      political_status: "",
      referrer: "",
      email: "",
      company_name: "",
      company_address: "",
      position: "",
      invoice_type: "",
      invoice_info: "",
      industry: "",
      employee_count: "",
      company_products: "",
      annual_sales: "",
      profit_margin: "",
      books_read: "",
      enrollment_reason_philosophy: "",
      enrollment_reason_change: "",
      enrollment_reason_other: "",
      learning_years_goal: "",
      learning_participation_goal: "",
      business_goal: "",
      other_goal: "",
      notes: ""
    },
    privacyConsent: false
  },

  onLoad(options) {
    const token = decodeScene((options && (options.scene || options.token)) || "");
    this.setData({ token });
    if (!token) {
      this.setData({
        state: "error",
        errorMessage: "请使用工作人员提供的有效小程序码进入。"
      });
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
    if (data.detail && typeof data.detail.message === "string") {
      return data.detail.message;
    }
    return "服务暂时不可用，请稍后重试。";
  },

  async loadForm() {
    this.setData({ state: "loading" });
    try {
      const response = await this.request(
        `/api/v1/public/enrollment/${encodeURIComponent(this.data.token)}`,
        { method: "GET" }
      );
      this.setData({
        state: "ready",
        formMeta: { ...this.data.formMeta, ...(response.data || {}) }
      });
    } catch (error) {
      this.setData({
        state: "error",
        errorMessage: error.message || "申请入口加载失败，请联系工作人员。"
      });
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
    const values = ["MALE", "FEMALE", "OTHER"];
    this.setData({ "form.gender": values[Number(event.detail.value)] || "" });
  },

  togglePrivacy() {
    this.setData({ privacyConsent: !this.data.privacyConsent });
  },

  openPrivacyContract() {
    if (typeof wx.openPrivacyContract === "function") {
      wx.openPrivacyContract({
        fail: () => this.showPrivacyFallback()
      });
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
    if (!this.data.privacyConsent) {
      wx.showToast({ title: "请先同意隐私保护指引", icon: "none" });
      return false;
    }
    return true;
  },

  async submit() {
    if (this.data.submitting || !this.validate()) return;
    const payload = trimOptional({
      ...this.data.form,
      name: this.data.form.name.trim(),
      phone: this.data.form.phone.trim(),
      employee_count: Number(this.data.form.employee_count),
      privacy_consent: true
    });
    this.setData({ submitting: true });
    wx.showLoading({ title: "正在提交" });
    try {
      await this.request(
        `/api/v1/public/enrollment/${encodeURIComponent(this.data.token)}`,
        { method: "POST", data: payload }
      );
      this.setData({ submitted: true });
    } catch (error) {
      wx.showModal({
        title: "提交未完成",
        content: error.message || "请稍后重试，或联系邀请您的工作人员。",
        showCancel: false
      });
    } finally {
      wx.hideLoading();
      this.setData({ submitting: false });
    }
  },

  retry() {
    this.loadForm();
  },

  onShareAppMessage() {
    return {
      title: "新学长信息登记",
      path: `/pages/enrollment/index?token=${encodeURIComponent(this.data.token)}`
    };
  }
});
