const app = getApp();
const { request } = require("../../utils/request");
const { classifyScanResult, enrollmentToken } = require("../../utils/scan");

Page({
  data: {
    scanning: false,
    statusType: "",
    statusMessage: "",
    showBindButton: false,
    showScanAgain: false
  },

  startScan() {
    if (this.data.scanning) return;
    this.setData({ scanning: true, statusType: "", statusMessage: "", showBindButton: false, showScanAgain: false });
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ["qrCode", "barCode"],
      success: result => this.handleScanResult(result),
      fail: error => {
        const message = String(error && error.errMsg || "");
        if (!/cancel/i.test(message)) {
          this.setData({ statusType: "error", statusMessage: "扫码没有完成，请重试。", showScanAgain: true });
        }
      },
      complete: () => this.setData({ scanning: false })
    });
  },

  async handleScanResult(result) {
    const target = classifyScanResult(result || {});
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
      await request("/api/v1/study-meetings/context", { auth: true });
      wx.navigateTo({ url: "/pages/study-meeting/index" });
    } catch (error) {
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
