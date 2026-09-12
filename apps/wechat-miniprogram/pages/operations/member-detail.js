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
    if (this.data.memberId) this.load();
  },
  onHide() { this.clearContact(); },
  onUnload() { this.clearContact(); },
  clearContact() {
    if (this.data.contactPhone) this.setData({ contactPhone: "" });
  },
  async load() {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request(
        `/api/v1/wechat/operations/members/${encodeURIComponent(this.data.memberId)}`,
        { auth: true }
      );
      this.setData({ detail: response.data || null });
    } catch (error) {
      this.setData({ errorMessage: error.message || "学长资料暂时无法加载" });
    } finally {
      this.setData({ loading: false });
    }
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
    wx.navigateTo({ url: `/pages/operations/renewal-watch?member_id=${encodeURIComponent(this.data.memberId)}` });
  },
  revealContact() {
    if (!((this.data.detail || {}).actions || {}).can_reveal_contact) return;
    wx.showModal({
      title: "查看联系方式",
      content: "仅用于本次关爱联系。系统会实时校验权限并记录查看行为。",
      confirmText: "确认查看",
      success: async result => {
        if (!result.confirm) return;
        try {
          const response = await request(
            `/api/v1/wechat/operations/members/${encodeURIComponent(this.data.memberId)}/contact-access`,
            { method: "POST", auth: true, data: { purpose: "移动端关爱联系" } }
          );
          const data = response.data || {};
          this.setData({ contactPhone: data.phone || "" });
        } catch (error) {
          wx.showToast({ title: error.message || "当前无法查看联系方式", icon: "none" });
        }
      }
    });
  },
  callContact() {
    if (this.data.contactPhone) wx.makePhoneCall({ phoneNumber: this.data.contactPhone });
  }
});
