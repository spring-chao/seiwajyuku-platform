const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
Page({
  data: { loading: true, month: "", records: [], errorMessage: "" },
  onLoad() { const now = new Date(); this.setData({ month: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}` }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { records: [], errorMessage: "", loading: false }); },
  onUnload() { this.onHide(); },
  chooseMonth(event) { this.setData({ month: event.detail.value }); this.load(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, records: [], errorMessage: "" });
    try {
      const response = await request(`/api/v1/wechat/operations/study-meeting-records?month=${this.data.month}`, { auth: true });
      if (current()) this.setData({ records: response.data || [] });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  openRecord(event) { wx.navigateTo({ url: `/pages/operations/study-meeting-detail?session_id=${event.currentTarget.dataset.id}` }); }
});
