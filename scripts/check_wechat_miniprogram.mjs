import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const repoRoot = resolve(fileURLToPath(import.meta.url), "..", "..");
const appRoot = join(repoRoot, "apps", "wechat-miniprogram");
const expectedAppId = process.env.MINIPROGRAM_EXPECTED_APPID || "wx9622a0ab5070ae2d";
const expectedEnvironment = process.env.MINIPROGRAM_EXPECTED_ENVIRONMENT || "PRODUCTION";
const expectedApiBaseUrl = process.env.MINIPROGRAM_EXPECTED_API_BASE_URL ||
  "https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.ap-shanghai.app.tcloudbase.com/platform";
const expectedDescription = process.env.MINIPROGRAM_EXPECTED_DESCRIPTION || "学长服务助手";
const errors = [];

function readJson(file) {
  try {
    return JSON.parse(readFileSync(file, "utf8"));
  } catch (error) {
    errors.push(`${relative(repoRoot, file)} JSON 无法解析: ${error.message}`);
    return null;
  }
}

const project = readJson(join(appRoot, "project.config.json"));
const app = readJson(join(appRoot, "app.json"));
const jsonFiles = [];

if (project) {
  if (project.appid !== expectedAppId) {
    errors.push(`project.config.json appid 必须为 ${expectedAppId}，实际为 ${project.appid}`);
  }
  if (project.description !== expectedDescription) {
    errors.push(`project.config.json description 必须为 ${expectedDescription}，实际为 ${project.description}`);
  }
  if (project.setting?.urlCheck !== true) {
    errors.push("正式配置必须显式开启 urlCheck");
  }
}

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  });
}

for (const file of walk(appRoot).filter((item) => item.endsWith(".json"))) {
  jsonFiles.push(file);
  if (file.endsWith("project.config.json")) continue;
  readJson(file);
}

const configFile = join(appRoot, "config.js");
let appConfig = null;
try {
  const module = { exports: {} };
  vm.runInNewContext(readFileSync(configFile, "utf8"), { module, exports: module.exports }, {
    filename: configFile
  });
  appConfig = module.exports;
} catch (error) {
  errors.push(`${relative(repoRoot, configFile)} 配置无法加载: ${error.message}`);
}

if (appConfig) {
  if (appConfig.environment !== expectedEnvironment) {
    errors.push(`config.js environment 必须为 ${expectedEnvironment}，实际为 ${appConfig.environment}`);
  }
  if (appConfig.apiBaseUrl !== expectedApiBaseUrl) {
    errors.push(`config.js apiBaseUrl 必须为 ${expectedApiBaseUrl}，实际为 ${appConfig.apiBaseUrl}`);
  }
}

if (app && app.window?.navigationBarTitleText !== "学长服务助手") {
  errors.push("app.json navigationBarTitleText 必须为 学长服务助手");
}

for (const file of walk(appRoot).filter((item) => /\.(js|json|md|wxml|wxss)$/.test(item))) {
  const source = readFileSync(file, "utf8");
  if (source.includes("盛和塾")) {
    errors.push(`${relative(repoRoot, file)} 仍包含禁止的用户可见品牌文案：盛和塾`);
  }
}

for (const file of walk(appRoot).filter((item) => item.endsWith(".js"))) {
  const check = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
  if (check.status !== 0) {
    errors.push(`${relative(repoRoot, file)} JavaScript 语法检查失败: ${check.stderr.trim()}`);
  }
  const source = readFileSync(file, "utf8");
  if (/app[_-]?secret\s*[:=]/i.test(source)) {
    errors.push(`${relative(repoRoot, file)} 禁止写入 AppSecret 值`);
  }
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}

console.log(JSON.stringify({
  status: "PASS",
  appid: project?.appid,
  environment: appConfig?.environment,
  apiBaseUrl: appConfig?.apiBaseUrl,
  urlCheck: project?.setting?.urlCheck,
  javascript: "PASS",
  json: `${jsonFiles.length} files PASS`,
  restrictedBrandText: "NONE",
}, null, 2));
