const app = getApp();

Page({
  data: { session: null },

  onShow() {
    this.setData({ session: app.globalData.studyMeetingResult || null });
  },

  backHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  }
});
