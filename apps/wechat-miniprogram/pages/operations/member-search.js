const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");

Page({
  data: { keyword: "", loading: false, results: [], errorMessage: "", careMode: false },
  onLoad(options) {
    this.setData({ careMode: options.mode === "care" });
  },
  onShow() { showPrivatePage(this); },
  onHide() { hidePrivatePage(this, { loading: false, results: [], keyword: "", errorMessage: "" }); },
  onUnload() { this.onHide(); },
  handleInput(event) { this.setData({ keyword: event.detail.value }); },
  async search() {
    const current = beginPrivateRequest(this);
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
      if (!current()) return;
      this.setData({ results: response.data || [] });
    } catch (error) {
      if (current()) this.setData({ errorMessage: error.message || "查询失败" });
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },
  openMember(event) {
    const memberId = event.currentTarget.dataset.memberId;
    if (memberId) wx.navigateTo({ url: `/pages/operations/member-detail?member_id=${encodeURIComponent(memberId)}` });
  }
});
