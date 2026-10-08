const { request } = require("../../utils/request");
const { sessionToken, beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { checkinTarget, contextPath, displayEvent, fallbackUrl } = require("../../utils/checkin");

Page({
  data: { event: null, url: "", loading: false, opening: false, errorMessage: "" },
  onLoad(options = {}) { this._target = checkinTarget(options); },
  onShow() { showPrivatePage(this); if (!this.data.opening) return this.loadFallback(); },
  onHide() { hidePrivatePage(this, { event: null, url: "", loading: false, opening: false, errorMessage: "" }); },
  onUnload() { this.onHide(); },
  async loadFallback() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, errorMessage: "", url: "", event: null });
    try {
      if (!this._target) throw new Error("活动码无效，请重新扫描。");
      const response = await request(contextPath(this._target), { auth: Boolean(sessionToken()) });
      if (!current()) return;
      const data = response.data || {};
      const url = fallbackUrl(data.fallback_url);
      if (!url) throw new Error("备用签到入口暂未配置，请联系现场工作人员。");
      this.setData({ url, event: displayEvent(data.event) });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message || "备用入口暂时无法打开，请联系工作人员。" }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  openWebview() { if (this.data.url) this.setData({ opening: true, errorMessage: "" }); },
  webviewFailed() {
    this.setData({ opening: false, errorMessage: "小程序内暂时无法打开此入口。请复制链接在微信或浏览器打开，或请现场工作人员协助签到。" });
  },
  copyLink() {
    if (!this.data.url) return;
    wx.setClipboardData({ data: this.data.url, success: () => wx.showToast({ title: "签到链接已复制", icon: "none" }) });
  }
});
