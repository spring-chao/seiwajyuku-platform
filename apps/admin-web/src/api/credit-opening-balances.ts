import { http } from "@/utils/http";

export type OpeningRow = {
  excel_row: number;
  member_code: string;
  member_name: string;
  class_label: string;
  points: string | null;
  errors?: string[];
};
export type OpeningPreview = {
  cutoff_date: string;
  rows: OpeningRow[];
  row_count: number;
  skipped_count: number;
  error_count: number;
  total_points: string;
  fingerprint: string | null;
  storage_available: boolean;
};
export type OpeningImport = {
  id: number;
  status: string;
  cutoff_date: string;
  original_filename: string;
  content_fingerprint: string;
  created_by: number;
  approved_by: number | null;
  row_count: number;
  total_points: string;
  rows: OpeningRow[];
};
export type OpeningWorkbench = {
  actor_user_id: number;
  storage_available: boolean;
  enabled: boolean;
  setup_allowed: boolean;
  setup_incomplete: boolean;
  release_commit: string;
  migration_sha256: string;
  can_approve: boolean;
  can_post: boolean;
  imports: OpeningImport[];
  summary: { entry_count: number; total_points: string };
};
type Result<T> = { success: boolean; data: T };
const base = "/api/v1/learning-credits/opening-balances";
export const getOpeningWorkbench = () =>
  http.request<Result<OpeningWorkbench>>("get", base);
export const downloadOpeningTemplate = () =>
  http.request<Blob>("get", `${base}/template`, {
    responseType: "blob",
    timeout: 60000
  });
const formData = (file: File, cutoff: string, fingerprint?: string) => {
  const data = new FormData();
  data.append("workbook", file);
  data.append("cutoff_date", cutoff);
  if (fingerprint) data.append("expected_fingerprint", fingerprint);
  return data;
};
export const inspectOpeningFile = (file: File, cutoff: string) =>
  http.request<Result<OpeningPreview>>("post", `${base}/preview`, {
    data: formData(file, cutoff),
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60000
  });
export const submitOpeningFile = (
  file: File,
  cutoff: string,
  fingerprint: string
) =>
  http.request<Result<{ id: number; status: string; idempotent: boolean }>>(
    "post",
    `${base}/imports`,
    {
      data: formData(file, cutoff, fingerprint),
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 60000
    }
  );
export const prepareOpeningStorage = (state: OpeningWorkbench) =>
  http.request<Result<{ status: string }>>("post", `${base}/setup`, {
    data: {
      expected_release_commit: state.release_commit,
      expected_migration_sha256: state.migration_sha256
    },
    timeout: 60000
  });
export const processOpeningImport = (
  row: OpeningImport,
  operation: "approve" | "post" | "cancel"
) =>
  http.request<Result<{ status: string }>>(
    "post",
    `${base}/imports/${row.id}/${operation}`,
    { data: { expected_fingerprint: row.content_fingerprint }, timeout: 60000 }
  );
