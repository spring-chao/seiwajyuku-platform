import { http } from "@/utils/http";

export type MeetingCourse = {
  course_key: string;
  course_name_snapshot: string;
  course_credit_snapshot: number | null;
  course_rule_status: "CONFIGURED" | "PENDING";
  completions?: { member_id: number; completion_status: "UNCONFIRMED" | "CONFIRMED" | "NOT_COMPLETED"; confirmed_by_user_id?: number | null }[];
};

export type StudyMeetingRecord = {
  id: number;
  session_code: string;
  class_org_unit_id: string;
  class_name: string;
  study_group_org_unit_id: string;
  group_name: string;
  learning_cycle_id: number;
  cycle_index: number;
  meeting_date: string;
  created_by_member_id: number;
  creator_name: string;
  has_course: boolean;
  courses: MeetingCourse[];
  course_key?: string | null;
  course_name_snapshot?: string | null;
  course_credit_snapshot?: number | null;
  course_rule_status?: "CONFIGURED" | "PENDING" | null;
  status: "DRAFT" | "SUBMITTED" | "CANCELLED";
  submitted_at?: string | null;
  created_at: string;
  updated_at: string;
  home_count: number;
  cross_group_count: number;
  total_count: number;
  home_group_member_count: number;
  home_group_attendance_rate: number | null;
  review_status: "CONFIRMED" | "UNCONFIRMED";
  review_fingerprint: string;
  reviewed_at?: string | null;
  reviewer_name?: string | null;
};

export type StudyMeetingAttendee = {
  id: number;
  member_id: number;
  name: string;
  phone_masked?: string;
  home_group_name?: string;
  attended_group_name?: string;
  attendance_type: "HOME_GROUP" | "CROSS_GROUP";
};

export type StudyMeetingRecordDetail = StudyMeetingRecord & {
  attendees: StudyMeetingAttendee[];
  home_attendees: StudyMeetingAttendee[];
  cross_group_attendees: StudyMeetingAttendee[];
  evidence: { id: number; expires_at: string } | null;
  can_edit_courses: boolean;
  can_edit_attendees: boolean;
  can_confirm_review: boolean;
  correction_lock_reason?: string | null;
  course_options: { course_key: string; course_name: string; credit_points: number; status: string }[];
};

export type MeetingMemberOption = {
  member_id: number;
  name: string;
  group_org_unit_id: string;
  group_name: string;
  attendance_type: "HOME_GROUP" | "CROSS_GROUP";
};
export type MeetingAttendeeOptions = {
  home_members: MeetingMemberOption[];
  cross_group_members: MeetingMemberOption[];
  unavailable_attendees: Pick<MeetingMemberOption, "member_id" | "name" | "attendance_type">[];
};
export const getStudyMeetingAttendeeOptions = (sessionId: number) =>
  http.request<{ success: boolean; data: MeetingAttendeeOptions }>(
    "get", `/api/v1/study-meetings/records/${sessionId}/attendee-options`
  );
export const correctStudyMeetingAttendees = (sessionId: number, data: {
  member_ids: number[]; expected_member_ids: number[]; note?: string;
}) => http.request<{ success: boolean; data: StudyMeetingRecordDetail }>(
  "patch", `/api/v1/study-meetings/records/${sessionId}/attendees`, { data }
);

export const getStudyMeetingPhoto = (sessionId: number) =>
  http.request<Blob>("get", `/api/v1/study-meetings/records/${sessionId}/evidence`, { responseType: "blob" });

export const correctStudyMeetingCourses = (
  sessionId: number,
  data: { course_keys: string[]; expected_course_keys: string[]; note?: string }
) => http.request<{ success: boolean; data: StudyMeetingRecordDetail }>(
  "patch", `/api/v1/study-meetings/records/${sessionId}/courses`, { data }
);

export const getStudyMeetingRecords = (params?: {
  status?: StudyMeetingRecord["status"];
  meeting_date_from?: string;
  meeting_date_to?: string;
}) =>
  http.request<{ success: boolean; data: { records: StudyMeetingRecord[] } }>(
    "get",
    "/api/v1/study-meetings/records",
    { params }
  );

export const getStudyMeetingRecord = (sessionId: number) =>
  http.request<{ success: boolean; data: StudyMeetingRecordDetail }>(
    "get",
    `/api/v1/study-meetings/records/${sessionId}`
  );

export const confirmStudyMeetingReview = (sessionId: number, expectedFingerprint: string) =>
  http.request<{ success: boolean; data: StudyMeetingRecordDetail }>(
    "post", `/api/v1/study-meetings/records/${sessionId}/review-confirmation`,
    { data: { expected_fingerprint: expectedFingerprint } }
  );

export type MeetingCreditPreview = {
  persisted: false;
  members: { member_id: number; name: string; attendance_type: string; home_group_name: string; group_points: number; course_points: number; expected_points: number; reasons: string[] }[];
  totals: { proposed_points: number };
};
export const getMeetingCreditPreview = (sessionId: number) =>
  http.request<{ success: boolean; data: MeetingCreditPreview }>("get", `/api/v1/study-meetings/records/${sessionId}/credit-preview`);
export const confirmMeetingCourseCompletion = (sessionId: number, data: {
  course_key: string; member_id: number; completion_status: string; expected_completion_status: string; note?: string;
}) => http.request<{ success: boolean; data: StudyMeetingRecordDetail }>("post", `/api/v1/study-meetings/records/${sessionId}/course-completions`, { data });
export const exportMeetingRecords = (params: {
  session_id?: number; status?: string; meeting_date_from?: string; meeting_date_to?: string;
}) => http.request<Blob>("get", "/api/v1/study-meetings/records-export", { params, responseType: "blob", timeout: 60000 });
