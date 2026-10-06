const app = getApp();
const { request } = require("../../utils/request");
const { historyPath, readHistory, categoriesFor } = require("../../utils/participation-history");
const session = () => app.globalData.personSessionToken || app.globalData.memberSessionToken || "";

function emptyHistory() {
  return { records: [], total: null, learningCount: null, activityCount: null,
    categories: [], nextPage: 1, hasMore: true,
    historyVersion: null, noticeMessage: "", refreshRequired: false,
    yearOptions: [{ value: "", label: "全部" }, { value: "2026", label: "2026年" }, { value: "2025", label: "2025年" }] };
}

Page({
  data: {
    ...emptyHistory(), kind: "learning", kindName: "学习", year: "", yearLabel: "全部年份",
    loading: false, errorMessage: "", sessionMissing: false
  },

  onLoad(options = {}) {
    const kind = options.kind === "activity" ? "activity" : "learning";
    const year = /^\d{4}$/.test(String(options.year || "")) ? String(options.year) : "";
    this.setData({ kind, kindName: kind === "learning" ? "学习" : "活动", year,
      yearLabel: year ? `${year}年` : "全部年份" });
  },

  onShow() { return this.reloadHistory(); },

  onHide() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this.setData({ ...emptyHistory(), loading: false, errorMessage: "", sessionMissing: false });
  },

  onUnload() { this.onHide(); },

  reloadHistory() {
    this._loadVersion = (this._loadVersion || 0) + 1;
    this._autoRefreshUsed = false;
    this.setData({ ...emptyHistory(), loading: false, errorMessage: "", sessionMissing: false });
    return this.loadMore();
  },

  changeKind(event) {
    const kind = event.currentTarget.dataset.kind;
    if (!["learning", "activity"].includes(kind) || kind === this.data.kind) return;
    this.setData({ kind, kindName: kind === "learning" ? "学习" : "活动" });
    return this.reloadHistory();
  },

  changeYear(event) {
    const year = String(event.currentTarget.dataset.year || "");
    if (!this.data.yearOptions.some(item => item.value === year) || year === this.data.year) return;
    this.setData({ year, yearLabel: year ? `${year}年` : "全部年份" });
    return this.reloadHistory();
  },

  async loadMore() {
    if (this.data.loading || !this.data.hasMore) return;
    const version = this._loadVersion;
    const token = session();
    if (!token) {
      this.setData({ ...emptyHistory(), hasMore: false, sessionMissing: true,
        errorMessage: "请先绑定学员身份，查看自己的记录。" });
      return;
    }
    const current = () => this._loadVersion === version && session() === token;
    let page = this.data.nextPage;
    const kind = this.data.kind;
    const year = this.data.year;
    let reloading = false;
    this.setData({ loading: true, errorMessage: "" });
    try {
      let response = await request(historyPath({ kind, year, page }), { auth: true });
      if (!current()) return;
      let history = readHistory(response.data, this.data.records.length);
      const changed = page > 1 && (history.total !== this.data.total ||
        (history.historyVersion && this.data.historyVersion && history.historyVersion !== this.data.historyVersion));
      if (changed) {
        // Never combine pages obtained from different versions of a person's
        // records. One automatic refresh is allowed until an explicit reload.
        if (this._autoRefreshUsed) {
          this.setData({ refreshRequired: true, hasMore: false, noticeMessage: "",
            errorMessage: "记录再次更新，请重新加载后继续查看。" });
          return;
        }
        this._autoRefreshUsed = true;
        reloading = true;
        page = 1;
        this.setData({ noticeMessage: "记录已更新，正在重新加载…" });
        response = await request(historyPath({ kind, year, page }), { auth: true });
        if (!current()) return;
        history = readHistory(response.data);
      }
      const years = [...new Set([2026, 2025, ...history.availableYears, ...(year ? [Number(year)] : [])])].sort((a, b) => b - a);
      this.setData({ records: page === 1 ? history.records : this.data.records.concat(history.records), total: history.total,
        learningCount: history.learningCount, activityCount: history.activityCount,
        categories: categoriesFor(kind, history.categoryCounts), hasMore: history.hasMore, nextPage: page + 1,
        historyVersion: history.historyVersion, refreshRequired: false,
        noticeMessage: reloading ? "记录已更新，已重新加载" : this.data.noticeMessage,
        yearOptions: [{ value: "", label: "全部" }, ...years.map(year => ({ value: String(year), label: `${year}年` }))] });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) {
        app.clearMemberSession();
        this.setData({ ...emptyHistory(), hasMore: false, sessionMissing: true,
          errorMessage: "绑定已失效，请重新绑定。" });
      } else this.setData({ errorMessage: `${this.data.kindName}记录暂时无法加载，请重试。`,
        ...(reloading ? { refreshRequired: true, hasMore: false, noticeMessage: "记录已更新，请重新加载。" } : {}) });
    } finally {
      if (this._loadVersion === version) {
        if (session() !== token) {
          this.setData({ ...emptyHistory(), hasMore: false, sessionMissing: !session(),
            errorMessage: !session() ? "绑定已失效，请重新绑定。" : "身份已变更，请重新加载。" });
        }
        this.setData({ loading: false });
      }
    }
  },

  openBinding() { wx.navigateTo({ url: "/pages/identity/bind" }); }
});
