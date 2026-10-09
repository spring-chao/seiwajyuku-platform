import { createHash } from "node:crypto";
import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from "node:fs";
import { isIP } from "node:net";
import { dirname, extname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const applicationRoot = join(repositoryRoot, "apps/wechat-miniprogram");
const productionHosts = [
  "shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com",
  "seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com"
];
const excluded = new Set(["tests", "node_modules", "README.md", "project.private.config.json"]);
const sha256 = value => createHash("sha256").update(value).digest("hex");
const inside = (parent, child) => {
  const path = relative(parent, child);
  return !path || (!path.startsWith(".." + sep) && path !== ".." && !isAbsolute(path));
};

function loadConfig(file) {
  const module = { exports: {} };
  vm.runInNewContext(readFileSync(file, "utf8"), { module, exports: module.exports }, { timeout: 1000 });
  return module.exports;
}

export function validateApiBase(value, extraProductionHosts = []) {
  let url;
  try { url = new URL(value); } catch { throw new Error("apiBase must be an actual isolated HTTPS API URL"); }
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.port && url.port !== "443") {
    throw new Error("apiBase requires HTTPS without credentials, query, fragment or custom port");
  }
  if (!host.includes(".") || isIP(host.replace(/^\[|\]$/g, "")) ||
      /(?:^|\.)(?:localhost|local|lan|invalid|test|example)$/.test(host) ||
      /(?:^|\.)example\.(?:com|net|org)$/.test(host)) {
    throw new Error("apiBase must use a real public HTTPS host, not a local or placeholder address");
  }
  const blocked = [...productionHosts, ...extraProductionHosts].map(item => item.toLowerCase().replace(/\.$/, ""));
  if (blocked.some(item => host === item || host.endsWith("." + item))) throw new Error("Known production API hosts cannot be used for a preview");
  url.hostname = host;
  return url.toString().replace(/\/$/, "");
}

// These endpoints expose runtime flags and readiness only. No auth, cookies,
// business records, redirects, TLS bypass or upload operation is used here.
export async function verifyStageApi(apiBase, fetchImpl = fetch) {
  apiBase = validateApiBase(apiBase);
  async function read(path) {
    const response = await fetchImpl(apiBase + path, {
      method: "GET", credentials: "omit", redirect: "error", signal: AbortSignal.timeout(8000),
      headers: { accept: "application/json" }
    });
    if (!response.ok) throw new Error("Staging API readiness endpoint was not successful");
    return response.json();
  }
  const runtime = await read("/api/v1/system/environment");
  if (!["dev", "test", "staging"].includes(runtime.environment) || runtime.production !== false ||
      runtime.production_mutations_allowed !== false || runtime.deployment_read_only !== false ||
      runtime.wechat_local_test_mode !== false || runtime.wechat_member_binding_enabled !== true ||
      runtime.identity_authorization_enabled !== true || runtime.signin_member_checkin_enabled !== true ||
      runtime.signin_management_enabled !== true) {
    throw new Error("API runtime must be non-production with real WeChat binding and signin enabled");
  }
  const health = await read("/health");
  if (health.status !== "ok" || health.service !== "seiwajyuku-platform-api") throw new Error("Staging platform health could not be verified");
  return { environment: runtime.environment, production: false, production_mutations_allowed: false,
    wechat_local_test_mode: false, binding_and_signin_enabled: true, health: "ok" };
}

function newOutputPath(value, root, source) {
  if (!value) throw new Error("A new output directory is required");
  const target = resolve(value);
  if (existsSync(target)) throw new Error("Output must be a completely new directory; existing files will not be overwritten");
  let ancestor = dirname(target);
  while (!existsSync(ancestor)) ancestor = dirname(ancestor);
  const effective = resolve(realpathSync(ancestor), relative(ancestor, target));
  if (inside(realpathSync(root), effective) || inside(realpathSync(source), effective)) {
    throw new Error("Preview output must stay outside the source repository");
  }
  return effective;
}

function sourceFiles(root) {
  return readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    if (entry.name.startsWith(".") || excluded.has(entry.name)) return [];
    const file = join(root, entry.name);
    if (lstatSync(file).isSymbolicLink()) throw new Error("Preview source must not contain symlinks");
    if (entry.isDirectory()) return sourceFiles(file);
    if (!entry.isFile()) throw new Error("Unsupported preview source entry");
    return [file];
  });
}

