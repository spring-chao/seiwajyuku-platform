const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { rate } = require("../../utils/mobile-activity");
const ENDPOINT = "/api/v1/wechat/volunteer-class-meetings";
const TABS = [{ key: "overall", name: "整体" }, { key: "morning", name: "上午" }, { key: "afternoon", name: "下午" }, { key: "konpa", name: "空巴" }];
Page({
  data: { loading: false, detailLoading: false, classes: [], classIndex: 0, today: "", month: "", period: "today", records: [], meetingIndex: 0, page: 1, hasMore: false, snapshot: null, view: null, tabs: TABS, activeScene: "overall", errorMessage: "", detailError: "", updatedLabel: "" },
  onShow() { showPrivatePage(this); this.initialize(); },
  onHide() { hidePrivatePage(this, { loading: false, detailLoading: false, classes: [], records: [], snapshot: null, view: null, errorMessage: "", detailError: "", updatedLabel: "" }); },
  onUnload() { this.onHide(); },
  async initialize() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, errorMessage: "", snapshot: null, view: null });
    try {
      const response = await request(ENDPOINT + "/context", { auth: true });
      if (!current()) return;
      this.setData({ classes: response.data.classes, classIndex: 0, today: response.data.today, month: response.data.month, period: "today", activeScene: "overall" });
      await this.loadMeetings();
    } catch (error) { if (current()) this.setData({ errorMessage: error.message || "暂时无法读取班会统计" }); }
    finally { if (current() && !this.data.classes.length) this.setData({ loading: false }); }
  },
  async loadMeetings(append = false) {
    const current = beginPrivateRequest(this, "list");
    beginPrivateRequest(this, "detail");
    const organization = this.data.classes[this.data.classIndex];
    if (!organization) return;
    const page = append ? this.data.page + 1 : 1;
    this.setData({ loading: true, errorMessage: "", detailError: "", snapshot: null, view: null });
    if (!append) this.setData({ records: [], hasMore: false, meetingIndex: 0 });
    try {
      let query = `month=${encodeURIComponent(this.data.month)}&class_org_unit_id=${encodeURIComponent(organization.id)}&page=${page}`;
      if (this.data.period === "today") query += `&day=${encodeURIComponent(this.data.today)}`;
      const response = await request(ENDPOINT + "?" + query, { auth: true });
      if (!current()) return;
      const records = [...(append ? this.data.records : []), ...(response.data.records || [])].map(row => ({ ...row, pickerLabel: `${String(row.event_date).slice(0, 10)} · ${row.name}` }));
      this.setData({ records, page, hasMore: response.data.has_more, meetingIndex: append ? this.data.meetingIndex : 0 });
      if (records.length) await this.loadSnapshot();
    } catch (error) { if (current()) this.setData({ errorMessage: error.message || "班会列表读取失败，请重试" }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  async loadSnapshot() {
    const current = beginPrivateRequest(this, "detail");
    const record = this.data.records[this.data.meetingIndex];
    if (!record) return;
    this.setData({ detailLoading: true, detailError: "", snapshot: null, view: null });
    try {
      const response = await request(ENDPOINT + "/" + encodeURIComponent(record.event_id), { auth: true });
      if (!current()) return;
      const d = new Date(response.data.updated_at);
      this.setData({ snapshot: response.data, updatedLabel: Number.isFinite(d.getTime()) ? d.toLocaleTimeString() : "刚刚" });
      this.renderScene();
    } catch (error) { if (current()) this.setData({ detailError: error.message || "统计读取失败，请刷新重试" }); }
    finally { if (current()) this.setData({ detailLoading: false }); }
  },
  renderScene() {
    const scene = this.data.snapshot && this.data.snapshot.scenes.find(item => item.key === this.data.activeScene);
    if (!scene) { this.setData({ view: null }); return; }
    const c = scene.class_statistics || {}, g = scene.group_totals || {};
    this.setData({ view: { ...scene, overallRate: rate(c.participation_rate), groupRate: rate(g.participation_rate, 0),
      present: c.present_count, total: c.roster_count, members: c.member_present_count, staff: c.staff_present_count,
      groupPresent: g.present_count, groupTotal: g.roster_count,
      groups: (scene.group_statistics || []).map(item => ({ ...item, key: item.group_org_unit_id || "ungrouped", rateLabel: rate(item.participation_rate, 0) })) } });
  },
  chooseScene(event) { const key = event.currentTarget.dataset.key; if (!TABS.some(item => item.key === key)) return; this.setData({ activeScene: key }); this.renderScene(); },
  chooseClass(event) { const index = Number(event.detail.value); if (!this.data.classes[index]) return; this.setData({ classIndex: index }); return this.loadMeetings(); },
  choosePeriod(event) { const period = event.currentTarget.dataset.period; if (!["today", "month"].includes(period)) return; this.setData({ period, month: period === "today" ? this.data.today.slice(0, 7) : this.data.month }); return this.loadMeetings(); },
  chooseMonth(event) { this.setData({ month: event.detail.value, period: "month" }); return this.loadMeetings(); },
  chooseMeeting(event) { const index = Number(event.detail.value); if (!this.data.records[index]) return; this.setData({ meetingIndex: index, activeScene: "overall" }); return this.loadSnapshot(); },
  loadMore() { if (!this.data.loading && this.data.hasMore) this.loadMeetings(true); },
  refresh() { if (!this.data.loading && !this.data.detailLoading) this.loadMeetings(); }
});
