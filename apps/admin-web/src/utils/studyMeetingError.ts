/** Keep actionable HTTP context, never render a server traceback or raw Axios config. */
export function studyMeetingErrorMessage(error: unknown, action = "学习会记录加载"): string {
  const failure = error as { response?: { status?: number; data?: { detail?: unknown } }; code?: string };
  const status = failure?.response?.status;
  if (!status && ["ECONNABORTED", "ETIMEDOUT"].includes(failure?.code || "")) {
    return `${action}超时：${action === "学习会导出" ? "生成文件耗时较长，请选择班级或缩小日期范围后重试。" : "服务响应较慢，请稍后重试。"}`;
  }
  if (failure?.code === "ERR_CANCELED") return `${action}已取消，可重新操作。`;
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
  const safeDetail = typeof detail === "string" && status && status < 500 && detail.length <= 200 &&
    !/[<>\u0000-\u001f]|Traceback|Authorization|password|token\s*[:=]/i.test(detail)
    ? detail : "";
  const reason = explanations[status || 0] || safeDetail ||
    (status ? "请求未完成，请核对输入后重试。" : "暂时无法连接服务，请检查网络后重试。");
  return `${action}失败${status ? `（HTTP ${status}）` : ""}：${reason}${safeDetail && safeDetail !== reason ? ` 原因：${safeDetail}` : ""}`;
}

/** Axios receives JSON errors as Blob when downloading Excel. Decode only a
 * small error body and retain HTTP context; never render HTML or raw config. */
export async function studyMeetingExportErrorMessage(error: unknown): Promise<string> {
  const failure = error as { response?: { status?: number; data?: unknown }; code?: string };
  const data = failure?.response?.data;
  if (data instanceof Blob && data.size <= 65536) {
    try {
      const payload = JSON.parse(await data.text());
      return studyMeetingErrorMessage({ code: failure.code, response: { status: failure.response?.status, data: payload } }, "学习会导出");
    } catch { /* Use the HTTP category when the body is HTML or unreadable. */ }
  }
  if (failure instanceof Error && failure.message === "INVALID_STUDY_MEETING_WORKBOOK") {
    return "学习会导出失败：未收到有效的 Excel 文件，请稍后重试。";
  }
  return studyMeetingErrorMessage(error, "学习会导出");
}

/** Do not download a gateway error page or an empty response as .xlsx. */
export async function validateStudyMeetingWorkbook(blob: Blob): Promise<void> {
  if (!(blob instanceof Blob) || blob.size < 4) throw new Error("INVALID_STUDY_MEETING_WORKBOOK");
  const signature = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
  if (signature[0] !== 0x50 || signature[1] !== 0x4b || signature[2] !== 3 || signature[3] !== 4) {
    throw new Error("INVALID_STUDY_MEETING_WORKBOOK");
  }
}
