const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");

function careUrl({ memberId, renewalCycleId, birthdayDueDate, operationItemId }) {
  const params = [`member_id=${encodeURIComponent(memberId)}`];
  if (renewalCycleId) params.push(`renewal_cycle_id=${encodeURIComponent(renewalCycleId)}`);
  if (birthdayDueDate) params.push(`birthday_due_date=${encodeURIComponent(birthdayDueDate)}`);
  if (operationItemId) params.push(`operation_item_id=${encodeURIComponent(operationItemId)}`);
  return `/pages/operations/care-record?${params.join("&")}`;
}

Page({
  data: {
    loading: true,
    memberId: null,
    detail: null,
    contactPhone: "",
    renewalCycleId: null,
    birthdayDueDate: "",
    operationItemId: null,
    wechatCopy: "",
    copyError: "",
    copyGenerating: false,
    copyTitle: "",
    errorMessage: ""
  },
  onLoad(options) {
    this.setData({
      memberId: Number(options.member_id) || null,
      renewalCycleId: Number(options.renewal_cycle_id) || null,
      birthdayDueDate: options.birthday_due_date || "",
      operationItemId: Number(options.operation_item_id) || null
    });
  },
  onShow() {
    this._visible = true;
    showPrivatePage(this);
    if (this.data.memberId) this.load();
  },
  onHide() { this.invalidatePage(); },
  onUnload() { this.invalidatePage(); },
  invalidatePage() {
    this._visible = false;
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.clearContact();
    hidePrivatePage(this, { detail: null, contactPhone: "", wechatCopy: "", copyTitle: "", copyError: "", copyGenerating: false, loading: false, errorMessage: "" });
  },
  clearContact() {
    this._contactVersion = (this._contactVersion || 0) + 1;
    if (this.data.contactPhone) this.setData({ contactPhone: "" });
  },
  async load() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const validSession = beginPrivateRequest(this);
    const current = () => validSession() && this._visible !== false && version === this._loadVersion;
    this.clearContact();
    this.setData({ loading: true, errorMessage: "", detail: null, wechatCopy: "", copyError: "" });
    try {
      const response = await request(
        `/api/v1/wechat/operations/members/${encodeURIComponent(this.data.memberId)}`,
        { auth: true }
      );
      if (current()) {
        this.setData({ detail: response.data || null });
        await this.loadCopy(version);
      }
    } catch (error) {
      if (current()) this.setData({ errorMessage: error.message || "学长资料暂时无法加载" });
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },
  async loadCopy(version = this._loadVersion) {
    const validSession = beginPrivateRequest(this, "copy");
    const birthday = this.data.birthdayDueDate;
    const cycleId = this.data.renewalCycleId;
    if (!birthday && !cycleId) return;
    const current = () => validSession() && this._visible !== false && version === this._loadVersion;
    if (current()) this.setData({ copyError: "", copyTitle: birthday ? "生日微信文案" : "续费微信文案" });
    try {
      const path = birthday
        ? `/api/v1/wechat/operations/members/${this.data.memberId}/birthday-copy`
        : `/api/v1/wechat/operations/renewals/${cycleId}`;
      const response = await request(path, { auth: true });
      if (current()) this.setData({ wechatCopy: birthday ? response.data.draft : response.data.wechat_copy || "该周期已结束，可在查看续费中处理状态。" });
    } catch (error) {
      if (current()) this.setData({ copyError: error.message || "文案暂时无法加载" });
    }
  },
  editCopy(event) { this.setData({ wechatCopy: event.detail.value }); },
  copyWechat() {
    if (this.data.wechatCopy) wx.setClipboardData({ data: this.data.wechatCopy });
  },
  retryCopy() { this.loadCopy(); },
  async regenerateCopy() {
    if (this.data.copyGenerating || this.data.loading) return;
    const birthday = this.data.birthdayDueDate;
    if (!birthday && !this.data.renewalCycleId) return;
    const current = beginPrivateRequest(this, "copy");
    const previous = this.data.wechatCopy;
    this.setData({ copyGenerating: true, copyError: "" });
    try {
      const path = birthday
        ? `/api/v1/wechat/operations/members/${this.data.memberId}/birthday-copy`
        : `/api/v1/wechat/operations/renewals/${this.data.renewalCycleId}/care-draft`;
      const response = await request(path, { method: "POST", auth: true });
      if (!current()) return;
      const result = response.data || {};
      if ((result.generation_status || result.status) !== "GENERATED") throw new Error("AI暂时无法生成，原文已保留，请稍后重试");
      if (this.data.wechatCopy !== previous) throw new Error("生成期间文案已编辑，已保留您的修改，可再次点击重新生成");
      this.setData({ wechatCopy: birthday ? result.draft : result.body });
      wx.showToast({ title: "文案已重新生成", icon: "success" });
    } catch (error) { if (current()) this.setData({ copyError: error.message || "生成失败，原文已保留" }); }
    finally { if (current()) this.setData({ copyGenerating: false }); }
  },
  recordCare() {
    const detail = this.data.detail || {};
    const actions = detail.actions || {};
    if (!actions.can_record_care && !actions.can_record_renewal) {
      wx.showToast({ title: "当前无权记录关爱", icon: "none" });
      return;
    }
    wx.navigateTo({
      url: careUrl({
        memberId: this.data.memberId,
        renewalCycleId: this.data.renewalCycleId && actions.can_record_renewal ? this.data.renewalCycleId : null,
        birthdayDueDate: this.data.birthdayDueDate,
        operationItemId: this.data.operationItemId
      })
    });
  },
  viewRenewal() {
    const detail = this.data.detail || {};
    if (!(detail.actions || {}).can_view_renewal) {
      wx.showToast({ title: "当前无权查看续费", icon: "none" });
      return;
    }
    const cycleId = this.data.renewalCycleId || (detail.renewal || {}).cycle_id;
    wx.navigateTo({ url: cycleId
      ? `/pages/operations/renewal-detail?cycle_id=${encodeURIComponent(cycleId)}&member_id=${encodeURIComponent(this.data.memberId)}`
      : `/pages/operations/renewal-watch?member_id=${encodeURIComponent(this.data.memberId)}` });
  },
  revealContact() {
    if (!((this.data.detail || {}).actions || {}).can_reveal_contact) return;
    const memberId = this.data.memberId;
    const version = this._contactVersion = (this._contactVersion || 0) + 1;
    const validSession = beginPrivateRequest(this, "contact");
    const current = () => validSession() && this._visible !== false && version === this._contactVersion && memberId === this.data.memberId;
    wx.showModal({
      title: "查看联系方式",
      content: "仅用于本次关爱联系。系统会实时校验权限并记录查看行为。",
      confirmText: "确认查看",
      success: async result => {
        if (!result.confirm || !current()) return;
        try {
          const response = await request(
            `/api/v1/wechat/operations/members/${encodeURIComponent(memberId)}/contact-access`,
            { method: "POST", auth: true, data: { purpose: "移动端关爱联系" } }
          );
          const data = response.data || {};
          if (current()) this.setData({ contactPhone: data.phone || "" });
        } catch (error) {
          if (current()) wx.showToast({ title: error.message || "当前无法查看联系方式", icon: "none" });
        }
      }
    });
  },
  callContact() {
    if (this.data.contactPhone) wx.makePhoneCall({ phoneNumber: this.data.contactPhone });
  }
});
