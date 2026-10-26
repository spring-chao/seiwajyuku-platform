const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
Page({
  data: { loading: true, month: "", records: [], organizations: [], canCreate: false, needsAuthorization: false, permissionMessage: "", page: 1, hasMore: false, errorMessage: "" },
  onLoad() { const now = new Date(); this.setData({ month: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}` }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { records: [], organizations: [], canCreate: false, needsAuthorization: false, permissionMessage: "", page: 1, hasMore: false, errorMessage: "", loading: false }); },
  onUnload() { this.onHide(); },
  chooseMonth(event) { this.setData({ month: event.detail.value }); this.load(); },
  loadMore() { this.load(true); },
  async load(append = false) {
    append = append === true;
    const nextPage = append ? this.data.page + 1 : 1;
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, records: append ? this.data.records : [], errorMessage: "", canCreate: false });
    try {
      const context = await request("/api/v1/wechat/operations/activity-context", { auth: true });
      if (!current()) return;
      const canView = context.data.can_view !== false;
      this.setData({ canCreate: !!context.data.organizations.length, needsAuthorization: !canView, permissionMessage: context.data.permission_message || "请管理员在员工管理中按已有服务范围授权活动签到运营能力" });
      if (!canView) { this.setData({records: [],hasMore:false}); return; }
      const response = await request(`/api/v1/wechat/operations/activities?month=${this.data.month}&page=${nextPage}`, { auth: true });
      if (!current()) return;
      this.setData({ records: append ? this.data.records.concat(response.data.records) : response.data.records, page: nextPage, hasMore: response.data.has_more });

    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  create() { wx.navigateTo({ url: "/pages/operations/activity-detail" }); },
  open(event) { wx.navigateTo({ url: `/pages/operations/activity-detail?event_id=${encodeURIComponent(event.currentTarget.dataset.id)}` }); }
});
