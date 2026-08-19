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

export type OperationsScheduleItem = {
  id: number | null;
  title: string;
  event_date: string | null;
  activity_type: string;
  org_name: string;
  class_org_unit_id?: string | null;
  class_name?: string | null;
  year_sequence?: number | null;
  status?: "SCHEDULED" | "PLANNED" | "UNSCHEDULED";
};

export type OperationsSnapshot = {
  period: string;
  scope_label: string;
  summary: {
    renewed_member_count: number | null;
    new_member_count: number;
    active_member_count: number;
    birthday_member_count: number;
    class_count: number;
    class_meeting_count: number;
    course_count: number;
    activity_count: number;
  };
  centers: {
    id: string;
    name: string;
    active_member_count: number;
  }[];
  birthday_members: {
    member_id: number;
    name: string;
    org_unit_id: string;
    org_name: string;
    class_org_unit_id?: string | null;
    class_name?: string | null;
    birthday: string;
  }[];
  classes: {
    class_org_unit_id: string;
    class_name: string;
    org_name: string;
    class_owner_org_unit_id?: string | null;
    class_owner_org_name?: string | null;
    class_owner_scope: "DIRECT" | "CENTER";
    class_meeting_count: number;
    class_meeting_at?: string | null;
    year_sequence?: number | null;
    status: "SCHEDULED" | "PLANNED" | "UNSCHEDULED";
  }[];
  class_meeting_schedule: OperationsScheduleItem[];
  class_meetings: OperationsScheduleItem[];
  courses: OperationsScheduleItem[];
  activities: OperationsScheduleItem[];
  data_quality: {
    missing_join_date_count: number;
    attendance_schedule_source_ready: boolean;
    course_schedule_source_ready: boolean;
    unscheduled_class_count: number;
    planned_class_count: number;
    unlinked_class_meeting_count: number;
    duplicate_class_node_count: number;
    invalid_direct_root_class_count: number;
    renewal_source_authorized: boolean;
    active_member_count_as_of: "CURRENT";
    notes: string[];
  };
};

export type ClassOperationsDetail = {
  class_org_unit_id: string;
  class_name: string;
  org_name: string;
  class_owner_org_unit_id?: string | null;
  class_owner_org_name?: string | null;
  class_owner_scope: "DIRECT" | "CENTER";
  period: string;
  active_member_count: number;
  weekly_meeting_at?: string | null;
  planned_class_meeting_at?: string | null;
  learning_month?: number | null;
  learning_progress?: string | null;
  class_meetings: OperationsScheduleItem[];
  class_attendance: AttendanceRate;
  groups: {
    id: string;
    name: string;
    planned_meeting_at?: string | null;
    events: OperationsScheduleItem[];
    attendance: AttendanceRate;
  }[];
  entrepreneur_count: number;
  entrepreneur_ratio?: number | null;
  executive_count: number;
  executive_ratio?: number | null;
  position_classification_note: string;
  revenue_growth_authorized: boolean;
  revenue_growing_member_count?: number | null;
  revenue_comparable_member_count?: number | null;
  revenue_growth_ratio?: number | null;
  updated_at?: string | null;
};

export type OperationRhythmStatus =
  | "PENDING"
  | "PLANNED"
  | "IN_PROGRESS"
  | "WAITING_EXTERNAL"
  | "COMPLETED"
  | "ATTENTION"
  | "CANCELLED";

export type OperationRhythmItem = {
  id: number;
  org_unit_id: string;
  org_name: string;
  organization_id?: string | null;
  organization_name?: string | null;
  class_org_unit_id?: string | null;
  class_name?: string | null;
  period: string;
  item_key: string;
  title: string;
  category: string;
  status: OperationRhythmStatus;
  status_label?: string;
  responsibility_role?: string | null;
  external_responsibility_role?: string | null;
  start_date?: string | null;
  due_date?: string | null;
  actual_at?: string | null;
  completion_note?: string | null;
  business_type?: string | null;
  business_id?: number | string | null;
  manual_override?: number;
  updated_at?: string | null;
};

