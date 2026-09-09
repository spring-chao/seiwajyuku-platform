import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const router = readFileSync(
  new URL("../src/router/modules/seiwajyuku.ts", import.meta.url),
  "utf8"
);
const routerUtils = readFileSync(
  new URL("../src/router/utils.ts", import.meta.url),
  "utf8"
);

test("business menus use permissions instead of role-name allowlists", () => {
  assert.doesNotMatch(router, /\broles\s*:/);
  for (const permission of [
    "members:read",
    "followups:manage",
    "renewals:read",
    "staff:manage",
    "org:manage"
  ]) {
    assert.match(router, new RegExp(permission.replace(":", "\\:")));
  }
  assert.match(routerUtils, /currentPermissions/);
  assert.match(routerUtils, /meta\?\.auths/);
});

test("staff catalog UI follows institution scope metadata", () => {
  const page = readFileSync(
    new URL("../src/views/seiwajyuku/staff-management.vue", import.meta.url),
    "utf8"
  );
  assert.match(page, /scope_root_org_unit_id/);
  assert.match(page, /scopeOrgTree/);
  assert.match(page, /duty_description/);
  assert.match(page, /scope_available/);
});
