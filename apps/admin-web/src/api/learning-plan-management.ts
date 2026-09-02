import { http } from "@/utils/http";

export type LearningPlan = {
  id: number;
  plan_key: string;
  plan_name: string;
  version_label: string;
  duration_cycles: number;
  status: "DRAFT" | "PUBLISHED" | "RETIRED" | "ARCHIVED";
  source_name?: string | null;
  cohort_tracks: { cohort_month: number | null; cycles: unknown[] }[];
};

export type LearningPlanBinding = {
  id: number;
  plan_version_id: number;
  plan_key: string;
  plan_name: string;
  version_label: string;
  cohort_month: number | null;
  started_at: string | null;
  status: "ACTIVE" | "COMPLETED" | "ENDED";
  duration_cycles: number;
  learning_round: number;
  start_cycle_index: number;
  ended_at: string | null;
  ended_reason: string | null;
  previous_binding_id: number | null;
  transition_type: "INITIAL" | "RESTART" | "RESUME" | "PLAN_SWITCH" | "CORRECTION";
  plan_status?: "DRAFT" | "PUBLISHED" | "RETIRED" | "ARCHIVED" | null;
  cycle_summary?: {
    materialized_cycles: number;
    completed_through_cycle: number;
    open_cycle_index: number | null;
  };
  is_current?: boolean;
};

export type LearningPlanHealthIssue = {
  class_org_unit_id: string;
  class_name: string;
  unit_code?: string | null;
  issue_type: string;
  severity: "BLOCKER";
  current_data: Record<string, unknown>;
  suggested_action: string;
};

export type LearningPlanHealthClass = {
  class_org_unit_id: string;
  unit_code: string;
  class_name: string;
  binding: LearningPlanBinding | null;
  plan_status: LearningPlanBinding["plan_status"];
  current_cycle: {
    learning_cycle_index: number;
    plan_cycle_id: number;
    cycle_status: string;
  } | null;
  group_count: number;
  volunteer_permission: "PASS" | "BLOCKED";
  status: "READY" | "BLOCKED";
  issues: LearningPlanHealthIssue[];
};

export type LearningPlanHealth = {
  generated_at: string;
  scope: string;
  assessment: "GO" | "NO-GO";
  summary: Record<string, number | string>;
  classes: LearningPlanHealthClass[];
  issues: LearningPlanHealthIssue[];
};

export type LearningPlanHistory = {
  class_org_unit_id: string;
  current_binding: LearningPlanBinding | null;
  bindings: LearningPlanBinding[];
  events: {
    action: string;
    resource_id?: string | null;
    purpose?: string | null;
    result?: string | null;
    before?: unknown;
    after?: unknown;
    created_at: string;
  }[];
  history_preserved: boolean;
};

export const getLearningPlans = () =>
  http.request<{ success: boolean; data: LearningPlan[] }>(
    "get",
    "/api/v1/learning-plans"
  );

export const getLearningPlanHealth = (classOrgUnitId?: string) =>
  http.request<{ success: boolean; data: LearningPlanHealth }>(
    "get",
    "/api/v1/classes/learning-plan-health",
    { params: classOrgUnitId ? { class_org_unit_id: classOrgUnitId } : undefined }
  );

export const getLearningPlanRecommendation = (startedAt: string) =>
  http.request<{ success: boolean; data: {
    plan_version_id: number;
    plan_name: string;
    version_label: string;
    cohort_month: number;
    start_cycle_index: number;
    started_at: string;
  } }>("get", "/api/v1/learning-plans/recommendation", {
    params: { started_at: startedAt }
  });

export const getLearningPlanHistory = (classOrgUnitId: string) =>
  http.request<{ success: boolean; data: LearningPlanHistory }>(
    "get",
    `/api/v1/classes/${encodeURIComponent(classOrgUnitId)}/learning-plan-history`
  );

export const bindLearningPlan = (
  classOrgUnitId: string,
  data: {
    plan_version_id: number;
    cohort_month: number | null;
    started_at?: string;
    start_cycle_index: number;
  }
) =>
  http.request<{ success: boolean; data: unknown }>(
    "post",
    `/api/v1/classes/${encodeURIComponent(classOrgUnitId)}/learning-plan-binding`,
    { data }
  );

export const resumeLearningPlan = (
  classOrgUnitId: string,
  data: {
    plan_version_id: number;
    cohort_month: number | null;
    started_at: string;
    start_cycle_index: number;
    reason: string;
  }
) =>
  http.request<{ success: boolean; data: unknown }>(
    "post",
    `/api/v1/classes/${encodeURIComponent(classOrgUnitId)}/learning-plan-resume`,
    { data }
  );

export const restartLearningPlan = (
  classOrgUnitId: string,
  data: {
    plan_version_id: number;
    cohort_month: number;
    started_at: string;
    reason: string;
  }
) =>
  http.request<{ success: boolean; data: unknown }>(
    "post",
    `/api/v1/classes/${encodeURIComponent(classOrgUnitId)}/learning-plan-restart`,
    { data }
  );

export const correctLearningPlan = (
  classOrgUnitId: string,
  data: {
    plan_version_id: number;
    cohort_month: number | null;
    learning_cycle_index: number;
    reason: string;
  }
) =>
  http.request<{ success: boolean; data: unknown }>(
    "post",
    `/api/v1/classes/${encodeURIComponent(classOrgUnitId)}/learning-plan-correction`,
    { data }
  );
