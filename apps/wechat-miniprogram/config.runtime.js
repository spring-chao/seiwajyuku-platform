const productionConfig = require("./config");
const devConfig = require("./config.dev");

function isDeveloperToolsBuild() {
  try {
    const accountInfo = wx.getAccountInfoSync();
    return accountInfo?.miniProgram?.envVersion === "develop";
  } catch (error) {
    // Node-based checks and trial/release runtimes must keep the production
    // configuration when the WeChat runtime API is unavailable.
    return false;
  }
}

module.exports = isDeveloperToolsBuild()
  ? { ...productionConfig, ...devConfig }
  : productionConfig;
