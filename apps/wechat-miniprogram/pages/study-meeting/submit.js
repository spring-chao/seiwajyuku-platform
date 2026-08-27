const app = getApp();
const { request } = require("../../utils/request");

function localDateString() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

Page({
  data: {
    loading: true,
    submitting: false,
    assignment: null,
    courses: [],
    hasCourse: false,
    courseIndex: -1,
    courseName: "",
    errorMessage: ""
  },

  onLoad() {
    this.loadContext();
  },

  async loadContext() {
    const draft = app.globalData.studyMeetingDraft || {};
    if (!draft.group_org_unit_id) {
      this.setData({ loading: false, errorMessage: "登记信息已过期，请重新选择小组。" });
      return;
    }
    try {
      const response = await request(`/api/v1/study-meetings/context?group_org_unit_id=${encodeURIComponent(draft.group_org_unit_id)}`, { auth: true });
      const context = response.data || {};
      this.setData({
        assignment: context.assignment,
        courses: context.courses || [],
        loading: false
      });
    } catch (error) {
      this.setData({ loading: false, errorMessage: error.message || "课程配置加载失败" });
    }
  },

  handleCourseSwitch(event) {
    const hasCourse = event.currentTarget.dataset.value === "true" || event.currentTarget.dataset.value === true;
    this.setData({ hasCourse, courseIndex: hasCourse ? this.data.courseIndex : -1, courseName: hasCourse ? this.data.courseName : "" });
  },

  handleCourseChange(event) {
    const index = Number(event.detail.value);
    const course = this.data.courses[index];
    this.setData({ courseIndex: index, courseName: course ? course.course_name : "" });
  },

  async submit() {
    const draft = app.globalData.studyMeetingDraft || {};
    if (this.data.hasCourse && this.data.courseIndex < 0) {
      wx.showToast({ title: "请选择本次观看的课程", icon: "none" });
      return;
    }
    const course = this.data.courses[this.data.courseIndex];
    this.setData({ submitting: true });
    try {
      const created = await request("/api/v1/study-meetings", {
        method: "POST",
        auth: true,
        data: {
          group_org_unit_id: draft.group_org_unit_id,
          meeting_date: localDateString(),
          member_ids: draft.member_ids || [],
          cross_group_member_ids: draft.cross_group_member_ids || [],
          has_course: this.data.hasCourse,
          course_key: course ? course.course_key : null
        }
      });
      const session = created.data;
      const submitted = await request(`/api/v1/study-meetings/${session.id}/submit`, { method: "POST", auth: true });
      app.globalData.studyMeetingResult = submitted.data || session;
      app.globalData.studyMeetingDraft = null;
      wx.redirectTo({ url: "/pages/study-meeting/result" });
    } catch (error) {
      wx.showToast({ title: error.message || "提交失败，请稍后重试", icon: "none", duration: 2600 });
    } finally {
      this.setData({ submitting: false });
    }
  }
});
