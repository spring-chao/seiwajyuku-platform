const { request } = require("../../utils/request");

const destinations = {
  today_actions: "/pages/operations/today-actions",
  member_search: "/pages/operations/member-search",
  followup_records: "/pages/operations/followups",
  study_meetings: "/pages/operations/study-meetings"
};

Page({
  data: { loading: true, entries: [], workerName: "", errorMessage: "" },
  onShow() { this.load(); },
  async load() {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request("/api/v1/wechat/operations/workbench", { auth: true });
      const data = response.data || {};
      this.setData({ workerName: data.worker_name || "工作人员", entries: data.entries || [] });
    } catch (error) {
      this.setData({ errorMessage: error.message || "当前无法读取工作人员权限" });
    } finally { this.setData({ loading: false }); }
  },
  openEntry(event) {
    const url = destinations[event.currentTarget.dataset.key];
    if (url) wx.navigateTo({ url });
  }
});
