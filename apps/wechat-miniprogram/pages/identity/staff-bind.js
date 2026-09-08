const app = getApp();
const { request } = require("../../utils/request");

Page({
  data: { username: "", password: "", loading: false },

  handleInput(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  async bindStaffIdentity() {
    const username = (this.data.username || "").trim();
    const password = this.data.password || "";
    if (!username || !password) {
      wx.showToast({ title: "请填写后台账号和密码", icon: "none" });
      return;
    }
    this.setData({ loading: true });
    try {
      const login = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
      if (!login.code) throw new Error("微信登录凭证获取失败，请重试");
      const response = await request("/api/v1/wechat/staff-bindings/verify", {
        method: "POST", data: { code: login.code, username, password }
      });
      const data = response.data || {};
      app.setPersonSession(data.access_token);
      this.setData({ password: "" });
      wx.showModal({
        title: "工作人员身份已确认",
        content: "移动端将实时使用你当前的后台账号、在职状态和组织授权。",
        showCancel: false,
        success: () => wx.reLaunch({ url: "/pages/home/index" })
      });
    } catch (error) {
      wx.showToast({ title: error.message || "暂时无法确认工作人员身份", icon: "none", duration: 2600 });
    } finally {
      this.setData({ loading: false });
    }
  }
});
