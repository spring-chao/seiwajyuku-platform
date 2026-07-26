import { http } from "@/utils/http";

export type AnnualPlan = {
  id: number;
  year: number;
  version: number;
  policy_text: string;
  status: string;
  write_enabled: number;
};

export type DashboardItem = {
  org_unit_id: string;
  org_name: string;
  metric_key: string;
  metric_name: string;
  unit: string;
  annual_target: number | string | null;
  mp?: { value: number | string | null; state: string };
  forecast?: { value: number | string | null; state: string };
  actual?: { value: number | string | null; state: string };
  forecast_achievement: number | string | null;
};

export type PeriodValue = {
  id: number;
  org_unit_id: string;
  org_name: string;
  metric_key: string;
  metric_name: string;
  unit: string;
  value_kind: "MP" | "FORECAST" | "ACTUAL";
  numeric_value: number | string | null;
  value_state: string;
  source_type: string;
  is_manual_override: number;
};

export const getAnnualPlans = () =>
  http.request<{ success: boolean; data: AnnualPlan[] }>(
    "get",
    "/api/v1/annual-plans"
  );

export const getMpDashboard = (params: {
  plan_id: number;
  month: number;
}) =>
  http.request<{
    success: boolean;
    data: {
      month: number;
      centers: { id: string; name: string }[];
      items: DashboardItem[];
    };
  }>("get", "/api/v1/analytics/mp-dashboard", { params });

export const getTargetVariances = (planId: number) =>
  http.request<{
    success: boolean;
    data: {
      metric_key: string;
      root_target: number;
      child_aggregate: number;
      aggregation: string;
      difference: number;
    }[];
  }>("get", "/api/v1/analytics/target-variances", {
    params: { plan_id: planId }
  });

export const getPeriodValues = (params: {
  plan_id: number;
  month: number;
  org_unit_id?: string;
}) =>
  http.request<{ success: boolean; data: PeriodValue[] }>(
    "get",
    "/api/v1/metric-period-values",
    { params }
  );

export const savePeriodValues = (
  planId: number,
  updates: { id: number; numeric_value: number | null; value_state?: string }[]
) =>
  http.request<{ success: boolean; data: { updated_count: number } }>(
    "put",
    "/api/v1/metric-period-values",
    {
      params: { plan_id: planId },
      data: { updates }
    }
  );

export type FollowupTask = {
  id: number;
  member_id: number;
  member_name: string;
  phone_masked: string;
  company_name?: string;
  org_name: string;
  service_purpose: string;
  assignee_name: string;
  status: "OPEN" | "IN_PROGRESS" | "CLOSED";
  due_at?: string;
  next_followup_at?: string;
  can_record: boolean;
};

export type Member = {
  id: number;
  member_code: string;
  name: string;
  org_unit_id: string;
  org_name: string;
  development_org_unit_id?: string;
  status: string;
  phone_masked: string;
  phone_last4?: string;
  company_name?: string;
  gender?: string;
  district?: string;
  company_address?: string;
  class_name?: string;
  group_name?: string;
  birthday?: string;
  join_date?: string;
  study_start_date?: string;
  membership_years?: number;
  renewal_month?: string;
  position?: string;
  referrer?: string;
  referrer_center?: string;
  industry_category?: string;
  industry?: string;
  company_products?: string;
  company_size?: string;
  notes?: string;
  enterprise_stage?: string;
  sensitivity_level: string;
};

export type OrgUnit = {
  id: string;
  unit_code: string;
  name: string;
  unit_type: string;
  parent_id?: string;
};

export type FollowupAssignee = {
  id: number;
  username: string;
  display_name: string;
};

export type ActivitySnapshot = {
  id: number;
  source_key: string;
  external_id: string;
  snapshot_type: "ATTENDANCE" | "READING";
  org_unit_id: string;
  activity_type: string;
  occurred_at: string;
  eligible_count: number;
  completed_count: number;
  title?: string;
  status: string;
};

export const getFollowupTasks = (status?: string) =>
  http.request<{ success: boolean; data: FollowupTask[] }>(
    "get",
    "/api/v1/followups/tasks",
    { params: status ? { status } : undefined }
  );

export const getMembers = (orgUnitId?: string) =>
  http.request<{ success: boolean; data: Member[] }>(
    "get",
    "/api/v1/members",
    { params: orgUnitId ? { org_unit_id: orgUnitId } : undefined }
  );

export const createMember = (data: {
  member_code?: string;
  name: string;
  org_unit_id: string;
  development_org_unit_id?: string;
  phone: string;
  company_name?: string;
  gender?: string;
  district?: string;
  company_address?: string;
  class_name?: string;
  group_name?: string;
  birthday?: string;
  join_date?: string;
  study_start_date?: string;
  membership_years?: number;
  renewal_month?: string;
  status?: string;
  position?: string;
  referrer?: string;
  referrer_center?: string;
  industry_category?: string;
  industry?: string;
  company_products?: string;
  annual_sales?: string;
  company_size?: string;
  profit_margin?: string;
  notes?: string;
}) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    "/api/v1/members",
    { data }
  );

export const getOrgUnits = () =>
  http.request<{ success: boolean; data: OrgUnit[] }>(
    "get",
    "/api/v1/org-units/tree"
  );

export const getFollowupAssignees = (orgUnitId?: string) =>
  http.request<{ success: boolean; data: FollowupAssignee[] }>(
    "get",
    "/api/v1/followups/assignees",
    { params: orgUnitId ? { org_unit_id: orgUnitId } : undefined }
  );

export const createFollowupTask = (data: {
  member_id: number;
  task_type: string;
  service_purpose: string;
  assigned_user_id: number;
  due_at?: string;
  confidentiality_level?: string;
}) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    "/api/v1/followups/tasks",
    { data, timeout: 60000 }
  );

export const revealMemberContact = (
  memberId: number,
  data: { task_id: number; purpose: string; client_reference?: string }
) =>
  http.request<{
    success: boolean;
    data: { name: string; phone: string; expires_in: string };
  }>("post", `/api/v1/members/${memberId}/contact-access`, { data });

export const createFollowupRecord = (
  taskId: number,
  data: {
    channel: string;
    contacted_at: string;
    outcome_code: string;
    subject_statement?: string;
    objective_facts?: string;
    staff_judgment?: string;
    next_action?: string;
    next_followup_at?: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/followups/tasks/${taskId}/records`,
    { data, timeout: 60000 }
  );

export const createVisitRecord = (
  taskId: number,
  data: {
    appointment_at?: string;
    visited_at: string;
    purpose: string;
    participants: string[];
    location_type: string;
    objective_facts: string;
    expressed_needs?: string;
    support_provided?: string;
    staff_judgment?: string;
    next_action?: string;
    next_followup_at?: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/followups/tasks/${taskId}/visits`,
    { data, timeout: 60000 }
  );

export const closeFollowupTask = (taskId: number, closureNote: string) =>
  http.request<{
    success: boolean;
    data: { id: number; status: "CLOSED" };
  }>("post", `/api/v1/followups/tasks/${taskId}/close`, {
    data: { closure_note: closureNote },
    timeout: 60000
  });

export const getActivities = (month?: string) =>
  http.request<{ success: boolean; data: ActivitySnapshot[] }>(
    "get",
    "/api/v1/activities",
    { params: month ? { month } : undefined }
  );
