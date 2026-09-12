const { request } = require("../../utils/request");

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
  chooseChannel(event) { this.setData({ channelIndex: Number(event.detail.value) || 0 }); },
  input(event) { this.setData({ [event.currentTarget.dataset.field]: event.detail.value }); },
  chooseNextDate(event) { this.setData({ nextFollowupAt: event.detail.value }); },
  async submit() {
    if (!this.data.memberId) return;
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
    this.setData({ saving: true });
    try {
      await request(path, { method: "POST", auth: true, data });
      wx.showToast({ title: "关爱已记录", icon: "success" });
      wx.navigateBack();
    } catch (error) {
      wx.showToast({ title: error.message || "记录失败", icon: "none" });
    } finally {
      this.setData({ saving: false });
    }
  }
});
