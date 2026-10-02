const app = getApp();
const { request } = require("../../utils/request");
const { resolveVolunteerServices } = require("../../utils/volunteer-services");

Page({
  data: {
    loading: true,
    member: null,
    isVolunteer: false,
    volunteerRoles: [],
    canManageStudyMeeting: false,
    serviceAssignments: [],
    errorMessage: ""
  },

  onShow() {
    this.loadServices();
  },

  onHide() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.setData({ loading: false, member: null, isVolunteer: false, volunteerRoles: [],
      serviceAssignments: [], canManageStudyMeeting: false });
  },

  onUnload() { this.onHide(); },

  async loadServices() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const token = app.globalData.memberSessionToken;
    const current = () => this._loadVersion === version && app.globalData.memberSessionToken === token;
    this.setData({ loading: true, member: null, errorMessage: "", isVolunteer: false, volunteerRoles: [], serviceAssignments: [], canManageStudyMeeting: false });
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
        const services = await request("/api/v1/wechat/volunteer-services", { auth: true });
        if (!current()) return;
        const serviceState = resolveVolunteerServices(services.data);
        this.setData({
          isVolunteer: serviceState.isVolunteer,
          volunteerRoles: serviceState.roles,
          serviceAssignments: serviceState.serviceAssignments,
          canManageStudyMeeting: serviceState.canManageStudyMeeting
        });
      } catch (error) {
        if (!current()) return;
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
      if (this._loadVersion === version) {
        if (app.globalData.memberSessionToken !== token) this.onHide();
        else this.setData({ loading: false });
      }
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
