/** Internal identifiers stay in requests; operators see Chinese business terms. */
const keyOf = value =>
  String(value || "")
    .trim()
    .toUpperCase()
    .replaceAll("-", "_");
const labelOf = (labels, value, fallback) => labels[keyOf(value)] || fallback;

export const taskTypeLabels = {
  CARE: "关怀",
  CLASS_MEETING: "班级学习会",
  ENTERPRISE_VISIT: "企业走访",
  GROUP_MEETING: "小组学习会",
  STUDY_MEETING: "小组学习会",
  KONPA: "空巴",
  OFFLINE_COURSE: "线下课程",
  ONLINE_COURSE: "线上课程",
  READING: "读书",
  REPORT_MEETING: "报告会",
  STUDY_TOUR: "游学"
};
export const taskTypeLabel = value =>
  labelOf(taskTypeLabels, value, "其他学习任务");
const activityLabels = {
  ...taskTypeLabels,
  COURSE: "课程",
  NATIONAL_REPORT: "全国报告会",
  CENTER_QUARTERLY_REPORT: "分中心季度报告会",
  CENTER_MONTHLY_REPORT: "分中心月度报告会",
  STAFF_TRAINING: "班主任辅导员培训会",
  BOARD_MEETING: "理事会",
  READING_SESSION: "读书会",
  OTHER: "其他活动"
};
export const activityTypeLabel = value =>
  labelOf(activityLabels, value, "其他活动");
export const isClassMeeting = value => keyOf(value) === "CLASS_MEETING";
const sessionLabels = {
  MORNING: "上午",
  AM: "上午",
  AFTERNOON: "下午",
  PM: "下午",
  EVENING: "晚上空巴",
  KONPA: "晚上空巴",
  KONPAI: "晚上空巴"
};
export function activitySessionLabel(activityType, code) {
  return isClassMeeting(activityType)
    ? labelOf(sessionLabels, code, "班会场次")
    : "整场";
}
export function activityDisplayTitle(title, activityType, code) {
  const base = String(title || "未命名活动")
    .replace(
      /\s*[-－—·]\s*(?:上午|下午|晚上?空巴|空巴|AM|PM|MORNING|AFTERNOON|EVENING|KONPA)\s*$/i,
      ""
    )
    .trim();
  return isClassMeeting(activityType) && code
    ? `${base} · ${activitySessionLabel(activityType, code)}`
    : base;
}
export const volunteerStatusOptions = [
  { value: "ACTIVE", label: "当前有效" },
  { value: "SUSPENDED", label: "已暂停" },
  { value: "ENDED", label: "已结束" },
  { value: "REVOKED", label: "已撤销" }
];
export const volunteerStatusLabel = value =>
  volunteerStatusOptions.find(item => item.value === keyOf(value))?.label ||
  "待核对";
export const organizationTypeLabel = value =>
  labelOf(
    {
      ROOT: "二级塾",
      SHUKU: "二级塾",
      REGIONAL_SHUKU: "二级塾",
      REGIONAL_CENTER: "分中心",
      CLASS: "班级",
      GROUP: "小组",
      SPECIAL_COHORT: "特殊班"
    },
    value,
    "组织"
  );
export const volunteerMigrationLabel = value =>
  labelOf(
    {
      ALREADY_V2: "已使用新任职体系",
      SAFE_TO_MIGRATE: "可人工确认",
      MANUAL_REVIEW: "需人工核验"
    },
    value,
    "待核验"
  );
export function registrationCount(row) {
  return Number.isInteger(row?.registration_count) &&
    row.registration_count >= 0
    ? row.registration_count
    : null;
}
export const planTransitionLabel = value =>
  labelOf(
    {
      INITIAL: "首次绑定",
      RESTART: "重新开始",
      RESUME: "恢复学习",
      PLAN_SWITCH: "切换计划",
      CORRECTION: "人工修正"
    },
    value,
    "计划调整"
  );
export const planBindingStatusLabel = value =>
  labelOf(
    { ACTIVE: "进行中", COMPLETED: "已完成", ENDED: "已结束" },
    value,
    "待核对"
  );
export const capabilityLabel = value =>
  labelOf({ STUDY_MEETING_MANAGE: "登记小组学习会" }, value, "其他已配置能力");
