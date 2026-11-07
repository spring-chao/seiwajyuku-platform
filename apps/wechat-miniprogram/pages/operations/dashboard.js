const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
Page({
  data: { loading: true, month: "", snapshot: null, metrics: [], errorMessage: "",
    birthdayMonth: 1, birthdayMonths: ["全部月份", ...Array.from({ length: 12 }, (_, i) => `${i + 1}月`)],
    birthdayClassId: "", birthdayClassIndex: 0, birthdayClasses: [{ id: "", name: "全部班级" }], birthdayMembers: [] },
  onLoad() { const now = new Date(); this.setData({ month: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`, birthdayMonth: now.getMonth() + 1 }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { snapshot: null, metrics: [], birthdayMembers: [], birthdayClasses: [{ id: "", name: "全部班级" }], birthdayClassIndex: 0, errorMessage: "", loading: false }); },
  onUnload() { this.onHide(); },
  chooseMonth(event) { this.setData({ month: event.detail.value, birthdayMonth: Number(event.detail.value.slice(5, 7)) }); return this.load(); },
  chooseBirthdayMonth(event) { this.setData({ birthdayMonth: Number(event.detail.value) }); return this.load(); },
  chooseBirthdayClass(event) {
    const index = Number(event.detail.value);
    const item = this.data.birthdayClasses[index];
    if (!item) return;
    this.setData({ birthdayClassId: item.id, birthdayClassIndex: index });
    this.filterBirthdays();
  },
  filterBirthdays() {
    const rows = (this.data.snapshot && this.data.snapshot.birthday_members) || [];
    const birthdayMembers = rows
      .filter(item => !this.data.birthdayClassId || item.class_org_unit_id === this.data.birthdayClassId)
      .map(item => ({ ...item, birthdayKey: `${item.member_id}:${item.due_date || item.birthday}` }));
    this.setData({ birthdayMembers });
  },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, snapshot: null, metrics: [], birthdayMembers: [], errorMessage: "" });
    try {
      const [year, month] = this.data.month.split("-").map(Number);
      const response = await request(`/api/v1/wechat/operations/dashboard?year=${year}&month=${month}&birthday_month=${this.data.birthdayMonth}`, { auth: true });
      if (!current()) return;
      const snapshot = response.data;
      const classes = new Map();
      (snapshot.classes || []).forEach(item => classes.set(item.class_org_unit_id, item.class_name));
      (snapshot.birthday_members || []).forEach(item => { if (item.class_org_unit_id) classes.set(item.class_org_unit_id, item.class_name); });
      const birthdayClasses = [{ id: "", name: "全部班级" }, ...Array.from(classes, ([id, name]) => ({ id, name }))];
      let birthdayClassIndex = birthdayClasses.findIndex(item => item.id === this.data.birthdayClassId);
      if (birthdayClassIndex < 0) birthdayClassIndex = 0;
      this.setData({ birthdayClasses, birthdayClassIndex, birthdayClassId: birthdayClasses[birthdayClassIndex].id });
      const fields = [["active_member_count", "当前在册"], ["new_member_count", "本月新增"], ["renewed_member_count", "本月续费"], ["birthday_member_count", "本月生日"], ["class_meeting_count", "班会场次"], ["activity_count", "活动场次"]];
      this.setData({ snapshot, metrics: fields.map(([key, label]) => ({ key, label, value: snapshot.summary[key] === null || snapshot.summary[key] === undefined ? "—" : snapshot.summary[key] })) });
      this.filterBirthdays();
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  openBirthday(event) {
    const item = event.currentTarget.dataset.item;
    if (!item || !item.member_id || !item.birthday) return;
    const dueDate = item.due_date || `${this.data.month.slice(0, 4)}-${item.birthday}`;
    wx.navigateTo({ url: `/pages/operations/member-detail?member_id=${item.member_id}&birthday_due_date=${dueDate}` });
  },
  openMeetings() { wx.navigateTo({ url: "/pages/operations/study-meetings" }); },
  openActivities() { wx.navigateTo({ url: "/pages/operations/activities" }); }
});
