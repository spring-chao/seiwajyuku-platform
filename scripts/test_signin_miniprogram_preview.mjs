import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { preparePreview, validateApiBase, verifyStageApi } from "./staging/prepare_signin_preview.mjs";

const stage = "https://stage.signin-fixture.net/platform";
const runtime = { environment: "staging", production: false, production_mutations_allowed: false,
  deployment_read_only: false, wechat_local_test_mode: false, wechat_member_binding_enabled: true,
  identity_authorization_enabled: true, signin_member_checkin_enabled: true, signin_management_enabled: true };

function fixture(t) {
  const folder = mkdtempSync(join(tmpdir(), "signin-preview-test-"));
  const resolvedFolder = realpathSync(folder);
  t.after(() => {
    // Remove only the uniquely created, explicitly verified test directory.
    assert.equal(realpathSync(folder), resolvedFolder);
    assert.ok(dirname(resolvedFolder) === realpathSync(tmpdir()));
    rmSync(folder, { recursive: true, force: true });
  });
  const repo = join(folder, "repo"), app = join(repo, "apps/wechat-miniprogram");
  mkdirSync(join(app, "pages/checkin"), { recursive: true });
  writeFileSync(join(app, "config.js"), 'module.exports = { environment: "PRODUCTION", apiBaseUrl: "https://production.signin-fixture.net/platform", sessionStorageKey: "fixture" };\n');
  writeFileSync(join(app, "project.config.json"), JSON.stringify({ appid: "synthetic-preview-app", setting: { urlCheck: true } }));
  writeFileSync(join(app, "app.json"), '{"pages":["pages/checkin/index"]}');
  writeFileSync(join(app, "pages/checkin/index.js"), 'Page({ data: {} });\n');
  writeFileSync(join(app, "pages/checkin/index.wxml"), '<view>synthetic</view>');
  writeFileSync(join(app, "project.private.config.json"), '{"setting":{"urlCheck":false}}');
  writeFileSync(join(app, ".env"), "SYNTHETIC_SECRET=do-not-copy");
  mkdirSync(join(app, "tests")); writeFileSync(join(app, "tests/private.test.js"), "throw new Error('do not pack');");
  return { folder, repo, app, output: join(folder, "new-preview") };
}

function verified() {
  return { environment: "staging", production: false, health: "ok", fixture_only: true };
}

test("cloud preview preserves production config and domain checks while selecting a dedicated test service", async t => {
  const f = fixture(t);
  writeFileSync(join(f.app, "config.dev.js"), 'module.exports = {apiBaseUrl: "http://127.0.0.1:8000"};');
  await preparePreview({ apiBase: stage, output: f.output, appRoot: f.app, repoRoot: f.repo,
    verify: async () => verified(), cloudbaseEnvironment: "synthetic-env",
    cloudrunService: "sj-signin-stg-20261009-70716ba4",
    signinEngineFunction: "checkinStg2026100970716ba4",
    signinEngineApiBase: "https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com/stg_signin_20261009_70716ba4/api" });
  const config = readFileSync(join(f.output, "wechat-miniprogram/config.js"), "utf8");
  assert.match(config, /"apiTransport": "cloudrun"/);
  assert.match(config, /seiwajyuku_signin_staging_session/);
  assert.doesNotMatch(readFileSync(join(f.output, "wechat-miniprogram/config.dev.js"), "utf8"), /127\.0\.0\.1/);
  assert.match(readFileSync(join(f.app, "config.js"), "utf8"), /PRODUCTION/);
  assert.equal(JSON.parse(readFileSync(join(f.output, "wechat-miniprogram/project.config.json"))).setting.urlCheck, true);
  const other = fixture(t);
  await assert.rejects(preparePreview({ apiBase: stage, output: other.output, appRoot: other.app, repoRoot: other.repo,
    verify: async () => verified(), cloudbaseEnvironment: "synthetic-env", cloudrunService: "seiwajyuku-platform-api" }), /dedicated signin test service/);
});

test("preview rejects preserved production hosts and unsafe or placeholder URLs before any service access", async t => {
  const f = fixture(t); let requests = 0;
  for (const url of ["https://production.signin-fixture.net/platform", "https://production.signin-fixture.net./other",
    "https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com/platform",
    "https://seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com",
    "http://stage.signin-fixture.net", "https://user:pass@stage.signin-fixture.net", stage + "?token=x", stage + "#secret",
    "https://stage.signin-fixture.net:8443", "https://127.0.0.1", "https://[::1]", "https://localhost", "https://stage.example.invalid", "https://stage.example.com"]) {
    await assert.rejects(preparePreview({ apiBase: url, output: f.output, appRoot: f.app, repoRoot: f.repo,
      verify: async () => { requests++; return verified(); } }));
    assert.equal(existsSync(f.output), false);
  }
  assert.equal(requests, 0);
  assert.equal(validateApiBase(stage + "/"), stage);
});

test("readiness reads environment before database health, without auth, cookies or redirects", async () => {
  const calls = [];
  const proof = await verifyStageApi(stage, async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => calls.length === 1 ? runtime : { status: "ok", service: "seiwajyuku-platform-api" } };
  });
  assert.deepEqual(calls.map(call => call.url), [stage + "/api/v1/system/environment", stage + "/health"]);
  for (const { options } of calls) {
    assert.equal(options.method, "GET"); assert.equal(options.redirect, "error"); assert.equal(options.credentials, "omit");
    assert.equal(options.headers.Authorization, undefined); assert.equal(options.headers.Cookie, undefined);
  }
  assert.equal(proof.health, "ok");
});

