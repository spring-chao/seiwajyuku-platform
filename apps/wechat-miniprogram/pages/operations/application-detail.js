const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
const labels = { SUBMITTED: "待审核", APPROVED: "审核通过", REJECTED: "已驳回", ENROLLED: "已入塾", CANCELLED: "已取消" };
Page({
  data: { loading: true, saving: false, exporting: false, canvasHeight: 960, applicationId: null, detail: null, organizations: [], orgIndex: -1, joinDate: "", name: "", note: "", amount: "", statusLabel: "", errorMessage: "" },
  onLoad(options) { this.setData({ applicationId: Number(options.application_id) }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { detail: null, organizations: [], name: "", note: "", amount: "", errorMessage: "", saving: false, exporting: false, loading: false }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, detail: null, errorMessage: "" });
    try {
      const response = await request(`/api/v1/wechat/operations/applications/${this.data.applicationId}`, { auth: true });
      if (!current()) return;
      const application = response.data.application;
      const organizations = response.data.organizations || [];
      if (current()) this.setData({ detail: response.data, organizations, orgIndex: organizations.findIndex(row => row.id === application.org_unit_id), name: application.name, joinDate: application.join_date ? String(application.join_date).slice(0, 10) : "", statusLabel: labels[application.application_status] || "待确认" });
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
  async saveImage() {
    if (this.data.exporting) return;
    const current = beginPrivateRequest(this, "image");
    this.setData({ exporting: true });
    try {
      const response = await request(`/api/v1/wechat/operations/applications/${this.data.applicationId}/image-summary`, { auth: true });
      if (!current()) return;
      const row = response.data;
      const lines = ["待入塾申请摘要", `申请编号：${row.application_no}`, `姓名：${row.name}`, `联系方式：${row.phone_masked || '未提供'}`, `企业：${row.company_name || '未提供'}`, `申请塾：${row.target_shuku_name || '待确认'}`, `管理组织：${row.org_unit_name || '待分配'}`, `审核状态：${labels[row.application_status] || '待确认'}`, `收款状态：${row.payment_status === 'PAID' ? '已确认收款' : '待确认'}`, `入塾日期：${row.join_date || '待确认'}`, "生成时间：" + new Date().toLocaleString(), "此图为脱敏摘要，实时状态以系统为准。"];
      const ctx = wx.createCanvasContext("applicationSummary", this);
      ctx.setFontSize(22);
      const wrapped = [];
      for (const line of lines.slice(1)) {
        let part = "";
        for (const char of line) { if (ctx.measureText(part + char).width > 580) { wrapped.push(part); part = ""; } part += char; }
        wrapped.push(part, "");
      }
      const canvasHeight = Math.max(960, 170 + wrapped.length * 32);
      await new Promise(resolve => this.setData({ canvasHeight }, resolve));
      if (!current()) return;
      ctx.setFillStyle("#ffffff"); ctx.fillRect(0, 0, 640, canvasHeight);
      ctx.setFillStyle("#741f2b"); ctx.fillRect(0, 0, 640, 100);
      ctx.setFillStyle("#ffffff"); ctx.setFontSize(30); ctx.fillText(lines[0], 28, 62);
      ctx.setFillStyle("#3d2a2a"); ctx.setFontSize(22);
      let y = 145;
      for (const line of wrapped) { ctx.fillText(line, 28, y); y += 32; }
      await new Promise(resolve => ctx.draw(false, resolve));
      if (!current()) return;
      const file = await new Promise((resolve, reject) => wx.canvasToTempFilePath({ canvasId: "applicationSummary", width: 640, height: canvasHeight, destWidth: 1280, destHeight: canvasHeight * 2, success: resolve, fail: reject }, this));
      if (!current()) return;
      await new Promise((resolve, reject) => wx.saveImageToPhotosAlbum({ filePath: file.tempFilePath, success: resolve, fail: reject }));
      if (current()) wx.showToast({ title: "申请摘要已保存" });
    } catch (error) { if (current()) wx.showModal({ title: "图片未保存", content: error.message || "请在小程序设置中允许保存到相册后重试。", showCancel: false }); }
    finally { if (current()) this.setData({ exporting: false }); }
  }
});