export type OperationRhythmSnapshot = {
  period: string;
  items: OperationRhythmItem[];
  views: {
    today: OperationRhythmItem[];
    next_7_days: OperationRhythmItem[];
    month: OperationRhythmItem[];
    attention: OperationRhythmItem[];
  };
  summary: {
    total: number;
    today_count: number;
    next_7_days_count: number;
    attention_count: number;
    status_counts: Record<OperationRhythmStatus, number>;
  };
  data_quality: { generated: boolean; notes: string[] };
  policy: string;
};

type AttendanceRate = {
  event_count: number;
  eligible_count: number;
  present_count: number;
  rate?: number | null;
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

export const getMpDashboard = (params: { plan_id: number; month: number }) =>
  http.request<{
    success: boolean;
    data: {
      month: number;
      centers: { id: string; name: string }[];
      items: DashboardItem[];
    };
  }>("get", "/api/v1/analytics/mp-dashboard", { params });

export const getOperationsSnapshot = (params: {
  year: number;
  month: number;
  birthday_month?: number;
}) =>
  http.request<{ success: boolean; data: OperationsSnapshot }>(
    "get",
    "/api/v1/analytics/operations-snapshot",
    { params }
  );

export const getOperationRhythmSnapshot = (params: {
  year: number;
  month: number;
  organization_id?: string;
  class_org_unit_id?: string;
  status?: OperationRhythmStatus;
}) =>
  http.request<{ success: boolean; data: OperationRhythmSnapshot }>(
    "get",
    "/api/v1/operations/rhythm/snapshot",
    { params }
  );

export const generateOperationRhythm = (params: {
  year: number;
  month: number;
}) =>
  http.request<{
    success: boolean;
    data: { period: string; cycle_count: number; created_item_count: number };
  }>("post", "/api/v1/operations/rhythm/generate", { params });

export const updateOperationRhythmItem = (
  itemId: number,
  data: {
    status?: OperationRhythmStatus;
    title?: string;
    note?: string | null;
    start_date?: string | null;
    due_date?: string | null;
  }
) =>
  http.request<{ success: boolean; data: OperationRhythmItem }>(
    "patch",
    `/api/v1/operations/rhythm/items/${itemId}`,
    { data }
  );

export const getClassOperations = (
  classOrgUnitId: string,
  params: { year: number; month: number }
) =>
  http.request<{ success: boolean; data: ClassOperationsDetail }>(
    "get",
    `/api/v1/analytics/class-operations/${classOrgUnitId}`,
    { params }
  );

export const updateClassOperations = (
  classOrgUnitId: string,
  params: { year: number; month: number },
  data: {
    weekly_meeting_at?: string | null;
    planned_class_meeting_at?: string | null;
    learning_month?: number | null;
    learning_progress?: string | null;
    revenue_growing_member_count?: number | null;
    revenue_comparable_member_count?: number | null;
    groups: { group_org_unit_id: string; planned_meeting_at?: string | null }[];
  }
) =>
  http.request<{ success: boolean; data: ClassOperationsDetail }>(
    "put",
    `/api/v1/analytics/class-operations/${classOrgUnitId}`,
    { params, data }
  );

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
  class_org_unit_id?: string;
  group_org_unit_id?: string;
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

export type MemberChangeHistory = {
  id: number;
  change_type: string;
  before_json: string;
  after_json: string;
  changed_by?: number;
  changed_at: string;
};

export type MemberTimelineEvent = {
  id: string;
  event_type: string;
  occurred_at?: string;
  title: string;
  status?: string;
  channel?: string;
  activity_type?: string;
  participant_type?: string;
  due_at?: string;
  updated_at?: string;
  phase?: string;
  actor_id?: number;
  duration_minutes?: number;
  source_system?: string;
};

export type MemberServiceSignal = {
  code: string;
  title: string;
  message: string;
  attention_level: "ACTION_REQUIRED" | "REVIEW";
  action_hint: string;
  rule_version: string;
  evidence: Record<string, boolean | number | string>;
  latest_feedback?: {
    id: number;
    status: MemberServiceSignalFeedbackStatus;
    created_at: string;
  } | null;
};

export type MemberServiceSignalFeedbackStatus =
  "CONFIRMED_VALID" | "NOT_APPLICABLE" | "DATA_CORRECTED";

export type MemberTimeline = {
  member: Pick<
    Member,
    | "id"
    | "name"
    | "org_unit_id"
    | "org_name"
    | "phone_masked"
    | "class_name"
    | "group_name"
    | "status"
  >;
  summary: Record<string, number>;
  service_signal_feedback_enabled: boolean;
  service_signals: MemberServiceSignal[];
  events: MemberTimelineEvent[];
};

export type BirthdayGreetingMemory = {
  id: string;
  occurred_on: string;
  year: number;
  month: number;
  title: string;
  activity_type: string;
  category: "SPECIAL_EXPERIENCE" | "LONG_TERM_COMPANIONSHIP" | "LEARNING_ACTIVITY";
  category_label: string;
  source_type: "ATTENDANCE" | "LEGACY_ACTIVITY_FACT";
  evidence_status: string;
  verified: true;
  selected_by_default: boolean;
};

export type BirthdayGreetingContext = {
  member: {
    id: number;
    name: string;
    birthday_month_day?: string | null;
    org_unit_id: string;
    org_name: string;
    class_org_unit_id?: string | null;
    class_name?: string | null;
    group_org_unit_id?: string | null;
    group_name?: string | null;
    join_date?: string | null;
    study_start_date?: string | null;
    membership_years?: number | null;
    membership_years_source: "OVERRIDE" | "JOIN_DATE" | "MISSING";
  };
  memories: BirthdayGreetingMemory[];
  selected_memory_ids: string[];
  data_quality: {
    facts_only: true;
    memory_count: number;
    join_date_available: boolean;
    attendance_source_readable: boolean;
    notes: string[];
  };
  policy: string;
};

export type BirthdayGreetingDraft = {
  member_id: number;
  tone: "standard" | "warm" | "concise";
  selected_memory_ids: string[];
  draft: string;
  facts_only: true;
  editable: true;
};

export type MemberEditProfile = {
  id: number;
  name: string;
  org_unit_id: string;
  development_org_unit_id?: string | null;
  status: string;
  phone: string | null;
  company_name?: string | null;
  gender?: string | null;
  district?: string | null;
  company_address?: string | null;
  birthday?: string | null;
  join_date?: string | null;
  study_start_date?: string | null;
  membership_years?: number | null;
  membership_years_inferred: boolean;
  renewal_month?: string | null;
  position?: string | null;
  referrer?: string | null;
  referrer_center?: string | null;
  industry_category?: string | null;
  industry?: string | null;
  company_products?: string | null;
  company_size?: string | null;
  notes?: string | null;
  class_org_unit_id?: string | null;
  class_org_name?: string | null;
  group_org_unit_id?: string | null;
  group_org_name?: string | null;
  annual_sales?: string | null;
  employee_count?: number | null;
  profit_margin?: string | null;
  renewal_month_overridden?: boolean;
  financial_fields_editable: boolean;
};

export type OrgUnit = {
  id: string;
  unit_code: string;
  name: string;
  unit_type: string;
  parent_id?: string;
  parent_name?: string | null;
  created_at?: string | null;
  duplicate_name?: boolean;
  is_name_canonical?: boolean;
};

export type LearningOrgReferenceCounts = {
  active_member_relations: number;
  active_children: number;
  active_events: number;
};

export type ManagedLearningOrgUnit = OrgUnit & {
  is_active: number | boolean;
  active_from?: string | null;
  active_until?: string | null;
  parent_type?: string | null;
  reference_counts: LearningOrgReferenceCounts;
};

export type LearningOrgManagement = {
  units: ManagedLearningOrgUnit[];
  centers: { id: string; name: string }[];
  classes: ManagedLearningOrgUnit[];
};

export type LearningOrgMovePreview = {
  unit_id: string;
  class_name: string;
  current_parent_id?: string | null;
  target_parent_id: string;
  target_parent_name: string;
  reference_counts: LearningOrgReferenceCounts;
  confirmation: string;
};

export type LearningGroupMemberTransferOptions = {
  source_group: { id: string; name: string };
  class: { id: string; name: string };
  members: Array<{
    member_id: number;
    member_code: string;
    name: string;
    phone_masked?: string | null;
  }>;
  target_groups: Array<{ id: string; name: string }>;
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
    development_center_match_counts: {
      center_name: string;
      match_count: number;
    }[];
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
    matched_profile_fields_needing_reconciliation: {
      field: string;
      count: number;
    }[];
  };
  production_existing_direct_class_records: {
    class_name: string;
    count: number;
  }[];
  issues: { code: string; count: number }[];
  write_gates: string[];
};

