const app = getApp();
const { request } = require("../../utils/request");
const session = () => app.globalData.personSessionToken || app.globalData.memberSessionToken || "";

Page({
  data: { entries: [], openingBalance: null, loading: false, hasMore: true, nextOffset: 0, snapshotId: null, errorMessage: "" },
  onShow() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.setData({ entries: [], openingBalance: null, loading: false, hasMore: true, nextOffset: 0, snapshotId: null, errorMessage: "" });
    return this.loadMore();
  },
  onHide() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.setData({ entries: [], openingBalance: null, loading: false });
  },
  onUnload() { this.onHide(); },
  async loadMore() {
    if (this.data.loading || !this.data.hasMore) return;
    const version = this._loadVersion;
    const token = session();
    if (!token) { this.setData({ entries: [], openingBalance: null, hasMore: false, errorMessage: "请先绑定学员身份。" }); return; }
    const current = () => this._loadVersion === version && session() === token;
    let path = `/api/v1/wechat/credit-entries?limit=20&offset=${this.data.nextOffset}`;
    if (this.data.snapshotId !== null) path += `&snapshot_id=${encodeURIComponent(this.data.snapshotId)}`;
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request(path, { auth: true });
      if (!current()) return;
      const data = response.data || {};
      const entries = (data.entries || []).map(item => ({
        ...item, pointsLabel: `${Number(item.points) > 0 ? "+" : ""}${item.points}分`
      }));
      const seen = new Set(this.data.entries.map(item => item.entry_ref));
      this.setData({ openingBalance: data.opening_balance?.entry_count ? data.opening_balance : null, entries: this.data.entries.concat(entries.filter(item => !seen.has(item.entry_ref))),
        hasMore: data.has_more === true, nextOffset: data.next_offset, snapshotId: data.snapshot_id });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ entries: [], openingBalance: null, hasMore: false, errorMessage: "绑定已失效，请重新绑定。" });
      } else this.setData({ errorMessage: "学分记录暂时无法加载，请重试。" });
    } finally {
      if (this._loadVersion === version) {
        if (session() !== token) this.setData({ entries: [], openingBalance: null, hasMore: false, nextOffset: 0, snapshotId: null });
        this.setData({ loading: false });
      }
    }
  },
  openEntry(event) {
    const ref = String(event.currentTarget.dataset.ref || "");
    if (!/^\d+$/.test(ref) || !this.data.entries.some(item => item.entry_ref === ref)) return;
    wx.navigateTo({ url: `/pages/credits/detail?entry_ref=${encodeURIComponent(ref)}` });
  },
  openBinding() { wx.navigateTo({ url: "/pages/identity/bind" }); }
});
