import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const compiled = ts.transpileModule(
  readFileSync(
    new URL("../src/utils/dashboard-centers.ts", import.meta.url),
    "utf8"
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022
    }
  }
).outputText;
const { centerScope } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);
const shukus = [
  { id: "org-suzhou", name: "苏州塾" },
  { id: "cz", name: "常州塾" },
  { id: "empty", name: "无分中心塾" }
];
const centers = [
  {
    id: "a",
    name: "同名分中心",
    active_member_count: 12,
    shuku_org_unit_id: "org-suzhou"
  },
  {
    id: "b",
    name: "同名分中心",
    active_member_count: 8,
    shuku_org_unit_id: "cz"
  }
];
test("default and switching never mix same-name centers across shukus", () => {
  assert.equal(centerScope(centers, shukus, "").total, 12);
  const result = centerScope(centers, shukus, "cz");
  assert.deepEqual(
    result.rows.map(row => row.id),
    ["b"]
  );
  assert.equal(result.total, 8);
});
test("empty shuku stays selected, and stale selection falls back within visible options", () => {
  assert.equal(centerScope(centers, shukus, "empty").rows.length, 0);
  assert.equal(
    centerScope([centers[1]], [shukus[1]], "org-suzhou").selectedId,
    "cz"
  );
  assert.equal(centerScope([], [], "org-suzhou").rows.length, 0);
});
test("unknown ownership remains separate and is never assigned by name", () => {
  const rows = [
    ...centers,
    { id: "unknown", name: "苏州分中心", active_member_count: 99 }
  ];
  assert.equal(centerScope(rows, shukus, "org-suzhou").total, 12);
  assert.equal(centerScope(rows, shukus, "unassigned").total, 99);
});