export async function preparePreview({ apiBase, output, appRoot = applicationRoot, repoRoot = repositoryRoot,
  verify = verifyStageApi, cloudbaseEnvironment = "", cloudrunService = "", signinEngineApiBase = "", signinEngineFunction = "" } = {}) {
  if (cloudbaseEnvironment || cloudrunService) {
    if (!/^[a-z0-9][a-z0-9-]{2,63}$/.test(cloudbaseEnvironment) ||
        !/^sj-signin-stg-20\d{6}-[a-f0-9]{8}$/.test(cloudrunService)) {
      throw new Error("Cloud transport requires an explicit environment and dedicated signin test service");
    }
    const suffix = cloudrunService.slice("sj-signin-stg-".length);
    if (signinEngineFunction !== "checkinStg" + suffix.replace(/-/g, "") ||
        !/^https:\/\/[a-z0-9.-]+\/stg_signin_20\d{6}_[a-f0-9]{8}\/api$/.test(signinEngineApiBase) ||
        !signinEngineApiBase.endsWith("/stg_signin_" + suffix.replace(/-/g, "_") + "/api")) {
      throw new Error("Cloud preview requires the matching test engine function and gateway namespace");
    }
  }
  const sourceConfigPath = join(appRoot, "config.js");
  const originalConfig = readFileSync(sourceConfigPath);
  const sourceConfig = loadConfig(sourceConfigPath);
  if (sourceConfig.environment !== "PRODUCTION") throw new Error("Expected the preserved production source config");
  const sourceHost = new URL(sourceConfig.apiBaseUrl).hostname;
  const stageBase = validateApiBase(apiBase, [sourceHost]);
  const target = newOutputPath(output, repoRoot, appRoot);
  const project = JSON.parse(readFileSync(join(appRoot, "project.config.json"), "utf8"));
  if (project.setting?.urlCheck !== true) throw new Error("Source project must already enable urlCheck");
  const files = sourceFiles(appRoot);
  const runtime = await verify(stageBase);
  const previewRoot = join(target, "wechat-miniprogram");
  mkdirSync(dirname(target), { recursive: true });
  mkdirSync(target);
  const manifest = { status: "PREPARATION_FAILED", created_at_utc: new Date().toISOString(),
    uploaded: false, phone_acceptance: false, environment: "STAGING", apiBaseUrl: stageBase,
    urlCheck: true, source: appRoot, source_config_sha256: sha256(originalConfig), project: previewRoot,
    api_readiness: runtime, wechat_legal_domains_verified: false, cloudbase_database_isolation_verified: false,
    api_transport: cloudrunService ? "cloudrun" : "request",
    cloudbase_environment: cloudbaseEnvironment || null, cloudrun_service: cloudrunService || null,
    requires_external_validation: ["Test database and collection namespace isolation",
      cloudrunService ? "Mini-program association with CloudBase environment" : "WeChat request legal domain",
      "Real phone, binding, native checkin and pending retry"], files: [] };
  try {
    for (const file of files) {
      const local = relative(appRoot, file);
      const destination = join(previewRoot, local);
      mkdirSync(dirname(destination), { recursive: true });
      let content = readFileSync(file);
      if (local === "config.js") content = Buffer.from("// Isolated preview artifact only; repository production config is preserved.\nmodule.exports = " +
        JSON.stringify({ ...sourceConfig, apiBaseUrl: stageBase, environment: "STAGING",
          sessionStorageKey: "seiwajyuku_signin_staging_session",
          ...(cloudrunService ? { apiTransport: "cloudrun", cloudbaseEnvironment, cloudrunService, signinEngineApiBase, signinEngineFunction } : {}) }, null, 2) + ";\n");
      if (local === "config.dev.js") content = Buffer.from("// Prepared preview uses the same verified test service on desktop and phone.\nmodule.exports = {};\n");
      if ([".js", ".json", ".wxml", ".wxss"].includes(extname(file))) {
        let text = content.toString("utf8").toLowerCase();
        // The shared environment gateway is permitted only for the exact
        // configured test namespace. It is used to validate the signed engine
        // ticket destination; cloudFunction carries the actual phone request.
        if (local === "config.js" && cloudrunService) text = text.replace(signinEngineApiBase.toLowerCase(), "");
        if ([...productionHosts, sourceHost.toLowerCase()].some(host => text.includes(host))) {
          throw new Error("Preview source still references a known production endpoint: " + local);
        }
      }
      writeFileSync(destination, content, { flag: "wx" });
      manifest.files.push({ path: local.split(sep).join("/"), sha256: sha256(content) });
    }
    if (!readFileSync(sourceConfigPath).equals(originalConfig)) throw new Error("Production source config changed during preparation");
    manifest.status = "PREPARED_NOT_UPLOADED";
    writeFileSync(join(target, "preview-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", { flag: "wx" });
    return manifest;
  } catch (error) {
    writeFileSync(join(target, "preview-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", { flag: "wx" });
    throw error;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    const options = {};
    for (let index = 0; index < args.length; index += 2) {
      const key = args[index];
      if (!["--api-base", "--output", "--cloudbase-env", "--cloudrun-service", "--engine-api-base", "--engine-function"].includes(key) || !args[index + 1] || options[key]) throw new Error("Invalid preview preparation arguments");
      options[key] = args[index + 1];
    }
    if (!options["--api-base"] || !options["--output"]) throw new Error("Usage: node scripts/staging/prepare_signin_preview.mjs --api-base <actual-isolated-HTTPS-API> --output <new-directory-outside-repo>");
    const manifest = await preparePreview({ apiBase: options["--api-base"], output: options["--output"],
      cloudbaseEnvironment: options["--cloudbase-env"], cloudrunService: options["--cloudrun-service"],
      signinEngineApiBase: options["--engine-api-base"], signinEngineFunction: options["--engine-function"] });
    console.log(JSON.stringify({ status: manifest.status, project: manifest.project, uploaded: false, phone_acceptance: false }));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
