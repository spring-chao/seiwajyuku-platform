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
  onShow() { this.load(); },
  showTab(event) {
    const activeTab = event.currentTarget.dataset.tab;
    this.setData({ activeTab, items: activeTab === "completed" ? this.data.completed : this.data.pending });
  },
  async load() {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request("/api/v1/wechat/operations/today-actions", { auth: true });
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
      this.setData({ errorMessage: error.message || "今日行动暂时无法加载" });
    } finally {
      this.setData({ loading: false });
    }
  },
  openMember(event) {
    const item = event.currentTarget.dataset.item;
    if (item && item.member_id) wx.navigateTo({ url: memberUrl(item) });
  }
});
