import { http } from "@/utils/http";

export type VolunteerServiceUnit = {
  id: string;
  unit_code: string;
  name: string;
  system_type: "CLASS_TEAM" | "GOVERNANCE" | "COMMITTEE_LINE" | "ACTIVITY";
  system_name: string;
  line_type: "LEARNING" | "OPERATIONS" | "DEVELOPMENT" | "GENERAL" | "SUPERVISION";
  line_name: string;
  parent_id?: string | null;
  parent_name?: string | null;
  home_shuku_org_unit_id?: string | null;
  home_shuku_name?: string | null;
  service_target_org_unit_id: string;
  service_target_name?: string | null;
  service_target_unit_type?: string | null;
  is_active: boolean;
  sort_order: number;
};

export type VolunteerPositionOption = {
  position_key: string;
  position_name: string;
  scope_level: string;
  capabilities: string[];
  capability_names: string[];
};

export type VolunteerAppointment = {
  id: number;
  member_id: number;
  member_name: string;
  member_status: string;
  home_shuku_name?: string | null;
  system_type?: string | null;
  system_name?: string | null;
  line_type?: string | null;
  line_name?: string | null;
  service_unit_id?: string | null;
  service_unit_name?: string | null;
  position_key: string;
  position_name: string;
  service_target_org_unit_id: string;
  service_target_name?: string | null;
  capabilities: string[];
  status: string;
  status_name: string;
  created_at?: string;
  ended_at?: string | null;
};

export type VolunteerRecommendationRule = {
  id: number;
  source_service_unit_id: string;
  source_service_unit_name: string;
  source_position_key: string;
  source_position_name: string;
  target_service_unit_id: string;
  target_service_unit_name: string;
  target_position_key: string;
  target_position_name: string;
  is_active: boolean;
};

const base = "/api/v1/volunteer-management";

export const getVolunteerServiceUnits = (params?: Record<string, unknown>) =>
  http.request<{ success: boolean; data: VolunteerServiceUnit[] }>("get", `${base}/service-units`, { params });

export const createVolunteerServiceUnit = (data: Omit<VolunteerServiceUnit, "id" | "system_name" | "line_name" | "parent_name" | "home_shuku_name" | "service_target_name" | "service_target_unit_type" | "is_active">) =>
  http.request<{ success: boolean; data: VolunteerServiceUnit }>("post", `${base}/service-units`, { data });

export const getVolunteerPositionOptions = (service_unit_id: string) =>
  http.request<{ success: boolean; data: { service_unit: VolunteerServiceUnit; positions: VolunteerPositionOption[] } }>(
    "get",
    `${base}/position-options`,
    { params: { service_unit_id } }
  );

export const getVolunteerAppointments = (params?: Record<string, unknown>) =>
  http.request<{ success: boolean; data: VolunteerAppointment[] }>("get", `${base}/appointments`, { params });

export const createVolunteerAppointment = (data: {
  member_id: number;
  service_unit_id: string;
  position_key: string;
  confirmation_note: string;
}) => http.request<{ success: boolean; data: { id: number; recommendations: VolunteerRecommendationRule[] } }>("post", `${base}/appointments`, { data });

export const changeVolunteerAppointmentStatus = (
  id: number,
  data: { status: "ACTIVE" | "SUSPENDED" | "ENDED" | "REVOKED"; reason: string }
) => http.request<{ success: boolean; data: { id: number; status: string } }>("post", `${base}/appointments/${id}/status`, { data });

export const getVolunteerRecommendationRules = (params?: Record<string, unknown>) =>
  http.request<{ success: boolean; data: VolunteerRecommendationRule[] }>("get", `${base}/recommendation-rules`, { params });

export const createVolunteerRecommendationRule = (data: {
  source_service_unit_id: string;
  source_position_key: string;
  target_service_unit_id: string;
  target_position_key: string;
}) => http.request<{ success: boolean; data: VolunteerRecommendationRule }>("post", `${base}/recommendation-rules`, { data });

export const acceptVolunteerRecommendation = (appointmentId: number, ruleId: number, confirmation_note: string) =>
  http.request<{ success: boolean; data: { target_appointment_id: number } }>(
    "post",
    `${base}/appointments/${appointmentId}/recommendations/${ruleId}/accept`,
    { data: { confirmation_note } }
  );

export const getVolunteerMigrationPreview = () =>
  http.request<{ success: boolean; data: { migration_executed: boolean; counts: Record<string, number>; entries: Array<Record<string, unknown>> } }>("get", `${base}/migration-preview`);
