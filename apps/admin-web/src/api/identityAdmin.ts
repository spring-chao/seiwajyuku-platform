import { http } from "@/utils/http";

export type IdentityCatalog = {
  position_keys: string[];
  appointment_keys: string[];
  scope_types: Array<"UNIT" | "SUBTREE">;
  terminal_statuses: Array<"SUSPENDED" | "ENDED" | "REVOKED">;
  writes_enabled: boolean;
  permission_matrix: Array<{
    role_key: string;
    role_name: string;
    permissions: Array<{
      permission_key: string;
      permission_name: string;
      sensitive_level: "INTERNAL" | "SENSITIVE" | "RESTRICTED";
    }>;
  }>;
};

export type IdentityOrgOption = {
  id: string;
  unit_code: string;
  name: string;
  unit_type: string;
  parent_id?: string;
};

export type PositionAssignment = {
  id: number;
  position_key: string;
  valid_from?: string;
  valid_until?: string;
  status: string;
  source_reference?: string;
};

export type ServiceResponsibility = {
  id: number;
  org_unit_id: string;
  org_name: string;
  scope_type: string;
  valid_from?: string;
  valid_until?: string;
  status: string;
  source_reference?: string;
};

export type Employment = {
  id: number;
  institution_id: string;
  institution_name: string;
  status: string;
  started_on?: string;
  ended_on?: string;
  source_reference?: string;
  positions: PositionAssignment[];
  service_responsibilities: ServiceResponsibility[];
};

export type VolunteerAppointment = {
  id: number;
  appointment_key: string;
  org_unit_id: string;
  org_name: string;
  scope_type: string;
  starts_at: string;
  ends_at: string;
  status: string;
  source_reference: string;
};

export type TechnicalAssignment = {
  id: number;
  assignment_purpose: string;
  starts_at: string;
  ends_at: string;
  status: string;
  source_reference: string;
};

export type IdentityAccount = {
  id: number;
  username: string;
  display_name: string;
  is_active: number;
  person_id?: string;
  employments: Employment[];
  volunteer_appointments: VolunteerAppointment[];
  technical_assignments: TechnicalAssignment[];
};

type Confirmation = {
  source_reference: string;
  confirmation_note: string;
};

export const getIdentityCatalog = () =>
  http.request<{ success: boolean; data: IdentityCatalog }>(
    "get",
    "/api/v1/identity-admin/catalog"
  );
export const getIdentityAccounts = () =>
  http.request<{ success: boolean; data: IdentityAccount[] }>(
    "get",
    "/api/v1/identity-admin/accounts"
  );

export const getIdentityOrgOptions = () =>
  http.request<{ success: boolean; data: IdentityOrgOption[] }>(
    "get",
    "/api/v1/identity-admin/org-options"
  );

export const initializeAccountIdentity = (
  userId: number,
  data: Confirmation
) =>
  http.request<{ success: boolean; data: { person_id: string } }>(
    "post",
    `/api/v1/identity-admin/accounts/${userId}/initialize`,
    { data }
  );

export const createAccountEmployment = (
  userId: number,
  data: Confirmation & {
    position_key: string;
    started_on: string;
    ended_on?: string;
    service_responsibilities: Array<{
      org_unit_id: string;
      scope_type: "UNIT" | "SUBTREE";
    }>;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/identity-admin/accounts/${userId}/employments`,
    { data }
  );

export const createAccountVolunteerAppointment = (
  userId: number,
  data: Confirmation & {
    appointment_key: string;
    org_unit_id: string;
    scope_type: "UNIT" | "SUBTREE";
    starts_at: string;
    ends_at: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/identity-admin/accounts/${userId}/volunteer-appointments`,
    { data }
  );

export const createAccountTechnicalAssignment = (
  userId: number,
  data: Confirmation & {
    assignment_purpose: string;
    starts_at: string;
    ends_at: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number } }>(
    "post",
    `/api/v1/identity-admin/accounts/${userId}/technical-assignments`,
    { data }
  );

export const changeIdentityAssignmentStatus = (
  assignmentType: string,
  assignmentId: number,
  data: {
    status: "SUSPENDED" | "ENDED" | "REVOKED";
    reason: string;
  }
) =>
  http.request<{ success: boolean; data: { id: number; status: string } }>(
    "post",
    `/api/v1/identity-admin/assignments/${assignmentType}/${assignmentId}/status`,
    { data }
  );
