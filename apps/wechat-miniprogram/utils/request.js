const app = getApp();

function request(path, options = {}) {
  const baseUrl = (app.globalData.apiBaseUrl || "").replace(/\/$/, "");
  const headers = { ...(options.header || {}) };
  if (options.auth && app.globalData.memberSessionToken) {
    headers.Authorization = `Bearer ${app.globalData.memberSessionToken}`;
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${baseUrl}${path}`,
      timeout: 20000,
      ...options,
      header: headers,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
          return;
        }
        const detail = response.data && response.data.detail;
        reject(new Error(typeof detail === "string" ? detail : "服务暂时不可用，请稍后重试。"));
      },
      fail(error) {
        reject(new Error(error.errMsg || "网络请求失败"));
      }
    });
  });
}

module.exports = { request };
