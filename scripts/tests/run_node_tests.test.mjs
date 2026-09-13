import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, sep } from "node:path";
import test from "node:test";
import { discoverTests, runTests } from "../run_node_tests.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "platform-test-entry-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const directory of ["apps/admin-web/tests", "apps/wechat-miniprogram/tests", "scripts"]) {
    mkdirSync(join(root, directory), { recursive: true });
  }
  return {
    root,
    put(path, content = "") {
      mkdirSync(dirname(join(root, path)), { recursive: true });
      writeFileSync(join(root, path), content);
    }
  };
}

test("new nested tests are discovered without YAML edits, helpers stay excluded", t => {
  const f = fixture(t);
  f.put("apps/admin-web/tests/staff-permission-driven.test.mjs");
  f.put("apps/admin-web/tests/nested/future.test.js");
  f.put("apps/admin-web/tests/helper.mjs");
  f.put("apps/wechat-miniprogram/tests/binding.test.js");
  f.put("scripts/test_identity_binding_miniprogram.mjs");
  f.put("scripts/test_miniprogram_portal.mjs");
  f.put("scripts/check_wechat_miniprogram.mjs");
  const normalized = scope => discoverTests(f.root, scope).map(path => path.split(sep).join("/"));
  assert.deepEqual(normalized("admin-web"), [
    "apps/admin-web/tests/nested/future.test.js",
    "apps/admin-web/tests/staff-permission-driven.test.mjs"
  ]);
  assert.deepEqual(normalized("miniprogram"), [
    "apps/wechat-miniprogram/tests/binding.test.js",
    "scripts/test_identity_binding_miniprogram.mjs",
    "scripts/test_miniprogram_portal.mjs"
  ]);
});

test("unknown scopes and empty suites cannot silently pass", t => {
  const f = fixture(t);
  assert.throws(() => discoverTests(f.root, "typo"), /Unknown test scope/);
  assert.throws(() => discoverTests(f.root, "admin-web"), /No tests found/);
  assert.throws(() => discoverTests(f.root, "miniprogram"), /No tests found/);
});

test("child test failures propagate to the entry point", t => {
  const f = fixture(t);
  f.put("apps/admin-web/tests/failing.test.mjs", 'import test from "node:test"; test("fixture", () => { throw new Error("expected fixture failure"); });');
  assert.notEqual(runTests(f.root, "admin-web"), 0);
  f.put("apps/admin-web/tests/failing.test.mjs", 'import test from "node:test"; test("fixture", () => {});');
  assert.equal(runTests(f.root, "admin-web"), 0);
});
