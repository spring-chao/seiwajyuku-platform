export const activityTypes = [
  ["course", "课程"],
  ["class_meeting", "班级学习会（三场）"],
  ["group_meeting", "小组学习会"],
  ["national_report", "全国报告会"],
  ["center_quarterly_report", "分中心季度报告会"],
  ["staff_training", "班主任辅导员培训会"],
  ["board_meeting", "理事会"],
  ["study_tour", "游学"],
  ["other", "其他"]
];
export const lifecycleLabels = {
  DRAFT: "草稿",
  CONFIRMED: "已确认",
  CANCELLED: "已取消"
};
export const sessionLabels = {
  MORNING: "上午",
  AFTERNOON: "下午",
  KONPA: "晚上空巴"
};
export const checkinLabels = {
  upcoming: "尚未开放",
  open: "签到开放",
  closed: "已关闭",
  ended: "已结束",
  draft: "草稿",
  cancelled: "已取消"
};

/** ID selection is explicit. Never infer organization membership from names. */
export function rosterSelection(option, scope = "class") {
  if (!option?.id) throw new Error("请选择可信组织名单");
  const classId =
    scope === "group"
      ? option.parent_id || option.class_org_unit_id
      : option.id;
  if (!classId) throw new Error("小组缺少可信班级组织 ID，请先修正组织关系");
  return {
    scope,
    org_unit_id: option.id,
    class_org_unit_id: classId,
    group_org_unit_id: scope === "group" ? option.id : "",
    center: option.center || option.center_name || "",
    center_name: option.center || option.center_name || "",
    class_name:
      option.class_name || (scope === "class" ? option.name : "") || "",
    group_name: scope === "group" ? option.group_name || option.name || "" : ""
  };
}

/** Format stored UTC timestamps in the platform's operating timezone. */
export function chinaLocalDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const get = type => parts.find(part => part.type === type)?.value;
  return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}

export function canMaintainRegistration(event) {
  return Boolean(
    event &&
    event.lifecycle_status !== "CANCELLED" &&
    ["upcoming", "open"].includes(event.checkin_status)
  );
}

export function canConfirmManually(event) {
  return Boolean(
    event &&
    event.lifecycle_status === "CONFIRMED" &&
    event.checkin_status === "open"
  );
}

export function filterAttendanceRows(rows, filter, keyword = "") {
  const text = keyword.trim().toLowerCase();
  return (rows || []).filter(row => {
    const checked =
      row.checked === true ||
      Boolean(row.checked_at) ||
      row.sign_status === "已签到";
    const matches =
      filter === "all" ||
      (filter === "checked" && checked) ||
      (filter === "pending" &&
        !checked &&
        !["late", "leave"].includes(row.attendance_status)) ||
      (filter === "late" && row.attendance_status === "late") ||
      (filter === "leave" && row.attendance_status === "leave") ||
      (filter === "cross" && row.attendance_role === "CROSS_CLASS_MEMBER") ||
      (filter === "team" &&
        Boolean(
          row.is_team ||
          row.team_id ||
          (row.actual_attendee_name &&
            row.registered_name &&
            row.actual_attendee_name !== row.registered_name)
        ));
    return (
      matches &&
      (!text ||
        [
          row.name,
          row.registered_name,
          row.actual_attendee_name,
          row.class_name,
          row.home_class_name,
          row.center,
          row.company
        ].some(value =>
          String(value || "")
            .toLowerCase()
            .includes(text)
        ))
    );
  });
}

/** Build display-only export cells. Contact fields are already masked by the API. */
export function attendanceExportRows(rows) {
  return [
    [
      "实际到场姓名",
      "原报名姓名",
      "联系方式（脱敏）",
      "公司",
      "分中心",
      "班级",
      "小组",
      "签到身份",
      "原班级",
      "组号",
      "桌号",
      "签到状态",
      "签到时间"
    ],
    ...(rows || []).map(row => [
      row.actual_attendee_name || row.name || "",
      row.registered_name || row.name || "",
      row.phone_masked || row.phone || "",
      row.company || "",
      row.center || "",
      row.class_name || "",
      row.group_name || "",
      row.attendance_role_label || row.attendance_role || "",
      row.home_class_name || "",
      row.group_num ?? "",
      row.dinner_table_num ?? "",
      row.sign_status || row.attendance_status_label || "",
      row.sign_time || row.checked_at || ""
    ])
  ];
}
