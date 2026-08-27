const appConfig = require("./config");

App({
  globalData: {
    ...appConfig,
    memberSessionToken: "",
    studyMeetingDraft: null
  },

  onLaunch() {
    // The session is revocable and short-lived.  It is the only mini-program
    // credential cached locally; enrollment handoff tokens are navigation-only
    // and are never stored here.
    try {
      this.globalData.memberSessionToken = wx.getStorageSync(appConfig.sessionStorageKey) || "";
    } catch (error) {
      this.globalData.memberSessionToken = "";
    }
  },

  setMemberSession(token) {
    this.globalData.memberSessionToken = token || "";
    try {
      if (token) wx.setStorageSync(appConfig.sessionStorageKey, token);
      else wx.removeStorageSync(appConfig.sessionStorageKey);
    } catch (error) {
      // A session still works in memory when local storage is unavailable.
    }
  },

  clearMemberSession() {
    this.setMemberSession("");
    this.globalData.studyMeetingDraft = null;
    this.globalData.studyMeetingResult = null;
  }
});
