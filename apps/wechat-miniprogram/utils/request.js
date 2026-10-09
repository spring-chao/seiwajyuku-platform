const app = getApp();
let initializedCloudEnvironment = "";

function request(path, options = {}) {
  const baseUrl = (app.globalData.apiBaseUrl || "").replace(/\/$/, "");
  const headers = { ...(options.header || {}) };
  if (options.auth && (app.globalData.personSessionToken || app.globalData.memberSessionToken)) {
    headers.Authorization = `Bearer ${app.globalData.personSessionToken || app.globalData.memberSessionToken}`;
  }
  return new Promise((resolve, reject) => {
    const success = response => {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
          return;
        }
        const detail = response.data && response.data.detail;
        const error = new Error(typeof detail === "string" ? detail : "服务暂时不可用，请稍后重试。");
        error.statusCode = response.statusCode;
        reject(error);
    };
    const fail = error => reject(new Error(error.errMsg || "网络请求失败"));
    if (app.globalData.apiTransport === "cloudrun") {
      const environment = app.globalData.cloudbaseEnvironment;
      const service = app.globalData.cloudrunService;
      if (!environment || !service || !wx.cloud || typeof wx.cloud.callContainer !== "function") {
        reject(new Error("云服务连接尚未就绪，请更新微信后重试。"));
        return;
      }
      try {
        if (initializedCloudEnvironment !== environment) {
          wx.cloud.init({ env: environment, traceUser: false });
          initializedCloudEnvironment = environment;
        }
        // The service and environment come from the prepared runtime config.
        // Callers cannot redirect an authenticated request to another service.
        wx.cloud.callContainer({
          config: { env: environment }, path,
          method: options.method || "GET", data: options.data,
          timeout: options.timeout || 20000,
          header: { ...headers, "X-WX-SERVICE": service }
        }).then(success, fail);
      } catch (error) { fail(error); }
      return;
    }
    wx.request({ url: `${baseUrl}${path}`, timeout: 20000, ...options, header: headers, success, fail });
  });
}

async function uploadPhoto(path, filePath) {
  const session = () => app.globalData.personSessionToken || app.globalData.memberSessionToken || "";
  const token = session();
  if (!token) throw new Error("请先绑定学员身份");
  const file = await new Promise((resolve, reject) => wx.getFileSystemManager().readFile({
    filePath, success: resolve, fail: () => reject(new Error("合影读取失败，请重新选择"))
  }));
  if (session() !== token) throw new Error("绑定已变更，请重新进入学习会");
  const bytes = new Uint8Array(file.data);
  if (!bytes.length || bytes.length > 5 * 1024 * 1024) throw new Error("请选择不超过5MB的合影");
  const png = bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71;
  const boundary = `study-photo-${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  const header = `--${boundary}\r\nContent-Disposition: form-data; name="photo"; filename="photo.${png ? "png" : "jpg"}"\r\nContent-Type: image/${png ? "png" : "jpeg"}\r\n\r\n`;
  const trailer = `\r\n--${boundary}--\r\n`;
  const body = new Uint8Array(header.length + bytes.length + trailer.length);
  for (let index = 0; index < header.length; index++) body[index] = header.charCodeAt(index);
  body.set(bytes, header.length);
  for (let index = 0; index < trailer.length; index++) body[header.length + bytes.length + index] = trailer.charCodeAt(index);
  // The existing evidence endpoint accepts the same multipart photo via
  // wx.request's ArrayBuffer support and the configured request legal domain.
  const response = await request(path, {
    method: "POST", auth: true, timeout: 30000, data: body.buffer,
    header: { "content-type": `multipart/form-data; boundary=${boundary}` }
  });
  if (session() !== token) throw new Error("绑定已变更，请重新进入学习会");
  return response;
}

module.exports = { request, uploadPhoto };