test("production, mock binding, closed gates and failed readiness cannot create a preview artifact", async t => {
  const f = fixture(t);
  for (const changed of [{ environment: "production", production: true }, { production_mutations_allowed: true },
    { deployment_read_only: true }, { wechat_local_test_mode: true }, { signin_member_checkin_enabled: false },
    { wechat_member_binding_enabled: false }, { signin_management_enabled: false }]) {
    let calls = 0;
    await assert.rejects(preparePreview({ apiBase: stage, output: f.output, appRoot: f.app, repoRoot: f.repo,
      verify: value => verifyStageApi(value, async () => { calls++; return { ok: true, json: async () => ({ ...runtime, ...changed }) }; }) }));
    assert.equal(calls, 1, "Rejected environment must not touch database health");
    assert.equal(existsSync(f.output), false);
  }
  for (const fetchImpl of [async () => { throw new Error("synthetic TLS/network failure"); },
    async () => ({ ok: false }), async () => ({ ok: true, json: async () => ({}) })]) {
    await assert.rejects(preparePreview({ apiBase: stage, output: f.output, appRoot: f.app, repoRoot: f.repo,
      verify: value => verifyStageApi(value, fetchImpl) }));
    assert.equal(existsSync(f.output), false);
  }
});

test("prepared STAGING copy preserves source bytes and domain validation and never claims an upload or phone acceptance", async t => {
  const f = fixture(t), source = readFileSync(join(f.app, "config.js"));
  const manifest = await preparePreview({ apiBase: stage, output: f.output, appRoot: f.app, repoRoot: f.repo, verify: async () => verified() });
  assert.equal(manifest.status, "PREPARED_NOT_UPLOADED"); assert.equal(manifest.uploaded, false); assert.equal(manifest.phone_acceptance, false);
  assert.equal(manifest.cloudbase_database_isolation_verified, false); assert.equal(manifest.wechat_legal_domains_verified, false);
  assert.equal(readFileSync(join(f.app, "config.js")).equals(source), true);
  const preview = join(f.output, "wechat-miniprogram");
  assert.match(readFileSync(join(preview, "config.js"), "utf8"), /"environment": "STAGING"/);
  assert.ok(readFileSync(join(preview, "config.js"), "utf8").includes(stage));
  assert.equal(JSON.parse(readFileSync(join(preview, "project.config.json"), "utf8")).setting.urlCheck, true);
  for (const file of ["project.private.config.json", ".env", "tests"]) assert.equal(existsSync(join(preview, file)), false);
  const saved = JSON.parse(readFileSync(join(f.output, "preview-manifest.json"), "utf8"));
  assert.ok(saved.files.length >= 4 && saved.files.every(file => /^[a-f0-9]{64}$/.test(file.sha256)));
  assert.equal(saved.uploaded, false);
});

test("existing or repository-internal output cannot be overwritten or probed", async t => {
  const f = fixture(t); mkdirSync(f.output); writeFileSync(join(f.output, "sentinel"), "preserve");
  let calls = 0;
  for (const output of [f.output, join(f.repo, "new-preview"), join(f.app, "nested-preview")]) {
    await assert.rejects(preparePreview({ apiBase: stage, output, appRoot: f.app, repoRoot: f.repo,
      verify: async () => { calls++; return verified(); } }));
  }
  assert.equal(calls, 0); assert.equal(readFileSync(join(f.output, "sentinel"), "utf8"), "preserve");
});

test("source and output symlink aliases cannot route a preview into another tree", async t => {
  const f = fixture(t);
  symlinkSync(f.repo, join(f.folder, "repo-alias"), "junction");
  await assert.rejects(preparePreview({ apiBase: stage, output: join(f.folder, "repo-alias/new-preview"), appRoot: f.app, repoRoot: f.repo, verify: async () => verified() }));
  symlinkSync(f.folder, join(f.app, "unsafe-alias"), "junction");
  await assert.rejects(preparePreview({ apiBase: stage, output: f.output, appRoot: f.app, repoRoot: f.repo, verify: async () => verified() }), /symlinks/);
  assert.equal(existsSync(f.output), false);
});

test("hardcoded production fallback leaves a failed manifest instead of a successful preview", async t => {
  const f = fixture(t);
  writeFileSync(join(f.app, "old-fallback.js"), 'const url = "https://production.signin-fixture.net/api";');
  await assert.rejects(preparePreview({ apiBase: stage, output: f.output, appRoot: f.app, repoRoot: f.repo, verify: async () => verified() }), /production endpoint/);
  const manifest = JSON.parse(readFileSync(join(f.output, "preview-manifest.json"), "utf8"));
  assert.equal(manifest.status, "PREPARATION_FAILED"); assert.equal(manifest.uploaded, false);
  assert.match(readFileSync(join(f.app, "config.js"), "utf8"), /PRODUCTION/);
});

test("CLI requires the real API URL and new output directory and offers no upload or verification bypass", () => {
  const script = resolve(dirname(fileURLToPath(import.meta.url)), "staging/prepare_signin_preview.mjs");
  for (const args of [[], ["--api-base", stage], ["--skip-verification", "true"], ["--upload", "true"]]) {
    const result = spawnSync(process.execPath, [script, ...args], { encoding: "utf8" });
    assert.equal(result.status, 1); assert.equal(result.stdout, "");
  }
});
