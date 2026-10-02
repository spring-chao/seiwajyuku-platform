const app = getApp();
const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");

Page({
  data: {
    name: "",
    phone: "",
    loading: false,
    preview: null
  },

  onShow() { showPrivatePage(this); },
  onHide() { hidePrivatePage(this, { name: "", phone: "", preview: null, loading: false }); },
  onUnload() { this.onHide(); },

  handleInput(event) {
    const field = event.currentTarget.dataset.field;
    this.setData({ [field]: event.detail.value });
  },

  async bindIdentity(event = {}) {
    if (this.data.loading) return;
    const current = beginPrivateRequest(this, "bind");
    if (!current()) return;
    const name = (this.data.name || "").trim();
    const phone = (this.data.phone || "").trim();
    if (!name || !/^1\d{10}$/.test(phone)) {
      wx.showToast({ title: "请填写姓名和11位手机号", icon: "none" });
      return;
    }
    this.setData({ loading: true });
    try {
      const login = await new Promise((resolve, reject) => {
        wx.login({
          success: resolve,
          fail: reject
        });
      });
      if (!current()) return;
      if (!login.code) throw new Error("微信登录凭证获取失败，请重试");
      const phoneVerification = event && event.detail && event.detail.code
        ? event.detail.code
        : "";
      const response = await request("/api/v1/wechat/person-bindings/verify", {
        method: "POST",
        data: {
          wx_login_code: login.code,
          name,
          phone,
          phone_verification: phoneVerification
        }
      });
      if (!current()) return;
      const data = response.data || {};
      if (!data.access_token) throw new Error("绑定响应无效，请重试");
      current.acceptSession(data.access_token);
      app.setPersonSession(data.access_token);
      this.setData({ preview: data.member || null });
      const identities = data.identities || {};
      const labels = {
        MEMBER: "学员",
        VOLUNTEER: "志工",
        OPERATIONS_EMPLOYEE: "专职人员"
      };
      const identityText = (identities.identity_kinds || [])
        .map(item => labels[item] || item)
        .join(" · ") || "人员身份";
      wx.showModal({
        title: "身份已绑定",
        content: `${(data.member && data.member.name_masked) || name}：${identityText}。`,
        showCancel: false,
        success: () => { if (current()) wx.navigateBack({ delta: 1 }); }
      });
    } catch (error) {
      if (!current()) return;
      const message = error.message || "暂时无法完成绑定";
      if (error.statusCode === 400) {
        wx.showModal({ title: "无法绑定", content: message, showCancel: false });
      } else {
        wx.showToast({ title: message, icon: "none", duration: 2600 });
      }
    } finally {
      if (current()) this.setData({ loading: false });
    }
  }
});
