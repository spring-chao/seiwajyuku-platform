import { readdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return walk(path);
    return entry.isFile() ? [path] : [];
  });
}

export function discoverTests(root, scope) {
  let files;
  if (scope === "admin-web") {
    files = walk(join(root, "apps/admin-web/tests"))
      .filter(path => /\.test\.(?:mjs|js)$/.test(path));
  } else if (scope === "miniprogram") {
    files = walk(join(root, "apps/wechat-miniprogram/tests"))
      .filter(path => /\.test\.(?:mjs|js)$/.test(path));
    files.push(...readdirSync(join(root, "scripts"))
      .filter(name => /^test_.*miniprogram.*\.mjs$/.test(name))
      .map(name => join(root, "scripts", name)));
  } else {
    throw new Error(`Unknown test scope: ${scope}. Use admin-web or miniprogram.`);
  }
  if (files.length === 0) throw new Error(`No tests found for ${scope}`);
  return files.map(path => relative(root, path)).sort();
}

export function runTests(root, scope, { list = false } = {}) {
  const files = discoverTests(root, scope);
  if (list) {
    console.log(files.join("\n"));
    return 0;
  }
  console.log(`Running ${files.length} ${scope} test files`);
  // A parent node:test process marks its children; this is a fresh test run.
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, ["--test", ...files], {
    cwd: root,
    env,
    stdio: "inherit"
  });
  if (result.error) throw result.error;
  return result.status ?? 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const [scope, ...options] = process.argv.slice(2);
    if (options.some(option => option !== "--list") || options.length > 1) {
      throw new Error("Usage: node scripts/run_node_tests.mjs <admin-web|miniprogram> [--list]");
    }
    process.exitCode = runTests(repositoryRoot, scope, { list: options.includes("--list") });
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
