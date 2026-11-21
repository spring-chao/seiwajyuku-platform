const { request } = require("../../utils/request");

function cacheImage(data, key) {
  if (!data) return "";
  const match = /^data:image\/(png|jpeg|jpg);base64,(.+)$/.exec(data);
  if (!match) return "";
  const filePath = `${wx.env.USER_DATA_PATH}/learning-resource-${key}.${match[1] === "png" ? "png" : "jpg"}`;
  wx.getFileSystemManager().writeFileSync(filePath, match[2], "base64");
  return filePath;
}
function prepare(q) {
  const image = cacheImage(q.qrData, `${q.imageIdx}-${q.qrNo}`);
  return { url: q.url, image, title: q.label || `二维码 ${q.qrNo}` };
}

Page({
  data: { view: "home", years: [], year: 1, months: [], blocks: [], loading: false, error: "", recent: [], videos: [], baidu: null },
  onLoad() { this._loadVersion = 0; this.loadCatalog(); },
  onUnload() { this._loadVersion++; },
  async loadCatalog() {
    this.setData({ loading: true, error: "" });
    try {
      const response = await request("/api/v1/wechat/learning-resources", { auth: true });
      this.setData({ years: response.data.years, months: response.data.years[0].months });
    } catch (error) { this.setData({ error: error.message }); }
    finally { this.setData({ loading: false }); }
  },
  home() { this._loadVersion++; this.setData({ view: "home", error: "", loading: false }); },
  years() { this._loadVersion++; this.setData({ view: "years", error: "", loading: false }); },
  selectYear(event) {
    const year = Number(event.currentTarget.dataset.year);
    this.setData({ year, months: this.data.years[year - 1].months });
  },
  async openMonth(event) {
    const year = Number(event.currentTarget.dataset.year || this.data.year);
    const month = Number(event.currentTarget.dataset.month);
    const version = ++this._loadVersion;
    this.setData({ loading: true, error: "", view: "month", blocks: [], monthLabel: "", note: "" });
    try {
      const response = await request(`/api/v1/wechat/learning-resources?year=${year}&month=${month}`, { auth: true });
      if (version !== this._loadVersion) return;
      const m = response.data;
      const blocks = [
        { title: "班会学习", topic: m.classTopic, qrs: m.classQrs.map(prepare) },
        ...(m.extraTopics.length || m.extraQrs.length ? [{ title: "补充学习内容", topic: m.extraTopics.join("\n"), qrs: m.extraQrs.map(prepare) }] : []),
        { title: "小组学习会", topic: m.groupTopic, qrs: m.groupQrs.map(prepare) }
      ];
      const label = `${this.data.years[year - 1].title} · ${m.label}`;
      const recent = [{ year, month, label }, ...this.data.recent.filter(x => x.year !== year || x.month !== month)].slice(0, 5);
      this.setData({ blocks, monthLabel: label, note: m.resourceNote || "", recent });
    } catch (error) { if (version === this._loadVersion) this.setData({ error: error.message }); }
    finally { if (version === this._loadVersion) this.setData({ loading: false }); }
  },
  async committee() {
    const version = ++this._loadVersion;
    this.setData({ view: "committee", loading: true, error: "", videos: [], baidu: null });
    try {
      const response = await request("/api/v1/wechat/learning-resources?committee=true", { auth: true });
      if (version !== this._loadVersion) return;
      const videos = response.data.videos.map(prepare);
      this.setData({ videos, baidu: { url: response.data.baidu.url, image: cacheImage(response.data.baidu.qrData, "baidu") } });
    } catch (error) { if (version === this._loadVersion) this.setData({ error: error.message }); }
    finally { if (version === this._loadVersion) this.setData({ loading: false }); }
  },
  preview(event) { const image = event.currentTarget.dataset.image; if (image) wx.previewImage({ current: image, urls: [image] }); },
  save(event) { wx.saveImageToPhotosAlbum({ filePath: event.currentTarget.dataset.image, success: () => wx.showToast({ title: "已保存" }), fail: () => wx.showToast({ title: "未保存，可放大后长按保存", icon: "none" }) }); },
  copy(event) { wx.setClipboardData({ data: event.currentTarget.dataset.url }); }
});
