const app = getApp();
const { request } = require("../../utils/request");

Page({
  data: {
    environment: app.globalData.environment,
    loading: true,
    portal: null,
    member: null,
    errorMessage: ""
  },

  onShow() {
    this.loadHome();
  },

  async loadHome() {
    this.setData({ loading: true, errorMessage: "" });
    const next = { portal: null, member: null };
    try {
      const portal = await request("/api/v1/public/portal");
      next.portal = portal.data || {};
    } catch (error) {
      next.errorMessage = error.message || "首页暂时无法加载，请稍后重试。";
    }
    if (app.globalData.memberSessionToken) {
      try {
        const me = await request("/api/v1/wechat/me", { auth: true });
        next.member = me.data && me.data.member;
      } catch (error) {
        // A revoked or expired binding is cleared locally.  The user can
        // still enter the public enrollment flow from this page.
        app.clearMemberSession();
      }
    }
    this.setData({ ...next, loading: false });
  },

  openEnrollment() {
    const entry = this.data.portal && this.data.portal.enrollment_entry;
    if (!entry || !entry.handoff_token) {
      wx.showToast({ title: "入塾入口暂未开放", icon: "none" });
      return;
    }
    wx.navigateTo({
      url: `/pages/enrollment/index?token=${encodeURIComponent(entry.handoff_token)}`
    });
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  openStudyMeeting() {
    if (!this.data.member) {
      this.openBinding();
      return;
    }
    wx.navigateTo({ url: "/pages/study-meeting/index" });
  },

  unbind() {
    wx.showModal({
      title: "解除本机绑定？",
      content: "解除后下次登记需要重新输入姓名和手机号。",
      success: async result => {
        if (!result.confirm || !app.globalData.memberSessionToken) return;
        try {
          await request("/api/v1/wechat/member-bindings/revoke", { method: "POST", auth: true });
          app.clearMemberSession();
          this.setData({ member: null });
          wx.showToast({ title: "已解除绑定", icon: "success" });
        } catch (error) {
          wx.showToast({ title: error.message || "解除失败", icon: "none" });
        }
      }
    });
  }
});
