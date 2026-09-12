const { request } = require("../../utils/request");

const destinations = {
  today_actions: "/pages/operations/today-actions",
  member_search: "/pages/operations/member-search",
  care_records: "/pages/operations/member-search?mode=care",
  renewal_watch: "/pages/operations/renewal-watch"
};

Page({
  data: {
    loading: true,
    entries: [],
    workerName: "",
    summary: { pending_action_count: 0, completed_action_count: 0 },
    greeting: "你好",
    errorMessage: ""
  },
  onShow() { this.load(); },
  greetingForNow() {
    const hour = new Date().getHours();
    if (hour < 12) return "上午好";
    if (hour < 18) return "下午好";
    return "晚上好";
  },
  async load() {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request("/api/v1/wechat/operations/workbench", { auth: true });
      const data = response.data || {};
      this.setData({
        workerName: data.worker_name || "工作人员",
        entries: data.entries || [],
        summary: data.summary || { pending_action_count: 0, completed_action_count: 0 },
        greeting: this.greetingForNow()
      });
    } catch (error) {
      this.setData({ errorMessage: error.message || "当前无法读取工作人员权限" });
    } finally { this.setData({ loading: false }); }
  },
  openEntry(event) {
    const url = destinations[event.currentTarget.dataset.key];
    if (url) wx.navigateTo({ url });
  }
});
