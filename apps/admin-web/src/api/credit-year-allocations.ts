import { http } from "@/utils/http";
import type { OpeningRow } from "./credit-opening-balances";
export type YearImport = {
  id: number;
  content_fingerprint: string;
  status: string;
  original_filename: string;
  year_index: number;
  cutoff_date: string;
  source_note: string;
  class_name: string;
  row_count: number;
  total_points: string;
  mode?: string;
  supplement_points?: string;
  rows: (OpeningRow & { source_name?: string; previous_points?: string; supplement_points?: string })[];
};
export type YearWorkbench = {
  storage_available: boolean;
  reconciliation_available: boolean;
  reconciliation_setup_allowed: boolean;
  reconciliation_setup_incomplete: boolean;
  reconciliation_migration_sha256: string;
  setup_allowed: boolean;
  setup_incomplete: boolean;
  release_commit: string;
  migration_sha256: string;
  bindings: { id: number; class_name: string }[];
  imports: YearImport[];
};
export type YearForm = {
  binding_id: number | undefined;
  year_index: number;
  cutoff_date: string;
  source_note: string;
  mode: string;
  name_overrides_json: string;
};
export type YearPreview = {
  rows: (OpeningRow & { source_name?: string; previous_points?: string; supplement_points?: string })[];
  row_count: number;
  error_count: number;
  total_points: string;
  mode?: string;
  supplement_points?: string;
  fingerprint: string | null;
  year_index: number;
  current_year_after_import: number;
  completed_days: number;
  class_name: string;
  storage_available: boolean;
};
type Result<T> = { success: boolean; data: T };
const base = "/api/v1/learning-credits/year-allocations";
export const getYearWorkbench = () =>
  http.request<Result<YearWorkbench>>("get", base);
const data = (file: File, form: YearForm, fingerprint?: string) => {
  const body = new FormData();
  body.append("workbook", file);
  Object.entries(form).forEach(([key, value]) =>
    body.append(key, String(value ?? ""))
  );
  if (fingerprint) body.append("expected_fingerprint", fingerprint);
  return body;
};
export const previewYearFile = (file: File, form: YearForm) =>
  http.request<Result<YearPreview>>("post", base + "/preview", {
    data: data(file, form),
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60000
  });
export const submitYearFile = (
  file: File,
  form: YearForm,
  fingerprint: string
) =>
  http.request<Result<{ id: number }>>("post", base + "/imports", {
    data: data(file, form, fingerprint),
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60000
  });
export const processYearImport = (
  row: YearImport,
  operation: "approve" | "post" | "cancel"
) =>
  http.request<Result<{ status: string }>>(
    "post",
    `${base}/imports/${row.id}/${operation}`,
    { data: { expected_fingerprint: row.content_fingerprint }, timeout: 60000 }
  );
export const setupYearStorage = (state: YearWorkbench) =>
  http.request<Result<{ status: string }>>("post", base + "/setup", {
    data: {
      expected_release_commit: state.release_commit,
      expected_migration_sha256: state.migration_sha256
    },
    timeout: 60000
  });

export const setupReconciliationStorage = (state: YearWorkbench) =>
  http.request<Result<{ status: string }>>("post", base + "/reconciliation-setup", {
    data: { expected_release_commit: state.release_commit, expected_migration_sha256: state.reconciliation_migration_sha256 },
    timeout: 60000
  });
