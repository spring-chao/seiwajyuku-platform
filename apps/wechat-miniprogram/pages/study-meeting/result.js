const app = getApp();

Page({
  data: { session: null, summary: null },

  onShow() {
    const session = app.globalData.studyMeetingResult || null;
    const attendees = session && Array.isArray(session.attendees) ? session.attendees : [];
    const learningContentResults = session && Array.isArray(session.learning_content_results)
      ? session.learning_content_results
      : [];
    const homeCount = attendees.filter(item => item.attendance_type === "HOME_GROUP").length;
    const crossCount = attendees.filter(item => item.attendance_type === "CROSS_GROUP").length;
    this.setData({
      session,
      summary: session ? {
        homeCount,
        crossCount,
        totalCount: attendees.length,
        completedContentCount: learningContentResults.filter(item => item.completed === true).length,
        contentCount: learningContentResults.length
      } : null
    });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
