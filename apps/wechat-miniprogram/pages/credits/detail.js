const app = getApp();
const { request } = require("../../utils/request");
const { pointsLabel } = require("../../utils/credit-display");
const session = () => app.globalData.personSessionToken || app.globalData.memberSessionToken || "";

Page({
  data: { loading: false, entry: null, errorMessage: "" },
  onLoad(options) { this._entryRef = String(options.entry_ref || ""); },
  async onShow() {
    const version = this._loadVersion = (this._loadVersion || 0) + 1;
    const token = session();
    this.setData({ loading: false, entry: null, errorMessage: "" });
    if (!token) { this.setData({ errorMessage: "请先绑定学员身份。" }); return; }
    if (!/^[1-9]\d*$/.test(this._entryRef)) { this.setData({ errorMessage: "学分记录不存在。" }); return; }
    const current = () => this._loadVersion === version && session() === token;
    this.setData({ loading: true });
    try {
      const response = await request(`/api/v1/wechat/credit-entries/${encodeURIComponent(this._entryRef)}`, { auth: true });
      if (!current()) return;
      const entry = response.data || {};
      this.setData({ entry: { ...entry, pointsLabel: pointsLabel(entry.points, true) } });
    } catch (error) {
      if (!current()) return;
      if (error.statusCode === 401) app.clearMemberSession();
      this.setData({ errorMessage: error.statusCode === 404 ? "学分记录不存在。" :
        error.statusCode === 401 ? "绑定已失效，请重新绑定。" : "学分详情暂时无法加载，请重试。" });
    } finally { if (this._loadVersion === version) this.setData({ loading: false }); }
  },
  onHide() { this._loadVersion = (this._loadVersion || 0) + 1; this.setData({ entry: null, loading: false }); },
  onUnload() { this.onHide(); },
  openOriginal() {
    const ref = this.data.entry && this.data.entry.original_entry_ref;
    if (!/^[1-9]\d*$/.test(String(ref || ""))) return;
    wx.navigateTo({ url: `/pages/credits/detail?entry_ref=${encodeURIComponent(ref)}` });
  }
});
