const app = getApp();
const { request } = require("../../utils/request");
const { historyPath, readHistory } = require("../../utils/participation-history");
const session = () => app.globalData.personSessionToken || app.globalData.memberSessionToken || "";

Page({
  data: {
    loading: true,
    member: null,
    currentLearning: [],
    recentLearning: [],
    learningCount: null,
    historyErrorMessage: "",
    currentLearningErrorMessage: "",
    creditSummary: null,
    creditEntries: [],
    creditErrorMessage: "",
    canManageStudyMeeting: false,
    errorMessage: ""
  },

  onShow() {
    this.loadLearning();
  },

  onHide() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.setData({ loading: false, member: null, currentLearning: [], recentLearning: [],
      learningCount: null, historyErrorMessage: "", currentLearningErrorMessage: "",
      creditSummary: null, creditEntries: [], canManageStudyMeeting: false });
  },

  onUnload() { this.onHide(); },

  async loadLearning() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const token = session();
    const current = () => this._loadVersion === version && session() === token;
    this.setData({
      loading: true,
      member: null,
      errorMessage: "",
      currentLearning: [],
      recentLearning: [],
      learningCount: null,
      historyErrorMessage: "",
      currentLearningErrorMessage: "",
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
      this.setData({ member });
      // Personal history and the existing services load independently. A failed
      // service cannot hide a person's participation records or formal credits.
      const [learningResult, historyResult, creditResult, contextResult] = await Promise.all([
        request("/api/v1/wechat/learning-summary", { auth: true })
          .then(value => ({ value })).catch(error => ({ error })),
        request(historyPath({ kind: "learning", pageSize: 5 }), { auth: true })
          .then(value => ({ value: readHistory(value.data) })).catch(error => ({ error })),
        request("/api/v1/wechat/credit-summary", { auth: true })
          .then(value => ({ value })).catch(error => ({ error })),
        // Only the existing volunteer management entry uses this endpoint.
        request("/api/v1/study-meetings/context", { auth: true })
          .then(value => ({ value })).catch(error => ({ error }))
      ]);
      if (!current()) return;
      if ([learningResult, historyResult, creditResult, contextResult]
        .some(result => result.error && result.error.statusCode === 401)) {
        app.clearMemberSession();
        this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
        return;
      }

      if (learningResult.value) {
        const summary = learningResult.value.data || {};
        const currentLearning = (summary.current_learning || []).map((item, index) => ({
          ...item,
          uiKey: `${item.class_name || "class"}-${item.group_name || "group"}-${index}`
        }));
        this.setData({ currentLearning });
      } else this.setData({ currentLearningErrorMessage: "当前学习安排暂时无法加载，请重试。" });

      if (historyResult.value) {
        this.setData({ recentLearning: historyResult.value.records, learningCount: historyResult.value.learningCount });
      } else this.setData({ historyErrorMessage: "学习记录暂时无法加载，请重试。" });

      if (creditResult.value) {
        const summary = creditResult.value.data || {};
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
      } else this.setData({ creditErrorMessage: "正式学分暂时无法加载，请重试。" });

      if (contextResult.value) {
        const assignments = (contextResult.value.data && contextResult.value.data.assignments) || [];
        const canManageStudyMeeting = assignments.some(item => item && item.current_cycle);
        this.setData({ canManageStudyMeeting });
      } else {
        const error = contextResult.error;
        // A normal学员没有学习会登记权限；这不是页面错误。
        if (error.statusCode !== 403 && error.statusCode !== 404) {
          this.setData({ currentLearningErrorMessage: "学习会登记入口暂时无法加载，请重试。" });
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
      if (this._loadVersion === version) {
        if (session() !== token) this.onHide();
        else this.setData({ loading: false });
      }
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

  openLearningHistory() {
    if (!this.data.member) return;
    wx.navigateTo({ url: "/pages/history/index?kind=learning" });
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
