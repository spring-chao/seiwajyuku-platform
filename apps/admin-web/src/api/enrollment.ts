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
  notice: string;
  privacy_notice: string;
  required_fields: string[];
  optional_fields: string[];
  collects_organization: false;
};

export type PublicEnrollmentPayload = {
  name: string;
  phone: string;
  privacy_consent: true;
  gender?: "MALE" | "FEMALE" | "OTHER";
  birthday: string;
  district?: string;
  political_status?: string;
  company_name: string;
  company_address: string;
  email?: string;
  position: string;
  referrer: string;
  invoice_info: string;
  invoice_type: string;
  industry_category?: string;
  industry: string;
  company_products: string;
  employee_count: number;
  books_read: string;
  enrollment_reason_philosophy: string;
  enrollment_reason_change: string;
  enrollment_reason_other: string;
  learning_years_goal?: string;
  learning_participation_goal?: string;
  business_goal?: string;
  other_goal?: string;
  annual_sales: string;
  profit_margin?: string;
  notes?: string;
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
  company_address?: string | null;
  email?: string | null;
  position?: string | null;
  referrer?: string | null;
  invoice_info?: string | null;
  invoice_type?: string | null;
  industry_category?: string | null;
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
  annual_sales?: string | null;
  profit_margin?: string | null;
  missing_gates: string[];
  can_enroll: boolean;
};

export type EnrollmentReviewPayload = {
  decision: "SAVE" | "APPROVE";
  review_note?: string;
  name?: string;
  gender?: "MALE" | "FEMALE" | "OTHER" | null;
  birthday?: string | null;
  district?: string | null;
  political_status?: string | null;
  company_address?: string | null;
  email?: string | null;
  company_name?: string | null;
  position?: string | null;
  referrer?: string | null;
  invoice_info?: string | null;
  invoice_type?: string | null;
  industry_category?: string | null;
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
