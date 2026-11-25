const { request } = require("../../utils/request");
const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");

const endpoint = "/api/v1/wechat/checkin/class-arrival";
const empty = { events: [], eventIndex: 0, rows: [], loading: false, confirming: false, error: "" };

Page({
  data: empty,
  onShow() { showPrivatePage(this); return this.refresh(); },
  onHide() { hidePrivatePage(this, empty); },
  onUnload() { this.onHide(); },

  async refresh() {
    const current = beginPrivateRequest(this);
    this.setData({ ...empty, loading: true });
    try {
      const { data } = await request(endpoint + "/events", { auth: true });
      if (!current()) return;
      const events = data.events.map(event => ({
        ...event, label: [event.class_name || event.name, event.session_name || (event.session_code === "MORNING" ? "上午" : "下午")].join(" · ")
      }));
      this.setData({ events });
      if (events.length) await this.loadRoster();
    } catch (error) {
      if (current()) this.setData({ error: error.message || "活动加载失败" });
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },

  async loadRoster() {
    const event = this.data.events[this.data.eventIndex];
    if (!event) return;
    const current = beginPrivateRequest(this, "roster");
    this.setData({ loading: true, rows: [], error: "" });
    try {
      const query = `?event_id=${encodeURIComponent(event.event_id)}&class_id=${encodeURIComponent(event.class_org_unit_id)}`;
      const { data } = await request(endpoint + "/roster" + query, { auth: true });
      if (!current()) return;
      this.setData({ rows: data.rows.sort((a, b) => Number(b.self) - Number(a.self)) });
    } catch (error) {
      if (current()) this.setData({ error: error.message || "名单加载失败" });
    } finally {
      if (current()) this.setData({ loading: false });
    }
  },

  pickEvent(e) {
    const eventIndex = Number(e.detail.value);
    if (this.data.loading || this.data.confirming || !this.data.events[eventIndex]) return;
    this.setData({ eventIndex });
    return this.loadRoster();
  },

  async confirm(e) {
    if (this.data.loading || this.data.confirming) return;
    const event = this.data.events[this.data.eventIndex];
    const row = this.data.rows.find(item => item.member_id === String(e.currentTarget.dataset.id));
    if (!event || !row || row.checked) return;
    const current = beginPrivateRequest(this, "confirm");
    this.setData({ confirming: true, error: "" });
    try {
      let choice;
      if (row.self && event.konpa_choice_required) {
        const selected = await new Promise(resolve => wx.showActionSheet({
          itemList: ["参加空巴并确认到场", "不参加空巴并确认到场"],
          success: selection => resolve(selection.tapIndex === 0),
          fail: () => resolve(undefined)
        }));
        if (selected === undefined || !current()) return;
        choice = selected;
      } else {
        const accepted = await new Promise(resolve => wx.showModal({
          title: "确认到场",
          content: `${event.label}：确认${row.self ? "本人" : row.name}已到场？`,
          success: result => resolve(result.confirm),
          fail: () => resolve(false)
        }));
        if (!accepted || !current()) return;
      }
      await request(endpoint + "/confirm", {
        method: "POST", auth: true,
        data: {
          event_id: event.event_id, class_id: event.class_org_unit_id, member_id: Number(row.member_id),
          ...(typeof choice === "boolean" ? { konpa_choice: choice } : {})
        }
      });
      if (!current()) return;
      this.setData({ rows: this.data.rows.map(item => item === row || item.member_id === row.member_id
        ? { ...item, checked: true } : item) });
      wx.showToast({ title: "已确认到场" });
    } catch (error) {
      if (current()) this.setData({ error: error.message || "确认失败，请刷新名单核对" });
    } finally {
      if (current()) this.setData({ confirming: false });
    }
  }
});
