const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
const statuses = [
  { value: "RENEWED", label: "已续费" }, { value: "NOT_RENEWING", label: "暂不续费" },
  { value: "IN_COMMUNICATION", label: "尚未续费，沟通中" }, { value: "DEFERRED", label: "延期关注" }
];
Page({
  data: { loading: true, saving: false, copyGenerating: false, copyError: "", cycleId: null, memberId: null, detail: null, wechatCopy: "", statuses, statusIndex: 0, errorMessage: "" },
  onLoad(options) { this.setData({ cycleId: Number(options.cycle_id), memberId: Number(options.member_id) || null }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { detail: null, wechatCopy: "", errorMessage: "", saving: false, loading: false, copyGenerating: false, copyError: "" }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, detail: null, errorMessage: "" });
    try {
      const response = await request(`/api/v1/wechat/operations/renewals/${this.data.cycleId}`, { auth: true });
      if (current()) this.setData({ detail: response.data, wechatCopy: response.data.wechat_copy || "" });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  editCopy(event) { this.setData({ wechatCopy: event.detail.value }); },
  copyWechat() { if (this.data.wechatCopy) wx.setClipboardData({ data: this.data.wechatCopy }); },
  async regenerateCopy() {
    if (this.data.copyGenerating || this.data.loading) return;
    if (!this.data.cycleId || !this.data.detail) return;
    const current = beginPrivateRequest(this, "copy");
    const previous = this.data.wechatCopy;
    this.setData({ copyGenerating: true, copyError: "" });
    try {
      const path = `/api/v1/wechat/operations/renewals/${this.data.cycleId}/care-draft`;
      const response = await request(path, { method: "POST", auth: true });
      if (!current()) return;
      const result = response.data || {};
      if ((result.generation_status || result.status) !== "GENERATED") throw new Error("AI暂时无法生成，原文已保留，请稍后重试");
      if (this.data.wechatCopy !== previous) throw new Error("生成期间文案已编辑，已保留您的修改，可再次点击重新生成");
      this.setData({ wechatCopy: result.body });
      wx.showToast({ title: "文案已重新生成", icon: "success" });
    } catch (error) { if (current()) this.setData({ copyError: error.message || "生成失败，原文已保留" }); }
    finally { if (current()) this.setData({ copyGenerating: false }); }
  },
  chooseStatus(event) { this.setData({ statusIndex: Number(event.detail.value) }); },
  saveStatus() {
    if (this.data.saving || !this.data.detail || !this.data.detail.can_manage) return;
    const current = beginPrivateRequest(this, "mutation");
    const status = statuses[this.data.statusIndex];
    wx.showModal({ title: "确认续费状态", content: `确认将本周期标记为“${status.label}”？已续费会同步现有学员状态规则。`, success: async result => {
      if (!result.confirm || !current() || this.data.saving) return;
      this.setData({ saving: true });
      try {
        await request(`/api/v1/wechat/operations/renewals/${this.data.cycleId}`, { auth: true, method: "PATCH", data: { status: status.value, expected_status: this.data.detail.cycle.status } });
        if (current()) { wx.showToast({ title: "状态已更新" }); await this.load(); }
      } catch (error) { if (current()) wx.showToast({ title: error.message, icon: "none" }); }
      finally { if (current()) this.setData({ saving: false }); }
    } });
  },
  openMember() { if (this.data.memberId) wx.navigateTo({ url: `/pages/operations/member-detail?member_id=${this.data.memberId}&renewal_cycle_id=${this.data.cycleId}` }); }
});
