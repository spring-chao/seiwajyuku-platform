import { http } from "@/utils/http";

export type SettlementBatchStatus =
  | "DRAFT"
  | "DRY_RUN"
  | "PENDING_APPROVAL"
  | "APPROVED"
  | "POSTING"
  | "POSTED"
  | "PARTIAL_FAILED"
  | "CLOSED"
  | "CANCELLED";

export type SettlementBatchType = "REGULAR" | "HISTORICAL_IMPORT" | "CORRECTION";

export type SettlementBatchListItem = {
  id: number;
  batch_no: string;
  batch_type: SettlementBatchType;
  source_type: string;
  class_org_unit_id: string | null;
  class_name: string | null;
  period_precision: string;
  period_start: string | null;
  period_end: string | null;
  period_year: number | null;
  period_month: number | null;
  status: SettlementBatchStatus;
  proposed_entry_count: number;
  proposed_points: string;
  blocked_count: number;
  duplicate_entry_count: number | null;
  posted_entry_count: number;
  posted_points: string;
  created_at: string;
  updated_at: string;
  source_fingerprint: string;
  rule_fingerprint: string;
};

export type SettlementBatchItem = {
  id: number;
  member_id: number | null;
  member_name_masked: string;
  source_ref: Record<string, string | number | null>;
  rule_key: string | null;
  rule_version: string | null;
  credit_category: string | null;
  credit_type: string | null;
  points: string | null;
  period_precision: string;
  period_year: number;
  period_month: number | null;
  status: string;
  blocking_reason: string | null;
  error_code: string | null;
  ledger_entry_id: number | null;
};

export type SettlementBatchActionResult = {
  id: number;
  batch_no: string;
  status: SettlementBatchStatus;
  proposed_entry_count: number;
  proposed_points: string;
  blocked_count: number;
  posted_entry_count: number;
  posted_points: string;
  source_fingerprint: string;
  rule_fingerprint: string;
  idempotent: boolean;
};

export type SettlementBatchDetail = SettlementBatchListItem & {
  approved_by: number | null;
  approved_at: string | null;
  approval_fingerprint: string | null;
  created_by_current_actor: boolean;
  items: SettlementBatchItem[];
};

export type SettlementBatchList = {
  storage_available: boolean;
  feature_gates: {
    dry_run_enabled: boolean;
    approval_enabled: boolean;
    post_enabled: boolean;
    settlement_enabled: boolean;
    historical_post_enabled: boolean;
  };
  total_count: number;
  status_counts: Record<SettlementBatchStatus, number>;
  batches: SettlementBatchListItem[];
};

export type CreditLedgerOverview = {
  entry_count: number;
  total_points: string;
  categories: Array<{ credit_category: string; entry_count: number; points: string }>;
  credit_types: Array<{ credit_type: string; entry_count: number; points: string }>;
};

export type HistoricalBatchDryRunResult = {
  settlement_batch_count: number;
  settlement_batches: SettlementBatchActionResult[];
  ready_item_count: number;
  ready_points: string;
  group_blocked_item_count: number;
  source_blocked_item_count: number;
  already_posted_item_count: number;
  learning_credit_entries_delta: number;
  ledger_write: boolean;
  staging_write: boolean;
};

export type LearningCreditLedgerEntry = {
  id: number;
  member_id: number;
  member_name: string;
  credit_category: string;
  credit_type: string;
  points: number | string;
  status: string;
  source_type: string;
  source_id: string;
  class_org_unit_id: string | null;
  rule_key: string | null;
  rule_version: string | null;
  rule_snapshot_json: string;
  occurred_at: string | null;
  occurred_precision: string;
  occurred_year: number;
  occurred_month: number | null;
  period_display: string | null;
  posted_at: string | null;
  reversal_of_entry_id: number | null;
  reversal_entry_id: number | null;
};

export const getSettlementBatches = (params?: {
  status?: SettlementBatchStatus;
  batch_type?: SettlementBatchType;
  class_org_unit_id?: string;
  limit?: number;
  offset?: number;
}) =>
  http.request<{ success: boolean; data: SettlementBatchList }>(
    "get", "/api/v1/learning-credits/settlement-batches", { params }
  );

export const getSettlementBatch = (batchId: number) =>
  http.request<{ success: boolean; data: SettlementBatchDetail }>(
    "get", `/api/v1/learning-credits/settlement-batches/${batchId}`
  );

export const dryRunStudyMeetingBatch = (sessionId: number) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/study-meetings/${sessionId}/dry-run`
  );

export const dryRunClassMeetingBatch = (eventGroupId: number) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/class-meetings/${eventGroupId}/dry-run`
  );

export const dryRunHistoricalCreditBatches = (importBatchId: number) =>
  http.request<{ success: boolean; data: HistoricalBatchDryRunResult }>(
    "post", `/api/v1/learning-credits/historical-imports/${importBatchId}/settlement-batches/dry-run`
  );

export const dryRunActivityCreditBatch = (type: "daily-reading" | "excellent-share", data: {
  class_org_unit_id: string;
  occurred_from: string;
  occurred_to: string;
}) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/${type}/dry-run`, { data }
  );

export const submitSettlementBatch = (batchId: number) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/${batchId}/submit-approval`
  );

export const approveSettlementBatch = (batchId: number) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/${batchId}/approve`
  );

export const postSettlementBatch = (batchId: number, resumePartialFailure = false) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/${batchId}/post`,
    { data: { resume_partial_failure: resumePartialFailure } }
  );

export const reconcileStalePostingBatch = (batchId: number) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/${batchId}/reconcile-posting`
  );

export const closeSettlementBatch = (batchId: number) =>
  http.request<{ success: boolean; data: SettlementBatchActionResult }>(
    "post", `/api/v1/learning-credits/settlement-batches/${batchId}/close`
  );

export const getLearningCreditEntries = (params?: {
  member_id?: number;
  occurred_from?: string;
  occurred_to?: string;
  credit_category?: string;
  source_type?: string;
}) =>
  http.request<{ success: boolean; data: { entries: LearningCreditLedgerEntry[] } }>(
    "get", "/api/v1/learning-credits/entries", { params }
  );

export const getCreditLedgerOverview = () =>
  http.request<{ success: boolean; data: CreditLedgerOverview }>(
    "get", "/api/v1/learning-credits/overview"
  );

export const reverseLearningCreditEntry = (entryId: number, reason: string) =>
  http.request<{ success: boolean; data: LearningCreditLedgerEntry }>(
    "post", `/api/v1/learning-credits/entries/${entryId}/reverse`, { data: { reason } }
  );
