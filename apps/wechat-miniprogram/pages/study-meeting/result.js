const app = getApp();

Page({
  data: { session: null, summary: null },

  onShow() {
    const session = app.globalData.studyMeetingResult || null;
    const attendees = session && Array.isArray(session.attendees) ? session.attendees : [];
    const homeCount = attendees.filter(item => item.attendance_type === "HOME_GROUP").length;
    const crossCount = attendees.filter(item => item.attendance_type === "CROSS_GROUP").length;
    this.setData({
      session,
      summary: session ? { homeCount, crossCount, totalCount: attendees.length } : null
    });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
