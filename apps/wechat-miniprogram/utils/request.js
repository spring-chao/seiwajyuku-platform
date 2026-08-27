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
        const error = new Error(typeof detail === "string" ? detail : "服务暂时不可用，请稍后重试。");
        error.statusCode = response.statusCode;
        reject(error);
      },
      fail(error) {
        reject(new Error(error.errMsg || "网络请求失败"));
      }
    });
  });
}

function uploadPhoto(path, filePath) {
  const baseUrl = (app.globalData.apiBaseUrl || "").replace(/\/$/, "");
  return new Promise((resolve, reject) => wx.uploadFile({
    url: baseUrl + path, filePath, name: "photo", timeout: 30000,
    header: { Authorization: "Bearer " + (app.globalData.memberSessionToken || "") },
    success(response) {
      let data;
      try { data = JSON.parse(response.data); } catch (_) { data = {}; }
      if (response.statusCode >= 200 && response.statusCode < 300) { resolve(data); return; }
      reject(new Error(typeof data.detail === "string" ? data.detail : "合影上传失败，请重试"));
    },
    fail() { reject(new Error("合影上传中断，请重试")); }
  }));
}

module.exports = { request, uploadPhoto };
