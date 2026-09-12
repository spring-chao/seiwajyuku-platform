const { request } = require("../../utils/request");

Page({
  data: { keyword: "", loading: false, results: [], errorMessage: "", careMode: false },
  onLoad(options) {
    this.setData({ careMode: options.mode === "care" });
  },
  handleInput(event) { this.setData({ keyword: event.detail.value }); },
  async search() {
    const keyword = (this.data.keyword || "").trim();
    if (!keyword) {
      wx.showToast({ title: "请输入姓名或手机号后4位", icon: "none" });
      return;
    }
    this.setData({ loading: true, errorMessage: "", results: [] });
    try {
      const response = await request(
        `/api/v1/wechat/operations/member-search?keyword=${encodeURIComponent(keyword)}`,
        { auth: true }
      );
      this.setData({ results: response.data || [] });
    } catch (error) {
      this.setData({ errorMessage: error.message || "查询失败" });
    } finally {
      this.setData({ loading: false });
    }
  },
  openMember(event) {
    const memberId = event.currentTarget.dataset.memberId;
    if (memberId) wx.navigateTo({ url: `/pages/operations/member-detail?member_id=${encodeURIComponent(memberId)}` });
  }
});
