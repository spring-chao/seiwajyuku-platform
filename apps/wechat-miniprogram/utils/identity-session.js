const app = getApp();
const { request } = require("./request");

function token() {
  return app.globalData.personSessionToken || app.globalData.memberSessionToken || "";
}

function identityChangePending() { return Boolean(app._identityRevokePending || app._identityBindPending); }

async function revokeCurrentBinding(isCurrent = () => true) {
  if (identityChangePending()) throw new Error("正在解除绑定，请稍候");
  const original = token();
  app._identityRevokePending = true;
  try {
    const login = await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }));
    if (!isCurrent() || token() !== original) return false;
    if (!login.code) throw new Error("微信登录凭证获取失败，请重试");
    const response = await request("/api/v1/wechat/member-bindings/revoke", {
      method: "POST", data: { wx_login_code: login.code },
      // Preserve compatibility with the existing token-based server. The new
      // server can recover the current WeChat binding without a cached token.
      header: original ? { Authorization: `Bearer ${original}` } : {}
    });
    if (!response || response.success === false || !response.data || response.data.revoked !== true) {
      throw new Error("服务器未确认解绑，请重试");
    }
    if (token() !== original) return false;
    if (typeof app.clearPersonSession === "function") app.clearPersonSession();
    else app.clearMemberSession();
    return true;
  } catch (error) {
    if (error.statusCode === 401) {
      throw new Error("当前登录已失效，请先用原绑定人员的姓名和手机号恢复登录，再从身份页解除当前微信绑定。");
    }
    throw error;
  } finally { app._identityRevokePending = false; }
}

module.exports = { identityChangePending, revokeCurrentBinding };
