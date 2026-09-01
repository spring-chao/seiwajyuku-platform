const app = getApp();
const { request } = require("../../utils/request");
const { normalizeMeetingPlan } = require("../../utils/study-meeting");

function toSelectionMap(ids) {
  return (ids || []).reduce((map, id) => {
    map[String(id)] = true;
    return map;
  }, {});
}

Page({
  data: {
    groupId: "",
    loading: true,
    assignment: null,
    meetingPlanReady: false,
    meetingPlanError: "",
    members: [],
    selected: {},
    crossMembers: [],
    crossSelected: {},
    searchText: "",
    searching: false,
    selectedCount: 0,
    crossSelectedCount: 0,
    showCrossSearch: false,
    errorMessage: ""
  },

  onLoad(options) {
    this.setData({ groupId: (options && options.groupId) || "" });
    this.loadMembers();
  },

  async loadMembers() {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request(`/api/v1/study-meetings/context?group_org_unit_id=${encodeURIComponent(this.data.groupId)}`, { auth: true });
      const context = response.data || {};
      const assignment = context.assignment || {};
      const meetingPlan = normalizeMeetingPlan(
        assignment.meeting_plan,
        assignment.current_cycle && assignment.current_cycle.learning_cycle_index
      );
      const hasCurrentCycle = Boolean(assignment.current_cycle);
      const meetingPlanError = hasCurrentCycle && !meetingPlan
        ? (assignment.meeting_plan_error || "当前学习周期内容配置尚未完成，请联系运营人员。")
        : "";
      const storedDraft = app.globalData.studyMeetingDraft || {};
      const draft = storedDraft.group_org_unit_id === this.data.groupId ? storedDraft : {};
      this.setData({
        assignment,
        meetingPlanReady: Boolean(meetingPlan && hasCurrentCycle),
        meetingPlanError,
        members: assignment.members || [],
        selected: toSelectionMap(draft.member_ids || (assignment.members || []).map(item => item.member_id)),
        crossSelected: toSelectionMap(draft.cross_group_member_ids || []),
        selectedCount: (draft.member_ids || (assignment.members || []).map(item => item.member_id)).length,
        crossSelectedCount: (draft.cross_group_member_ids || []).length,
        loading: false
      });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "本组成员加载失败" });
    }
  },

  toggleMember(event) {
    const id = String(event.currentTarget.dataset.memberId);
    const selected = { ...this.data.selected, [id]: !this.data.selected[id] };
    this.setData({ selected, selectedCount: Object.keys(selected).filter(key => selected[key]).length });
  },

  selectAll() {
    const selected = this.data.members.reduce((map, item) => {
      map[String(item.member_id)] = true;
      return map;
    }, {});
    this.setData({ selected, selectedCount: this.data.members.length });
  },

  clearAll() {
    this.setData({ selected: {}, selectedCount: 0 });
  },

  toggleCrossSearch() {
    this.setData({ showCrossSearch: !this.data.showCrossSearch });
  },

  handleSearch(event) {
    this.setData({ searchText: event.detail.value });
  },

  async searchCross() {
    if (!this.data.searchText.trim()) {
      wx.showToast({ title: "请输入学长姓名", icon: "none" });
      return;
    }
    this.setData({ searching: true });
    try {
      const q = encodeURIComponent(this.data.searchText || "");
      const response = await request(`/api/v1/study-meetings/cross-group-members?group_org_unit_id=${encodeURIComponent(this.data.groupId)}&q=${q}`, { auth: true });
      this.setData({ crossMembers: (response.data && response.data.members) || [] });
    } catch (error) {
      wx.showToast({ title: error.message || "搜索失败", icon: "none" });
    } finally {
      this.setData({ searching: false });
    }
  },

  toggleCross(event) {
    const id = String(event.currentTarget.dataset.memberId);
    const crossSelected = { ...this.data.crossSelected, [id]: !this.data.crossSelected[id] };
    this.setData({ crossSelected, crossSelectedCount: Object.keys(crossSelected).filter(key => crossSelected[key]).length });
  },

  continueSubmit() {
    if (!this.data.meetingPlanReady) {
      wx.showToast({ title: "当前学习周期内容尚未配置", icon: "none" });
      return;
    }
    const memberIds = Object.keys(this.data.selected).filter(id => this.data.selected[id]).map(Number);
    const crossIds = Object.keys(this.data.crossSelected).filter(id => this.data.crossSelected[id]).map(Number);
    if (!memberIds.length && !crossIds.length) {
      wx.showToast({ title: "至少选择一名实际参加学长", icon: "none" });
      return;
    }
    const previousDraft = app.globalData.studyMeetingDraft || {};
    app.globalData.studyMeetingDraft = {
      ...previousDraft,
      group_org_unit_id: this.data.groupId,
      member_ids: memberIds,
      cross_group_member_ids: crossIds
    };
    wx.navigateTo({ url: "/pages/study-meeting/submit" });
  }
});
