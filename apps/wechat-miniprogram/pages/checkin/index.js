const app = getApp();
const { request } = require("../../utils/request");
const { sessionToken, beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { checkinTarget, checkinPath, contextPath, targetQuery, displayEvent, fallbackUrl, engineConfirmUrl, directConfirm, platformRescueNotice } = require("../../utils/checkin");
const { successCard } = require("../../utils/checkin-success");

function empty() {
  return { event: null, member: null, registration: null, loading: false, confirming: false,
    konpaChoice: null, konpaRequired: false, canCheckin: false, alreadyChecked: false, bindingRequired: false, requiresFallback: false,
    fallbackAvailable: false, guestName: "", guestAllowed: false, nameCandidates: [], selectedCandidateIndexes: [], guestResults: [], historyKind: "activity", notice: "", errorMessage: "", rescueNotice: "", result: null, successCard: null };
}

Page({
  data: empty(),
  onLoad(options = {}) { this._target = checkinTarget(options); },
  onShow() { showPrivatePage(this); return this.loadContext(); },
  onHide() {
    this._checkinTicket = ""; this._engineConfirmUrl = ""; this._nameSelections = [];
    hidePrivatePage(this, empty());
  },
  onUnload() { this.onHide(); },

  async loadContext() {
    if (!this._target) {
      this.setData({ ...empty(), errorMessage: "这个活动码无效，请重新扫描现场活动码。" });
      return;
    }
    const current = beginPrivateRequest(this, "context");
    const hadSession = Boolean(sessionToken());
    this._checkinTicket = ""; this._engineConfirmUrl = "";
    this._nameSelections = [];
    this.setData({ ...empty(), loading: true });
    try {
      let response;
      if (sessionToken()) response = await request(contextPath(this._target), { auth: true });
      else {
        const login = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
        if (!current()) return;
        if (!login.code) throw new Error("微信身份暂时无法确认，请重试。");
        response = await request("/api/v1/wechat/checkin/entry", {
          method: "POST", data: { ...this._target, wx_login_code: login.code }
        });
      }
      if (!current()) return;
      const data = response.data || {};
      if (data.access_token) {
        current.acceptSession(data.access_token);
        app.setPersonSession(data.access_token);
      }
      if (!data.event || !data.event.event_id) throw new Error("活动信息暂时不可用，请联系现场工作人员。");
      const ticket = typeof data.checkin_ticket === "string" ? data.checkin_ticket : "";
      if (ticket) {
        const url = engineConfirmUrl(data.engine_confirm_url, app.globalData.apiBaseUrl, app.globalData);
        if (!url || ticket.length > 8192 || !/^[A-Za-z0-9._-]+$/.test(ticket)) throw new Error("现场签到服务地址未确认，请联系工作人员。");
        // Keep the ticket outside render data and persistent application state.
        this._checkinTicket = ticket; this._engineConfirmUrl = url;
      }
      this.setData({ event: displayEvent(data.event), konpaRequired: data.event.konpa_choice_required === true, member: data.member || null,
        registration: data.registration || null, canCheckin: Boolean(data.can_checkin && data.member),
        alreadyChecked: Boolean(data.already_checked_in), bindingRequired: false,
        guestAllowed: Boolean(!data.member && data.guest_allowed),
        requiresFallback: Boolean(data.requires_fallback), fallbackAvailable: Boolean(fallbackUrl(data.fallback_url)),
        historyKind: data.history_kind === "learning" ? "learning" : "activity",
        notice: data.notice || "" });
      if (data.already_checked_in && data.receipt) {
        const result = { status: "ALREADY_CHECKED_IN", receipt: data.receipt, checked_at: data.receipt.checked_at };
        this.setData({ result, successCard: successCard(result, { event: data.event, member: data.member }) });
      }
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        current.acceptSession("");
        app.clearMemberSession();
        if (hadSession) return this.loadContext();
        this.setData({ errorMessage: error.message || "微信身份暂时无法确认，请重试。" });
      } else this.setData({ errorMessage: error.message || "活动信息暂时无法加载，请重试。", rescueNotice: platformRescueNotice(error) });
    } finally { if (current()) this.setData({ loading: false }); }
  },

  inputGuestName(event) {
    if (this.data.confirming || this.data.guestResults.length) return;
    this._nameSelections = [];
    this.setData({ guestName: event.detail.value, nameCandidates: [], selectedCandidateIndexes: [], notice: "", errorMessage: "" });
  },
  selectGuestCandidates(event) {
    if (this.data.confirming) return;
    const values = event.detail && event.detail.value;
    const selected = Array.isArray(values) ? [...new Set(values.map(String))].filter(value => {
      const index = Number(value);
      return String(index) === value && Number.isInteger(index) && index >= 0 && this._nameSelections &&
        this._nameSelections[index] && !this._nameSelections[index].result;
    }) : [];
    this.setData({ selectedCandidateIndexes: selected, errorMessage: "",
      nameCandidates: this.data.nameCandidates.map((row, index) => ({ ...row, selected: selected.includes(String(index)) })) });
  },
  async submitGuest() {
    if (this.data.confirming || !this.data.guestAllowed || this.data.alreadyChecked || !this.data.event) return;
    if (this.data.konpaRequired && typeof this.data.konpaChoice !== 'boolean') {
      this.setData({errorMessage:'请选择是否参加空巴'}); return;
    }
    const name = String(this.data.guestName || "").trim();
    if (!name || name.length > 120 || /[\x00-\x1f\x7f]/.test(name)) {
      this.setData({ errorMessage: "请填写有效报名姓名。" }); return;
    }
    const current = beginPrivateRequest(this, "confirm");
    this.setData({ confirming: true, errorMessage: "" });
    try {
      if (!this._nameSelections || !this._nameSelections.length) {
        const login = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
        if (!current()) return;
        if (!login.code) throw new Error("微信身份暂时无法确认，请重试。");
        const response = await request("/api/v1/wechat/checkin/guest-lookup", {
          method: "POST", data: { ...this._target, name, wx_login_code: login.code }
        });
        if (!current()) return;
        const candidates = ((response.data || {}).candidates || []).filter(row => row.candidate_token);
        // Selection credentials never enter render data or application storage.
        this._nameSelections = candidates.map(row => ({ name, token: row.candidate_token }));
        this.setData({ nameCandidates: candidates.map(({ candidate_token, ...row }) => ({ ...row, selected: false })),
          selectedCandidateIndexes: [], notice: "" });
        if (!candidates.length) {
          this.setData({ errorMessage: "未查询到您的报名信息，请联系工作人员，谢谢" }); return;
        }
        if (candidates.length > 1) return;
      }
      const indexes = this._nameSelections.length === 1 ? [0] : this.data.selectedCandidateIndexes.map(Number);
      if (!indexes.length) {
        this.setData({ errorMessage: "请勾选需要签到的报名信息。" }); return;
      }
      const selections = [...new Set(indexes)].map(index => ({ index, selected: this._nameSelections[index] }));
      if (selections.some(({ selected }) => !selected || selected.name !== name || selected.result)) {
        throw new Error("报名选项已变化，请重新核对。");
      }
      let failed = 0;
      for (const { index, selected } of selections) {
        if (!current()) return;
        try {
          // Each selected original registration is confirmed independently. A
          // fresh login code is required for each request; never reuse one.
          const login = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
          if (!current()) return;
          if (!login.code) throw new Error("微信身份暂时无法确认，请重试。");
          const response = await request("/api/v1/wechat/checkin/guest-confirm", {
            method: "POST", data: { ...this._target, name, candidate_token: selected.token, wx_login_code: login.code,
              ...(this.data.konpaRequired ? {konpa_choice:this.data.konpaChoice} : {}) }
          });
          if (!current()) return;
          const result = response.data || {};
          if (result.participant_type !== "GUEST" || !result.checked_at || !["CHECKED_IN", "ALREADY_CHECKED_IN"].includes(result.status)) throw new Error("签到结果暂未确认，请重试。");
          selected.result = result;
          this.setData({ nameCandidates: this.data.nameCandidates.map((row, rowIndex) => rowIndex === index ? { ...row, completed: true, selected: false, error: "" } : row),
            selectedCandidateIndexes: this.data.selectedCandidateIndexes.filter(value => Number(value) !== index) });
        } catch (error) {
          if (!current()) return;
          failed += 1;
          this.setData({ nameCandidates: this.data.nameCandidates.map((row, rowIndex) => rowIndex === index ? { ...row, error: error.message || "签到暂时未完成，请重试。" } : row) });
        }
      }
      const completed = this._nameSelections.filter(row => row.result);
      const cards = this._nameSelections.map((row, index) => row.result ? { key: String(index), card: successCard(row.result, { event: this.data.event, name }) } : null).filter(Boolean);
      const allSelectedCompleted = !failed && completed.length > 0;
      const lastResult = completed.length ? completed[completed.length - 1].result : null;
      this.setData({ guestResults: this._nameSelections.length > 1 ? cards : [],
        result: allSelectedCompleted ? lastResult : null,
        successCard: this._nameSelections.length === 1 && allSelectedCompleted ? cards[0].card : null,
        alreadyChecked: allSelectedCompleted, guestAllowed: !allSelectedCompleted,
        errorMessage: failed ? (completed.length ? `已完成 ${completed.length} 条报名签到，其余未完成，请重试。` : "签到暂时未完成，请重试。") : "",
        notice: "" });
      if (allSelectedCompleted) {
        this._nameSelections = [];
        this.setData({ nameCandidates: [], selectedCandidateIndexes: [], guestName: "" });
      }
    } catch (error) {
      if (current()) this.setData({ errorMessage: error.message || "签到暂时未完成，请重试。" });
    } finally { if (current()) this.setData({ confirming: false }); }
  },

  chooseKonpa(event) {
    if (!this.data.confirming) this.setData({ konpaChoice: event.detail.value === "yes" });
  },
  async confirmCheckin() {
    if (this.data.confirming || !this.data.canCheckin || this.data.alreadyChecked || !this.data.event) return;
    if (this.data.konpaRequired && typeof this.data.konpaChoice !== "boolean") {
      this.setData({ errorMessage: "请选择是否参加空巴" }); return;
    }
    const current = beginPrivateRequest(this, "confirm");
    const eventId = this.data.event.event_id;
    this.setData({ confirming: true, errorMessage: "" });
    try {
      // The platform resolves the member and registration from the credential.
      // Names, phone numbers, member IDs and registration IDs are never sent.
      const data = { event_id: String(eventId) };
      if (this.data.konpaRequired) data.konpa_choice = this.data.konpaChoice;
      if (this._target.token) data.token = this._target.token;
      let response;
      if (this._checkinTicket) {
        const engine = await directConfirm(this._engineConfirmUrl, this._checkinTicket, app.globalData, data.konpa_choice);
        response = { data: { status: engine.already === true ? "ALREADY_CHECKED_IN" : "CHECKED_IN",
          checked_at: engine.checked_at || (engine.data && engine.data.checked_at),
          sync_status: engine.sync_status === "SYNCED" ? "SYNCED" : "PENDING",
          message: engine.msg, receipt: engine.data, event: this.data.event, history_kind: this.data.historyKind } };
      } else {
        // Older contexts without a ticket retain the authenticated bridge.
        response = await request("/api/v1/wechat/checkin/confirm", { method: "POST", auth: true, data });
      }
      if (!current()) return;
      const result = response.data || {};
      if (!["CHECKED_IN", "ALREADY_CHECKED_IN"].includes(result.status)) throw new Error("签到结果暂未确认，请重试；重复确认不会重复签到。");
      if (result.event && String(result.event.event_id) !== String(eventId)) throw new Error("返回的场次不一致，请重新确认当前活动。");
      this._checkinTicket = ""; this._engineConfirmUrl = "";
      this.setData({ result, successCard: successCard(result, { event: this.data.event, member: this.data.member, registration: this.data.registration }), alreadyChecked: true, canCheckin: false,
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
