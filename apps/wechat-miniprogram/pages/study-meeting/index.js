const app = getApp();
const { request } = require("../../utils/request");
const {
  applyLearningContentResults,
  normalizeMeetingPlan,
  serializeLearningContentResults
} = require("../../utils/study-meeting");

Page({
  data: {
    loading: true,
    context: null,
    assignment: null,
    meetingSteps: [],
    learningContents: [],
    learningContentResults: [],
    meetingPlanReady: false,
    meetingPlanError: "",
    errorMessage: "",
    selectedGroupId: ""
  },

  onShow() {
    this.loadContext();
  },

  async loadContext(groupId) {
    this.setData({
      loading: true,
      errorMessage: "",
      context: null,
      assignment: null,
      meetingSteps: [],
      learningContents: [],
      learningContentResults: [],
      meetingPlanReady: false,
      meetingPlanError: ""
    });
    try {
      const suffix = groupId ? `?group_org_unit_id=${encodeURIComponent(groupId)}` : "";
      const response = await request(`/api/v1/study-meetings/context${suffix}`, { auth: true });
      const context = response.data || {};
      const selected = groupId || context.selected_group_org_unit_id || "";
      const assignment = context.assignment || null;
      const meetingPlan = assignment && normalizeMeetingPlan(
        assignment.meeting_plan,
        assignment.current_cycle && assignment.current_cycle.learning_cycle_index
      );
      const hasCurrentCycle = Boolean(assignment && assignment.current_cycle);
      const meetingPlanError = hasCurrentCycle && !meetingPlan
        ? (assignment.meeting_plan_error || "当前学习周期内容配置尚未完成，请联系运营人员。")
        : "";
      const draft = appDraftForGroup(selected);
      const learningContents = meetingPlan ? meetingPlan.learningContents : [];
      const learningContentResults = applyLearningContentResults(
        learningContents,
        draft.learning_content_results
      );
      this.setData({
        context,
        assignment,
        meetingSteps: meetingPlan ? meetingPlan.steps : [],
        learningContents,
        learningContentResults,
        meetingPlanReady: Boolean(meetingPlan && hasCurrentCycle),
        meetingPlanError,
        selectedGroupId: selected,
        loading: false
      });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "学习会入口暂时不可用" });
    }
  },

  chooseGroup(event) {
    const groupId = event.currentTarget.dataset.groupId;
    this.loadContext(groupId);
  },

  toggleLearningContent(event) {
    const contentKey = String(event.currentTarget.dataset.contentKey || "");
    const learningContentResults = this.data.learningContentResults.map(item =>
      item.contentKey === contentKey ? { ...item, completed: !item.completed } : item
    );
    this.setData({ learningContentResults });
    saveLearningContentResults(this.data.selectedGroupId, learningContentResults);
  },

  openLearningContent(event) {
    const label = event.currentTarget.dataset.label || "扫码打开学习内容";
    wx.showToast({ title: `${label}请使用对应二维码`, icon: "none" });
  },

  openMembers() {
    if (!this.data.selectedGroupId) {
      wx.showToast({ title: "请选择本次学习会小组", icon: "none" });
      return;
    }
    if (!this.data.meetingPlanReady) {
      wx.showToast({ title: "当前学习周期内容尚未配置", icon: "none" });
      return;
    }
    saveLearningContentResults(this.data.selectedGroupId, this.data.learningContentResults);
    wx.navigateTo({
      url: `/pages/study-meeting/members?groupId=${encodeURIComponent(this.data.selectedGroupId)}`
    });
  }
});

function appDraftForGroup(groupId) {
  const draft = app.globalData.studyMeetingDraft || {};
  return draft.group_org_unit_id === groupId ? draft : {};
}

function saveLearningContentResults(groupId, results) {
  if (!groupId) return;
  const previous = appDraftForGroup(groupId);
  app.globalData.studyMeetingDraft = {
    ...previous,
    group_org_unit_id: groupId,
    learning_content_results: serializeLearningContentResults(results)
  };
}
