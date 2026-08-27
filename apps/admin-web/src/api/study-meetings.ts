import { http } from "@/utils/http";

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
};

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
