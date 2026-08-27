/** Keep actionable HTTP context, never render a server traceback or raw Axios config. */
export function studyMeetingErrorMessage(error: unknown, action = "学习会记录加载"): string {
  const failure = error as { response?: { status?: number; data?: { detail?: unknown } }; code?: string };
  const status = failure?.response?.status;
  const explanations: Record<number, string> = {
    401: "登录已失效，请重新登录当前环境。",
    403: "当前账号没有所需权限或记录不在授权范围，请联系管理员核对权限。",
    404: "当前接口尚未开放或前后端版本不一致，请核对部署环境。",
    500: "服务器处理异常，请由管理员检查后端日志及数据库迁移。",
    502: "后端服务暂不可用，请稍后重试。",
    503: "服务尚未就绪，请稍后重试。",
    504: "后端服务响应超时，请稍后重试。"
  };
  const detail = failure?.response?.data?.detail;
  const safeDetail = typeof detail === "string" && status && status < 500 && detail.length <= 200
    ? detail : "";
  const reason = explanations[status || 0] || safeDetail ||
    (status ? "请求未完成，请核对输入后重试。" : "无法连接 API，请检查网络及当前环境的 API 地址。");
  return `${action}失败${status ? `（HTTP ${status}）` : ""}：${reason}${safeDetail && safeDetail !== reason ? ` 原因：${safeDetail}` : ""}`;
}
