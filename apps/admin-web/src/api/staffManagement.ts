import { http } from "@/utils/http";

export type StaffScopeType = "UNIT" | "SUBTREE";

export type StaffAuthorizationGrant = {
  id?: number | null;
  role_key: string;
  role_name: string;
  org_unit_id: string;
  org_name?: string | null;
  scope_type: StaffScopeType;
  valid_from?: string | null;
  valid_until?: string | null;
  status?: string | null;
  sensitive_levels?: string[];
};

export type StaffPosition = {
  id?: number;
  position_key: string;
  position_name: string;
  valid_from?: string | null;
  valid_until?: string | null;
  status?: string | null;
};

export type StaffRecord = {
  id: number;
  name: string;
  login_account: string;
  phone_masked?: string | null;
  gender?: string | null;
  is_active: boolean;
  last_login_at?: string | null;
  institution_id: string;
  institution_name: string;
  department_name?: string | null;
  supervisor_user_id?: number | null;
  supervisor_name?: string | null;
  employment_status: string;
  started_on?: string | null;
  ended_on?: string | null;
  positions: StaffPosition[];
  authorization_grants: StaffAuthorizationGrant[];
  scopes: Array<{
    org_unit_id: string;
    org_name?: string | null;
    scope_type: StaffScopeType;
  }>;
  authorization_mode: "EXPLICIT" | "LEGACY_COMPATIBILITY" | "UNCONFIGURED";
};

export type StaffRole = {
  role_key: string;
  role_name: string;
  sensitive_levels: string[];
  assignable: boolean;
  permissions: Array<{
    permission_key: string;
    permission_name: string;
    sensitive_level: "INTERNAL" | "SENSITIVE" | "RESTRICTED";
  }>;
};

export type StaffCatalog = {
  writes_enabled: boolean;
  actor_is_highest_admin: boolean;
  positions: Array<{
    position_key: string;
    position_name: string;
    duty_description: string;
    role_key?: string | null;
    role_name?: string | null;
    mapping_status: "AUTO" | "MAPPING_REVIEW_REQUIRED";
  }>;
  roles: StaffRole[];
  org_units: Array<{
    id: string;
    unit_code: string;
    name: string;
    unit_type: string;
    parent_id?: string | null;
  }>;
  institutions: Array<{
    id: string;
    institution_code: string;
    name: string;
    source_name?: string;
    business_code?: string;
    institution_type: string;
    parent_id?: string | null;
    scope_root_org_unit_id?: string | null;
    scope_root_name?: string | null;
    scope_available?: boolean;
  }>;
  missing_institutions: Array<{
    business_code: string;
    name: string;
    reason: string;
  }>;
  departments: string[];
  supervisors: Array<{ id: number; name: string; institution_name: string }>;
  scope_types: StaffScopeType[];
};

export type StaffGrantInput = {
  role_key: string;
  org_unit_id: string;
  scope_type: StaffScopeType;
  // Legacy archive fields only; normal management no longer sends terms.
  valid_from?: string | null;
  valid_until?: string | null;
};

export type StaffPayload = {
  name: string;
  login_account?: string | null;
  temporary_password?: string | null;
  is_active?: boolean;
  phone?: string | null;
  gender: "MALE" | "FEMALE";
  replace_phone?: boolean;
  institution_id: string;
  department_name?: string | null;
  supervisor_user_id?: number | null;
  position_keys: string[];
  employment_status?: "ACTIVE" | "LEAVE";
  started_on?: string | null;
  ended_on?: string | null;
  responsibility_org_unit_id?: string;
  responsibility_scope_type?: StaffScopeType;
  grants?: StaffGrantInput[];
  authorization_basis?: string;
  authorization_reason?: string;
};

export type StaffChangePreview = {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  diff: {
    added_grants: StaffAuthorizationGrant[];
    removed_grants: StaffAuthorizationGrant[];
    changed_grants: Array<{
      before: StaffAuthorizationGrant;
      after: StaffAuthorizationGrant;
    }>;
    added_position_keys: string[];
    removed_position_keys: string[];
  };
  sensitive_expansion: boolean;
  requires_business_reason: boolean;
};

export type AuthorizationMigrationPreview = {
  employment_id: number;
  user_id?: number | null;
  name: string;
  current_positions: StaffPosition[];
  current_permissions: string[];
  current_service_scopes: Array<{
    org_unit_id: string;
    org_name?: string | null;
    scope_type: StaffScopeType;
  }>;
  proposed_authorization_grants: StaffAuthorizationGrant[];
  proposed_permissions: string[];
  permission_diff: { added: string[]; removed: string[] };
  deduplicated_grant_count: number;
  status: "SAFE_TO_MIGRATE" | "REVIEW_REQUIRED";
  blockers: string[];
  write_action: "PREVIEW_ONLY";
};

export const getStaffCatalog = () =>
  http.request<{ success: boolean; data: StaffCatalog }>(
    "get",
    "/api/v1/staff-management/catalog"
  );

export const getStaffList = (params?: {
  org_unit_id?: string;
  department_name?: string;
  position_key?: string;
  role_key?: string;
  is_active?: boolean;
  query?: string;
}) =>
  http.request<{ success: boolean; data: StaffRecord[] }>(
    "get",
    "/api/v1/staff-management/staff",
    { params }
  );

export const getStaff = (userId: number) =>
  http.request<{ success: boolean; data: StaffRecord }>(
    "get",
    `/api/v1/staff-management/staff/${userId}`
  );

export const createStaff = (data: StaffPayload & { login_account: string }) =>
  http.request<{
    success: boolean;
    data: { id: number; employment_id: number; temporary_password?: string | null };
  }>("post", "/api/v1/staff-management/staff", { data });

export const previewStaffChange = (userId: number, data: StaffPayload) =>
  http.request<{ success: boolean; data: StaffChangePreview }>(
    "post",
    `/api/v1/staff-management/staff/${userId}/change-preview`,
    { data }
  );

export const updateStaff = (userId: number, data: StaffPayload) =>
  http.request<{
    success: boolean;
    data: { id: number; sessions_revoked: boolean; preview: StaffChangePreview };
  }>("put", `/api/v1/staff-management/staff/${userId}`, { data });

export const getAuthorizationMigrationPreview = () =>
  http.request<{ success: boolean; data: AuthorizationMigrationPreview[] }>(
    "get",
    "/api/v1/staff-management/authorization-migration-preview"
  );

export const resetStaffPassword = (
  userId: number,
  data: { password: string; reason?: string }
) =>
  http.request<{ success: boolean; data: { id: number; sessions_revoked: boolean } }>(
    "post",
    `/api/v1/iam/users/${userId}/password`,
    { data }
  );
