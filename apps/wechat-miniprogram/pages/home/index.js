const app = getApp();
const { request } = require("../../utils/request");

Page({
  data: {
    loading: true,
    portal: null,
    member: null,
    displayRole: "",
    displayScope: "",
    errorMessage: ""
  },

  onShow() {
    this.loadHome();
  },

  async loadHome() {
    this.setData({ loading: true, errorMessage: "" });
    const next = { portal: null, member: null, displayRole: "", displayScope: "" };
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
        next.displayScope = `${(next.member && next.member.class_name) || "暂未关联班级"} · ${(next.member && next.member.study_group_name) || "暂未关联小组"}`;
        // Keep the home page human-readable while still resolving the current
        // position from the server's capability/org-scope result.  The
        // technical role keys never reach the UI.
        try {
          const contextResponse = await request("/api/v1/study-meetings/context", { auth: true });
          const assignments = (contextResponse.data && contextResponse.data.assignments) || [];
          const roles = [...new Set(assignments.map(item => item.position_name || (item.role_key === "CLASS_COUNSELOR" ? "班级志工" : "小组志工")).filter(Boolean))];
          next.displayRole = roles.join("、");
          if (assignments.length === 1) {
            const assignment = assignments[0];
            next.displayScope = `${assignment.class_name || ""} · ${assignment.group_name || ""}`.replace(/^ · | · $/g, "");
          } else if (assignments.length > 1) {
            next.displayScope = `可登记 ${assignments.length} 个小组`;
          }
        } catch (error) {
          // The home page remains usable when the study-meeting feature is
          // closed or the member has no current registration appointment.
        }
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
