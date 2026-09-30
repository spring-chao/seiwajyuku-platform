const app = getApp();
const { request } = require("../../utils/request");

Page({
  data: {
    loading: true,
    member: null,
    currentLearning: [],
    recentLearning: [],
    creditSummary: null,
    creditEntries: [],
    creditErrorMessage: "",
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
    this.setData({
      loading: true,
      errorMessage: "",
      currentLearning: [],
      recentLearning: [],
      creditSummary: null,
      creditEntries: [],
      creditErrorMessage: "",
      canManageStudyMeeting: false
    });
    if (!token) {
      this.setData({ loading: false, member: null });
      return;
    }
    try {
      const me = await request("/api/v1/wechat/me", { auth: true });
      if (!current()) return;
      const member = me.data && me.data.member;
      if (!member || !member.member_id) throw new Error("暂时无法确认学员身份，请重试。");
      const summaryResponse = await request("/api/v1/wechat/learning-summary", { auth: true });
      if (!current()) return;
      const summary = summaryResponse.data || {};
      const currentLearning = (summary.current_learning || []).map((item, index) => ({
        ...item,
        uiKey: `${item.class_name || "class"}-${item.group_name || "group"}-${index}`
      }));
      const recentLearning = (summary.recent_learning || []).map((item, index) => ({
        ...item,
        uiKey: `${item.occurred_at || "date"}-${item.title || "learning"}-${index}`,
        occurredAtLabel: String(item.occurred_at || "").slice(0, 10).replace(/-/g, "/")
      }));
      this.setData({ member, currentLearning, recentLearning });

      try {
        const creditResponse = await request("/api/v1/wechat/credit-summary", { auth: true });
        if (!current()) return;
        const summary = creditResponse.data || {};
        const creditEntries = (summary.recent_entries || []).map((item, index) => ({
          ...item,
          uiKey: `${item.period_display || "period"}-${item.credit_type_label || "credit"}-${index}`,
          pointsLabel: `${Number(item.points) > 0 ? "+" : ""}${String(item.points || "0.00")}分`
        }));
        this.setData({
          creditSummary: {
            ...summary,
            totalPointsLabel: `${summary.total_points || "0.00"}分`,
            currentYearPointsLabel: `${summary.current_year_points || "0.00"}分`,
            standardPointsLabel: `${summary.standard_learning_points || "0.00"}分`,
            extensionPointsLabel: `${summary.extension_activity_points || "0.00"}分`
          },
          creditEntries
        });
      } catch (error) {
        if (!current()) return;
        if (error.statusCode === 401) {
          app.clearMemberSession();
          this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
          return;
        }
        this.setData({ creditErrorMessage: "正式学分暂时无法加载，请重试。" });
      }

      // This endpoint is only for the existing volunteer management entry;
      // it must never be used as an ordinary member's learning history.
      try {
        const response = await request("/api/v1/study-meetings/context", { auth: true });
        if (!current()) return;
        const assignments = (response.data && response.data.assignments) || [];
        const canManageStudyMeeting = assignments.some(item => item && item.current_cycle);
        this.setData({ canManageStudyMeeting });
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

  openCreditHistory() {
    if (!this.data.member) return;
    wx.navigateTo({ url: "/pages/credits/index" });
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
