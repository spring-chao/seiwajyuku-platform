/** Report the failed section without exposing transport config or backend traces. */
export function creditSettlementErrorMessage(error: unknown, action: string, readOnly = false): string {
  const failure = error as {
    response?: { status?: number; data?: { detail?: unknown } };
    code?: string;
    message?: string;
  };
  const status = failure?.response?.status;
  const detail = failure?.response?.data?.detail;
  const inputMessages = new Set([
    "学员 ID 必须为正整数",
    "请选择班级和完整的每周统计日期；跨月周分段导出",
    "请补全临时暂停学员和日期，或移除空行",
    "所选区间没有有效班级名册",
    "请先选择班级",
    "请选择班级，并填写至少4字的来源确认说明",
    "请选择本次学习日并填写实际班会开始时间",
    "请先选择填写后的.xlsx表格"
  ]);
  if (!status && inputMessages.has(failure?.message || "")) return failure.message!;
  if (status === 503 && typeof detail === "object" && detail !== null &&
      "code" in detail && detail.code === "CREDIT_LEDGER_SCHEMA_UNAVAILABLE") {
    return "学分账本期间结构尚未就绪，请联系管理员完成升级后再使用账本。";
  }
  const reasons: Record<number, string> = {
    401: "登录已失效，请重新登录。",
    403: "当前账号没有所需权限或记录不在授权范围。",
    404: "接口尚未开放或前后端版本不一致。",
    500: "服务器处理异常，请联系管理员检查后端日志和数据库结构。",
    502: "后端服务暂不可用，请稍后重试。",
    503: "后端服务尚未就绪，请稍后重试。",
    504: "后端服务响应超时，请稍后重试。"
  };
  const safeDetail = status && status < 500 && typeof detail === "string" &&
    detail.length <= 200 && !/Traceback|<\/?\w|password|secret|token/i.test(detail)
      ? detail : "";
  const timedOut = !status && ["ECONNABORTED", "ETIMEDOUT"].includes(failure?.code || "");
  const reason = safeDetail || reasons[status || 0] || (timedOut ?
    (readOnly ? "导出响应超时，未能下载文件；可稍后重试或选择班级缩小范围，本次不会修改学分。" : "学分接口响应超时，结果可能已保存；请先核对处理结果，再决定是否重试。") : "") ||
    (status ? "请求未完成，请核对输入后重试。" :
      "无法连接学分接口，请检查网络；若其他页面正常，请联系管理员检查该接口。");
  return `${action}${status ? `（HTTP ${status}）` : ""}：${reason}`;
}
