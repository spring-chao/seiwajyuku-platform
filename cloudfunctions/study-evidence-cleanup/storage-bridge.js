"use strict";

const { createHash, createHmac, timingSafeEqual } = require("node:crypto");
const PATH = "/study-evidence-storage";
const KEY = /^study-meetings\/production\/20[0-9]{2}\/(0[1-9]|1[0-2])\/[a-f0-9]{32}\.(jpg|png)$/;
const enc = value => encodeURIComponent(value).replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase());
const hmac = (key, value) => createHmac("sha1", key).update(value).digest("hex");
const canonical = values => Object.keys(values).sort().map(k => `${enc(k)}=${enc(values[k])}`).join("&");

function signedObjectRequest(payload, env = process.env, now = Math.floor(Date.now() / 1000)) {
  if (!payload || Object.keys(payload).some(k => !["method", "key", "content_type"].includes(k)) ||
      !KEY.test(payload.key || "") || !["PUT", "GET", "HEAD", "DELETE"].includes(payload.method)) {
    throw new Error("invalid object request");
  }
  const bucket = env.CLOUDBASE_STORAGE_BUCKET || "", region = env.CLOUDBASE_STORAGE_REGION || "";
  if (!/^[a-z0-9-]+-[0-9]+$/.test(bucket) || region !== "ap-shanghai") throw new Error("storage not configured");
  const id = env.TENCENTCLOUD_SECRETID, secret = env.TENCENTCLOUD_SECRETKEY, token = env.TENCENTCLOUD_SESSIONTOKEN;
  if (!id || !secret || !token) throw new Error("temporary runtime identity unavailable");
  const host = `${bucket}.cos.${region}.myqcloud.com`;
  const headers = { host };
  if (payload.method === "PUT") {
    const type = payload.key.endsWith(".png") ? "image/png" : "image/jpeg";
    if (payload.content_type !== type) throw new Error("invalid media type");
    Object.assign(headers, { "content-type": type, "x-cos-acl": "private", "if-none-match": "*" });
  }
  const query = { "x-cos-security-token": token }, time = `${now};${now + 90}`;
  const http = `${payload.method.toLowerCase()}\n/${payload.key}\n${canonical(query)}\n${canonical(headers)}\n`;
  const digest = createHash("sha1").update(http).digest("hex");
  const signature = hmac(hmac(secret, time), `sha1\n${time}\n${digest}\n`);
  Object.assign(query, { "q-sign-algorithm": "sha1", "q-ak": id, "q-sign-time": time,
    "q-key-time": time, "q-header-list": Object.keys(headers).sort().join(";"),
    "q-url-param-list": "x-cos-security-token", "q-signature": signature });
  delete headers.host;
  return { url: `https://${host}/${payload.key}?${canonical(query)}`, headers, expires_in: 90 };
}

function response(statusCode, data) {
  return { statusCode, headers: { "Content-Type": "application/json", "Cache-Control": "private, no-store" },
    body: JSON.stringify(data), isBase64Encoded: false };
}

function handle(event) {
  if (event.httpMethod !== "POST") return response(405, { error: "method not allowed" });
  // CloudBase strips the configured route prefix by default. Accept only
  // that mapped root or the full path when passthrough is enabled.
  if (![PATH, "/", ""].includes(event.path)) return response(404, { error: "not found" });
  const expected = process.env.STUDY_EVIDENCE_CLEANUP_TOKEN || "";
  const headers = Object.fromEntries(Object.entries(event.headers || {}).map(([k,v]) => [k.toLowerCase(),v]));
  const supplied = headers["x-study-evidence-cleanup-token"] || "";
  if (typeof supplied !== "string" || expected.length < 32 || Buffer.byteLength(supplied) !== Buffer.byteLength(expected) ||
      !timingSafeEqual(Buffer.from(supplied), Buffer.from(expected))) return response(401, { error: "unauthorized" });
  if (process.env.STUDY_EVIDENCE_STORAGE_BRIDGE_ENABLED !== "true") return response(503, { error: "storage unavailable" });
  try {
    const body = event.isBase64Encoded ? Buffer.from(event.body || "", "base64").toString("utf8") : event.body;
    if (typeof body !== "string" || Buffer.byteLength(body) > 2048) return response(400, { error: "invalid request" });
    return response(200, signedObjectRequest(JSON.parse(body)));
  } catch (_) {
    // Never put runtime credentials, signed URLs, or provider errors in logs.
    return response(503, { error: "storage unavailable" });
  }
}

module.exports = { handle, signedObjectRequest };