export type FullClassRosterPreflight = {
  mode: "READ_ONLY_FULL_CLASS_ROSTER_PREFLIGHT";
  automatic_production_write_allowed: false;
  source_name: string;
  source_sha256: string;
  source: {
    sheet_name: string;
    active_member_count: number;
    with_class_count: number;
    missing_class_count: number;
    ordinary_class_member_count: number;
    direct_class_member_count: number;
    ordinary_class_count: number;
    direct_class_count: number;
    valid_group_pair_count: number;
    ordinary_group_pair_count: number;
    direct_group_pair_count: number;
    note_only_group_count: number;
    by_center: { center_name: string; count: number }[];
    by_class: { class_name: string; count: number }[];
  };
  organization: {
    root_match_count: number;
    development_center_match_counts: {
      center_name: string;
      match_count: number;
    }[];
    class_status: {
      class_name: string;
      member_count: number;
      scope: "DIRECT" | "ORDINARY";
      expected_parent: string;
      active_class_matches: number;
      correct_parent_matches: number;
      action: "REUSE" | "CREATE_OR_RESOLVE" | "REVIEW";
    }[];
    class_action_summary: { action: string; count: number }[];
    group_action_summary: { action: string; count: number }[];
  };
  matching: {
    summary: { status: string; count: number }[];
    no_production_match_by_class: { class_name: string; count: number }[];
    fields_or_relations_needing_reconciliation: {
      field: string;
      count: number;
    }[];
  };
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
  org_unit_type?: string;
  study_org_unit_id?: string;
  class_name?: string | null;
  session_count: number;
  record_count: number;
  present_count: number;
  class_member_count: number;
  class_present_count: number;
  region_member_count: number;
  region_present_count: number;
};

