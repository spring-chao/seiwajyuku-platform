import { readdirSync, readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(fileURLToPath(import.meta.url), "..", "..");
const appRoot = join(repoRoot, "apps", "wechat-miniprogram");
const expectedAppId = process.env.MINIPROGRAM_EXPECTED_APPID || "wxb63212d3ce337b81";
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
for (const file of ["app.json", "sitemap.json", "pages/enrollment/index.json"]) {
  readJson(join(appRoot, file));
}

if (project) {
  if (project.appid !== expectedAppId) {
    errors.push(`project.config.json appid 必须为 ${expectedAppId}，实际为 ${project.appid}`);
  }
  if (!String(project.description || "").includes("[TEST]")) {
    errors.push("project.config.json 必须包含 [TEST] 环境标记");
  }
  if (project.setting?.urlCheck !== false) {
    errors.push("TEST 配置必须显式关闭 urlCheck；正式发布前应切换为正式配置并恢复域名校验");
  }
}

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = join(directory, entry.name);
    return entry.isDirectory() ? walk(file) : [file];
  });
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
  environment: "TEST",
  urlCheck: project?.setting?.urlCheck,
  javascript: "PASS",
  json: "PASS",
}, null, 2));
