import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const cleanup = require("./index.js");
const bridge = require("./storage-bridge.js");
const key = `study-meetings/production/2026/10/${"a".repeat(32)}.jpg`;
const runtime = {
  CLOUDBASE_STORAGE_BUCKET: "7368-synthetic-123",
  CLOUDBASE_STORAGE_REGION: "ap-shanghai",
  TENCENTCLOUD_SECRETID: "synthetic-id",
  TENCENTCLOUD_SECRETKEY: "synthetic-secret",
  TENCENTCLOUD_SESSIONTOKEN: "synthetic+token/=",
};

test("boundedLimit keeps the scheduler batch within the service limit", () => {
  assert.equal(cleanup._test.boundedLimit(undefined), 500);
  assert.equal(cleanup._test.boundedLimit("17"), 17);
  assert.equal(cleanup._test.boundedLimit("9999"), 500);
  assert.equal(cleanup._test.boundedLimit("invalid"), 500);
});

test("COS signature fixture binds one private PUT and expires after 90 seconds", () => {
  const result = bridge.signedObjectRequest({ method: "PUT", key, content_type: "image/jpeg" }, runtime, 1700000000);
  const url = new URL(result.url);
  assert.equal(url.hostname, "7368-synthetic-123.cos.ap-shanghai.myqcloud.com");
  assert.equal(url.pathname, `/${key}`);
  assert.equal(url.searchParams.get("q-sign-time"), "1700000000;1700000090");
  assert.equal(url.searchParams.get("q-signature"), "2f2113fb150321c9311b8be15a5e5a145f09fc53");
  assert.equal(url.searchParams.get("q-header-list"), "content-type;host;if-none-match;x-cos-acl");
  assert.equal(url.searchParams.get("x-cos-security-token"), runtime.TENCENTCLOUD_SESSIONTOKEN);
  assert.deepEqual(result.headers, { "content-type": "image/jpeg", "x-cos-acl": "private", "if-none-match": "*" });
  assert.equal(result.expires_in, 90);
  assert.ok(!JSON.stringify(result).includes(runtime.TENCENTCLOUD_SECRETKEY));
});

test("storage signing rejects arbitrary resources, nonproduction keys and invalid media", () => {
  for (const payload of [
    { method: "PUT", key, content_type: "text/plain" },
    { method: "PUT", key, content_type: "image/png" },
    { method: "LIST", key },
    { method: "GET", key, bucket: "other" },
    { method: "GET", key: key.replace("production", "test") },
    { method: "GET", key: key.replace("/10/", "/13/") },
    { method: "DELETE", key: "study-meetings/production/../file.jpg" },
  ]) assert.throws(() => bridge.signedObjectRequest(payload, runtime));
  assert.throws(() => bridge.signedObjectRequest({ method: "GET", key }, { ...runtime, TENCENTCLOUD_SESSIONTOKEN: "" }));
  assert.throws(() => bridge.signedObjectRequest({ method: "GET", key }, { ...runtime, CLOUDBASE_STORAGE_REGION: "other" }));
  for (const method of ["GET", "HEAD", "DELETE"]) {
    const result = bridge.signedObjectRequest({ method, key }, runtime, 1700000000);
    assert.deepEqual(result.headers, {});
    assert.equal(new URL(result.url).searchParams.get("q-header-list"), "host");
  }
});

test("HTTP invocations require the server token and never trigger scheduled cleanup", async () => {
  const values = { ...runtime, STUDY_EVIDENCE_CLEANUP_TOKEN: "synthetic-server-token-".repeat(3),
    STUDY_EVIDENCE_STORAGE_BRIDGE_ENABLED: "true" };
  const previous = Object.fromEntries(Object.keys(values).map(k => [k, process.env[k]]));
  const previousFetch = globalThis.fetch;
  Object.assign(process.env, values);
  globalThis.fetch = () => { throw new Error("HTTP signing must not run cleanup"); };
  const event = { httpMethod: "POST", path: "/study-evidence-storage", headers: {},
    body: JSON.stringify({ method: "PUT", key, content_type: "image/jpeg" }) };
  try {
    assert.equal((await cleanup.main({ ...event, httpMethod: "GET" }, {})).statusCode, 405);
    assert.equal((await cleanup.main(event, {})).statusCode, 401);
    assert.equal((await cleanup.main({ ...event, headers: { "x-study-evidence-cleanup-token": "wrong" } }, {})).statusCode, 401);
    event.headers["X-Study-Evidence-Cleanup-Token"] = values.STUDY_EVIDENCE_CLEANUP_TOKEN;
    assert.equal((await cleanup.main({ ...event, path: "/other" }, {})).statusCode, 404);
    const result = await cleanup.main(event, {});
    assert.equal(result.statusCode, 200);
    assert.equal(result.headers["Cache-Control"], "private, no-store");
    assert.equal(JSON.parse(result.body).expires_in, 90);
    assert.ok(!result.body.includes(values.STUDY_EVIDENCE_CLEANUP_TOKEN));
    assert.equal((await cleanup.main({ ...event, body: "x".repeat(2049) }, {})).statusCode, 400);
    assert.equal((await cleanup.main({ ...event, body: "not json" }, {})).statusCode, 503);
    const encoded = { ...event, body: Buffer.from(event.body).toString("base64"), isBase64Encoded: true };
    assert.equal((await cleanup.main(encoded, {})).statusCode, 200);
    delete process.env.TENCENTCLOUD_SECRETKEY;
    assert.deepEqual(JSON.parse((await cleanup.main(event, {})).body), { error: "storage unavailable" });
    process.env.STUDY_EVIDENCE_STORAGE_BRIDGE_ENABLED = "false";
    assert.equal((await cleanup.main(event, {})).statusCode, 503);
  } finally {
    globalThis.fetch = previousFetch;
    for (const [k, v] of Object.entries(previous)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  }
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
