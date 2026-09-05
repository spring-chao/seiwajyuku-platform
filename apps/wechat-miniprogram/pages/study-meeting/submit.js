const app = getApp();
const { request, uploadPhoto } = require("../../utils/request");
const {
  applyLearningContentResults,
  normalizeMeetingPlan,
  requiredLearningContentConfirmed,
  requiredLearningContentComplete,
  serializeLearningContentResults
} = require("../../utils/study-meeting");

function localDateString() {
  const now = new Date();
  return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-");
}

Page({
  data: {
    loading: true, submitting: false, choosingPhoto: false, assignment: null,
    meetingPlanReady: false, meetingPlanError: "", meetingSteps: [],
    learningContentResults: [], requiredContentCount: 0, completedRequiredCount: 0,
    allRequiredContentConfirmed: true, allRequiredContentCompleted: true, homeCount: 0, crossCount: 0, totalCount: 0,
    crossSummary: "", errorMessage: "", photoPath: "", evidenceEnabled: false
  },

  onLoad() { this.loadContext(); },

  async loadContext() {
    const draft = app.globalData.studyMeetingDraft || {};
    if (!draft.group_org_unit_id) {
      this.setData({ loading: false, errorMessage: "登记信息已过期，请重新选择小组。" });
      return;
    }
    try {
      const response = await request("/api/v1/study-meetings/context?group_org_unit_id=" + encodeURIComponent(draft.group_org_unit_id), { auth: true });
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
      const homeCount = (draft.member_ids || []).length;
      const crossCount = (draft.cross_group_member_ids || []).length;
      const learningContentResults = applyLearningContentResults(
        meetingPlan ? meetingPlan.learningContents : [],
        draft.learning_content_results
      );
      const requiredContentCount = learningContentResults.filter(item => item.required).length;
      const completedRequiredCount = learningContentResults.filter(item => item.required && item.completed).length;
      const allRequiredContentConfirmed = requiredLearningContentConfirmed(learningContentResults);
      this.setData({ assignment,
        meetingPlanReady: Boolean(meetingPlan && hasCurrentCycle),
        meetingPlanError, meetingSteps: meetingPlan ? meetingPlan.steps : [],
        learningContentResults, requiredContentCount, completedRequiredCount,
        allRequiredContentConfirmed,
        allRequiredContentCompleted: requiredLearningContentComplete(learningContentResults),
        homeCount, crossCount, totalCount: homeCount + crossCount,
        crossSummary: crossCount ? "，其他小组 " + crossCount + " 人" : "",
        evidenceEnabled: context.evidence_enabled === true, loading: false });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "学习会信息加载失败" });
    }
  },

  setLearningContentCompletion(event) {
    if (this.data.submitting) return;
    const contentKey = String(event.currentTarget.dataset.contentKey || "");
    const completed = event.currentTarget.dataset.completed === "yes";
    const learningContentResults = this.data.learningContentResults.map(item =>
      item.contentKey === contentKey ? { ...item, completed, confirmed: true } : item
    );
    this.setData({
      learningContentResults,
      completedRequiredCount: learningContentResults.filter(item => item.required && item.completed).length,
      allRequiredContentConfirmed: requiredLearningContentConfirmed(learningContentResults),
      allRequiredContentCompleted: requiredLearningContentComplete(learningContentResults)
    });
    const draft = app.globalData.studyMeetingDraft || {};
    app.globalData.studyMeetingDraft = {
      ...draft,
      learning_content_results: serializeLearningContentResults(learningContentResults)
    };
  },

  openLearningContent(event) {
    const label = event.currentTarget.dataset.label || "扫码打开学习内容";
    wx.showToast({ title: `${label}请使用对应二维码`, icon: "none" });
  },

  async choosePhoto() {
    if (this.data.submitting || this.data.choosingPhoto) return;
    this.setData({ choosingPhoto: true });
    try {
      const chosen = await new Promise((resolve, reject) => wx.chooseMedia({
        count: 1, mediaType: ["image"], sourceType: ["album", "camera"], sizeType: ["compressed"],
        success: resolve, fail: reject
      }));
      const compressed = await new Promise((resolve, reject) => wx.compressImage({
        src: chosen.tempFiles[0].tempFilePath, quality: 75, compressedWidth: 1920,
        success: resolve, fail: reject
      }));
      const info = await new Promise((resolve, reject) => wx.getFileSystemManager().getFileInfo({
        filePath: compressed.tempFilePath, success: resolve, fail: reject
      }));
      if (info.size > 5 * 1024 * 1024) throw new Error("合影超过5MB，请选择较小的图片");
      this.setData({ photoPath: compressed.tempFilePath });
    } catch (error) {
      if (!String(error.errMsg || "").includes("cancel")) {
        wx.showToast({ title: error.message || "合影处理失败，请重新选择", icon: "none" });
      }
    } finally { this.setData({ choosingPhoto: false }); }
  },

  previewPhoto() {
    if (this.data.photoPath) wx.previewImage({ urls: [this.data.photoPath], current: this.data.photoPath });
  },

  async submit() {
    if (this.data.submitting || this.data.choosingPhoto) return;
    if (!this.data.meetingPlanReady) {
      wx.showToast({ title: "当前学习周期内容尚未配置", icon: "none" }); return;
    }
    if (!this.data.allRequiredContentConfirmed) {
      wx.showToast({ title: "请先确认本期必学是否已完成", icon: "none" }); return;
    }
    if (!this.data.allRequiredContentCompleted) {
      wx.showToast({ title: "请先确认本期必学内容已完成", icon: "none" }); return;
    }
    if (!this.data.photoPath) {
      wx.showToast({ title: "请先拍摄或选择一张合影", icon: "none" }); return;
    }
    if (!this.data.evidenceEnabled) {
      wx.showToast({ title: "合影功能尚未开启，请联系运营人员", icon: "none" }); return;
    }
    const draft = app.globalData.studyMeetingDraft || {};
    const payload = {
      group_org_unit_id: draft.group_org_unit_id, meeting_date: localDateString(),
      member_ids: draft.member_ids || [], cross_group_member_ids: draft.cross_group_member_ids || []
    };
    const fingerprint = JSON.stringify(payload);
    this.setData({ submitting: true });
    try {
      // Retry the same draft after upload/submit failure; never silently submit twice.
      if (draft.sessionId && draft.payloadFingerprint !== fingerprint) {
        const existing = await request("/api/v1/study-meetings/" + draft.sessionId, { auth: true });
        if (existing.data.status === "SUBMITTED") { this.complete(existing.data); return; }
        delete draft.sessionId; delete draft.uploadedPhotoPath;
      }
      if (!draft.sessionId) {
        const created = await request("/api/v1/study-meetings", { method: "POST", auth: true, data: payload });
        draft.sessionId = created.data.id;
        draft.payloadFingerprint = fingerprint;
        app.globalData.studyMeetingDraft = draft;
      } else {
        const existing = await request("/api/v1/study-meetings/" + draft.sessionId, { auth: true });
        if (existing.data.status === "SUBMITTED") { this.complete(existing.data); return; }
        if (!existing.data.evidence) delete draft.uploadedPhotoPath;
      }
      if (draft.uploadedPhotoPath !== this.data.photoPath) {
        await uploadPhoto("/api/v1/study-meetings/" + draft.sessionId + "/evidence", this.data.photoPath);
        draft.uploadedPhotoPath = this.data.photoPath;
      }
      const submitted = await request("/api/v1/study-meetings/" + draft.sessionId + "/submit", { method: "POST", auth: true });
      this.complete(submitted.data);
    } catch (error) {
      wx.showToast({ title: error.message || "提交失败，请稍后重试", icon: "none", duration: 2600 });
    } finally { this.setData({ submitting: false }); }
  },

  complete(session) {
    app.globalData.studyMeetingResult = {
      ...session,
      learning_content_results: serializeLearningContentResults(this.data.learningContentResults)
    };
    app.globalData.studyMeetingDraft = null;
    wx.redirectTo({ url: "/pages/study-meeting/result" });
  }
});
