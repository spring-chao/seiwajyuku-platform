const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
function key() { return `mobile-${Date.now()}-${Math.random().toString(36).slice(2)}`; }
Page({
  data: { loading: true, saving: false, adding: false, eventId: null, detail: null, organizations: [], orgIndex: 0, title: "", eventDate: "", status: "DRAFT", checkinStart: "08:00", checkinEnd: "22:00", personPhone: "", personName: "", errorMessage: "" },
  onLoad(options) { this._createKey = key(); this._attendeeKey = key(); this.setData({ eventId: options.event_id || null }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { hidePrivatePage(this, { detail: null, organizations: [], title: "", personName: "", personPhone: "", errorMessage: "", saving: false, adding: false, loading: false }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, detail: null, errorMessage: "" });
    try {
      if (this.data.eventId) {
        const response = await request(`/api/v1/wechat/operations/activities/${encodeURIComponent(this.data.eventId)}`, { auth: true });
        if (!current()) return;
        const group = response.data.group;
        if (current()) this.setData({ detail: response.data, title: group.title, eventDate: String(group.event_date).slice(0, 10), status: group.status });
      } else {
        const response = await request("/api/v1/wechat/operations/activity-context", { auth: true });
        if (!current()) return;
        const now = new Date();
        this.setData({ organizations: response.data.organizations, eventDate: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}` });
      }
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  input(event) { const field = event.currentTarget.dataset.field; if (["title", "personName", "personPhone"].includes(field)) { this.setData({ [field]: event.detail.value }); if (field === "personName" || field === "personPhone") this._attendeeKey = key(); } },
  chooseOrg(event) { this.setData({ orgIndex: Number(event.detail.value) }); },
  chooseDate(event) { this.setData({ eventDate: event.detail.value }); },
  chooseTime(event) { this.setData({ [event.currentTarget.dataset.field]: event.detail.value }); },
  async save() {
    if (this.data.saving) return;
    const current = beginPrivateRequest(this, "mutation");
    const group = this.data.detail && this.data.detail.group;
    const org = this.data.organizations[this.data.orgIndex];
    if (!this.data.title.trim() || (!group && !org)) { wx.showToast({ title: "请填写名称并选择组织", icon: "none" }); return; }
    this.setData({ saving: true });
    try {
      const response = await request(`/api/v1/wechat/operations/activities${this.data.eventId ? "/" + encodeURIComponent(this.data.eventId) : ""}`, { auth: true, method: this.data.eventId ? "PATCH" : "POST", data: { title: this.data.title, event_date: this.data.eventDate, org_unit_id: group ? group.org_unit_id : org.id, request_id: this._createKey, expected_revision: this.data.detail ? this.data.detail.revision : null, checkin_start: this.data.checkinStart, checkin_end: this.data.checkinEnd } });
      if (!current()) return;
      if (response.data.id) { this.setData({ eventId: response.data.id }); wx.showToast({ title: "活动已保存" }); await this.load(); } else { wx.showModal({ title: "已提交活动", content: "请返回活动列表核对创建结果。", showCancel: false }); }
    } catch (error) { if (current()) wx.showModal({ title: "保存未完成", content: error.message, showCancel: false }); }
    finally { if (current()) this.setData({ saving: false }); }
  },
  changeStatus() {
    if (!this.data.detail || this.data.saving) return;
    const current = beginPrivateRequest(this, "confirm");
    const choices = [];
    if (this.data.detail.can_update) choices.push({label:"确认活动",value:"CONFIRMED"},{label:"设为草稿",value:"DRAFT"});
    if (this.data.detail.can_manage) choices.push({label:"取消活动",value:"CANCELLED"});
    if (!choices.length) return;
    wx.showActionSheet({ itemList: choices.map(item => item.label), success: result => {
      if (!current()) return;
      const chosen = choices[result.tapIndex];
      wx.showModal({ title: chosen.label, content: `确认将活动设为“${chosen.label}”？`, success: async result => {
        if (!result.confirm || !current() || this.data.saving) return;
        this.setData({saving:true});
        try {
          await request(`/api/v1/wechat/operations/activities/${encodeURIComponent(this.data.eventId)}/lifecycle`, {auth:true,method:"POST",data:{status:chosen.value}});
          if (current()) await this.load();
        } catch(error) {if(current()) wx.showModal({title:"操作未完成",content:error.message,showCancel:false});}
        finally {if(current()) this.setData({saving:false});}
      }});
    }});
  },
  async addAttendee() {
    if (this.data.adding) return;
    const current = beginPrivateRequest(this, "attendee");
    if (!this.data.personName.trim()) { wx.showToast({ title: "请填写临时参加人员姓名", icon: "none" }); return; }
    this.setData({ adding: true });
    try {
      await request(`/api/v1/wechat/operations/activities/${encodeURIComponent(this.data.eventId)}/attendees`, { auth: true, method: "POST", data: { name: this.data.personName, phone: this.data.personPhone || null, request_id: this._attendeeKey } });
      if (!current()) return;
      this._attendeeKey = key(); this.setData({ personName: "", personPhone: "", selectedMember: null }); wx.showToast({ title: "已保存临时报名" }); await this.load();
    } catch (error) { if (current()) wx.showModal({ title: "登记未完成", content: error.message, showCancel: false }); }
    finally { if (current()) this.setData({ adding: false }); }
  }
});
