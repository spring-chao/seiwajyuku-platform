const app = getApp();
const { request } = require("../../utils/request");
const { resolveVolunteerServices } = require("../../utils/volunteer-services");

function clearLocalSession() {
  if (typeof app.clearPersonSession === "function") {
    app.clearPersonSession();
    return;
  }
  // Keep the shipped member-only pages and their lightweight test harnesses
  // compatible while the app-level credential becomes person-scoped.
  if (typeof app.clearMemberSession === "function") {
    app.clearMemberSession();
    return;
  }
  if (app.globalData) {
    app.globalData.personSessionToken = "";
    app.globalData.memberSessionToken = "";
  }
}

Page({
  data: {
    loading: true,
    portal: null,
    member: null,
    identities: null,
    isEmployee: false,
    operationEntries: [],
    identityState: "checking",
    canManageStudyMeeting: false,
    isVolunteer: false,
    volunteerRoles: [],
    serviceMessage: "",
    displayRole: "",
    displayScope: "",
    errorMessage: "",
    bindingActionLabel: "绑定我的身份"
  },

  onShow() {
    this.loadHome();
  },

  async loadHome() {
    const version = this._homeLoadVersion = (this._homeLoadVersion || 0) + 1;
    const token = app.globalData.personSessionToken || app.globalData.memberSessionToken;
    const current = () => this._homeLoadVersion === version;
    this.setData({ loading: true, errorMessage: "", canManageStudyMeeting: false });
    const next = {
      portal: null, member: null, identities: null, isEmployee: false, operationEntries: [], identityState: token ? "unknown" : "unbound",
      canManageStudyMeeting: false, isVolunteer: false, volunteerRoles: [],
      serviceMessage: "", displayRole: "", displayScope: "",
      bindingActionLabel: this.data.bindingActionLabel || "绑定我的身份"
    };
    if (token) {
      try {
        const me = await request("/api/v1/wechat/me", { auth: true });
        if (!current()) return;
        next.member = me.data && me.data.member;
        next.identities = me.data && me.data.identities;
        next.isEmployee = Boolean(next.identities && next.identities.operations && next.identities.operations.is_employee);
        if (!next.member && !next.isEmployee) throw new Error("当前没有可用的学长或工作人员身份，请联系工作人员。");
        next.identityState = "bound";
        this.setData({ member: next.member, identities: next.identities, identityState: "bound", portal: null });
        next.displayScope = next.member
          ? `${next.member.class_name || "暂未关联班级"} · ${next.member.study_group_name || "暂未关联小组"}`
          : "工作人员移动运营身份已确认";
        // Volunteer identity is resolved independently from study-meeting
        // context. The backend returns capabilities; technical role keys never
        // reach the UI and study-meeting pages still enforce their own checks.
        if (next.member) try {
          const servicesResponse = await request("/api/v1/wechat/volunteer-services", { auth: true });
          const serviceState = resolveVolunteerServices(servicesResponse.data);
          next.volunteerRoles = serviceState.roles;
          next.isVolunteer = serviceState.isVolunteer;
          next.canManageStudyMeeting = serviceState.canManageStudyMeeting;
          next.displayRole = [...new Set(serviceState.roles.map(item => item.position_name || "志工").filter(Boolean))].join("、");
          if (next.isVolunteer && !next.canManageStudyMeeting) {
            next.serviceMessage = "当前暂无需要操作的线上服务。";
          }
        } catch (error) {
          if (error.statusCode === 401) {
            clearLocalSession();
            next.identityState = "unbound";
            next.member = null;
          } else if (error.statusCode !== 403 && error.statusCode !== 404) {
            next.errorMessage = "志工服务暂时无法加载，请重试。";
          }
        }
        if (next.isEmployee) {
          try {
            const workbench = await request("/api/v1/wechat/operations/workbench", { auth: true });
            next.operationEntries = (workbench.data && workbench.data.entries) || [];
          } catch (error) {
            if (error.statusCode === 401) {
              clearLocalSession();
              next.identityState = "unbound";
              next.member = null;
              next.isEmployee = false;
            } else if (error.statusCode !== 403 && error.statusCode !== 404) {
              next.errorMessage = "工作人员入口暂时无法加载，请重试。";
            }
          }
        }
      } catch (error) {
        if (!current()) return;
        if (error.statusCode === 401) {
          clearLocalSession();
          next.identityState = "unbound";
          next.member = null;
        } else {
          // A network/server error is NOT evidence of being an applicant.
          next.errorMessage = "暂时无法确认学员身份，请重试。";
        }
      }
    }
    if (!current()) return;
    if (next.identityState === "unbound") {
      try {
        const portal = await request("/api/v1/public/portal");
        next.portal = portal.data || {};
      } catch (error) {
        next.errorMessage = error.message || "入塾入口暂时无法加载，请重试。";
      }
    }
    if (!current()) return;
    this.setData({ ...next, loading: false });
    // A staff-only person should land directly on the operational workbench.
    // A member/volunteer/staff composite keeps the combined entry page so the
    // person session can expose each identity without merging permissions.
    const isStaffOnly = next.isEmployee && !next.member && !next.isVolunteer && next.operationEntries.length;
    if (isStaffOnly && !this._staffOnlyRedirected) {
      this._staffOnlyRedirected = true;
      wx.redirectTo({ url: "/pages/operations/index" });
    } else if (!isStaffOnly) {
      this._staffOnlyRedirected = false;
    }
  },

  openEnrollment() {
    if (this.data.loading || this.data.identityState !== "unbound") return;
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

  openLearning() {
    if (this.data.identityState !== "bound") return;
    wx.navigateTo({ url: "/pages/learning/index" });
  },

  openProfile() {
    if (this.data.identityState !== "bound") return;
    wx.navigateTo({ url: "/pages/profile/index" });
  },

  openServices() {
    if (this.data.identityState !== "bound" || !this.data.isVolunteer) return;
    wx.navigateTo({ url: "/pages/services/index" });
  },

  openStudyMeeting() {
    if (this.data.loading || !this.data.member || !this.data.canManageStudyMeeting) return;
    wx.navigateTo({ url: "/pages/study-meeting/index" });
  },

  openOperations() {
    if (!this.data.isEmployee) return;
    wx.navigateTo({ url: "/pages/operations/index" });
  },

  unbind() {
    wx.showModal({
      title: "解除本机绑定？",
      content: "解除后下次登记需要重新输入姓名和手机号。",
      success: async result => {
        if (!result.confirm || !(app.globalData.personSessionToken || app.globalData.memberSessionToken)) return;
        try {
          await request("/api/v1/wechat/member-bindings/revoke", { method: "POST", auth: true });
          this._homeLoadVersion = (this._homeLoadVersion || 0) + 1;
          clearLocalSession();
          this.setData({ member: null, identityState: "unbound", canManageStudyMeeting: false,
            isVolunteer: false, volunteerRoles: [], displayRole: "", displayScope: "", serviceMessage: "",
            bindingActionLabel: "重新绑定我的身份", isEmployee: false, operationEntries: [], identities: null });
          wx.showToast({ title: "已解除绑定", icon: "success" });
          await this.loadHome();
        } catch (error) {
          wx.showToast({ title: error.message || "解除失败", icon: "none" });
        }
      }
    });
  }
});
