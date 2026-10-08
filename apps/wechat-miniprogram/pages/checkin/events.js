const app = getApp();
const { request } = require("../../utils/request");
const { sessionToken, beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { checkinTarget, checkinPath, displayEvent } = require("../../utils/checkin");

Page({
  data: { events: [], loading: false, bindingRequired: false, errorMessage: "" },
  onShow() { showPrivatePage(this); return this.loadEvents(); },
  onHide() { hidePrivatePage(this, { events: [], loading: false, bindingRequired: false, errorMessage: "" }); },
  onUnload() { this.onHide(); },

  async loadEvents() {
    const current = beginPrivateRequest(this);
    this.setData({ events: [], loading: false, bindingRequired: !sessionToken(), errorMessage: "" });
    if (!sessionToken()) return;
    this.setData({ loading: true });
    try {
      const response = await request("/api/v1/wechat/checkin/events", { auth: true });
      if (!current()) return;
      const events = response.data && response.data.events;
      if (!Array.isArray(events)) throw new Error("当前活动暂时无法加载，请重试。");
      this.setData({ events: events.filter(event => checkinTarget({ event_id: event.event_id })).map(displayEvent) });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        current.acceptSession("");
        app.clearMemberSession();
        this.setData({ events: [], bindingRequired: true, errorMessage: "绑定已失效，请重新绑定。" });
      } else this.setData({ errorMessage: error.message || "当前活动暂时无法加载，请重试。" });
    } finally { if (current()) this.setData({ loading: false }); }
  },
  openEvent(event) {
    const target = checkinTarget({ event_id: event.currentTarget.dataset.eventId });
    if (target) wx.navigateTo({ url: checkinPath(target) });
  },
  openBinding() { wx.navigateTo({ url: `/pages/identity/bind?return_checkin=${encodeURIComponent("/pages/checkin/events")}` }); },
  openScan() { wx.navigateTo({ url: "/pages/scan/index" }); }
});
