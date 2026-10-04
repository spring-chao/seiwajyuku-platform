const { sessionToken, beginPrivateRequest, hidePrivatePage, showPrivatePage } = require("../../utils/page-session");
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
    crossSummary: "", errorMessage: "", photoPath: "", evidenceEnabled: false,
    photoLoaded: false, photoPreviewError: false, photoUploadState: "", photoFeedback: "",
    submitStage: "", submitError: ""
  },

  onLoad() { this.loadContext(); },

  onShow() { showPrivatePage(this); if (this._shown) this.loadContext(); this._shown = true; },
  onHide() { hidePrivatePage(this, { loading: false, submitting: false, choosingPhoto: false, assignment: null, meetingPlanReady: false, meetingSteps: [], learningContentResults: [], homeCount: 0, crossCount: 0, totalCount: 0, crossSummary: "", photoPath: "", evidenceEnabled: false, photoLoaded: false, photoPreviewError: false, photoUploadState: "", photoFeedback: "", submitStage: "", submitError: "" }); },
  onUnload() { this._unloaded = true; this._photoChoice = null; this.onHide(); },

  async loadContext() {
    const current = beginPrivateRequest(this);
    const draft = app.globalData.studyMeetingDraft || {};
    if (!draft.group_org_unit_id) {
      this.setData({ loading: false, errorMessage: "登记信息已过期，请重新选择小组。" });
      return;
    }
    const choosingPhoto = Boolean(this._photoChoice && this._photoChoice.token === sessionToken() && this._photoChoice.draft === draft);
    this.setData({ loading: true, errorMessage: "", choosingPhoto });
    try {
      const response = await request("/api/v1/study-meetings/context?group_org_unit_id=" + encodeURIComponent(draft.group_org_unit_id), { auth: true });
      if (!current()) return;
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
      this.restorePhoto(draft);
    } catch (error) {
      if (current()) this.setData({ loading: false, errorMessage: error.message || "学习会信息加载失败" });
    }
  },

  setLearningContentCompletion(event) {
    if (this.data.submitting || this.data.choosingPhoto) return;
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
    const draft = app.globalData.studyMeetingDraft;
    if (!draft || !draft.group_org_unit_id || this._unloaded || this._privateVisible === false) return;
    // Camera/album can hide this page. Only this local selection may finish
    // while hidden; network requests still use the normal private-page guard.
    const choice = { draft, token: sessionToken() };
    this._photoChoice = choice;
    delete draft.photoSelectionError;
    const current = () => this.photoChoiceCurrent(choice);
    this.setData({ choosingPhoto: true, photoFeedback: "" });
    try {
      const chosen = await new Promise((resolve, reject) => wx.chooseMedia({
        count: 1, mediaType: ["image"], sourceType: ["album", "camera"], sizeType: ["compressed"],
        success: resolve, fail: reject
      }));
      if (!current()) return;
      const compressed = await new Promise((resolve, reject) => wx.compressImage({
        src: chosen.tempFiles[0].tempFilePath, quality: 75, compressedWidth: 1920,
        success: resolve, fail: reject
      }));
      if (!current()) return;
      const info = await new Promise((resolve, reject) => wx.getFileSystemManager().getFileInfo({
        filePath: compressed.tempFilePath, success: resolve, fail: reject
      }));
      if (!current()) return;
      if (!info.size) throw new Error("合影文件为空，请重新选择");
      if (info.size > 5 * 1024 * 1024) throw new Error("合影超过5MB，请选择较小的图片");
      // In-memory, person-scoped draft only. App clears it on identity change.
      draft.photoPath = compressed.tempFilePath;
      delete draft.uploadedPhotoPath;
      if (this._privateVisible !== false) {
        this.setData({ photoLoaded: false, photoPreviewError: false, photoFeedback: "", submitError: "" });
        this.restorePhoto(draft);
      }
    } catch (error) {
      if (current() && !String(error.errMsg || "").includes("cancel")) {
        // Also keep errors that arrive before onShow, without restoring hidden UI.
        draft.photoSelectionError = error.message || "合影处理失败，请重新选择";
        if (this._privateVisible !== false) this.restorePhoto(draft);
      }
    } finally {
      const valid = current();
      if (this._photoChoice === choice) this._photoChoice = null;
      if (valid && this._privateVisible !== false) this.setData({ choosingPhoto: false });
    }
  },

  photoChoiceCurrent(choice) {
    if (!choice || this._unloaded || this._photoChoice !== choice) return false;
    if (sessionToken() !== choice.token) { this.onHide(); return false; }
    return app.globalData.studyMeetingDraft === choice.draft;
  },

  restorePhoto(draft) {
    if (this._privateVisible === false || this._unloaded || app.globalData.studyMeetingDraft !== draft) return;
    const photoPath = draft.photoPath || "";
    this.setData({ photoPath,
      photoUploadState: photoPath ? (draft.uploadedPhotoPath === photoPath ? "uploaded" : "selected") : "",
      photoFeedback: draft.photoSelectionError || "" });
  },

  photoLoaded(event) {
    if (this._privateVisible === false || event.currentTarget.dataset.path !== this.data.photoPath) return;
    this.setData({ photoLoaded: true, photoPreviewError: false });
  },

  photoLoadFailed(event) {
    if (this._privateVisible === false || event.currentTarget.dataset.path !== this.data.photoPath) return;
    this.setData({ photoLoaded: false, photoPreviewError: true });
  },

  previewPhoto() {
    if (this.data.submitting || this.data.choosingPhoto || this.data.photoPreviewError) return;
    if (this.data.photoPath) wx.previewImage({ urls: [this.data.photoPath], current: this.data.photoPath });
  },

  async submit() {
    if (this.data.submitting || this.data.choosingPhoto) return;
    const current = beginPrivateRequest(this, "submit");
    if (!current()) return;
    if (!this.data.meetingPlanReady) {
      wx.showToast({ title: "当前学习周期内容尚未配置", icon: "none" }); return;
    }
    if (!this.data.photoPath) {
      wx.showToast({ title: "请先拍摄或选择一张合影", icon: "none" }); return;
    }
    if (this.data.photoPreviewError) {
      wx.showToast({ title: "合影预览失败，请重新选择", icon: "none" }); return;
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
    this.setData({ submitting: true, submitStage: "正在准备登记…", submitError: "", photoFeedback: "" });
    try {
      // Retry the same draft after upload/submit failure; never silently submit twice.
      if (draft.sessionId && draft.payloadFingerprint !== fingerprint) {
        const existing = await request("/api/v1/study-meetings/" + draft.sessionId, { auth: true });
        if (!current()) return;
        if (existing.data.status === "SUBMITTED") { this.complete(existing.data); return; }
        delete draft.sessionId; delete draft.uploadedPhotoPath;
      }
      if (!draft.sessionId) {
        const created = await request("/api/v1/study-meetings", { method: "POST", auth: true, data: payload });
        if (!current()) return;
        draft.sessionId = created.data.id;
        draft.payloadFingerprint = fingerprint;
        app.globalData.studyMeetingDraft = draft;
      } else {
        const existing = await request("/api/v1/study-meetings/" + draft.sessionId, { auth: true });
        if (!current()) return;
        if (existing.data.status === "SUBMITTED") { this.complete(existing.data); return; }
        if (!existing.data.evidence) delete draft.uploadedPhotoPath;
      }
      if (draft.uploadedPhotoPath !== this.data.photoPath) {
        this.setData({ photoUploadState: "uploading", submitStage: "正在上传合影…" });
        await uploadPhoto("/api/v1/study-meetings/" + draft.sessionId + "/evidence", this.data.photoPath);
        if (!current()) return;
        draft.uploadedPhotoPath = this.data.photoPath;
      }
      this.setData({ photoUploadState: "uploaded", submitStage: "正在提交登记…" });
      const submitted = await request("/api/v1/study-meetings/" + draft.sessionId + "/submit", { method: "POST", auth: true });
      if (current()) this.complete(submitted.data);
    } catch (error) {
      if (current()) {
        const uploadFailed = this.data.photoUploadState === "uploading";
        this.setData({
          photoUploadState: uploadFailed ? "failed" : this.data.photoUploadState,
          photoFeedback: uploadFailed ? "已保留这张合影，请检查网络后重试提交。" : "",
          submitError: uploadFailed ? "合影上传失败，本次登记尚未提交。" : this.data.photoUploadState === "uploaded"
            ? "合影已上传，本次登记尚未完成，请重试提交。" : "本次登记尚未提交成功，请重试。"
        });
        wx.showToast({ title: error.message || "提交失败，请稍后重试", icon: "none", duration: 2600 });
      }
    } finally { if (current()) this.setData({ submitting: false, submitStage: "" }); }
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