export type AttendanceActivityRow = AttendanceEventGroup & {
  external_group_id?: string;
  session_id: number;
  session_code: string;
  session_name?: string | null;
  session_order: number;
  scheduled_start_at?: string | null;
  scheduled_end_at?: string | null;
  session_status?: string;
  display_title?: string;
};

export type AttendanceSession = {
  id: number;
  event_group_id: number;
  session_code: string;
  session_name?: string;
  session_order: number;
  checkin_start_at?: string;
  scheduled_start_at?: string;
  scheduled_end_at?: string;
  checkin_end_at?: string;
  status: string;
  record_count: number;
  present_count: number;
  class_member_count: number;
  class_present_count: number;
  region_member_count: number;
  region_present_count: number;
  total_points?: number | string | null;
};

export type AttendanceClassParticipation = {
  class_org_unit_id: string;
  class_name: string;
  class_member_count: number;
  class_present_count: number;
};

export type AttendanceEventGroupDetail = {
  group: AttendanceEventGroup & {
    external_group_id: string;
    source_updated_at?: string;
    created_at: string;
    updated_at: string;
  };
  sessions: AttendanceSession[];
  class_breakdown: AttendanceClassParticipation[];
};

export type AttendanceRecord = {
  id: number;
  attendance_session_id: number;
  member_id?: number | null;
  member_code_snapshot?: string | null;
  name_snapshot?: string | null;
  participant_type: string;
  score_eligible: number;
  attendance_status: string;
  checked_at?: string | null;
  checked_at_review_status?: "TIME_BEFORE_CHECKIN_START" | null;
  checkin_source?: string | null;
  session_code: string;
  session_name?: string | null;
  title?: string | null;
  event_date: string;
  final_points?: number | string | null;
  is_late?: number | null;
  is_early_leave?: number | null;
};

