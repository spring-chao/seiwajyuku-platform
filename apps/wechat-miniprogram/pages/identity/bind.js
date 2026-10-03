const app = getApp();
const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { identityChangePending, revokeCurrentBinding } = require("../../utils/identity-session");

Page({
  data: {
    name: "",
    phone: "",
    loading: false,
    unbinding: false,
    preview: null
  },

  onShow() { showPrivatePage(this); },
  onHide() { hidePrivatePage(this, { name: "", phone: "", preview: null, loading: false, unbinding: false }); },
  onUnload() { this.onHide(); },

  handleInput(event) {
    const field = event.currentTarget.dataset.field;
    this.setData({ [field]: event.detail.value });
  },

  async bindIdentity(event = {}) {
    if (this.data.loading || this.data.unbinding || identityChangePending()) return;
    const current = beginPrivateRequest(this, "bind");
    if (!current()) return;
    const name = (this.data.name || "").trim();
    const phone = (this.data.phone || "").trim();
    if (!name || !/^1\d{10}$/.test(phone)) {
      wx.showToast({ title: "请填写姓名和11位手机号", icon: "none" });
      return;
    }
    this.setData({ loading: true });
    app._identityBindPending = true;
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
        const conflict = /^当前微信已绑定其他/.test(message);
        wx.showModal({ title: conflict ? "当前微信已有绑定" : "无法绑定", content: message,
          showCancel: conflict, confirmText: conflict ? "解除绑定" : "确定", cancelText: "返回",
          success: result => { if (conflict && result.confirm && current()) this.unbindCurrentIdentity(); }
        });
      } else {
        wx.showToast({ title: message, icon: "none", duration: 2600 });
      }
    } finally {
      app._identityBindPending = false;
      if (current()) this.setData({ loading: false });
    }
  },

  async unbindCurrentIdentity() {
    if (this.data.loading || this.data.unbinding || identityChangePending()) return;
    const current = beginPrivateRequest(this, "unbind");
    if (!current()) return;
    this.setData({ unbinding: true });
    try {
      if (!await revokeCurrentBinding(current)) return;
      current.acceptSession("");
      if (!current()) return;
      this.setData({ preview: null });
      wx.showToast({ title: "已解绑，请重新点击绑定", icon: "none", duration: 2600 });
    } catch (error) {
      if (current()) wx.showModal({ title: "解绑未完成", content: error.message || "请稍后重试", showCancel: false });
    } finally { if (current()) this.setData({ unbinding: false }); }
  }
});
