const { request } = require("../../utils/request");

Page({
  data: {
    loading: true,
    context: null,
    errorMessage: "",
    selectedGroupId: ""
  },

  onShow() {
    this.loadContext();
  },

  async loadContext(groupId) {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const suffix = groupId ? `?group_org_unit_id=${encodeURIComponent(groupId)}` : "";
      const response = await request(`/api/v1/study-meetings/context${suffix}`, { auth: true });
      const context = response.data || {};
      const selected = groupId || context.selected_group_org_unit_id || "";
      this.setData({ context, selectedGroupId: selected, loading: false });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "学习会入口暂时不可用" });
    }
  },

  chooseGroup(event) {
    const groupId = event.currentTarget.dataset.groupId;
    this.loadContext(groupId);
  },

  openMembers() {
    if (!this.data.selectedGroupId) {
      wx.showToast({ title: "请选择本次学习会小组", icon: "none" });
      return;
    }
    wx.navigateTo({
      url: `/pages/study-meeting/members?groupId=${encodeURIComponent(this.data.selectedGroupId)}`
    });
  }
});
