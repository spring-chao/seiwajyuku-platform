import { http } from "@/utils/http";
export type SourceKind = "CLASS_MEETING" | "DAILY_READING" | "EXCELLENT_SHARE";
export type SourceRow = {
  excel_row: number;
  member_code: string;
  member_name: string;
  occurred_on?: string;
  source_reference?: string;
  proposed_points?: string;
  credit_status?: string;
  credit_reasons?: string[];
  errors?: string[];
  record_json?: string;
};
export type SourceBatch = {
  id: number;
  batch_no: string;
  period_start: string;
  period_end: string;
  member_count: number;
  status: string;
  proposed_entry_count: number;
  proposed_points: string;
  blocked_count: number;
  posted_entry_count: number;
  posted_points: string;
  created_by_current_actor: boolean;
};
export type SourceImport = {
  posting_job?: { id: number; status: "PENDING" | "RUNNING" | "PAUSED" | "SUCCEEDED" | "NEEDS_REVIEW"; pause_requested: boolean; error_code?: string } | null;
  duplicate_upload_count?: number;
  posting_progress?: { state: string; total_entries: number; posted_entries: number; remaining_entries: number; completed: boolean };
  source_only?: boolean;
  member_count: number;
  period_start?: string;
  period_end?: string;
  settlement_fingerprint: string;
  settlement_summary: { proposed_points: string; posted_points: string; entry_count: number };
  member_results?: { member_code: string; member_name: string; credit_count: number; proposed_points: string; posted_points: string; blocked_count: number }[];
  id: number;
  class_name: string;
  original_filename: string;
  source_kind: SourceKind;
  row_count: number;
  created_at: string;
  source_note: string;
  rows: SourceRow[];
  batches: SourceBatch[];
};
export type SourceWorkbench = {
  posting_jobs_available?: boolean;
  posting_jobs_setup_allowed?: boolean;
  posting_jobs_migration_sha256?: string;
  reading_calendar: {
    status: string;
    day_count: number;
    version_label: string | null;
  };
  classes: { id: string; name: string; center_name?: string }[];
  cycles: {
    id: number;
    class_org_unit_id: string;
    learning_cycle_index: number;
    cycle_status: string;
    class_meeting_status: string;
    actual_class_meeting_at: string | null;
  }[];
  imports: SourceImport[];
  storage_available: boolean;
  setup_allowed: boolean;
  setup_incomplete: boolean;
  release_commit: string;
  migration_sha256: string;
  can_import_activity: boolean;
  can_import_attendance: boolean;
};
export type SourcePreview = {
  accepted_count: number;
  ignored_count: number;
  rows: SourceRow[];
  row_count: number;
  error_count: number;
  fingerprint: string | null;
  proposed_points: string;
  storage_available: boolean;
};
export type SourceForm = {
  kind: SourceKind;
  class_id: string;
  options_json: string;
  source_note: string;
};
type Result<T> = { success: boolean; data: T };
const base = "/api/v1/learning-credits/excel";
export type ReadingWeeklyMember = { id: number; name: string; member_code: string; group_name: string };
export type ReadingWeeklyPause = { member_id?: number; occurred_from: string; occurred_to: string; reason: "COURSE" | "TEMPORARY" };
export const readingWeeklyRoster = (class_id: string, occurred_from: string, occurred_to: string) =>
  http.request<Result<{ members: ReadingWeeklyMember[] }>>("get", base + "/reading-weekly-roster", {
    params: { class_id, occurred_from, occurred_to }, timeout: 60000
  });
export const readingWeeklyExport = (data: { class_id: string; occurred_from: string; occurred_to: string; pauses: ReadingWeeklyPause[] }) =>
  http.request<Blob>("post", base + "/reading-weekly-export", { data, responseType: "blob", timeout: 60000 });

export const monthlyCoreOptions = () =>
  http.request<Result<{ centers: { id: string; name: string }[] }>>("get", base + "/monthly-core-options");
export const monthlyCoreExport = (center_id: string, month: string) =>
  http.request<Blob>("get", base + "/monthly-core-export", {
    params: { center_id, month }, responseType: "blob", timeout: 180000
  });
