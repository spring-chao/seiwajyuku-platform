const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
Page({
  data: { loading: true, month: "", records: [], organizations: [], canCreate: false, page: 1, hasMore: false, errorMessage: "" },
  onLoad() { const now = new Date(); this.setData({ month: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}` }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { records: [], organizations: [], canCreate: false, page: 1, hasMore: false, errorMessage: "", loading: false }); },
  onUnload() { this.onHide(); },
  chooseMonth(event) { this.setData({ month: event.detail.value }); this.load(); },
  loadMore() { this.load(true); },
  async load(append = false) {
    append = append === true;
    const nextPage = append ? this.data.page + 1 : 1;
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, records: append ? this.data.records : [], errorMessage: "", canCreate: false });
    try {
      const response = await request(`/api/v1/wechat/operations/activities?month=${this.data.month}&page=${nextPage}`, { auth: true });
      if (!current()) return;
      this.setData({ records: append ? this.data.records.concat(response.data.records) : response.data.records, page: nextPage, hasMore: response.data.has_more });
      const context = await request("/api/v1/wechat/operations/activity-context", { auth: true });
      if (current()) this.setData({ canCreate: !!context.data.organizations.length });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  create() { wx.navigateTo({ url: "/pages/operations/activity-detail" }); },
  open(event) { wx.navigateTo({ url: `/pages/operations/activity-detail?event_id=${encodeURIComponent(event.currentTarget.dataset.id)}` }); }
});
