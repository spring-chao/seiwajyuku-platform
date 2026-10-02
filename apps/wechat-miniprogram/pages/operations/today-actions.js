const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");

function memberUrl(item) {
  const params = [`member_id=${encodeURIComponent(item.member_id)}`];
  if (item.renewal_cycle_id) params.push(`renewal_cycle_id=${encodeURIComponent(item.renewal_cycle_id)}`);
  if (item.birthday_due_date) params.push(`birthday_due_date=${encodeURIComponent(item.birthday_due_date)}`);
  if (item.operation_item_id) params.push(`operation_item_id=${encodeURIComponent(item.operation_item_id)}`);
  return `/pages/operations/member-detail?${params.join("&")}`;
}

Page({
  data: {
    loading: true,
    pending: [],
    completed: [],
    items: [],
    activeTab: "pending",
    summary: { pending_action_count: 0, completed_action_count: 0 },
    errorMessage: ""
  },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { loading: false, pending: [], completed: [], items: [], summary: {}, errorMessage: "" }); },
  onUnload() { this.onHide(); },

  showTab(event) {
    const activeTab = event.currentTarget.dataset.tab;
    this.setData({ activeTab, items: activeTab === "completed" ? this.data.completed : this.data.pending });
  },
  async load() {
    this.onHide();
    showPrivatePage(this);
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request("/api/v1/wechat/operations/today-actions", { auth: true });
      if (!current()) return;
      const data = response.data || {};
      const pending = data.pending || [];
      const completed = data.completed || [];
      this.setData({
        pending,
        completed,
        items: this.data.activeTab === "completed" ? completed : pending,
        summary: data.summary || { pending_action_count: 0, completed_action_count: 0 }
      });
    } catch (error) {
      if (current()) this.setData({ errorMessage: error.message || "今日行动暂时无法加载" });
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },
  openMember(event) {
    const item = event.currentTarget.dataset.item;
    if (item && item.member_id) wx.navigateTo({ url: memberUrl(item) });
  }
});