export const sourcePrepareCalendar = () =>
  http.request<Result<unknown>>("post", base + "/reading-calendar/setup", {
    timeout: 60000
  });
export const sourceWorkbench = () =>
  http.request<Result<SourceWorkbench>>("get", base, { timeout: 60000 });
export const sourceGet = (id: number) =>
  http.request<Result<SourceImport>>("get", `${base}/imports/${id}`, {
    timeout: 60000
  });
export const sourceExportOptions = () =>
  http.request<Result<{ classes: { id: string; name: string; center_name?: string }[] }>>(
    "get",
    base + "/export-options",
    { timeout: 60000 }
  );
export const sourceTemplate = (kind: SourceKind, class_id: string) =>
  http.request<Blob>("get", base + "/template", {
    params: { kind, class_id },
    responseType: "blob"
  });
export const sourceExport = (
  class_id: string,
  occurred_from: string,
  occurred_to: string,
  member_id?: number
) =>
  http.request<Blob>("get", base + "/export", {
    params: {
      class_id: class_id || undefined,
      occurred_from,
      member_id,
      occurred_to: occurred_to || undefined
    },
    responseType: "blob",
    timeout: 60000
  });
const payload = (file: File, form: SourceForm, fingerprint?: string) => {
  const data = new FormData();
  data.append("workbook", file);
  Object.entries(form).forEach(([key, value]) => data.append(key, value));
  if (fingerprint) data.append("expected_fingerprint", fingerprint);
  return {
    data,
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 180000
  };
};
export const sourcePreview = (file: File, form: SourceForm) =>
  http.request<Result<SourcePreview>>(
    "post",
    base + "/preview",
    payload(file, form)
  );
export const sourceRegister = (
  file: File,
  form: SourceForm,
  fingerprint: string
) =>
  http.request<Result<{ id: number; idempotent: boolean }>>(
    "post",
    base + "/imports",
    payload(file, form, fingerprint)
  );
export const sourceFindSaved = (fingerprint: string, class_id: string, kind: SourceKind) =>
  http.request<Result<{ id: number | null }>>("get", base + "/saved-source", {
    params: { fingerprint, class_id, kind }, timeout: 30000
  });
export const sourceCreateBatches = (id: number) =>
  http.request<Result<SourceImport>>("post", `${base}/imports/${id}/batches`, {
    timeout: 180000
  });
export const sourceAction = (
  id: number,
  operation: "confirm",
  expected_fingerprint: string,
  resume_partial_failure = false
) =>
  http.request<Result<SourceImport>>(
    "post",
    `${base}/imports/${id}/actions/${operation}`,
    { data: { expected_fingerprint, resume_partial_failure, incremental: true }, timeout: 60000 }
  );
export const sourceReconcile = (id: number) =>
  http.request<Result<SourceImport>>("post", `${base}/imports/${id}/actions/reconcile`, { timeout: 60000 });
export const sourceSubmitPostingJob = (id: number, expected_fingerprint: string, resume_partial_failure = false) =>
  http.request<Result<{ job: NonNullable<SourceImport["posting_job"]>; idempotent: boolean }>>("post", `${base}/imports/${id}/posting-job`, {
    data: { expected_fingerprint, resume_partial_failure }, timeout: 60000
  });
export const sourcePausePostingJob = (id: number) =>
  http.request<Result<{ job: NonNullable<SourceImport["posting_job"]> }>>("post", `${base}/imports/${id}/posting-job/pause`, { timeout: 60000 });
export const sourceSetupPostingJobs = (state: SourceWorkbench) =>
  http.request<Result<{ status: string }>>("post", base + "/posting-jobs/setup", { data: {
    expected_release_commit: state.release_commit, expected_migration_sha256: state.posting_jobs_migration_sha256
  }, timeout: 60000 });
export const sourceSetup = (state: SourceWorkbench) =>
  http.request<Result<{ status: string }>>("post", base + "/setup", {
    data: {
      expected_release_commit: state.release_commit,
      expected_migration_sha256: state.migration_sha256
    },
    timeout: 60000
  });
