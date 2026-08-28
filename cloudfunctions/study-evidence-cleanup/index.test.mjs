import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const cleanup = require("./index.js");

test("boundedLimit keeps the scheduler batch within the service limit", () => {
  assert.equal(cleanup._test.boundedLimit(undefined), 500);
  assert.equal(cleanup._test.boundedLimit("17"), 17);
  assert.equal(cleanup._test.boundedLimit("9999"), 500);
  assert.equal(cleanup._test.boundedLimit("invalid"), 500);
});

test("boundedTimeout falls back from invalid values and caps long requests", () => {
  assert.equal(cleanup._test.boundedTimeout(undefined), 30000);
  assert.equal(cleanup._test.boundedTimeout("invalid"), 30000);
  assert.equal(cleanup._test.boundedTimeout("500"), 30000);
  assert.equal(cleanup._test.boundedTimeout("45000"), 45000);
  assert.equal(cleanup._test.boundedTimeout("999999"), 120000);
});

test("cleanupEndpointUrl accepts only the exact HTTPS internal route", () => {
  const previous = process.env.PLATFORM_API_CLEANUP_URL;
  try {
    process.env.PLATFORM_API_CLEANUP_URL =
      "https://api.example.invalid/api/v1/internal/study-evidence/";
    assert.equal(
      cleanup._test.cleanupEndpointUrl(),
      "https://api.example.invalid/api/v1/internal/study-evidence",
    );
    process.env.PLATFORM_API_CLEANUP_URL = "http://api.example.invalid/api/v1/internal/study-evidence";
    assert.throws(() => cleanup._test.cleanupEndpointUrl(), /HTTPS cleanup endpoint/);
    process.env.PLATFORM_API_CLEANUP_URL = "https://api.example.invalid/health";
    assert.throws(() => cleanup._test.cleanupEndpointUrl(), /HTTPS cleanup endpoint/);
  } finally {
    if (previous === undefined) delete process.env.PLATFORM_API_CLEANUP_URL;
    else process.env.PLATFORM_API_CLEANUP_URL = previous;
  }
});

test("main calls only the protected platform-api endpoint and returns safe counts", async () => {
  const previousFetch = globalThis.fetch;
  const previous = {
    url: process.env.PLATFORM_API_CLEANUP_URL,
    token: process.env.STUDY_EVIDENCE_CLEANUP_TOKEN,
    limit: process.env.STUDY_EVIDENCE_CLEANUP_LIMIT,
  };
  process.env.PLATFORM_API_CLEANUP_URL = "https://api.example.invalid/api/v1/internal/study-evidence";
  process.env.STUDY_EVIDENCE_CLEANUP_TOKEN = "x".repeat(64);
  process.env.STUDY_EVIDENCE_CLEANUP_LIMIT = "23";
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return new Response(JSON.stringify({
      success: true,
      data: { candidates: 3, deleted: 2, errors: 0 },
    }), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const result = await cleanup.main({}, {});
    assert.equal(result.ok, true);
    assert.deepEqual(result, { candidates: 3, deleted: 2, errors: 0, ok: true });
    assert.equal(request.url, process.env.PLATFORM_API_CLEANUP_URL);
    assert.equal(request.options.method, "POST");
    assert.equal(request.options.redirect, "error");
    assert.equal(request.options.headers["x-study-evidence-cleanup-token"], process.env.STUDY_EVIDENCE_CLEANUP_TOKEN);
    assert.deepEqual(JSON.parse(request.options.body), { limit: 23 });
  } finally {
    globalThis.fetch = previousFetch;
    for (const [key, value] of Object.entries({
      PLATFORM_API_CLEANUP_URL: previous.url,
      STUDY_EVIDENCE_CLEANUP_TOKEN: previous.token,
      STUDY_EVIDENCE_CLEANUP_LIMIT: previous.limit,
    })) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});
