const app = getApp();
const { request } = require("../../utils/request");

Page({
  data: {
    loading: true,
    member: null,
    canManageStudyMeeting: false,
    serviceAssignments: [],
    errorMessage: ""
  },

  onShow() {
    this.loadServices();
  },

  async loadServices() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const current = () => this._loadVersion === version;
    const token = app.globalData.memberSessionToken;
    this.setData({ loading: true, errorMessage: "", serviceAssignments: [], canManageStudyMeeting: false });
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
      try {
        const context = await request("/api/v1/study-meetings/context", { auth: true });
        if (!current()) return;
        const assignments = (context.data && context.data.assignments) || [];
        this.setData({
          serviceAssignments: assignments.map(item => ({
            key: item.group_org_unit_id || item.class_org_unit_id || item.position_name,
            positionName: item.position_name || "志工",
            scopeName: item.group_name || item.class_name || "服务范围暂未记录"
          })),
          canManageStudyMeeting: assignments.some(item => Boolean(item.group_org_unit_id))
        });
      } catch (error) {
        if (error.statusCode === 401) {
          app.clearMemberSession();
          if (current()) this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
        } else if (error.statusCode !== 403 && error.statusCode !== 404 && current()) {
          this.setData({ errorMessage: "服务列表暂时无法加载，请重试。" });
        }
      }
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
      } else {
        this.setData({ errorMessage: error.message || "服务列表暂时无法加载，请重试。" });
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
