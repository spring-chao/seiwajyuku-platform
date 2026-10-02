const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
Page({
  data: { loading: true, records: [], errorMessage: "" },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { records: [], loading: false, errorMessage: "" }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, records: [], errorMessage: "" });
    try {
      const response = await request("/api/v1/wechat/operations/study-meetings", { auth: true });
      if (current()) this.setData({ records: response.data || [] });
    } catch (error) {
      if (current()) this.setData({ errorMessage: error.message || "学习会记录暂时无法加载" });
    } finally { if (current()) this.setData({ loading: false }); }
  }
});
