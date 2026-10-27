const ACTIVITY_TYPES = [
  { value: "class_meeting", label: "班级学习会（三场）" }, { value: "course", label: "课程" },
  { value: "national_report", label: "全国报告会" }, { value: "center_quarterly_report", label: "分中心季度报告会" },
  { value: "staff_training", label: "班主任辅导员培训会" }, { value: "board_meeting", label: "理事会" },
  { value: "study_tour", label: "游学" }, { value: "other", label: "其他活动" }
];
const CLASS_TIMES = {
  morning_checkin_start: "07:30", morning_scheduled_start: "09:00", morning_checkin_end: "10:30", morning_scheduled_end: "12:00",
  afternoon_checkin_start: "12:10", afternoon_scheduled_start: "13:30", afternoon_checkin_end: "15:00", afternoon_scheduled_end: "17:00",
  konpa_checkin_start: "17:10", konpa_scheduled_start: "18:00", konpa_checkin_end: "20:30", konpa_scheduled_end: "20:30"
};
const PERIODS = [{ key: "morning", label: "上午" }, { key: "afternoon", label: "下午" }, { key: "konpa", label: "晚上空巴" }];
const CLOCK_FIELDS = [{ key: "checkin_start", label: "签到开放" }, { key: "checkin_end", label: "签到截止" }, { key: "scheduled_start", label: "活动开始" }, { key: "scheduled_end", label: "活动结束" }];
function clockFromIso(value, fallback) {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return fallback;
  const d = new Date(time + 8 * 3600000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
function rate(value, digits = 1) { return typeof value === "number" && Number.isFinite(value) ? (value * 100).toFixed(digits) + "%" : "—"; }
function rowActions(detail, row) {
  const event = detail.group || {};
  if (!row.registration_id || row.checked || row.checked_at || event.status === "CANCELLED" || !["upcoming", "open"].includes(event.checkin_status)) return [];
  const actions = [];
  if (detail.can_manage && event.status === "CONFIRMED" && event.checkin_status === "open" && !row.is_team && !["EVENT_TEAM_MEMBER", "COURSE_TEAM_MEMBER"].includes(row.attendance_role)) actions.push({ label: "人工签到", value: "manual_checkin" });
  if (detail.can_status) {
    if (String(event.session_code).toUpperCase() !== "KONPA") actions.push({ label: "标记预计迟到", value: "late" });
    actions.push({ label: "标记请假", value: "leave" }, { label: "恢复待签到", value: "pending" });
  }
  if (detail.can_manage) actions.push({ label: "删除未签到报名", value: "registration_delete" });
  return actions;
}
function decorate(detail, keyword = "", scope = "overall") {
  const s = detail.statistics || {};
  const scene = scope === "overall" ? null : (s.class_session_statistics || []).find(row => row.event_id === (detail.group.id || detail.group.event_id));
  const c = (scope === "overall" ? s.class_statistics : scene) || {}, g = (scope === "overall" ? s.group_totals : scene && scene.group_totals) || {};
  const groupRows = ((scope === "overall" ? s.group_statistics : scene && scene.group_statistics) || []).map(row => ({ ...row, group_id: row.group_org_unit_id || "ungrouped", rateLabel: rate(row.participation_rate, 0) }));
  const query = keyword.trim().toLowerCase();
  const attendees = (detail.attendees || []).map(row => ({ ...row,
    displayName: row.registered_display_name || row.registered_name || row.name,
    statusLabel: row.checked || row.checked_at ? "已签到" : ({ late: "预计迟到", leave: "已请假", pending: "未签到" }[row.attendance_status] || "未签到"),
    hasActions: rowActions(detail, row).length > 0
  })).filter(row => !query || [row.displayName, row.actual_attendee_name, row.company, row.current_group_name].some(v => String(v || "").toLowerCase().includes(query)));
  return { attendees, groupRows, statistics: {
    isClassMeeting: String(detail.group.activity_type).toLowerCase() === "class_meeting",
    scopeLabel: scope === "overall" ? "整体" : (detail.group.session_name || "当前场次"),
    overallRate: rate(c.participation_rate), memberRate: rate(c.member_participation_rate), groupRate: rate(g.participation_rate, 0),
    overallPresent: c.present_count ?? "—", rosterCount: c.roster_count ?? "—", memberPresent: c.member_present_count ?? "—", staffPresent: c.staff_present_count ?? "—",
    sessionTotal: String(detail.group.activity_type).toLowerCase() === "class_meeting" && scope !== "overall" ? scene?.roster_count ?? "—" : s.home_class_total ?? s.total ?? "—",
    sessionChecked: String(detail.group.activity_type).toLowerCase() === "class_meeting" && scope !== "overall" ? scene?.member_present_count ?? "—" : s.home_class_checked ?? s.checked ?? "—", onsite: s.onsite_total ?? s.checked ?? "—",
    basis: scope === "overall" ? "上午、下午合并去重，空巴另计。" : "仅统计当前选择的场次，不合并其他场次。", message: (scope !== "overall" && !scene ? "本场次参会统计暂不可用，请刷新重试。" : s.group_statistics_message) || detail.statistics_error || ""
  }};
}
module.exports = { ACTIVITY_TYPES, CLASS_TIMES, PERIODS, CLOCK_FIELDS, clockFromIso, rate, rowActions, decorate };
