Page({
  data: { document: null, activeSection: "" },

  onLoad() {
    const channel = this.getOpenerEventChannel();
    if (channel && typeof channel.on === "function") {
      channel.on("joiningRules", document => {
        if (!this._closed && document && document.scopeOrgUnitId && Array.isArray(document.sections)) {
          this.setData({ document });
        }
      });
    }
  },

  onUnload() { this._closed = true; },

  goToSection(event) {
    const id = event.currentTarget.dataset.section;
    if (!this.data.document || !this.data.document.sections.some(section => section.id === id)) return;
    this.setData({ activeSection: "section-" + id });
  },

  copyPublicInfo(event) {
    const value = event.currentTarget.dataset.value;
    if (value) wx.setClipboardData({ data: String(value) });
  },

  returnToForm() { wx.navigateBack(); }
});
