const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
const filters = [{ value: "SUBMITTED", label: "待审核" }, { value: "APPROVED", label: "已审核" }, { value: "ENROLLED", label: "已入塾" }, { value: "", label: "全部" }];
const labels = { SUBMITTED: "待审核", APPROVED: "审核通过", REJECTED: "已驳回", ENROLLED: "已入塾", CANCELLED: "已取消" };
Page({
  data: { loading: true, records: [], keyword: "", filterIndex: 0, filters, errorMessage: "" },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { records: [], keyword: "", errorMessage: "", loading: false }); },
  onUnload() { this.onHide(); },
  input(event) { this.setData({ keyword: event.detail.value }); },
  chooseFilter(event) { this.setData({ filterIndex: Number(event.detail.value) }); this.load(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, errorMessage: "", records: [] });
    try {
      const filter = filters[this.data.filterIndex].value;
      const response = await request(`/api/v1/wechat/operations/applications?query=${encodeURIComponent(this.data.keyword)}${filter ? "&application_status=" + filter : ""}`, { auth: true });
      if (current()) this.setData({ records: (response.data || []).map(row => ({ ...row, statusLabel: labels[row.application_status] || "待确认" })) });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  open(event) { wx.navigateTo({ url: `/pages/operations/application-detail?application_id=${event.currentTarget.dataset.id}` }); }
});