export type AttendanceSyncStatus = {
  state: "NO_RUNS" | "RUNNING" | "HEALTHY" | "WARNING" | "CRITICAL";
  alert_threshold: number;
  consecutive_failure_count: number;
  last_run: {
    status: string;
    started_at: string;
    finished_at?: string;
    received_sessions: number;
    received_records: number;
    error_count: number;
    has_error_summary: boolean;
  } | null;
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
  production_linked: number;
  production_unlinked: number;
  importable: number;
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

export type RenewalCycle = {
  id: number;
  member_id: number;
  member_code: string;
  member_name: string;
  renewal_year: number;
  org_unit_id: string;
  org_name: string;
  member_org_unit_id: string;
  member_development_org_unit_id?: string;
  member_class_name?: string;
  member_group_name?: string;
  imported_org_unit_id?: string;
  imported_org_name?: string;
  due_month: number;
  phase: string;
  status: string;
  result?: string;
  assigned_user_id?: number;
  assigned_user_name?: string;
  completed_at?: string;
  updated_at: string;
};

export type RenewalCoverageSummary = {
  member_total: number;
  active_member_total: number;
  cycle_total: number;
  ready_to_create_count: number;
  missing_renewal_month_count: number;
  inactive_member_count: number;
  suspended_member_count: number;
};

export type RenewalCoverageRow = {
  member_id: number;
  member_code: string;
  member_name: string;
  member_status: "ACTIVE" | "INACTIVE" | "SUSPENDED";
  renewal_month?: string | null;
  member_class_name?: string | null;
  member_group_name?: string | null;
  org_unit_id: string;
  org_name: string;
  cycle_id?: number | null;
  due_month?: number | null;
  cycle_status?: string | null;
  updated_at?: string | null;
  sync_status:
    | "SYNCED"
    | "SYNCED_INACTIVE"
    | "SYNCED_SUSPENDED"
    | "READY_TO_CREATE"
    | "MISSING_RENEWAL_MONTH"
    | "INACTIVE"
    | "SUSPENDED";
  can_create_cycle: boolean;
};

export type RenewalCoverage = {
  year: number;
  summary: RenewalCoverageSummary;
  rows: RenewalCoverageRow[];
  truncated: boolean;
};

export type SystemEnvironment = {
  environment: string;
  production: boolean;
  production_mutations_allowed: boolean;
  deployment_read_only: boolean;
  identity_authorization_enabled: boolean;
  identity_admin_writes_enabled: boolean;
  volunteer_service_invitations_enabled: boolean;
  member_service_signal_feedback_enabled: boolean;
};

export type RenewalFollowup = {
  id: number;
  followed_at: string;
  followed_by?: number;
  channel: string;
  summary: string;
  intention?: string;
  needs_support: number;
  next_action?: string;
  next_followup_at?: string;
};

export const getFollowupTasks = (status?: string) =>
  http.request<{ success: boolean; data: FollowupTask[] }>(
    "get",
    "/api/v1/followups/tasks",
    { params: status ? { status } : undefined }
  );

export const getMembers = (orgUnitId?: string) =>
  http.request<{ success: boolean; data: Member[] }>("get", "/api/v1/members", {
    params: orgUnitId ? { org_unit_id: orgUnitId } : undefined
  });

export const getMemberChangeHistory = (memberId: number) =>
  http.request<{ success: boolean; data: MemberChangeHistory[] }>(
    "get",
    `/api/v1/members/${memberId}/change-history`
  );

export const getMemberTimeline = (memberId: number, limit = 100) =>
  http.request<{ success: boolean; data: MemberTimeline }>(
    "get",
    `/api/v1/members/${memberId}/timeline`,
    { params: { limit } }
  );

export const getBirthdayGreetingContext = (memberId: number) =>
  http.request<{ success: boolean; data: BirthdayGreetingContext }>(
    "get",
    `/api/v1/members/${memberId}/birthday-greeting-context`
  );

export const generateBirthdayGreetingDraft = (
  memberId: number,
  data: {
    selected_memory_ids: string[];
    tone: "standard" | "warm" | "concise";
  }
) =>
  http.request<{ success: boolean; data: BirthdayGreetingDraft }>(
    "post",
    `/api/v1/members/${memberId}/birthday-greeting-draft`,
    { data }
  );

export const submitMemberServiceSignalFeedback = (
  memberId: number,
  signalCode: string,
  data: {
    rule_version: string;
    status: MemberServiceSignalFeedbackStatus;
  }
) =>
  http.request<{
    success: boolean;
    data: {
      id: number;
      status: MemberServiceSignalFeedbackStatus;
      created_at: string;
    };
  }>(
    "post",
    `/api/v1/members/${memberId}/service-signals/${signalCode}/feedback`,
    { data }
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
  class_org_unit_id?: string;
  group_org_unit_id?: string;
  birthday?: string;
  join_date?: string;
  study_start_date?: string;
  membership_years?: number;
  renewal_month?: string;
  renewal_month_overridden?: boolean;
  status?: string;
  position?: string;
  referrer?: string;
  referrer_center?: string;
  industry_category?: string;
  industry?: string;
  company_products?: string;
  annual_sales?: string;
  employee_count?: number;
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

export const getLearningOrgManagement = () =>
  http.request<{ success: boolean; data: LearningOrgManagement }>(
    "get",
    "/api/v1/iam/org-units/learning-management"
  );

export const createLearningOrgUnit = (data: {
  name: string;
  unit_type: "CLASS" | "GROUP";
  parent_id: string;
  confirmation: string;
}) =>
  http.request<{ success: boolean; data: { id: string } }>(
    "post",
    "/api/v1/iam/org-units/learning-management",
    { data }
  );

export const previewLearningOrgMove = (
  unitId: string,
  targetParentId: string
) =>
  http.request<{ success: boolean; data: LearningOrgMovePreview }>(
    "get",
    `/api/v1/iam/org-units/${unitId}/move-preview`,
    { params: { target_parent_id: targetParentId } }
  );

export const moveLearningOrgUnit = (
  unitId: string,
  data: {
    target_parent_id: string;
    reason: string;
    confirmation: string;
  }
) =>
  http.request<{ success: boolean; data: LearningOrgMovePreview }>(
    "post",
    `/api/v1/iam/org-units/${unitId}/move`,
    { data }
  );

export const deactivateLearningOrgUnit = (
  unitId: string,
  data: { reason: string; confirmation: string }
) =>
  http.request<{ success: boolean; data: { id: string; is_active: boolean } }>(
    "post",
    `/api/v1/iam/org-units/${unitId}/deactivate`,
    { data }
  );

export const applyDuplicateClassCleanup = (data: {
  confirmation: string;
  class_names: string[];
}) =>
  http.request<{ success: boolean; data: { deactivated_duplicate_classes: number } }>(
    "post",
    "/api/v1/iam/org-units/class-name-cleanup",
    { data }
  );

export const getLearningGroupMemberTransferOptions = (unitId: string) =>
  http.request<{ success: boolean; data: LearningGroupMemberTransferOptions }>(
    "get",
    `/api/v1/iam/org-units/${unitId}/group-member-transfer-options`
  );

export const transferLearningGroupMember = (
  unitId: string,
  data: {
    member_id: number;
    target_group_org_unit_id: string;
    reason: string;
    confirmation: string;
  }
) =>
  http.request<{ success: boolean; data: { member_id: number } }>(
    "post",
    `/api/v1/iam/org-units/${unitId}/group-member-transfer`,
    { data }
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

export const previewFullClassRosterWorkbook = (workbook: File) => {
  const data = new FormData();
  data.append("workbook", workbook);
  return http.request<{
    success: boolean;
    data: FullClassRosterPreflight;
  }>("post", "/api/v1/class-roster-preflight/preview", {
    data,
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000
  });
};

export const applyFullClassRosterOrganization = (
  workbook: File,
  confirmationText: string
) => {
  const data = new FormData();
  data.append("workbook", workbook);
  data.append("confirmation_text", confirmationText);
  return http.request<{
    success: boolean;
    data: {
      batch_id: number;
      status: "APPLIED" | "ALREADY_APPLIED";
      created_classes: number;
      created_groups: number;
      members_changed: 0;
    };
  }>("post", "/api/v1/class-roster-org-import/apply", {
    data,
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000
  });
};

export const applyFullClassRosterRelations = (workbook: File) => {
  const data = new FormData();
  data.append("workbook", workbook);
  return http.request<{
    success: boolean;
    data: {
      batch_id: number;
      status: "APPLIED" | "ALREADY_APPLIED";
      matched_members?: number;
      relations_added?: number;
      members_changed?: 0;
    };
  }>("post", "/api/v1/class-roster-org-import/apply-relations", {
    data,
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000
  });
};

export const applyDirectClassWorkbook = (workbook: File) => {
  const data = new FormData();
  data.append("workbook", workbook);
  return http.request<{
    success: boolean;
    data: {
      batch_id: number;
      created: number;
      updated: number;
      relations: number;
      notes: number;
    };
  }>("post", "/api/v1/direct-class-import/apply", {
    data,
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 120000
  });
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

export const getMemberEditProfile = (memberId: number) =>
  http.request<{
    success: boolean;
    data: MemberEditProfile;
  }>("get", `/api/v1/members/${memberId}/edit-profile`);

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

export const getAttendanceActivityRows = (month?: string) =>
  http.request<{ success: boolean; data: AttendanceActivityRow[] }>(
    "get",
    "/api/v1/attendance/activity-sessions",
    { params: month ? { month } : undefined }
  );

export const getAttendanceSyncStatus = () =>
  http.request<{ success: boolean; data: AttendanceSyncStatus }>(
    "get",
    "/api/v1/attendance/sync/status"
  );

export const getRenewalAssignees = (orgUnitId?: string) =>
  http.request<{ success: boolean; data: FollowupAssignee[] }>(
    "get",
    "/api/v1/renewals/assignees",
    { params: orgUnitId ? { org_unit_id: orgUnitId } : undefined }
  );

export const updateMember = (
  memberId: number,
  data: {
    name?: string;
    status?: string;
    phone?: string | null;
    company_name?: string | null;
    gender?: string | null;
    district?: string | null;
    company_address?: string | null;
    birthday?: string | null;
    join_date?: string | null;
    study_start_date?: string | null;
    membership_years?: number | null;
    renewal_month?: string | null;
    renewal_month_overridden?: boolean;
    position?: string | null;
    referrer?: string | null;
    referrer_center?: string | null;
    industry_category?: string | null;
    industry?: string | null;
    company_products?: string | null;
    annual_sales?: string | null;
    employee_count?: number | null;
    company_size?: string | null;
    profit_margin?: string | null;
    notes?: string | null;
    org_unit_id?: string;
    development_org_unit_id?: string | null;
    class_org_unit_id?: string | null;
    group_org_unit_id?: string | null;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "patch",
    `/api/v1/members/${memberId}`,
    { data }
  );

export const getAttendanceEventGroupDetail = (groupId: number) =>
  http.request<{ success: boolean; data: AttendanceEventGroupDetail }>(
    "get",
    `/api/v1/attendance/event-groups/${groupId}`
  );

export const getAttendanceRecords = (
  eventGroupId: number,
  sessionId?: number
) =>
  http.request<{ success: boolean; data: AttendanceRecord[] }>(
    "get",
    "/api/v1/attendance/records",
    {
      params: {
        event_group_id: eventGroupId,
        ...(sessionId ? { session_id: sessionId } : {})
      }
    }
  );

export const downloadAttendanceRecords = (
  eventGroupId: number,
  sessionId?: number
) =>
  http.request<Blob>(
    "get",
    `/api/v1/attendance/event-groups/${eventGroupId}/records.xlsx`,
    {
      params: sessionId ? { session_id: sessionId } : undefined,
      responseType: "blob"
    }
  );

export type AttendanceReconciliationItem = {
  key:
    | "unmatched_attendance_records"
    | "active_members_missing_phone_hash"
    | "active_members_missing_primary_region"
    | "active_members_missing_study_class"
    | "active_members_missing_study_group"
    | "active_members_expected_no_study_group";
  count: number;
};

export const getAttendanceReconciliationSummary = (month?: string) =>
  http.request<{
    success: boolean;
    data: {
      scope: "AGGREGATE_ONLY";
      write_enabled: false;
      items: AttendanceReconciliationItem[];
    };
  }>("get", "/api/v1/attendance/reconciliation-summary", {
    params: month ? { month } : undefined
  });

export type AttendanceReconciliationQueueRow = {
  id: number;
  member_code_snapshot?: string;
  name_snapshot?: string;
  attendance_status: string;
  checked_at?: string;
  session_name?: string;
  title?: string;
  event_date: string;
  org_unit_id: string;
  study_org_unit_id?: string;
};

export const getAttendanceReconciliationQueue = (params?: {
  issue?: AttendanceReconciliationItem["key"];
  month?: string;
  limit?: number;
  offset?: number;
}) =>
  http.request<{
    success: boolean;
    data: {
      scope: "MANUAL_REVIEW_READ_ONLY";
      issue: AttendanceReconciliationItem["key"];
      write_enabled: false;
      total: number;
      limit: number;
      offset: number;
      rows: AttendanceReconciliationQueueRow[];
    };
  }>("get", "/api/v1/attendance/reconciliation-queue", { params });

export type AttendanceReconciliationBreakdownRow = {
  org_unit_id: string;
  org_name: string;
  count: number;
};

export const getAttendanceReconciliationBreakdown = (
  issue: AttendanceReconciliationItem["key"],
  month?: string
) =>
  http.request<{
    success: boolean;
    data: {
      scope: "AGGREGATE_ONLY";
      issue: AttendanceReconciliationItem["key"];
      rows: AttendanceReconciliationBreakdownRow[];
    };
  }>("get", "/api/v1/attendance/reconciliation-breakdown", {
    params: { issue, ...(month ? { month } : {}) }
  });

export const getRenewalOverview = (year: number) =>
  http.request<{
    success: boolean;
    data: { year: number; rows: RenewalOverviewRow[] };
  }>("get", "/api/v1/renewals/overview", { params: { year } });

export const previewRenewalImport = (renewalFile: File, masterFile: File) => {
  const data = new FormData();
  data.append("renewal_file", renewalFile);
  data.append("master_file", masterFile);
  return http.request<{
    success: boolean;
    data: {
      batch_id: number | null;
      persisted: boolean;
      summary: RenewalImportSummary;
      review_rows: RenewalImportSample[];
      assistance_rows: RenewalImportSample[];
      matched_samples: RenewalImportSample[];
      issue_summary: Record<string, number>;
    };
  }>("post", "/api/v1/renewals/imports/preview", {
    data,
    timeout: 120000,
    headers: { "Content-Type": "multipart/form-data" }
  });
};

export const getRenewalCycles = (
  year: number,
  params: {
    status?: string;
    org_unit_id?: string;
    due_month?: number;
    member_name?: string;
    renewal_status?: "UNRENEWED" | "RENEWED" | "ALL";
    include_past?: boolean;
  } = {}
) =>
  http.request<{ success: boolean; data: RenewalCycle[] }>(
    "get",
    "/api/v1/renewals/cycles",
    { params: { year, ...params } }
  );

export const getRenewalCoverage = (
  year: number,
  params: {
    org_unit_id?: string;
    member_name?: string;
    include_synced?: boolean;
    actionable_only?: boolean;
    limit?: number;
  } = {}
) =>
  http.request<{ success: boolean; data: RenewalCoverage }>(
    "get",
    "/api/v1/renewals/coverage",
    { params: { year, ...params } }
  );

export const createRenewalCycleFromMember = (
  memberId: number,
  renewalYear: number
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/renewals/cycles/from-member/${memberId}`,
    {
      data: {
        renewal_year: renewalYear,
        confirmation: "确认从学员主档建立续费周期"
      }
    }
  );

export const getSystemEnvironment = () =>
  http.request<SystemEnvironment>("get", "/api/v1/system/environment");

export const applyRenewalImport = (
  batchId: number,
  renewalYear: number,
  confirmation: string
) =>
  http.request<{
    success: boolean;
    data: { created: number; updated: number; skipped: number };
  }>("post", `/api/v1/renewals/imports/${batchId}/apply`, {
    data: { renewal_year: renewalYear, confirmation }
  });

export const updateRenewalCycle = (
  cycleId: number,
  data: {
    status?: string;
    phase?: string;
    result?: string;
    assigned_user_id?: number;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "patch",
    `/api/v1/renewals/cycles/${cycleId}`,
    { data }
  );

export const getRenewalFollowups = (cycleId: number) =>
  http.request<{ success: boolean; data: RenewalFollowup[] }>(
    "get",
    `/api/v1/renewals/cycles/${cycleId}/followups`
  );

export const createRenewalFollowup = (
  cycleId: number,
  data: {
    channel: string;
    summary: string;
    intention?: string;
    needs_support?: boolean;
    next_action?: string;
    next_followup_at?: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/renewals/cycles/${cycleId}/followups`,
    { data }
  );
