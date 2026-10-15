import type { MemberRosterImportReceipt } from "@/api/seiwajyuku";

export const rosterFieldLabel = (key: string) => ({
  position: "职务", company_name: "公司名称", district: "隶属区",
  company_address: "公司地址", class_committee_name: "班组委名称",
  referrer: "推荐人", referrer_center: "推荐人所属分中心",
  industry_category: "行业分类", industry: "所属行业", company_products: "公司产品",
  birthday: "生日", birth_date: "生日", gender: "性别", join_date: "入塾日期",
  study_start_date: "学习开始日期", membership_years: "会籍年数",
  renewal_month: "续费月份", employee_count: "员工人数", notes: "备注",
  annual_sales: "销售收入（加密）"
}[key] || key);

export function rosterReceiptMessage(receipt: MemberRosterImportReceipt) {
  const prefix = receipt.status === "ALREADY_APPLIED"
    ? "该文件此前已成功导入，本次未重复写入。" : "资料补充已成功保存。";
  if (receipt.created === undefined) return prefix + "该批次未提供可查看的统计明细。";
  return `${prefix}新增 ${receipt.created} 人、更新 ${receipt.updated ?? 0} 人、补充组织关系 ${receipt.relations ?? 0} 条；跳过待复核 ${receipt.skipped_manual_review ?? 0} 条。`;
}

export function rosterImportFailure(error: unknown) {
  const status = (error as { response?: { status?: number } })?.response?.status;
  const rejected = status && [400, 401, 403, 404, 413, 422].includes(status);
  return rejected
    ? { kind: "failed" as const, message: "资料补充失败，本次未保存。请核对文件、权限或登录状态后重新预检。" }
    : { kind: "unknown" as const, message: "未收到明确的导入结果，暂不能确认是否保存成功。请用同一文件重新预检，系统会核验已有批次；不要根据超时判断失败。" };
}

// Save the committed receipt before refreshing. Refresh errors must never be
// reported as a failed write or discard the evidence of a successful import.
export async function applyWithRosterReceipt(
  apply: () => Promise<MemberRosterImportReceipt>,
  save: (receipt: MemberRosterImportReceipt) => void,
  refresh: () => Promise<unknown>
) {
  const receipt = await apply();
  save(receipt);
  try {
    await refresh();
    return false;
  } catch {
    return true;
  }
}
