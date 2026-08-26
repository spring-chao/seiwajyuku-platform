import { http } from "@/utils/http";

export type EnrollmentApplicationStatus =
  "SUBMITTED" | "APPROVED" | "REJECTED" | "ENROLLED" | "CANCELLED";

export type EnrollmentPaymentStatus =
  "UNCONFIRMED" | "PAID" | "WAIVED" | "SPECIAL_APPROVED";

export type EnrollmentComputedStatus =
  | "PENDING_REVIEW"
  | "PENDING_PAYMENT"
  | "PENDING_CENTER"
  | "PENDING_ENROLLMENT"
  | "ENROLLED"
  | "REJECTED"
  | "CANCELLED";

export type PublicEnrollmentForm = {
  title: string;
  link_name: string;
  subtitle?: string;
  notice: string;
  privacy_notice: string;
  required_fields: string[];
  optional_fields: string[];
  industry_options: string[];
  invoice_types: { value: "NORMAL" | "SPECIAL" | "NONE"; label: string }[];
  profit_margin_options: { value: string; label: string }[];
  growth_target_options: { value: string; label: string }[];
  goal_year_options: string[];
  collects_organization: false;
};

export type PublicEnrollmentPayload = {
  name: string;
  phone: string;
  privacy_consent: true;
  gender?: "MALE" | "FEMALE";
  birthday: string;
  district?: string;
  political_status?: string;
  company_name: string;
  company_tax_id?: string;
  company_address: string;
  email?: string;
  position: string;
  referrer: string;
  invoice_type: "NORMAL" | "SPECIAL" | "NONE";
  invoice_info?: string;
  invoice_title?: string;
  invoice_tax_id?: string;
  invoice_registered_address?: string;
  invoice_phone?: string;
  invoice_bank?: string;
  invoice_account?: string;
  industry_category?: string;
  industry_other?: string;
  industry?: string;
  company_products: string;
  employee_count: number;
  books_read?: string;
  enrollment_reason_philosophy?: string;
  enrollment_reason_change?: string;
  enrollment_reason_other?: string;
  learning_years_goal?: string;
  learning_participation_goal?: string;
  business_goal?: string;
  other_goal?: string;
  goal_years?: string;
  revenue_growth_target?: string;
  profit_growth_target?: string;
  annual_sales: string;
  profit_margin?: string;
  notes?: string;
  rules_acknowledged: true;
};

export type EnrollmentApplicationListItem = {
  id: number;
  application_no: string;
  name: string;
  phone_masked: string;
  phone?: string;
  company_name?: string | null;
  application_status: EnrollmentApplicationStatus;
  payment_status: EnrollmentPaymentStatus;
  computed_status: EnrollmentComputedStatus;
  duplicate_member_risk: boolean;
  org_unit_id?: string | null;
  org_unit_name?: string | null;
  join_date?: string | null;
  converted_member_id?: number | null;
  created_at: string;
  updated_at: string;
};

export type EnrollmentApplicationDetail = EnrollmentApplicationListItem & {
  link_name: string;
  gender?: "MALE" | "FEMALE" | "OTHER" | null;
  birthday?: string | null;
  district?: string | null;
  political_status?: string | null;
  company_tax_id?: string | null;
  company_address?: string | null;
  email?: string | null;
  position?: string | null;
  referrer?: string | null;
  invoice_info?: string | null;
  invoice_type?: string | null;
  invoice_title?: string | null;
  invoice_tax_id?: string | null;
  invoice_registered_address?: string | null;
  invoice_phone?: string | null;
  invoice_bank?: string | null;
  invoice_account?: string | null;
  industry_category?: string | null;
  industry_other?: string | null;
  industry?: string | null;
  company_products?: string | null;
  employee_count?: number | null;
  books_read?: string | null;
  enrollment_reason_philosophy?: string | null;
  enrollment_reason_change?: string | null;
  enrollment_reason_other?: string | null;
  learning_years_goal?: string | null;
  learning_participation_goal?: string | null;
  business_goal?: string | null;
  other_goal?: string | null;
  goal_years?: string | null;
  revenue_growth_target?: string | null;
  profit_growth_target?: string | null;
  notes?: string | null;
  privacy_consent_at: string;
  reviewed_at?: string | null;
  reviewer_name?: string | null;
  review_note?: string | null;
  rejected_at?: string | null;
  rejection_reason?: string | null;
  payment_amount?: string | number | null;
  payment_note?: string | null;
  payment_confirmed_at?: string | null;
  payment_confirmer_name?: string | null;
  converted_at?: string | null;
  converter_name?: string | null;
  has_enterprise_financial_data: boolean;
  financial_fields_visible: boolean;
  invoice_fields_visible: boolean;
  rules_acknowledged: boolean;
  annual_sales?: string | null;
  profit_margin?: string | null;
  missing_gates: string[];
  can_enroll: boolean;
};

