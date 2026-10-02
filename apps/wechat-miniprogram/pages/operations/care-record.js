const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage, sessionToken } = require("../../utils/page-session");

Page({
  data: {
    memberId: null,
    renewalCycleId: null,
    birthdayDueDate: "",
    operationItemId: null,
    channels: [
      { name: "微信", value: "WECHAT" },
      { name: "电话", value: "PHONE" },
      { name: "面谈", value: "MEETING" },
      { name: "其他", value: "OTHER" }
    ],
    channelIndex: 0,
    situation: "",
    nextAction: "",
    nextFollowupAt: "",
    saving: false
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
    showPrivatePage(this);
    if (this._formToken !== undefined && this._formToken !== sessionToken()) {
      this.setData({ situation: "", nextAction: "", nextFollowupAt: "" });
      this._submission = null;
      this._submitted = false;
    }
    this._formToken = sessionToken();
  },
  onHide() { hidePrivatePage(this, { saving: false, situation: "", nextAction: "", nextFollowupAt: "" }); },
  onUnload() { this.onHide(); },
  chooseChannel(event) { this.setData({ channelIndex: Number(event.detail.value) || 0 }); },
  input(event) { this.setData({ [event.currentTarget.dataset.field]: event.detail.value }); },
  chooseNextDate(event) { this.setData({ nextFollowupAt: event.detail.value }); },
  async submit() {
    if (!this.data.memberId || this.data.saving || this._submitted) return;
    const current = beginPrivateRequest(this, "submit");
    if (!current()) return;
    const isBirthday = Boolean(this.data.birthdayDueDate);
    const situation = (this.data.situation || "").trim();
    if (!isBirthday && !situation) {
      wx.showToast({ title: "请填写本次情况", icon: "none" });
      return;
    }
    const channel = this.data.channels[this.data.channelIndex].value;
    let path = `/api/v1/wechat/operations/members/${encodeURIComponent(this.data.memberId)}/care-records`;
    let data = {
      channel,
      situation,
      next_action: (this.data.nextAction || "").trim() || null,
      next_followup_at: this.data.nextFollowupAt || null
    };
    if (isBirthday) {
      path = `/api/v1/wechat/operations/members/${encodeURIComponent(this.data.memberId)}/birthday-care`;
      data = { due_date: this.data.birthdayDueDate, channel, operation_item_id: this.data.operationItemId || null };
    } else if (this.data.renewalCycleId) {
      path = `/api/v1/wechat/operations/members/${encodeURIComponent(this.data.memberId)}/renewal-care-records`;
      data.renewal_cycle_id = this.data.renewalCycleId;
    }
    // Retain the same key after an uncertain network response. An edited form
    // is a new action; the payload fingerprint stays in page memory only.
    const fingerprint = JSON.stringify({ path, data });
    if (!this._submission || this._submission.fingerprint !== fingerprint) {
      this._submission = {
        fingerprint,
        key: `mobile-${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
      };
    }
    if (!isBirthday) data.idempotency_key = this._submission.key;
    this.setData({ saving: true });
    try {
      await request(path, { method: "POST", auth: true, data });
      if (!current()) return;
      this._submitted = true;
      wx.showToast({ title: "关爱已记录", icon: "success" });
      wx.navigateBack();
    } catch (error) {
      if (current()) wx.showToast({ title: error.message || "记录失败", icon: "none" });
    } finally {
      if (current()) this.setData({ saving: false });
    }
  }
});
