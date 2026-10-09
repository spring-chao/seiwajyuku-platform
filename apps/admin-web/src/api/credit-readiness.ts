import { http } from "@/utils/http";

export type RuleImage = {
  course_key: string;
  course_name: string;
  year_index: number;
  credit_points: number;
  status: string;
};
export type RuleApplyBaseline = {
  expected_release_commit: string;
  expected_production_fingerprint: string;
  expected_canonical_fingerprint: string;
  expected_course_rule_version_id: number;
  expected_course_rule_status: "DRAFT";
  expected_rule_count: 14;
  expected_placeholder_keys: string[];
};
export type CreditReadiness = {
  status: "READY" | "BLOCKED" | "ALREADY_APPLIED";
  checked_at: string;
  release_commit: string;
  rule_count: number;
  target_rule_count: number;
  version: {
    id: number;
    plan_key: string;
    version_label: string;
    status: string;
  };
  ledger: { entry_count: number; points: string };
  applied_migrations: string[];
  blockers: { code: string; message: string }[];
  changes: Record<string, number>;
  bindings: {
    total: number;
    generic_frozen: number;
    course_frozen: number;
  } | null;
  plan: {
    course_key: string;
    action: string;
    before: RuleImage | null;
    after: RuleImage | null;
  }[];
  apply_payload: RuleApplyBaseline | null;
};

const endpoint =
  "/api/v1/ops/production-actions/g5-4-course-rule-reconciliation";

// Both calls use the normal authenticated client and one fixed API operation.
// A deployment route token may target a verified candidate; no alternate host,
// token extraction, fallback route, or retry is provided here.
const routeParams = (token?: string) =>
  token ? { sj_canary: token } : undefined;
export const getCreditReadiness = (token?: string) =>
  http.request<{ success: true; data: CreditReadiness }>(
    "get",
    `${endpoint}/preflight`,
    {
      params: routeParams(token)
    }
  );
export const applyCreditRules = (
  data: RuleApplyBaseline & { execution_reason: string },
  token?: string
) =>
  http.request<{
    success: true;
    data: { status: "APPLIED" | "ALREADY_APPLIED"; ledger_delta: number };
  }>("post", `${endpoint}/apply`, {
    data,
    params: routeParams(token),
    timeout: 30000
  });
