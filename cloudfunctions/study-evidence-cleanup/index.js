"use strict";

// Scheduled invocations orchestrate bounded cleanup and monthly progression
// in platform-api while its HTTP request is entitled to CPU. The
// authenticated HTTP route signs one private object operation using this
// existing function's temporary runtime identity; it never accepts business
// records, changes permissions, or returns a photo to anonymous callers.

const DEFAULT_LIMIT = 500;
const DEFAULT_TIMEOUT_MS = 30000;
const MAX_TIMEOUT_MS = 120000;

function requiredEnv(name) {
  const value = String(process.env[name] || "").trim();
  if (!value) throw new Error(`${name} is not configured`);
  return value;
}

function cleanupEndpointUrl() {
  const raw = requiredEnv("PLATFORM_API_CLEANUP_URL");
  let parsed;
  try {
    parsed = new URL(raw);
  } catch (_) {
    throw new Error("PLATFORM_API_CLEANUP_URL is invalid");
  }
  const path = parsed.pathname.replace(/\/+$/, "");
  if (
    parsed.protocol !== "https:" ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    path !== "/api/v1/internal/study-evidence"
  ) {
    throw new Error("PLATFORM_API_CLEANUP_URL must be the HTTPS cleanup endpoint");
  }
  parsed.pathname = path;
  return parsed.toString();
}

function boundedLimit(value) {
  const parsed = Number.parseInt(String(value || DEFAULT_LIMIT), 10);
  if (!Number.isFinite(parsed) || parsed < 1) return DEFAULT_LIMIT;
  return Math.min(parsed, DEFAULT_LIMIT);
}

function boundedTimeout(value) {
  const parsed = Number.parseInt(String(value || DEFAULT_TIMEOUT_MS), 10);
  if (!Number.isFinite(parsed) || parsed < 1000) return DEFAULT_TIMEOUT_MS;
  return Math.min(parsed, MAX_TIMEOUT_MS);
}

async function requestMaintenance(url, token, payload, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "accept": "application/json",
        "content-type": "application/json",
        "x-study-evidence-cleanup-token": token,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
      redirect: "error",
    });
    const text = await response.text();
    let report = null;
    try {
      report = text ? JSON.parse(text) : null;
    } catch (_) {
      report = null;
    }
    if (!response.ok) {
      throw new Error(`maintenance endpoint returned HTTP ${response.status}`);
    }
    return report;
  } finally {
    clearTimeout(timeout);
  }
}

function validCounts(data, keys) {
  return data && keys.every(key => Number.isInteger(data[key]) && data[key] >= 0);
}

exports.main = async (_event, _context) => {
  if (_event && _event.httpMethod) return require("./storage-bridge").handle(_event);
  const url = cleanupEndpointUrl();
  const token = requiredEnv("STUDY_EVIDENCE_CLEANUP_TOKEN");
  if (token.length < 32) throw new Error("STUDY_EVIDENCE_CLEANUP_TOKEN is too short");
  const timeoutMs = boundedTimeout(process.env.CLEANUP_REQUEST_TIMEOUT_MS);
  const monthlyUrl = new URL(url);
  monthlyUrl.pathname = "/api/v1/internal/learning-cycle-monthly";
  // One maintenance failure must not prevent the independent task from
  // running. Both requests finish before the scheduled invocation returns.
  const [cleanup, refresh] = await Promise.allSettled([
    requestMaintenance(url, token, { limit: boundedLimit(process.env.STUDY_EVIDENCE_CLEANUP_LIMIT) }, timeoutMs),
    requestMaintenance(monthlyUrl.toString(), token, {}, timeoutMs),
  ]);
  const report = cleanup.status === "fulfilled" ? cleanup.value : null;
  const data = report && report.data;
  const cleanupValid = Boolean(report && report.success === true && validCounts(data, ["candidates", "deleted", "errors"]));
  const monthlyReport = refresh.status === "fulfilled" ? refresh.value : null;
  const rawMonthly = monthlyReport && monthlyReport.data;
  const monthlyValid = Boolean(monthlyReport && typeof monthlyReport.success === "boolean" && rawMonthly &&
    typeof rawMonthly.enabled === "boolean" && validCounts(rawMonthly, ["scanned", "updated", "failed", "repair_required", "review_required"]));
  const monthly = {
    ok: monthlyValid && monthlyReport.success === true && rawMonthly.failed === 0 && rawMonthly.repair_required === 0 && rawMonthly.review_required === 0,
    enabled: monthlyValid && rawMonthly.enabled,
    scanned: monthlyValid ? rawMonthly.scanned : 0,
    updated: monthlyValid ? rawMonthly.updated : 0,
    failed: monthlyValid ? rawMonthly.failed : 1,
    repair_required: monthlyValid ? rawMonthly.repair_required : 0,
    review_required: monthlyValid ? rawMonthly.review_required : 0,
  };
  // Return counts for the CloudBase invocation result, but never return or log
  // the authentication token or any upstream body containing sensitive data.
  return {
    ok: cleanupValid && data.errors === 0 && monthly.ok,
    candidates: cleanupValid ? data.candidates : 0,
    deleted: cleanupValid ? data.deleted : 0,
    errors: cleanupValid ? data.errors : 1,
    monthly,
  };
};

exports._test = { boundedLimit, boundedTimeout, cleanupEndpointUrl };
