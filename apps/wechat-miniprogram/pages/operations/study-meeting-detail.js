const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request, downloadProtected } = require("../../utils/request");
Page({
  data: { loading: true, saving: false, sessionId: null, detail: null, errorMessage: "", resultMessage: "" },
  onLoad(options) { this.setData({ sessionId: Number(options.session_id) }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { this.clearPhoto(); hidePrivatePage(this, { detail: null, errorMessage: "", resultMessage: "", saving: false, loading: false }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, detail: null, errorMessage: "" });
    try {
      const response = await request(`/api/v1/wechat/operations/study-meeting-records/${this.data.sessionId}`, { auth: true });
      if (current()) this.setData({ detail: response.data });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  async viewPhoto() {
    const current = beginPrivateRequest(this, "photo");
    try {
      this.clearPhoto();
      const path = await downloadProtected(`/api/v1/wechat/operations/study-meeting-records/${this.data.sessionId}/evidence-preview`);
      if (current()) { this._photoPath = path; wx.previewImage({ urls: [path] }); }
      else wx.getFileSystemManager().unlink({ filePath: path, fail() {} });
    }
    catch (error) { if (current()) wx.showToast({ title: error.message, icon: "none" }); }
  },
  clearPhoto() { if (this._photoPath) { wx.getFileSystemManager().unlink({ filePath: this._photoPath, fail() {} }); this._photoPath = ""; } },
  verifySettle() {
    if (this.data.saving || !this.data.detail || !this.data.detail.can_settle) return;
    const current = beginPrivateRequest(this, "mutation");
    wx.showModal({ title: "核实并结算", content: "请核对合影、参加人员及课程完成明细。确认后按预览中的可结算项目自动入账，受阻项目保留待处理，重复操作不会重复入账。", confirmText: "核实并结算", success: async result => {
      if (!result.confirm || !current() || this.data.saving) return;
      this.setData({ saving: true, resultMessage: "" });
      try {
        const response = await request(`/api/v1/wechat/operations/study-meeting-records/${this.data.sessionId}/verify-settle`, { auth: true, method: "POST", data: { expected_revision: this.data.detail.revision } });
        if (!current()) return;
        const settlement = response.data.credit_settlement || {};
        this.setData({ resultMessage: settlement.message || (settlement.status === "POSTED" ? `已核实，累计入账 ${settlement.posted_points} 学分；重复核实不会重复入账。` : "已记录核实，学分结算尚未全部完成，请查看结算状态后重试。") });
        await this.load();
      } catch (error) { if (current()) wx.showModal({ title: "未完成结算", content: error.message, showCancel: false }); }
      finally { if (current()) this.setData({ saving: false }); }
    } });
  }
});
