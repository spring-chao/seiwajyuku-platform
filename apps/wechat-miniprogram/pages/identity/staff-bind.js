const app = getApp();
const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { identityChangePending } = require("../../utils/identity-session");

Page({
  data: { username: "", password: "", loading: false },

  onShow() { showPrivatePage(this); },
  onHide() { hidePrivatePage(this, { username: "", password: "", loading: false }); },
  onUnload() { this.onHide(); },

  handleInput(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  async bindStaffIdentity() {
    if (this.data.loading || identityChangePending()) return;
    const current = beginPrivateRequest(this, "bind");
    if (!current()) return;
    const username = (this.data.username || "").trim();
    const password = this.data.password || "";
    if (!username || !password) {
      wx.showToast({ title: "请填写后台账号和密码", icon: "none" });
      return;
    }
    this.setData({ loading: true });
    app._identityBindPending = true;
    try {
      const login = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
      if (!current()) return;
      if (!login.code) throw new Error("微信登录凭证获取失败，请重试");
      const response = await request("/api/v1/wechat/staff-bindings/verify", {
        method: "POST", data: { code: login.code, username, password }
      });
      if (!current()) return;
      const data = response.data || {};
      if (!data.access_token) throw new Error("绑定响应无效，请重试");
      current.acceptSession(data.access_token);
      app.setPersonSession(data.access_token);
      this.setData({ password: "" });
      wx.showModal({
        title: "工作人员身份已确认",
        content: "移动端将实时使用你当前的后台账号、在职状态和组织授权。",
        showCancel: false,
        success: () => { if (current()) wx.reLaunch({ url: "/pages/home/index" }); }
      });
    } catch (error) {
      if (current()) wx.showToast({ title: error.message || "暂时无法确认工作人员身份", icon: "none", duration: 2600 });
    } finally {
      app._identityBindPending = false;
      if (current()) this.setData({ loading: false });
    }
  }
});
