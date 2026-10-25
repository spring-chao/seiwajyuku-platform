const { beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
const { request } = require("../../utils/request");
const activity = require("../../utils/mobile-activity");
function key() { return `mobile-${Date.now()}-${Math.random().toString(36).slice(2)}`; }
Page({
  data: { activityTypes: activity.ACTIVITY_TYPES, typeIndex: 7, activityType: "other", classes: [], classIndex: 0, classTimes: { ...activity.CLASS_TIMES }, periods: activity.PERIODS, clockFields: activity.CLOCK_FIELDS, sessions: [], statistics: null, groupRows: [], people: [], keyword: "", codePath: "", loading: true, saving: false, adding: false, eventId: null, detail: null, organizations: [], orgIndex: 0, title: "", eventDate: "", status: "DRAFT", checkinStart: "08:00", checkinEnd: "22:00", personPhone: "", personName: "", errorMessage: "" },
  onLoad(options) { this._createKey = key(); this._attendeeKey = key(); this.setData({ eventId: options.event_id || null }); },
  onShow() { showPrivatePage(this); this.load(); },
  onHide() { this._personIntents = {}; this.clearCode(); hidePrivatePage(this, { classes: [], sessions: [], statistics: null, groupRows: [], people: [], keyword: "", codePath: "", detail: null, organizations: [], title: "", personName: "", personPhone: "", errorMessage: "", saving: false, adding: false, loading: false }); },
  onUnload() { this.onHide(); },
  async load() {
    const current = beginPrivateRequest(this);
    this.setData({ loading: true, detail: null, errorMessage: "" });
    try {
      if (this.data.eventId) {
        const response = await request(`/api/v1/wechat/operations/activities/${encodeURIComponent(this.data.eventId)}`, { auth: true });
        if (!current()) return;
        const group = response.data.group;
        if (current()) this.setData({ detail: response.data, sessions: response.data.sessions || [group], ...this.viewData(response.data), title: group.title, eventDate: String(group.event_date).slice(0, 10), status: group.status, checkinStart: activity.clockFromIso(group.checkin_start_at, "08:00"), checkinEnd: activity.clockFromIso(group.checkin_end_at, "22:00") });
      } else {
        const response = await request("/api/v1/wechat/operations/activity-context", { auth: true });
        if (!current()) return;
        const now = new Date();
        this.setData({ organizations: response.data.organizations, classes: response.data.classes || [], activityTypes: response.data.activity_types || activity.ACTIVITY_TYPES, eventDate: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}` });
      }
    } catch (error) { if (current()) this.setData({ errorMessage: error.message }); }
    finally { if (current()) this.setData({ loading: false }); }
  },
  input(event) { const field = event.currentTarget.dataset.field; if (["title", "personName", "personPhone"].includes(field)) { this.setData({ [field]: event.detail.value }); if (field === "personName" || field === "personPhone") this._attendeeKey = key(); } },
  viewData(detail) { const result = activity.decorate(detail, this.data.keyword); return { people: result.attendees, groupRows: result.groupRows, statistics: result.statistics }; },
  chooseType(event) { const typeIndex = Number(event.detail.value); this.setData({ typeIndex, activityType: this.data.activityTypes[typeIndex].value }); this.autoTitle(); },
  chooseClass(event) { this.setData({ classIndex: Number(event.detail.value) }); this.autoTitle(); },
  autoTitle() { const row = this.data.classes[this.data.classIndex]; if (this.data.activityType === "class_meeting" && row && (!this.data.title || this.data.title === this._autoTitle)) { this._autoTitle = row.name + "班级学习会"; this.setData({ title: this._autoTitle }); } },
  search(event) { this.setData({ keyword: event.detail.value }); if (this.data.detail) this.setData(this.viewData(this.data.detail)); },
  switchSession(event) { const id = event.currentTarget.dataset.id; if (this.data.saving || this.data.adding || id === this.data.eventId) return; this.onHide(); showPrivatePage(this); this.setData({ eventId: id }); this.load(); },
  chooseOrg(event) { this.setData({ orgIndex: Number(event.detail.value) }); },
  chooseDate(event) { this.setData({ eventDate: event.detail.value }); },
  chooseTime(event) { const field = event.currentTarget.dataset.field; if (["checkinStart", "checkinEnd"].includes(field)) this.setData({ [field]: event.detail.value }); else if (Object.prototype.hasOwnProperty.call(activity.CLASS_TIMES, field)) this.setData({ classTimes: { ...this.data.classTimes, [field]: event.detail.value } }); },
  async save() {
    if (this.data.saving) return;
    const current = beginPrivateRequest(this, "mutation");
    const group = this.data.detail && this.data.detail.group;
    const selectedClass = this.data.classes[this.data.classIndex];
    const org = this.data.activityType === "class_meeting" ? selectedClass : this.data.organizations[this.data.orgIndex];
    if (!this.data.title.trim() || (!group && !org)) { wx.showToast({ title: "请填写名称并选择组织", icon: "none" }); return; }
    this.setData({ saving: true });
    try {
      if (!this.data.eventId && this.data.activityType === "class_meeting") {
        const confirmation = await new Promise(resolve => wx.showModal({ title: "创建并确认三场班级会", content: `将为${selectedClass.name}创建上午、下午、空巴三个场次，报名以创建时的当前可信在册名单为准。请核对日期、名称及各场时间。`, success: resolve }));
        if (!confirmation.confirm || !current()) return;
      }
      const response = await request(`/api/v1/wechat/operations/activities${this.data.eventId ? "/" + encodeURIComponent(this.data.eventId) : ""}`, { auth: true, method: this.data.eventId ? "PATCH" : "POST", data: { activity_type: group ? group.activity_type || "other" : this.data.activityType, class_org_unit_id: selectedClass && this.data.activityType === "class_meeting" ? selectedClass.id : null, class_times: this.data.classTimes, title: this.data.title, event_date: this.data.eventDate, org_unit_id: group ? group.org_unit_id : org.id, request_id: this._createKey, expected_revision: this.data.detail ? this.data.detail.revision : null, checkin_start: this.data.checkinStart, checkin_end: this.data.checkinEnd } });
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
      wx.showModal({ title: chosen.label, content: `确认将活动设为“${chosen.label}”？${this.data.sessions.length > 1 ? "此操作会影响同一班会的全部场次。" : ""}`, success: async result => {
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
  },
  clearCode() { if (this._codePath) { wx.getFileSystemManager().unlink({ filePath: this._codePath, fail() {} }); this._codePath = ""; } },
  async showCode() {
    if (!this.data.detail || !this.data.detail.can_code) return;
    const current = beginPrivateRequest(this, "code");
    try {
      const response = await request(`/api/v1/wechat/operations/activities/${encodeURIComponent(this.data.eventId)}/entry-code`, { auth: true, method: "POST", data: {} });
      if (!current()) return;
      this.clearCode();
      const filePath = `${wx.env.USER_DATA_PATH}/activity-entry-${Date.now()}-${Math.random().toString(36).slice(2)}.png`;
      await new Promise((resolve, reject) => wx.getFileSystemManager().writeFile({ filePath, data: response.data.image_base64, encoding: "base64", success: resolve, fail: reject }));
      if (!current()) { wx.getFileSystemManager().unlink({ filePath, fail() {} }); return; }
      this._codePath = filePath; this.setData({ codePath: filePath });
    } catch (error) { if (current()) wx.showModal({ title: "签到码未生成", content: error.message || error.errMsg || "请稍后重试", showCancel: false }); }
  },
  async setCheckinState() {
    if (!this.data.detail || this.data.saving) return;
    const current = beginPrivateRequest(this, "confirm");
    const opened = this.data.detail.group.manual_status === "closed" || this.data.detail.group.checkin_status === "closed";
    wx.showModal({ title: opened ? "开放当前场次签到" : "关闭当前场次签到", content: "请核对所选场次。签到日期和时间窗口仍按现有规则校验。", success: result => { if (result.confirm && current()) this.performOperation("checkin-state", { open: opened, expected_revision: this.data.detail.revision }); } });
  },
  async performOperation(action, data) {
    if (this.data.saving) return;
    const current = beginPrivateRequest(this, "mutation");
    this.setData({ saving: true });
    try {
      const response = await request(`/api/v1/wechat/operations/activities/${encodeURIComponent(this.data.eventId)}/${action}`, { auth: true, method: "POST", data });
      if (current()) { this._personIntents = {}; wx.showToast({ title: response.data.message || "已处理", icon: "none" }); await this.load(); }
    } catch (error) { if (current()) wx.showModal({ title: "操作未完成", content: error.message, showCancel: false }); }
    finally { if (current()) this.setData({ saving: false }); }
  },
  syncRoster() { const current = beginPrivateRequest(this, "confirm"); wx.showModal({ title: "增补可信班级名单", content: "只增补未开始场次，保留已有报名与签到。", success: result => { if (result.confirm && current()) this.performOperation("sync-roster", {}); } }); },
  managePerson(event) {
    if (!this.data.detail || this.data.saving) return;
    const row = this.data.detail.attendees.find(row => String(row.registration_id) === String(event.currentTarget.dataset.id));
    if (!row) return;
    const current = beginPrivateRequest(this, "confirm");
    const actions = activity.rowActions(this.data.detail, row);
    if (!actions.length) return;
    wx.showActionSheet({ itemList: actions.map(item => item.label), success: selected => {
      if (!current()) return;
      const choice = actions[selected.tapIndex];
      const name = row.registered_display_name || row.registered_name || row.name;
      wx.showModal({ title: choice.label, content: `确认对“${name}”${choice.label}？${choice.value === "registration_delete" ? "仅删除本场未签到报名。" : ""}`, success: result => {
        if (!result.confirm || !current()) return;
        const intent = this._personIntents || (this._personIntents = {}), intentKey = `${this.data.eventId}:${row.registration_id}:${choice.value}`;
        const requestId = intent[intentKey] || (intent[intentKey] = key());
        this.performOperation("row-action", { action: choice.value, registration_id: String(row.registration_id), expected_checked: Boolean(row.checked || row.checked_at), expected_status: row.attendance_status || "pending", request_id: requestId });
      }});
    }});
  }
});
