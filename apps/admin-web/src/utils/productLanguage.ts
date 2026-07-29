export type ProductLanguageContext = "OPERATIONS" | "VOLUNTEER";

const employeeRoles = new Set([
  "operations_admin",
  "ops_center_director",
  "ops_center_operations",
  "ops_center_learning",
  "ops_center_development",
  "ops_center_management",
  "ops_center_data",
  "ops_center_finance",
  "ops_center_administration"
]);

const volunteerRoles = new Set([
  "regional_manager",
  "class_counselor",
  "group_leader",
  "volunteer_director",
  "volunteer_regional_lead",
  "volunteer_regional_service",
  "volunteer_class_counselor",
  "volunteer_class_committee",
  "volunteer_group_leader",
  "volunteer_group_committee",
  "volunteer_activity"
]);

export function productLanguageContext(
  roles: string[]
): ProductLanguageContext {
  if (roles.some(role => employeeRoles.has(role))) return "OPERATIONS";
  if (roles.some(role => volunteerRoles.has(role))) return "VOLUNTEER";
  return "OPERATIONS";
}
const language = {
  OPERATIONS: {
    pageKicker: "关怀任务闭环",
    item: "任务",
    items: "关怀任务",
    create: "创建任务",
    createTitle: "创建关怀任务",
    created: "关怀任务已创建",
    assignee: "责任人",
    deadline: "截止时间",
    itemType: "任务类型",
    close: "关闭",
    closeTitle: "关闭任务",
    closeSuccess: "任务已关闭",
    closeNote: "任务关闭说明",
    closed: "已关闭",
    open: "待执行",
    inProgress: "跟进中",
    empty: "暂无关怀任务，请先在学员管理中建立试点学员",
    accessHint: "联系方式默认脱敏，只有当前有效任务责任人可按用途临时查看。",
    privateLabel: "仅责任人可见",
    managerLabel: "组织管理人员可见",
    onlyAssignee: "仅责任人可操作"
  },
  VOLUNTEER: {
    pageKicker: "学长关怀协同",
    item: "服务事项",
    items: "关怀服务事项",
    create: "发起服务事项",
    createTitle: "发起学长关怀服务事项",
    created: "关怀服务事项已发起",
    assignee: "担当人",
    deadline: "建议完成时间",
    itemType: "服务事项类型",
    close: "确认圆满",
    closeTitle: "确认服务圆满",
    closeSuccess: "已确认服务圆满",
    closeNote: "服务结果说明",
    closed: "已确认完成",
    open: "待协力",
    inProgress: "服务中",
    empty: "暂无待协力的关怀服务事项",
    accessHint: "联系方式默认脱敏，只有当前有效担当人可按用途临时查看。",
    privateLabel: "仅担当人可见",
    managerLabel: "运营中心协同人员可见",
    onlyAssignee: "仅担当人可操作"
  }
} as const;

export function productCopy(context: ProductLanguageContext) {
  return language[context];
}

export function adaptVolunteerMessage(message: string): string {
  return message
    .replaceAll("任务责任人", "服务事项担当人")
    .replaceAll("当前任务", "当前服务事项")
    .replaceAll("联系任务", "联系服务事项")
    .replaceAll("跟进任务", "跟进服务事项")
    .replaceAll("任务", "服务事项")
    .replaceAll("责任人", "担当人")
    .replaceAll("截止时间", "建议完成时间")
    .replaceAll("已关闭", "已确认完成")
    .replaceAll("关闭", "确认圆满");
}
