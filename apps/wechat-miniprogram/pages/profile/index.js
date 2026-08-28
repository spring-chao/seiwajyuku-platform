const app = getApp();
const { request } = require("../../utils/request");

function joinDateLabel(value) {
  const match = String(value || "").match(/^(\d{4})(?:-(\d{2}))?/);
  if (!match) return "暂未记录";
  return match[2] ? `${match[1]}年${Number(match[2])}月入塾` : `${match[1]}年入塾`;
}

Page({
  data: {
    loading: true,
    member: null,
    joinDateLabel: "暂未记录",
    volunteerAppointments: [],
    errorMessage: ""
  },

  onShow() {
    this.loadProfile();
  },

  async loadProfile() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const current = () => this._loadVersion === version;
    const token = app.globalData.memberSessionToken;
    this.setData({ loading: true, errorMessage: "", volunteerAppointments: [] });
    if (!token) {
      this.setData({ loading: false, member: null });
      return;
    }
    try {
      const response = await request("/api/v1/wechat/me", { auth: true });
      if (!current()) return;
      const member = response.data && response.data.member;
      if (!member || !member.member_id) throw new Error("暂时无法确认学员身份，请重试。");
      this.setData({ member, joinDateLabel: joinDateLabel(member.join_date || member.study_start_date) });

      // Volunteer appointments are intentionally read from the existing
      // capability-aware context. Ordinary learners simply see no service
      // history here; no new profile API is invented for this shell page.
      try {
        const context = await request("/api/v1/study-meetings/context", { auth: true });
        if (!current()) return;
        const assignments = (context.data && context.data.assignments) || [];
        this.setData({
          volunteerAppointments: assignments.map(item => ({
            key: `${item.group_org_unit_id || item.class_org_unit_id || item.position_name}`,
            positionName: item.position_name || "志工",
            scopeName: item.group_name || item.class_name || "服务范围暂未记录"
          }))
        });
      } catch (error) {
        if (error.statusCode === 401) {
          app.clearMemberSession();
          if (current()) this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
        }
      }
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
      } else {
        this.setData({ errorMessage: error.message || "个人资料暂时无法加载，请重试。" });
      }
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
