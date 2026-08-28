import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";

const repoRoot = join(fileURLToPath(new URL("..", import.meta.url)));
const miniRoot = join(repoRoot, "apps", "wechat-miniprogram");

function read(relativePath) {
  return readFileSync(join(repoRoot, relativePath), "utf8");
}

test("P3 registers the four learner entry pages", () => {
  const app = JSON.parse(read("apps/wechat-miniprogram/app.json"));
  const sitemap = JSON.parse(read("apps/wechat-miniprogram/sitemap.json"));
  const expected = [
    "pages/learning/index",
    "pages/scan/index",
    "pages/profile/index",
    "pages/services/index"
  ];
  for (const page of expected) {
    assert.ok(app.pages.includes(page), `${page} must be registered in app.json`);
    assert.ok(sitemap.rules.some(rule => rule.page === page), `${page} must be allowed by sitemap`);
  }
});

test("bound home exposes four plain-language entries without technical fields", () => {
  const template = read("apps/wechat-miniprogram/pages/home/index.wxml");
  for (const label of ["我的学习", "扫码", "我的盛和塾", "我的服务"]) {
    assert.match(template, new RegExp(label));
  }
  for (const handler of ["openLearning", "openScan", "openProfile", "openServices"]) {
    assert.match(template, new RegExp(`bindtap=\"${handler}\"`));
  }
  for (const technicalField of ["org_unit_id", "learning_cycle_id", "capability", "scope_type", "role_key"]) {
    assert.doesNotMatch(template, new RegExp(technicalField));
  }
});

test("scan classifier recognizes existing internal entry formats", () => {
  const source = read("apps/wechat-miniprogram/utils/scan.js");
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module
  }, { filename: join(miniRoot, "utils/scan.js") });
  const { classifyScanResult: classify } = module.exports;
  assert.equal(classify({ path: "pages/study-meeting/index" }), "study-meeting");
  assert.equal(classify({ result: '{"type":"study_meeting"}' }), "study-meeting");
  assert.equal(classify({ path: "pages/enrollment/index?token=test-token" }), "enrollment");
  assert.equal(classify({ result: "https://example.invalid/checkin" }), "activity");
  assert.equal(classify({ result: "not-a-supported-code" }), "unknown");
});
