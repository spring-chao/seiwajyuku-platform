const labels = {
  MALE: "男", FEMALE: "女", OTHER: "其他", NORMAL: "普票", SPECIAL: "专票", NONE: "无需开票",
  GE_10_PERCENT: "10%及以上", LT_10_PERCENT: "0%～10%以下", LOSS: "亏损", UNSET: "暂不设定",
  SUBMITTED: "待审核", APPROVED: "审核通过", ENROLLED: "已入塾", REJECTED: "已驳回", CANCELLED: "已取消", PAID: "已确认收款", UNPAID: "待收款"
};
function applicationSections(a, complete = false) {
  const value = (key) => {
    if (key === "phone") return complete ? a.phone || a.phone_masked : a.phone_masked;
    if (["annual_sales", "profit_margin"].includes(key) && !a.financial_fields_visible && a.has_enterprise_financial_data) return "已填写（当前无查看权限）";
    if (key === "rules_acknowledged") return a[key] ? "已确认" : "未确认";
    const v = key === "industry_category" ? a[key] || a.industry : a[key];
    return v === null || v === undefined || v === "" ? "未填写" : labels[v] || String(v);
  };
  const section = (title, fields) => ({ title, fields: fields.map(([key, label]) => ({ key, label, value: value(key) })) });
  return [
    section("基本资料", [["name","姓名"],["phone","联系方式"],["gender","性别"],["birthday","生日"],["district","所在地区"],["political_status","政治面貌"],["social_role","社会任职"],["email","邮箱"],["referrer","推荐人"],["referrer_org_unit_name","推荐人所属分中心/指导团"],["target_shuku_name","申请加入的塾"],["org_unit_name","正式管理组织"],["join_date","入塾日期"]]),
    section("企业资料", [["company_name","企业名称"],["position","职务"],["industry_category","行业大类"],["industry_other","其他行业"],["company_address","企业地址"],["company_products","主要产品或服务"],["employee_count","员工人数"],["annual_sales","年销售额（万）"],["profit_margin","利润率"]]),
    section("开票资料", [["invoice_type","发票类型"],["invoice_title","发票抬头"],["invoice_tax_id","发票税号"],["company_tax_id","企业税号"],["invoice_registered_address","注册地址"],["invoice_phone","注册电话"],["invoice_bank","开户银行"],["invoice_account","银行账号"],["invoice_info","历史开票资料"]]),
    section("学习期望与入塾初心", [["goal_years","计划学习年限（年）"],["revenue_growth_target","业绩提升目标（倍）"],["profit_growth_target","利润提升目标（倍）"],["books_read","所读稻盛和夫著作"],["enrollment_reason_philosophy","认同的哲学理念"],["enrollment_reason_change","期望改变或努力方向"],["enrollment_reason_other","入塾初心补充"],["learning_years_goal","历史学习年限目标"],["learning_participation_goal","学习与活动参与目标"],["business_goal","企业业绩目标"],["other_goal","其他目标"]]),
    section("补充与申请进度", [["notes","补充内容"],["review_note","审核备注"],["application_no","申请编号"],["application_status","审核状态"],["payment_status","收款状态"],["rules_acknowledged","守则与缴费说明"],["created_at","提交时间"],["reviewed_at","审核时间"],["payment_confirmed_at","收款确认时间"],["converted_at","正式入塾时间"]])
  ];
}
const invoke = (wx, method, args = {}, context) => new Promise((resolve, reject) => wx[method]({ ...args, success: resolve, fail: reject }, context));
function imageError(error, stage) {
  const message = String(error && (error.errMsg || error.message) || "");
  if (/privacy|隐私/i.test(message)) return "请先同意小程序隐私授权；如仍失败，请联系管理员核验保存图片的隐私接口声明。";
  if (/auth deny|auth denied|authorize|permission|权限/i.test(message)) return "相册权限未开启，请点击“去设置”允许保存到相册，再点击保存。";
  if (stage === "draw") return "申请图片生成失败，请刷新后重试。资料较长时会分为多张图片，不会截断内容。";
  return "相册保存失败，请检查系统对微信的照片权限及存储空间后重试。";
}
async function drawApplicationImages(wx, page, sections, current) {
  const lines = ["入塾申请资料", ...sections.flatMap(s => [s.title, ...s.fields.map(f => `${f.label}：${f.value}`), ""])];
  const measure = wx.createCanvasContext("applicationSummary", page);
  measure.setFontSize(22);
  const wrapped = [];
  for (const source of lines) for (const line of source.split(/\r?\n/)) {
    let part = "";
    for (const c of line) {
      if (part && measure.measureText(part + c).width > 580) { wrapped.push(part); part = ""; }
      part += c;
    }
    wrapped.push(part);
  }
  const pages = [];
  for (let start = 0; start < wrapped.length; start += 43) pages.push(wrapped.slice(start, start + 43));
  const files = [];
  for (let index = 0; index < pages.length; index++) {
    if (!current()) return files;
    const canvasHeight = Math.max(400, pages[index].length * 34 + 150);
    await new Promise(resolve => page.setData({ canvasHeight }, resolve));
    if (wx.nextTick) await new Promise(resolve => wx.nextTick(resolve));
    if (!current()) return files;
    const ctx = wx.createCanvasContext("applicationSummary", page);
    ctx.setFillStyle("#ffffff"); ctx.fillRect(0, 0, 640, canvasHeight);
    ctx.setFillStyle("#741f2b"); ctx.fillRect(0, 0, 640, 80);
    ctx.setFillStyle("#ffffff"); ctx.setFontSize(25); ctx.fillText(`入塾申请资料 ${index + 1}/${pages.length}`, 28, 50);
    ctx.setFillStyle("#3d2a2a"); ctx.setFontSize(22);
    pages[index].forEach((line, n) => ctx.fillText(line, 28, 125 + n * 34));
    await new Promise(resolve => ctx.draw(false, resolve));
    if (!current()) return files;
    const file = await invoke(wx, "canvasToTempFilePath", { canvasId: "applicationSummary", width: 640, height: canvasHeight, destWidth: 1280, destHeight: canvasHeight * 2, fileType: "png" }, page);
    if (!current()) { wx.getFileSystemManager().unlink({ filePath: file.tempFilePath, fail() {} }); return files; }
    files.push(file.tempFilePath);
    page._imagePaths = files;
  }
  return files;
}
module.exports = { applicationSections, invoke, imageError, drawApplicationImages };
