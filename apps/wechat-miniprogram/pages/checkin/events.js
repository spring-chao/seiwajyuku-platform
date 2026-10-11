const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { checkinTarget, checkinPath, displayEvent, platformRescueNotice } = require("../../utils/checkin");

Page({
  data: { events: [], loading: false, errorMessage: "", rescueNotice: "" },
  onShow() { showPrivatePage(this); return this.loadEvents(); },
  onHide() { hidePrivatePage(this, { events: [], loading: false, errorMessage: "", rescueNotice: "" }); },
  onUnload() { this.onHide(); },

  async loadEvents() {
    const current = beginPrivateRequest(this);
    this.setData({ events: [], loading: true, errorMessage: "", rescueNotice: "" });
    try {
      const response = await request("/api/v1/wechat/checkin/open-events");
      if (!current()) return;
      const events = response.data && response.data.events;
      if (!Array.isArray(events)) throw new Error("当前活动暂时无法加载，请重试。");
      const shown = events.filter(event => checkinTarget({ event_id: event.event_id }) &&
        event.lifecycle_status === "CONFIRMED" && ["open", "upcoming"].includes(event.checkin_status)).map(displayEvent);
      this.setData({ events: shown });
      const open = shown.filter(event => event.checkin_status === "open");
      // Only the server's currently open session may be selected automatically.
      // Returning from it keeps this list available instead of navigating in a loop.
      if (open.length === 1 && this._autoOpened !== String(open[0].event_id)) {
        this._autoOpened = String(open[0].event_id);
        wx.navigateTo({ url: checkinPath(checkinTarget({ event_id: open[0].event_id })) });
      }
    } catch (error) {
      if (!current()) return;
      this.setData({ errorMessage: error.message || "当前活动暂时无法加载，请重试。", rescueNotice: platformRescueNotice(error) });
    } finally { if (current()) this.setData({ loading: false }); }
  },
  openEvent(event) {
    const target = checkinTarget({ event_id: event.currentTarget.dataset.eventId });
    const chosen = this.data.events.find(item => String(item.event_id) === String(target && target.event_id));
    if (target && chosen && chosen.checkin_status === "open") wx.navigateTo({ url: checkinPath(target) });
  },
});
