// Match public/index.html showSuccess; render plain text, never engine HTML.
function chinaDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
}

function successCard(result, fallback = {}) {
  const receipt = result.receipt || result.data;
  const data = receipt && Object.keys(receipt).length ? receipt : fallback.registration || {};
  const event = data.event || fallback.event || {};
  const name = data.name || fallback.name || (fallback.member && (fallback.member.name || fallback.member.name_masked)) || "";
  const seats = [];
  if (data.show_group !== "false" && data.group_num) seats.push(`大会现场：第 ${data.group_num} 组`);
  if (data.show_dinner_table !== "false" && data.dinner_table_num) seats.push(`空巴晚宴：第 ${data.dinner_table_num} 桌`);
  const lines = [];
  if (data.event || fallback.event) lines.push(`活动：${event.event_date || ""} · ${event.activity_type_name || ""} · ${event.name || event.title || ""}`);
  if (data.attendance_role === "CROSS_CLASS_MEMBER") {
    lines.push("本次作为外班学长参加");
    if (data.home_class_name) lines.push(`原班级：${data.home_class_name}`);
  }
  if (["COURSE_TEAM_MEMBER", "EVENT_TEAM_MEMBER"].includes(data.attendance_role)) {
    lines.push("本次使用团队报名名额签到");
    if (data.registered_name) lines.push(`报名登记姓名：${data.registered_name}`);
  }
  if (data.group_value) lines.push(`${data.group_type || "分组"}：${data.group_value}`);
  else if (data.class_name) lines.push(`班级：${data.class_name}`);
  else if (data.center) lines.push(`分中心：${data.center}`);
  else if (data.group_name) lines.push(`小组：${data.group_name}`);
  if (data.company) lines.push(`公司：${data.company}`);
  if (data.multi_total > 1) lines.push(`多人报名：已签到 ${data.multi_checked}/${data.multi_total} 人${result.checked_count ? `（本次 ${result.checked_count} 人）` : ""}`);
  if (result.already === true || result.status === "ALREADY_CHECKED_IN") lines.push(`（已签到 · ${chinaDateTime(data.checked_at || result.checked_at)}）`);
  return { welcome: `欢迎 ${name}学长！`, seats, lines: lines.length ? lines : ["签到成功"] };
}

module.exports = { successCard };
