const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");

Page({
  data: { care: null, selectionChanged: false, loading: false, busy: false, errorMessage: "" },

  onLoad(options) {
    this._memberId = Number(options.member_id);
    this._dueDate = options.due_date || "";
  },
  onShow() {
    showPrivatePage(this);
    return this.load();
  },
  onHide() {
    hidePrivatePage(this, { care: null, selectionChanged: false, loading: false, busy: false, errorMessage: "" });
  },
  onUnload() { this.onHide(); },

  path() {
    return `/api/v1/wechat/operations/members/${encodeURIComponent(this._memberId)}/anniversary-care`;
  },
  showCare(result, ids = result.selected_ids) {
    const selected = new Set(ids);
    const memories = result.memories.map(item => ({ ...item, checked: selected.has(item.id) }));
    const selectionChanged = selected.size !== result.selected_ids.length ||
      result.selected_ids.some(id => !selected.has(id));
    this.setData({ care: { ...result, memories }, selectionChanged });
  },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, errorMessage: "" });
    try {
      if (!this._memberId || !/^\d{4}-\d{2}-\d{2}$/.test(this._dueDate)) throw new Error("请从周年名单打开此页面");
      const response = await request(`${this.path()}?due_date=${encodeURIComponent(this._dueDate)}`, { auth: true });
      if (current()) this.showCare(response.data);
    } catch (error) {
      if (current()) this.setData({ errorMessage: error.message || "无法加载周年关怀" });
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },
  chooseMemories(event) {
    const care = this.data.care;
    if (this.data.busy || !care?.can_manage || care.send_status === "SENT") return;
    const ids = event.detail.value;
    if (ids.length > 3) {
      this.showCare(care, care.memories.filter(item => item.checked).map(item => item.id));
      wx.showToast({ title: "最多选择三条经历", icon: "none" });
      return;
    }
    this.showCare(care, ids);
  },
  regenerate() {
    const care = this.data.care;
    if (care?.can_manage && care.draft && care.send_status !== "SENT") {
      return this.update("regenerate", care.memories.filter(item => item.checked).map(item => item.id));
    }
  },
  async update(action, ids = []) {
    if (this.data.busy) return;
    const current = beginPrivateRequest(this, "update");
    this.setData({ busy: true, errorMessage: "" });
    try {
      const response = await request(`${this.path()}/${action}`, {
        method: "POST", auth: true, data: { due_date: this._dueDate, memory_ids: ids }
      });
      if (current()) this.showCare(response.data);
    } catch (error) {
      if (current()) {
        this.setData({ errorMessage: error.message || "操作未完成，请重试" });
      }
    } finally {
      if (current()) this.setData({ busy: false });
    }
  },
  copy() {
    const care = this.data.care;
    if (this.data.busy || this.data.selectionChanged || care?.review_status !== "APPROVED" || !care.draft || care.send_status === "SENT") return;
    wx.setClipboardData({ data: care.draft });
  },
  markSent() {
    const care = this.data.care;
    if (this.data.busy || this.data.selectionChanged || !care?.can_mark_sent || care.review_status !== "APPROVED" || care.send_status === "SENT") return;
    const current = beginPrivateRequest(this, "confirmation");
    wx.showModal({
      title: "登记已发送",
      content: "仅在祝福已经发送到微信群后登记。",
      confirmText: "登记发送",
      success: result => { if (current() && result.confirm && !this.data.selectionChanged) this.update("sent"); }
    });
  }
});

