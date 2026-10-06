const app = getApp();
const { request } = require("../../utils/request");
const { resolveVolunteerServices } = require("../../utils/volunteer-services");
const { historyPath, readHistory } = require("../../utils/participation-history");
const session = () => app.globalData.personSessionToken || app.globalData.memberSessionToken || "";

function emptyParticipation() {
  return { recentLearning: [], recentActivities: [], learningCount: null, activityCount: null,
    learningErrorMessage: "", activityErrorMessage: "" };
}

function joinDateLabel(value) {
  const match = String(value || "").match(/^(\d{4})(?:-(\d{2}))?/);
  if (!match) return "暂未记录";
  return match[2] ? `${match[1]}年${Number(match[2])}月入塾` : `${match[1]}年入塾`;
}

function monthLabel(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})/);
  if (!match) return "时间待确认";
  return `${match[1]}年${Number(match[2])}月`;
}

function volunteerHistoryItem(item, index) {
  const createdAt = monthLabel(item.created_at);
  const endedAt = item.ended_at
    ? `；结束操作：${monthLabel(item.ended_at)}`
    : "";
  return {
    key: `${item.position_name || "志工"}-${item.scope_name || "服务范围"}-${item.created_at || index}`,
    positionName: item.position_name || "志工",
    scopeName: item.scope_name || "服务范围暂未记录",
    statusName: item.status_name || "状态待确认",
    rangeLabel: `系统确认：${createdAt}${endedAt}`
  };
}

Page({
  data: {
    loading: true,
    member: null,
    identityKinds: [],
    isEmployee: false,
    employeeName: "",
    joinDateLabel: "暂未记录",
    currentVolunteerServices: [],
    volunteerAppointments: [],
    ...emptyParticipation(),
    volunteerErrorMessage: "",
    errorMessage: ""
  },

  onShow() {
    this.loadProfile();
  },

  onHide() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.setData({ loading: false, member: null, identityKinds: [], isEmployee: false,
      employeeName: "", joinDateLabel: "暂未记录", currentVolunteerServices: [], volunteerAppointments: [],
      ...emptyParticipation() });
  },

  onUnload() { this.onHide(); },

  async loadProfile() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const token = session();
    const current = () => this._loadVersion === version && session() === token;
    this.setData({
      loading: true,
      member: null,
      identityKinds: [],
      isEmployee: false,
      employeeName: "",
      joinDateLabel: "暂未记录",
      errorMessage: "",
      volunteerErrorMessage: "",
      currentVolunteerServices: [],
      volunteerAppointments: [],
      ...emptyParticipation()
    });
    if (!token) {
      this.setData({ loading: false, member: null });
      return;
    }
    try {
      const response = await request("/api/v1/wechat/me", { auth: true });
      if (!current()) return;
      const member = response.data && response.data.member;
      const identities = response.data && response.data.identities;
      if (!member || !member.member_id) throw new Error("暂时无法确认学员身份，请重试。");
      this.setData({
        member,
        identityKinds: (identities && identities.identity_kinds) || ["MEMBER"],
        isEmployee: Boolean(identities && identities.operations && identities.operations.is_employee),
        employeeName: (identities && identities.operations && identities.operations.display_name) || "",
        joinDateLabel: joinDateLabel(member.join_date || member.study_start_date)
      });

      const [servicesResult, historyResult, learningResult, activityResult] = await Promise.all([
        request("/api/v1/wechat/volunteer-services", { auth: true })
          .then(value => ({ value }))
          .catch(error => ({ error })),
        request("/api/v1/wechat/volunteer-history", { auth: true })
          .then(value => ({ value }))
          .catch(error => ({ error })),
        request(historyPath({ kind: "learning", pageSize: 3 }), { auth: true })
          .then(value => ({ value: readHistory(value.data) }))
          .catch(error => ({ error })),
        request(historyPath({ kind: "activity", pageSize: 3 }), { auth: true })
          .then(value => ({ value: readHistory(value.data) }))
          .catch(error => ({ error }))
      ]);
      if (!current()) return;
      const sessionError = [servicesResult, historyResult, learningResult, activityResult]
        .some(result => result.error && result.error.statusCode === 401);
      if (sessionError) {
        app.clearMemberSession();
        this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
        return;
      }

      const serviceState = servicesResult.value
        ? resolveVolunteerServices(servicesResult.value.data)
        : { serviceAssignments: [] };
      const history = historyResult.value && historyResult.value.data
        ? historyResult.value.data.appointments || []
        : [];
      const volunteerFailed = Boolean(servicesResult.error || historyResult.error);
      this.setData({
        currentVolunteerServices: serviceState.serviceAssignments,
        volunteerAppointments: history.map(volunteerHistoryItem),
        volunteerErrorMessage: volunteerFailed ? "部分志工服务记录暂时无法加载，请重试。" : "",
        recentLearning: learningResult.value ? learningResult.value.records : [],
        learningCount: learningResult.value ? learningResult.value.learningCount : null,
        learningErrorMessage: learningResult.error ? "学习记录暂时无法加载，请重试。" : "",
        recentActivities: activityResult.value ? activityResult.value.records : [],
        activityCount: activityResult.value ? activityResult.value.activityCount : null,
        activityErrorMessage: activityResult.error ? "活动记录暂时无法加载，请重试。" : ""
      });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ member: null, errorMessage: "绑定已失效，请重新绑定。" });
      } else {
        this.setData({ errorMessage: error.message || "个人资料暂时无法加载，请重试。" });
      }
    } finally {
      if (this._loadVersion === version) {
        if (session() !== token) this.onHide();
        else this.setData({ loading: false });
      }
    }
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  openLearning() {
    wx.navigateTo({ url: "/pages/learning/index" });
  },

  openHistory(event) {
    if (!this.data.member) return;
    const kind = event.currentTarget.dataset.kind === "activity" ? "activity" : "learning";
    wx.navigateTo({ url: `/pages/history/index?kind=${kind}` });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
