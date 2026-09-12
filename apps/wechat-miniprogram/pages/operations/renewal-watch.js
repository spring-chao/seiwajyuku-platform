const { request } = require("../../utils/request");

Page({
  data: { loading: true, groups: [], items: [], activeStage: "OBSERVE_3", focusMemberId: null, errorMessage: "" },
  onLoad(options) { this.setData({ focusMemberId: Number(options.member_id) || null }); },
  onShow() { this.load(); },
  chooseStage(event) {
    const activeStage = event.currentTarget.dataset.stage;
    const group = this.data.groups.find(item => item.key === activeStage);
    this.setData({ activeStage, items: group ? group.items : [] });
  },
  async load() {
    this.setData({ loading: true, errorMessage: "" });
    try {
      const response = await request("/api/v1/wechat/operations/renewal-watch", { auth: true });
      const groups = (response.data && response.data.groups) || [];
      const selected = groups.find(group => group.key === this.data.activeStage) || groups[0] || { key: "OBSERVE_3", items: [] };
      this.setData({ groups, activeStage: selected.key, items: selected.items || [] });
    } catch (error) {
      this.setData({ errorMessage: error.message || "续费关注暂时无法加载" });
    } finally {
      this.setData({ loading: false });
    }
  },
  openMember(event) {
    const item = event.currentTarget.dataset.item;
    if (!item || !item.member_id) return;
    wx.navigateTo({
      url: `/pages/operations/member-detail?member_id=${encodeURIComponent(item.member_id)}&renewal_cycle_id=${encodeURIComponent(item.cycle_id)}`
    });
  }
});
