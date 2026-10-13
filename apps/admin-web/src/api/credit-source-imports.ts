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
  status: string;
  proposed_entry_count: number;
  proposed_points: string;
  blocked_count: number;
  posted_entry_count: number;
  posted_points: string;
  created_by_current_actor: boolean;
};
export type SourceImport = {
  source_only?: boolean;
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
  reading_calendar: {
    status: string;
    day_count: number;
    version_label: string | null;
  };
  classes: { id: string; name: string }[];
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
  http.request<Result<{ classes: { id: string; name: string }[] }>>(
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
  occurred_to: string
) =>
  http.request<Blob>("get", base + "/export", {
    params: {
      class_id: class_id || undefined,
      occurred_from,
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
    timeout: 60000
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
  http.request<Result<{ id: number }>>(
    "post",
    base + "/imports",
    payload(file, form, fingerprint)
  );
export const sourceCreateBatches = (id: number) =>
  http.request<Result<SourceImport>>("post", `${base}/imports/${id}/batches`, {
    timeout: 60000
  });
export const sourceAction = (
  id: number,
  operation: "submit" | "approve" | "post"
) =>
  http.request<Result<SourceImport>>(
    "post",
    `${base}/imports/${id}/actions/${operation}`,
    { timeout: 60000 }
  );
export const sourceSetup = (state: SourceWorkbench) =>
  http.request<Result<{ status: string }>>("post", base + "/setup", {
    data: {
      expected_release_commit: state.release_commit,
      expected_migration_sha256: state.migration_sha256
    },
    timeout: 60000
  });