export type EnrollmentReviewPayload = {
  decision: "SAVE" | "APPROVE";
  review_note?: string;
  name?: string;
  gender?: "MALE" | "FEMALE" | null;
  birthday?: string | null;
  district?: string | null;
  political_status?: string | null;
  company_address?: string | null;
  company_tax_id?: string | null;
  email?: string | null;
  company_name?: string | null;
  position?: string | null;
  referrer?: string | null;
  invoice_info?: string | null;
  invoice_type?: string | null;
  invoice_title?: string | null;
  invoice_tax_id?: string | null;
  invoice_registered_address?: string | null;
  invoice_phone?: string | null;
  invoice_bank?: string | null;
  invoice_account?: string | null;
  industry_category?: string | null;
  industry_other?: string | null;
  industry?: string | null;
  company_products?: string | null;
  employee_count?: number | null;
  books_read?: string | null;
  enrollment_reason_philosophy?: string | null;
  enrollment_reason_change?: string | null;
  enrollment_reason_other?: string | null;
  learning_years_goal?: string | null;
  learning_participation_goal?: string | null;
  business_goal?: string | null;
  other_goal?: string | null;
  goal_years?: string | null;
  revenue_growth_target?: string | null;
  profit_growth_target?: string | null;
  annual_sales?: string | null;
  profit_margin?: string | null;
  notes?: string | null;
  org_unit_id?: string | null;
  join_date?: string | null;
};

export type EnrollmentLink = {
  id: number;
  name: string;
  status: "ACTIVE" | "DISABLED";
  created_at?: string;
  updated_at?: string;
  disabled_at?: string | null;
  last_rotated_at?: string | null;
  raw_token?: string;
};

export type EnrollmentMiniProgramCode = {
  link_id: number;
  name: string;
  page: string;
  image_data_url: string;
  generated_at: string;
};

export const getPublicEnrollmentForm = (token: string) =>
  http.request<{ success: boolean; data: PublicEnrollmentForm }>(
    "get",
    `/api/v1/public/enrollment/${encodeURIComponent(token)}`
  );

export const submitPublicEnrollment = (
  token: string,
  data: PublicEnrollmentPayload
) =>
  http.request<{
    success: boolean;
    data: { accepted: true; message: string };
  }>("post", `/api/v1/public/enrollment/${encodeURIComponent(token)}`, {
    data
  });

export const getEnrollmentApplications = (params?: {
  application_status?: EnrollmentApplicationStatus;
  payment_status?: EnrollmentPaymentStatus;
  query?: string;
  limit?: number;
}) =>
  http.request<{ success: boolean; data: EnrollmentApplicationListItem[] }>(
    "get",
    "/api/v1/enrollment-applications",
    { params }
  );

export const getEnrollmentApplication = (applicationId: number) =>
  http.request<{ success: boolean; data: EnrollmentApplicationDetail }>(
    "get",
    `/api/v1/enrollment-applications/${applicationId}`
  );

export const reviewEnrollmentApplication = (
  applicationId: number,
  data: EnrollmentReviewPayload
) =>
  http.request<{ success: boolean; data: EnrollmentApplicationDetail }>(
    "patch",
    `/api/v1/enrollment-applications/${applicationId}/review`,
    { data }
  );

export const confirmEnrollmentPayment = (
  applicationId: number,
  data: { payment_status: "PAID"; amount?: string; note?: string }
) =>
  http.request<{ success: boolean; data: EnrollmentApplicationDetail }>(
    "post",
    `/api/v1/enrollment-applications/${applicationId}/payment-confirmation`,
    { data }
  );

export const rejectEnrollmentApplication = (
  applicationId: number,
  reason: string
) =>
  http.request<{ success: boolean; data: EnrollmentApplicationDetail }>(
    "post",
    `/api/v1/enrollment-applications/${applicationId}/reject`,
    { data: { reason } }
  );

export const enrollApplication = (applicationId: number) =>
  http.request<{
    success: boolean;
    data: { application_id: number; member_id: number; idempotent: boolean };
  }>("post", `/api/v1/enrollment-applications/${applicationId}/enroll`);

export const getActiveEnrollmentLink = () =>
  http.request<{ success: boolean; data: EnrollmentLink | null }>(
    "get",
    "/api/v1/enrollment-links/active"
  );

export const createEnrollmentLink = (name: string) =>
  http.request<{ success: boolean; data: EnrollmentLink }>(
    "post",
    "/api/v1/enrollment-links",
    { data: { name } }
  );

export const rotateEnrollmentLink = (linkId: number) =>
  http.request<{ success: boolean; data: EnrollmentLink }>(
    "post",
    `/api/v1/enrollment-links/${linkId}/rotate`
  );

export const disableEnrollmentLink = (linkId: number) =>
  http.request<{ success: boolean; data: EnrollmentLink }>(
    "post",
    `/api/v1/enrollment-links/${linkId}/disable`
  );

export const generateEnrollmentMiniProgramCode = (
  linkId: number,
  rawToken: string
) =>
  http.request<{ success: boolean; data: EnrollmentMiniProgramCode }>(
    "post",
    `/api/v1/enrollment-links/${linkId}/mini-program-code`,
    { data: { raw_token: rawToken } }
  );
