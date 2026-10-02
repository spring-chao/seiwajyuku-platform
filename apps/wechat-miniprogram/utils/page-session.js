function sessionToken() {
  const data = getApp().globalData;
  return data.personSessionToken || data.memberSessionToken || "";
}

function beginPrivateRequest(page, channel = "load") {
  let token = sessionToken();
  const epoch = page._privateEpoch || 0;
  const versions = page._privateVersions || (page._privateVersions = {});
  const version = versions[channel] = (versions[channel] || 0) + 1;
  const current = () => {
    if ((page._privateEpoch || 0) !== epoch || versions[channel] !== version || page._privateVisible === false) return false;
    if (sessionToken() !== token) {
      page.onHide();
      return false;
    }
    return true;
  };
  // A successful binding installs its own new session synchronously. Subsequent
  // modal callbacks must still reject any later identity replacement.
  current.acceptSession = next => { token = next || ""; };
  return current;
}

function hidePrivatePage(page, reset) {
  page._privateVisible = false;
  page._privateEpoch = (page._privateEpoch || 0) + 1;
  page.setData(reset);
}

function showPrivatePage(page) {
  page._privateVisible = true;
}

module.exports = { sessionToken, beginPrivateRequest, hidePrivatePage, showPrivatePage };
