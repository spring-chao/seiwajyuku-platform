import { http } from "@/utils/http";

export type AttendanceManagementOperation =
  | "admin_events"
  | "stats"
  | "ops_roster_options"
  | "ops_roster_members"
  | "event_update"
  | "event_lifecycle_update"
  | "create_class_meeting_sessions"
  | "sync_class_roster"
  | "upload_preview"
  | "upload"
  | "registration"
  | "registration_delete"
  | "attendance_status"
  | "export"
  | "manual_checkin"
  | "event_detail"
  | "create_event"
  | "import_preview"
  | "import_apply"
  | "class_roster_reconciliation";

export type EngineResult = {
  ok?: boolean;
  success?: boolean;
  msg?: string;
  [key: string]: any;
};
export type ManagedEvent = {
  event_id: string;
  event_group_id?: string;
  name: string;
  event_date: string;
  activity_type: string;
  lifecycle_status: "DRAFT" | "CONFIRMED" | "CANCELLED";
  manual_status?: "active" | "closed";
  checkin_status?: string;
  session_code?: string;
  session_name?: string;
  session_order?: number;
  checkin_start_at?: string;
  checkin_end_at?: string;
  scheduled_start_at?: string;
  scheduled_end_at?: string;
  org_unit_id?: string;
  class_org_unit_id?: string;
  group_org_unit_id?: string;
  center_name?: string;
  class_name?: string;
  group_name?: string;
  total?: number;
  checked?: number;
  sessions?: ManagedEvent[];
};

/** Only the explicitly exposed management operations may reach the engine. */
export async function manageAttendance<T extends EngineResult = EngineResult>(
  operation: AttendanceManagementOperation,
  data: Record<string, unknown> = {}
): Promise<T> {
  const result = await http.request<T>(
    "post",
    `/api/v1/attendance/manage/${operation}`,
    { data }
  );
  if (result.ok === false || result.success === false) {
    throw new Error(result.msg || "签到管理操作失败");
  }
  return result;
}

export type AttendanceCode = {
  image_base64: string;
  mime_type: string;
  token: string;
  event_id: string;
  page: string;
  env_version: "develop" | "trial" | "release";
};

export const generateAttendanceCode = async (
  eventId: string,
  envVersion: "develop" | "trial" | "release"
) => {
  const result = await http.request<{ success: boolean; data: AttendanceCode }>(
    "post",
    `/api/v1/attendance/manage/events/${encodeURIComponent(eventId)}/code`,
    { data: { env_version: envVersion } }
  );
  if (!result.success) throw new Error("小程序码生成失败");
  return result.data;
};
