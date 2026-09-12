// Developer Tools (envVersion=develop) use the isolated test API from
// config.dev.js; trial/release builds keep the production gateway in config.js.
const appConfig = require("./config.runtime");

App({
  globalData: {
    ...appConfig,
    // A WeChat session now represents a natural person. The older member key
    // remains as a compatibility alias for already shipped member pages.
    personSessionToken: "",
    memberSessionToken: "",
    studyMeetingDraft: null
  },

  onLaunch() {
    // The session is revocable and short-lived.  It is the only mini-program
    // credential cached locally; enrollment handoff tokens are navigation-only
    // and are never stored here.
    try {
      const token = wx.getStorageSync(appConfig.sessionStorageKey) || "";
      this.globalData.personSessionToken = token;
      this.globalData.memberSessionToken = token;
    } catch (error) {
      this.globalData.personSessionToken = "";
      this.globalData.memberSessionToken = "";
    }
  },

  setPersonSession(token) {
    this.globalData.personSessionToken = token || "";
    this.globalData.memberSessionToken = token || "";
    try {
      if (token) wx.setStorageSync(appConfig.sessionStorageKey, token);
      else wx.removeStorageSync(appConfig.sessionStorageKey);
    } catch (error) {
      // A session still works in memory when local storage is unavailable.
    }
  },

  setMemberSession(token) {
    this.setPersonSession(token);
  },

  clearPersonSession() {
    this.setPersonSession("");
    this.globalData.studyMeetingDraft = null;
    this.globalData.studyMeetingResult = null;
  },

  clearMemberSession() {
    this.clearPersonSession();
  }
});
