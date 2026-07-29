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
  org_unit_id: string;
  org_name: string;
  service_purpose: string;
  assignee_name: string;
  status: "OPEN" | "IN_PROGRESS" | "CLOSED";
  due_at?: string;
  next_followup_at?: string;
  can_record: boolean;
  can_close: boolean;
};

export type FollowupInvitation = {
  id: number;
  task_id: number;
  invitation_type: "ASSIGNEE" | "COMPANION";
  status:
    | "PENDING"
    | "ACCEPTED"
    | "ADJUSTMENT_REQUESTED"
    | "UNAVAILABLE"
    | "CANCELLED"
    | "EXPIRED";
  invitation_message?: string;
  proposed_due_at?: string;
  requested_due_at?: string;
  response_note?: string;
  valid_until: string;
  member_id: number;
  member_name: string;
  phone_masked: string;
  company_name?: string;
  service_purpose: string;
  due_at?: string;
  org_name: string;
  inviter_name: string;
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

export type DirectClassPreflight = {
  mode: "READ_ONLY_PRODUCTION_PREFLIGHT";
  automatic_production_write_allowed: false;
  source_name: string;
  source_sha256: string;
  source: {
    active_direct_member_count: number;
    by_class: { class_name: string; count: number }[];
    sheet_name: string;
  };
  organization: {
    root_unit_code: string;
    root_match_count: number;
    development_center_match_counts: { center_name: string; match_count: number }[];
    direct_class_status: {
      class_name: string;
      active_class_matches: number;
      correct_parent_matches: number;
      action: "REUSE" | "CREATE_OR_RESOLVE";
    }[];
  };
  matching: {
    summary: { status: string; count: number }[];
    no_production_match_by_class: { class_name: string; count: number }[];
    matched_profile_fields_needing_reconciliation: { field: string; count: number }[];
  };
  production_existing_direct_class_records: { class_name: string; count: number }[];
  issues: { code: string; count: number }[];
  write_gates: string[];
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

export type AttendanceEventGroup = {
  id: number;
  source_key: string;
  title?: string;
  event_date: string;
  activity_type: string;
  status: string;
  org_unit_id: string;
  org_name: string;
  study_org_unit_id?: string;
  session_count: number;
  record_count: number;
  present_count: number;
};

export type RenewalOverviewRow = {
  org_unit_id: string;
  org_name: string;
  due_month: number;
  status: string;
  count: number;
};

export type RenewalImportSummary = {
  total: number;
  matched: number;
  needs_review: number;
  invalid: number;
  assistance_review: number;
};

export type RenewalImportSample = {
  row_no: number;
  name: string;
  center_name: string;
  class_name?: string;
  due_month?: number;
  match_status: string;
  proposed_status: string;
  history_note?: string;
  assistance_note?: string;
  issue_code?: string;
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

export const previewDirectClassWorkbook = (workbook: File) => {
  const data = new FormData();
  data.append("workbook", workbook);
  return http.request<{ success: boolean; data: DirectClassPreflight }>(
    "post",
    "/api/v1/direct-class-preflight/preview",
    {
      data,
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60000
    }
  );
};

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
  invitation_mode?: boolean;
  invitation_message?: string;
  invitation_valid_until?: string;
}) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    "/api/v1/followups/tasks",
    { data, timeout: 60000 }
  );

export const getFollowupCapabilities = () =>
  http.request<{
    success: boolean;
    data: { enabled: boolean; production_mutations_approved: boolean };
  }>("get", "/api/v1/followups/capabilities");

export const getMyFollowupInvitations = () =>
  http.request<{ success: boolean; data: FollowupInvitation[] }>(
    "get",
    "/api/v1/followups/invitations/mine"
  );

export const createFollowupInvitation = (
  taskId: number,
  data: {
    invited_user_id: number;
    invitation_type: "ASSIGNEE" | "COMPANION";
    invitation_message?: string;
    proposed_due_at?: string;
    valid_until: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/followups/tasks/${taskId}/invitations`,
    { data }
  );

export const acceptFollowupInvitation = (
  invitationId: number,
  responseNote?: string
) =>
  http.request("post", `/api/v1/followups/invitations/${invitationId}/accept`, {
    data: { response_note: responseNote }
  });

export const requestFollowupInvitationAdjustment = (
  invitationId: number,
  requestedDueAt: string,
  responseNote: string
) =>
  http.request(
    "post",
    `/api/v1/followups/invitations/${invitationId}/adjustment-request`,
    {
      data: {
        requested_due_at: requestedDueAt,
        response_note: responseNote
      }
    }
  );

export const markFollowupInvitationUnavailable = (
  invitationId: number,
  responseNote: string
) =>
  http.request(
    "post",
    `/api/v1/followups/invitations/${invitationId}/unavailable`,
    { data: { response_note: responseNote } }
  );

export const revealMemberContact = (
  memberId: number,
  data: { task_id: number; purpose: string; client_reference?: string }
) =>
  http.request<{
    success: boolean;
    data: { name: string; phone: string; expires_in: string };
  }>("post", `/api/v1/members/${memberId}/contact-access`, { data });

export const getMemberDetail = (memberId: number) =>
  http.request<{
    success: boolean;
    data: Record<string, string | number | null>;
  }>("get", `/api/v1/members/${memberId}/detail`);

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

export const getAttendanceEventGroups = (month?: string) =>
  http.request<{ success: boolean; data: AttendanceEventGroup[] }>(
    "get",
    "/api/v1/attendance/event-groups",
    { params: month ? { month } : undefined }
  );

export const getRenewalOverview = (year: number) =>
  http.request<{
    success: boolean;
    data: { year: number; rows: RenewalOverviewRow[] };
  }>("get", "/api/v1/renewals/overview", { params: { year } });

export const previewRenewalImport = (
  renewalFile: File,
  masterFile: File
) => {
  const data = new FormData();
  data.append("renewal_file", renewalFile);
  data.append("master_file", masterFile);
  return http.request<{
    success: boolean;
    data: {
      batch_id: number;
      summary: RenewalImportSummary;
      samples: RenewalImportSample[];
    };
  }>("post", "/api/v1/renewals/imports/preview", {
    data,
    timeout: 120000,
    headers: { "Content-Type": "multipart/form-data" }
  });
};
