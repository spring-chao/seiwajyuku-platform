const app = getApp();
const { request } = require("../../utils/request");

Page({
  data: {
    loading: true,
    member: null,
    currentLearning: [],
    canManageStudyMeeting: false,
    errorMessage: ""
  },

  onShow() {
    this.loadLearning();
  },

  async loadLearning() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const current = () => this._loadVersion === version;
    const token = app.globalData.memberSessionToken;
    this.setData({ loading: true, errorMessage: "", currentLearning: [], canManageStudyMeeting: false });
    if (!token) {
      this.setData({ loading: false, member: null });
      return;
    }
    try {
      const me = await request("/api/v1/wechat/me", { auth: true });
      if (!current()) return;
      const member = me.data && me.data.member;
      if (!member || !member.member_id) throw new Error("暂时无法确认学员身份，请重试。");
      this.setData({ member });

      // The current learning service is already exposed by the study-meeting
      // context API. History and credit settlement remain separate services;
      // do not manufacture values on the mini-program when those APIs are not
      // available to a learner yet.
      try {
        const response = await request("/api/v1/study-meetings/context", { auth: true });
        if (!current()) return;
        const assignments = (response.data && response.data.assignments) || [];
        const currentLearning = assignments
          .filter(item => item && item.current_cycle)
          .map(item => ({
            groupId: item.group_org_unit_id,
            className: item.class_name || "",
            groupName: item.group_name || "",
            positionName: item.position_name || "志工",
            cycleIndex: item.current_cycle.learning_cycle_index,
            memberCount: item.member_count || 0
          }));
        this.setData({
          currentLearning,
          canManageStudyMeeting: currentLearning.length > 0
        });
      } catch (error) {
        if (error.statusCode === 401) {
          app.clearMemberSession();
          if (current()) this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
          return;
        }
        // A normal学员没有学习会登记权限；这不是页面错误。
        if (error.statusCode !== 403 && error.statusCode !== 404 && current()) {
          this.setData({ errorMessage: "当前学习安排暂时无法加载，请重试。" });
        }
      }
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
      } else {
        this.setData({ errorMessage: error.message || "学习服务暂时无法加载，请重试。" });
      }
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },

  openStudyMeeting() {
    if (!this.data.canManageStudyMeeting) return;
    wx.navigateTo({ url: "/pages/study-meeting/index" });
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
