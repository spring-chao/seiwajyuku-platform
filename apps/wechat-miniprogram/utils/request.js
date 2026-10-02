const app = getApp();

function request(path, options = {}) {
  const baseUrl = (app.globalData.apiBaseUrl || "").replace(/\/$/, "");
  const headers = { ...(options.header || {}) };
  if (options.auth && (app.globalData.personSessionToken || app.globalData.memberSessionToken)) {
    headers.Authorization = `Bearer ${app.globalData.personSessionToken || app.globalData.memberSessionToken}`;
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
