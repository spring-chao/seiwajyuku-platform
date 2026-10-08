const app = getApp();
const { request } = require("../../utils/request");
const { sessionToken, beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { checkinTarget, checkinPath, contextPath, targetQuery, displayEvent, fallbackUrl, engineConfirmUrl, directConfirm, platformRescueNotice } = require("../../utils/checkin");

function empty() {
  return { event: null, member: null, registration: null, loading: false, confirming: false,
    canCheckin: false, alreadyChecked: false, bindingRequired: false, requiresFallback: false,
    fallbackAvailable: false, historyKind: "activity", notice: "", errorMessage: "", rescueNotice: "", result: null };
}

Page({
  data: empty(),
  onLoad(options = {}) { this._target = checkinTarget(options); },
  onShow() { showPrivatePage(this); return this.loadContext(); },
  onHide() {
    this._checkinTicket = ""; this._engineConfirmUrl = "";
    hidePrivatePage(this, empty());
  },
  onUnload() { this.onHide(); },

  async loadContext() {
    if (!this._target) {
      this.setData({ ...empty(), errorMessage: "这个活动码无效，请重新扫描现场活动码。" });
      return;
    }
    const current = beginPrivateRequest(this, "context");
    this._checkinTicket = ""; this._engineConfirmUrl = "";
    this.setData({ ...empty(), loading: true });
    try {
      const response = await request(contextPath(this._target), { auth: Boolean(sessionToken()) });
      if (!current()) return;
      const data = response.data || {};
      if (!data.event || !data.event.event_id) throw new Error("活动信息暂时不可用，请联系现场工作人员。");
      const ticket = typeof data.checkin_ticket === "string" ? data.checkin_ticket : "";
      if (ticket) {
        const url = engineConfirmUrl(data.engine_confirm_url, app.globalData.apiBaseUrl);
        if (!url || ticket.length > 8192 || !/^[A-Za-z0-9._-]+$/.test(ticket)) throw new Error("现场签到服务地址未确认，请联系工作人员。");
        // Keep the ticket outside render data and persistent application state.
        this._checkinTicket = ticket; this._engineConfirmUrl = url;
      }
      this.setData({ event: displayEvent(data.event), member: data.member || null,
        registration: data.registration || null, canCheckin: Boolean(data.can_checkin && data.member),
        alreadyChecked: Boolean(data.already_checked_in), bindingRequired: !data.member,
        requiresFallback: Boolean(data.requires_fallback), fallbackAvailable: Boolean(fallbackUrl(data.fallback_url)),
        historyKind: data.history_kind === "learning" ? "learning" : "activity",
        notice: data.notice || "" });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        current.acceptSession("");
        app.clearMemberSession();
        this.setData({ ...empty(), bindingRequired: true, notice: "绑定已失效，请重新绑定后继续签到。" });
      } else this.setData({ errorMessage: error.message || "活动信息暂时无法加载，请重试。", rescueNotice: platformRescueNotice(error) });
    } finally { if (current()) this.setData({ loading: false }); }
  },

  async confirmCheckin() {
    if (this.data.confirming || !this.data.canCheckin || this.data.alreadyChecked || !this.data.event) return;
    const current = beginPrivateRequest(this, "confirm");
    const eventId = this.data.event.event_id;
    this.setData({ confirming: true, errorMessage: "" });
    try {
      // The platform resolves the member and registration from the credential.
      // Names, phone numbers, member IDs and registration IDs are never sent.
      const data = { event_id: String(eventId) };
      if (this._target.token) data.token = this._target.token;
      let response;
      if (this._checkinTicket) {
        const engine = await directConfirm(this._engineConfirmUrl, this._checkinTicket);
        response = { data: { status: engine.already === true ? "ALREADY_CHECKED_IN" : "CHECKED_IN",
          checked_at: engine.checked_at || (engine.data && engine.data.checked_at),
          sync_status: engine.sync_status === "SYNCED" ? "SYNCED" : "PENDING",
          message: engine.msg, event: this.data.event, history_kind: this.data.historyKind } };
      } else {
        // Older contexts without a ticket retain the authenticated bridge.
        response = await request("/api/v1/wechat/checkin/confirm", { method: "POST", auth: true, data });
      }
      if (!current()) return;
      const result = response.data || {};
      if (!["CHECKED_IN", "ALREADY_CHECKED_IN"].includes(result.status)) throw new Error("签到结果暂未确认，请重试；重复确认不会重复签到。");
      if (result.event && String(result.event.event_id) !== String(eventId)) throw new Error("返回的场次不一致，请重新确认当前活动。");
      this._checkinTicket = ""; this._engineConfirmUrl = "";
      this.setData({ result, alreadyChecked: true, canCheckin: false,
        historyKind: result.history_kind === "learning" ? "learning" : "activity",
        notice: result.message || (result.status === "ALREADY_CHECKED_IN" ? "本场次已签到" : "签到成功") });
    } catch (error) {
      if (!current()) return;
      if (error.engineTicketError) {
        this._checkinTicket = ""; this._engineConfirmUrl = "";
        this.setData({ canCheckin: false, errorMessage: "现场签到凭证已过期，请重新确认活动后签到。" });
      } else if (error.statusCode === 401) {
        current.acceptSession("");
        app.clearMemberSession();
        this.setData({ ...empty(), bindingRequired: true, notice: "绑定已失效，请重新绑定后继续签到。" });
      } else this.setData({ errorMessage: error.message || "暂未收到签到结果，请重试；重复确认不会重复签到。" });
    } finally { if (current()) this.setData({ confirming: false }); }
  },

  openBinding() {
    if (!this._target) return;
    wx.navigateTo({ url: `/pages/identity/bind?return_checkin=${encodeURIComponent(checkinPath(this._target))}` });
  },
  openFallback() {
    if (!this._target || !this.data.fallbackAvailable) return;
    wx.navigateTo({ url: `/pages/checkin/legacy?${targetQuery(this._target)}` });
  },
  openHistory() {
    const kind = this.data.historyKind;
    // The existing history page reloads onShow; do not invent a local attendance
    // record when the server reports synchronization pending.
    wx.navigateTo({ url: `/pages/history/index?kind=${kind}` });
  },
  openEvents() { wx.navigateTo({ url: "/pages/checkin/events" }); }
});
