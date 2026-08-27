const app = getApp();
const { request, uploadPhoto } = require("../../utils/request");

function localDateString() {
  const now = new Date();
  return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-");
}

Page({
  data: {
    loading: true, submitting: false, choosingPhoto: false, assignment: null,
    courses: [], visibleCourses: [], selectedCourseKeys: [], courseSearch: "",
    hasCourse: false, homeCount: 0, crossCount: 0, totalCount: 0,
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
      const homeCount = (draft.member_ids || []).length;
      const crossCount = (draft.cross_group_member_ids || []).length;
      const courses = (context.courses || []).map(course => ({
        ...course, selected: false,
        creditLabel: course.status === "CONFIGURED" ? course.credit_points + "学分" : "学分待配置"
      }));
      this.setData({ assignment: context.assignment, courses, visibleCourses: courses,
        homeCount, crossCount, totalCount: homeCount + crossCount,
        crossSummary: crossCount ? "，其他小组 " + crossCount + " 人" : "",
        evidenceEnabled: context.evidence_enabled === true, loading: false });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "课程配置加载失败" });
    }
  },

  handleCourseSwitch(event) {
    if (this.data.submitting) return;
    const hasCourse = String(event.currentTarget.dataset.value) === "true";
    this.setData({ hasCourse });
    if (!hasCourse) {
      this.setData({ selectedCourseKeys: [], courses: this.data.courses.map(item => ({ ...item, selected: false })) });
      this.filterCourses();
    }
  },

  toggleCourse(event) {
    if (this.data.submitting) return;
    const key = event.currentTarget.dataset.key;
    const courses = this.data.courses.map(item => item.course_key === key ? { ...item, selected: !item.selected } : item);
    this.setData({ courses, selectedCourseKeys: courses.filter(item => item.selected).map(item => item.course_key) });
    this.filterCourses();
  },

  searchCourses(event) {
    this.setData({ courseSearch: event.detail.value });
    this.filterCourses();
  },

  filterCourses() {
    const term = this.data.courseSearch.trim();
    this.setData({ visibleCourses: this.data.courses.filter(item => !term || item.course_name.includes(term)) });
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
    if (this.data.hasCourse && !this.data.selectedCourseKeys.length) {
      wx.showToast({ title: "请选择本次观看的课程", icon: "none" }); return;
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
      member_ids: draft.member_ids || [], cross_group_member_ids: draft.cross_group_member_ids || [],
      has_course: this.data.hasCourse, course_keys: this.data.hasCourse ? this.data.selectedCourseKeys : []
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
    app.globalData.studyMeetingResult = session;
    app.globalData.studyMeetingDraft = null;
    wx.redirectTo({ url: "/pages/study-meeting/result" });
  }
});
