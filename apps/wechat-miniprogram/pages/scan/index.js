const app = getApp();
const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { classifyScanResult, enrollmentToken } = require("../../utils/scan");

Page({
  data: {
    scanning: false,
    statusType: "",
    statusMessage: "",
    showBindButton: false,
    showScanAgain: false
  },

  onShow() { showPrivatePage(this); },
  onHide() { hidePrivatePage(this, { scanning: false, statusType: "", statusMessage: "", showBindButton: false, showScanAgain: false }); },
  onUnload() { this.onHide(); },

  startScan() {
    if (this.data.scanning) return;
    const current = beginPrivateRequest(this, "scan");
    this.setData({ scanning: true, statusType: "", statusMessage: "", showBindButton: false, showScanAgain: false });
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ["qrCode", "barCode"],
      success: result => { if (current()) this.handleScanResult(result); },
      fail: error => {
        if (!current()) return;
        const message = String(error && error.errMsg || "");
        if (!/cancel/i.test(message)) {
          this.setData({ statusType: "error", statusMessage: "扫码没有完成，请重试。", showScanAgain: true });
        }
      },
      complete: () => { if (current()) this.setData({ scanning: false }); }
    });
  },

  async handleScanResult(result) {
    const current = beginPrivateRequest(this, "resolve");
    if (!current()) return;
    const target = classifyScanResult(result || {});
    if (target === "activity") {
      const { checkinTarget, checkinPath } = require("../../utils/checkin");
      const activity = checkinTarget(result || {});
      if (activity) {
        wx.navigateTo({ url: checkinPath(activity) });
      } else {
        this.setData({ statusType: "error", statusMessage: "活动码缺少有效场次，请扫描现场活动签到码。", showScanAgain: true });
      }
      return;
    }
    if (target === "enrollment") {
      if (app.globalData.memberSessionToken) {
        this.setData({ statusType: "info", statusMessage: "你已绑定正式学员身份，无需重复申请。", showScanAgain: true });
        return;
      }
      const token = enrollmentToken(result || {});
      if (token) {
        wx.navigateTo({ url: `/pages/enrollment/index?token=${encodeURIComponent(token)}` });
        return;
      }
      this.setData({ statusType: "error", statusMessage: "这个入塾申请二维码无效，请联系运营老师。", showScanAgain: true });
      return;
    }

    if (target !== "study-meeting") {
      this.setData({ statusType: "info", statusMessage: "这个二维码暂不支持，请联系运营老师。", showScanAgain: true });
      return;
    }

    if (!app.globalData.memberSessionToken) {
      this.setData({ statusType: "info", statusMessage: "请先绑定正式学员身份，再使用学习服务。", showBindButton: true, showScanAgain: true });
      return;
    }
    try {
      await request("/api/v1/wechat/me", { auth: true });
      if (!current()) return;
      await request("/api/v1/study-meetings/context", { auth: true });
      if (!current()) return;
      wx.navigateTo({ url: "/pages/study-meeting/index" });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ statusType: "info", statusMessage: "绑定已失效，请重新绑定。", showBindButton: true, showScanAgain: true });
      } else if (error.statusCode === 403 || error.statusCode === 404) {
        this.setData({ statusType: "info", statusMessage: "当前没有可登记的学习服务。", showScanAgain: true });
      } else {
        this.setData({ statusType: "error", statusMessage: error.message || "学习服务暂时无法打开，请重试。", showScanAgain: true });
      }
    }
  },

  openBinding() {
    wx.navigateTo({ url: "/pages/identity/bind" });
  },

  clearStatus() {
    this.setData({ statusType: "", statusMessage: "", showBindButton: false, showScanAgain: false });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
