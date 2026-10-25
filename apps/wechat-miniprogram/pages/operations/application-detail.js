const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
const { applicationSections, invoke, imageError, drawApplicationImages } = require("../../utils/application-material");
const labels = { SUBMITTED: "待审核", APPROVED: "审核通过", REJECTED: "已驳回", ENROLLED: "已入塾", CANCELLED: "已取消" };
Page({
  data: { sections: [], loading: true, saving: false, exporting: false, canvasHeight: 960, applicationId: null, detail: null, organizations: [], orgIndex: -1, joinDate: "", name: "", note: "", amount: "", statusLabel: "", errorMessage: "" },
  onLoad(options) { this.setData({ applicationId: Number(options.application_id) }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { this.clearImages(); hidePrivatePage(this, { sections: [], detail: null, organizations: [], name: "", note: "", amount: "", errorMessage: "", saving: false, exporting: false, loading: false }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, detail: null, errorMessage: "" });
    try {
      const response = await request(`/api/v1/wechat/operations/applications/${this.data.applicationId}`, { auth: true });
      if (!current()) return;
      const application = response.data.application;
      const organizations = response.data.organizations || [];
      if (current()) this.setData({ sections: applicationSections(application), detail: response.data, organizations, orgIndex: organizations.findIndex(row => row.id === application.org_unit_id), name: application.name, joinDate: application.join_date ? String(application.join_date).slice(0, 10) : "", statusLabel: labels[application.application_status] || "待确认" });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  input(event) { const field = event.currentTarget.dataset.field; if (["name", "note", "amount"].includes(field)) this.setData({ [field]: event.detail.value }); },
  chooseOrg(event) { this.setData({ orgIndex: Number(event.detail.value) }); },
  chooseDate(event) { this.setData({ joinDate: event.detail.value }); },
  async perform(action, data, method = "POST") {
    if (this.data.saving) return;
    const current = beginPrivateRequest(this, "mutation");
    this.setData({ saving: true });
    try {
      await request(`/api/v1/wechat/operations/applications/${this.data.applicationId}/${action}`, { auth: true, method, data: { ...data, expected_updated_at: String(this.data.detail.application.updated_at) } });
      if (current()) { wx.showToast({ title: "处理已保存" }); await this.load(); }
    } catch (error) { if (current()) wx.showModal({ title: "处理未完成", content: error.message, showCancel: false }); }
    finally { if (current()) this.setData({ saving: false }); }
  },
  review(event) {
    const current = beginPrivateRequest(this, "confirm");
    const decision = event.currentTarget.dataset.decision;
    const data = { decision, review_note: this.data.note || null, name: this.data.name, join_date: this.data.joinDate || null };
    if (this.data.orgIndex >= 0) data.org_unit_id = this.data.organizations[this.data.orgIndex].id;
    if (decision === "SAVE") { this.perform("review", data, "PATCH"); return; }
    wx.showModal({ title: "确认审核通过", content: "已核对申请资料、申请加入的塾及管理组织？审核通过后仍需独立确认收款和正式入塾。", success: result => { if (result.confirm && current()) this.perform("review", data, "PATCH"); } });
  },
  confirmPayment() {
    const current = beginPrivateRequest(this, "confirm");
    if (!this.data.amount || !/^\d+(\.\d{1,2})?$/.test(this.data.amount)) { wx.showToast({ title: "请填写实际收款金额", icon: "none" }); return; }
    wx.showModal({ title: "确认已收款", content: `确认已核实收到 ${this.data.amount} 元？`, success: result => { if (result.confirm && current()) this.perform("payment", { payment_status: "PAID", amount: this.data.amount, note: this.data.note || null }); } });
  },
  reject() {
    const current = beginPrivateRequest(this, "confirm");
    if (this.data.note.trim().length < 4) { wx.showToast({ title: "请填写至少4字的驳回原因", icon: "none" }); return; }
    wx.showModal({ title: "确认驳回申请", content: this.data.note, success: result => { if (result.confirm && current()) this.perform("reject", { reason: this.data.note }); } });
  },
  enroll() { const current = beginPrivateRequest(this, "confirm"); wx.showModal({ title: "确认正式入塾", content: "确认将该申请转为正式学员？系统将再次校验审核、收款、组织和重复学员等条件。", success: result => { if (result.confirm && current()) this.perform("enroll", {}); } }); },
  clearImages() {
    for (const filePath of this._imagePaths || []) wx.getFileSystemManager().unlink({ filePath, fail() {} });
    this._imagePaths = [];
    this._imageRun = null;
  },
  async saveImage() {
    if (this.data.exporting || !this.data.detail) return;
    const current = beginPrivateRequest(this, "image");
    const complete = this.data.detail.actions.visit_export;
    const imageRun = {}; this._imageRun = imageRun;
    this.setData({ exporting: true });
    let stage = "authorization", saved = 0, total = 0;
    try {
      let recipient;
      if (complete) {
        const choice = await invoke(wx, "showActionSheet", { itemList: ["保存完整资料：班主任走访面聊", "保存完整资料：董事走访面聊"] });
        if (!current()) return;
        recipient = choice.tapIndex === 0 ? "CLASS_TEACHER" : "DIRECTOR";
      }
      if (wx.requirePrivacyAuthorize) await invoke(wx, "requirePrivacyAuthorize");
      if (!current()) return;
      if (wx.getSetting) {
        const setting = await invoke(wx, "getSetting");
        if (!current()) return;
        if (setting.authSetting && setting.authSetting["scope.writePhotosAlbum"] === false) throw new Error("auth deny");
        if (wx.authorize && (!setting.authSetting || setting.authSetting["scope.writePhotosAlbum"] !== true)) await invoke(wx, "authorize", { scope: "scope.writePhotosAlbum" });
      }
      if (!current()) return;
      stage = "request";
      const response = complete
        ? await request(`/api/v1/wechat/operations/applications/${this.data.applicationId}/visit-image-data`, { auth: true, method: "POST", data: { recipient, confirmed: true } })
        : await request(`/api/v1/wechat/operations/applications/${this.data.applicationId}/image-summary`, { auth: true });
      if (!current()) return;
      const row = complete ? response.data.application : response.data;
      const sections = applicationSections(row, Boolean(complete));
      sections.push({ title: "使用说明", fields: [{ label: "用途", value: complete ? response.data.purpose : "脱敏申请资料，实时状态以系统为准" }, { label: "生成时间", value: complete ? response.data.exported_at : new Date().toLocaleString() }] });
      stage = "draw";
      const files = await drawApplicationImages(wx, this, sections, current);
      total = files.length;
      if (!current()) return;
      stage = "album";
      for (const filePath of files) {
        if (!current()) return;
        await invoke(wx, "saveImageToPhotosAlbum", { filePath });
        saved++;
      }
      if (current()) wx.showToast({ title: `已保存 ${saved} 张申请资料图片`, icon: "none" });
    } catch (error) {
      const raw = String(error && (error.errMsg || error.message) || "");
      if (/cancel/i.test(raw)) return;
      if (current()) {
        const permission = /auth deny|auth denied|authorize|permission|权限/i.test(raw);
        wx.showModal({ title: "图片未全部保存", content: (saved ? `已保存 ${saved}/${total} 张。` : "") + (stage === "request" ? (error.message || "申请资料读取失败，请重试。") : imageError(error, stage)), showCancel: permission, confirmText: permission ? "去设置" : "确定", success: result => { if (permission && result.confirm && current()) wx.openSetting({}); } });
      }
    } finally {
      if (this._imageRun === imageRun) this.clearImages();
      if (current()) this.setData({ exporting: false });
    }
  }
});
