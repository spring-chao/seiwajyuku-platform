"use strict";

// This function is intentionally only an orchestrator.  Database selection,
// expiry checks, object-prefix validation, deletion, audit, and idempotency
// remain in platform-api's cleanup_evidence service.

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

async function requestCleanup(url, token, limit, timeoutMs) {
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
      body: JSON.stringify({ limit }),
      signal: controller.signal,
      redirect: "error",
    });
    const text = await response.text();
    let payload = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch (_) {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(`cleanup endpoint returned HTTP ${response.status}`);
    }
    return payload;
  } finally {
    clearTimeout(timeout);
  }
}

exports.main = async (_event, _context) => {
  const url = cleanupEndpointUrl();
  const token = requiredEnv("STUDY_EVIDENCE_CLEANUP_TOKEN");
  if (token.length < 32) throw new Error("STUDY_EVIDENCE_CLEANUP_TOKEN is too short");
  const timeoutMs = boundedTimeout(process.env.CLEANUP_REQUEST_TIMEOUT_MS);
  const report = await requestCleanup(url, token, boundedLimit(process.env.STUDY_EVIDENCE_CLEANUP_LIMIT), timeoutMs);
  const data = report && report.data ? report.data : {};
  // Return counts for the CloudBase invocation result, but never return or log
  // the authentication token or any upstream body containing sensitive data.
  return {
    ok: Boolean(report && report.success) && Number(data.errors || 0) === 0,
    candidates: Number(data.candidates || 0),
    deleted: Number(data.deleted || 0),
    errors: Number(data.errors || 0),
  };
};

exports._test = { boundedLimit, boundedTimeout, cleanupEndpointUrl };
