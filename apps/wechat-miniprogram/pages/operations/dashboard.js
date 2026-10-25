const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
Page({
  data: { loading: true, month: "", snapshot: null, metrics: [], errorMessage: "" },
  onLoad() { const now = new Date(); this.setData({ month: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}` }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { snapshot: null, metrics: [], errorMessage: "", loading: false }); },
  onUnload() { this.onHide(); },
  chooseMonth(event) { this.setData({ month: event.detail.value }); this.load(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, snapshot: null, errorMessage: "" });
    try {
      const [year, month] = this.data.month.split("-").map(Number);
      const response = await request(`/api/v1/wechat/operations/dashboard?year=${year}&month=${month}`, { auth: true });
      if (!current()) return;
      const snapshot = response.data;
      const fields = [["active_member_count", "当前在册"], ["new_member_count", "本月新增"], ["renewed_member_count", "本月续费"], ["birthday_member_count", "本月生日"], ["class_meeting_count", "班会场次"], ["activity_count", "活动场次"]];
      this.setData({ snapshot, metrics: fields.map(([key, label]) => ({ key, label, value: snapshot.summary[key] === null || snapshot.summary[key] === undefined ? "—" : snapshot.summary[key] })) });
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  openBirthday(event) {
    const item = event.currentTarget.dataset.item;
    if (!item || !item.member_id || !item.birthday) return;
    wx.navigateTo({ url: `/pages/operations/member-detail?member_id=${item.member_id}&birthday_due_date=${this.data.month.slice(0, 4)}-${item.birthday}` });
  },
  openMeetings() { wx.navigateTo({ url: "/pages/operations/study-meetings" }); },
  openActivities() { wx.navigateTo({ url: "/pages/operations/activities" }); }
});
