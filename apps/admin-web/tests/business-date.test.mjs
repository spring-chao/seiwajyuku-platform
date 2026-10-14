import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
const code = ts.transpileModule(
  readFileSync(
    new URL("../src/utils/business-date.ts", import.meta.url),
    "utf8"
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022
    }
  }
).outputText;
const { chinaDate, chinaTime, chinaTimestampDate, createBusinessDayRefresh } =
  await import(
    `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
  );
test("today follows Beijing midnight rather than UTC or browser timezone", () => {
  assert.equal(chinaDate(new Date("2026-10-14T15:59:59Z")), "2026-10-14");
  assert.equal(chinaDate(new Date("2026-10-14T16:00:00Z")), "2026-10-15");
  assert.equal(chinaDate(new Date("2026-12-31T16:00:00Z")), "2027-01-01");
  assert.equal(chinaTime("2026-10-14 17:30:00"), "01:30");
  assert.equal(chinaTimestampDate("2026-10-14 17:30:00"), "2026-10-15");
});
test("crossing midnight refreshes once, including a suspended tab resuming next day", () => {
  let day = "2026-10-14",
    calls = 0;
  const check = createBusinessDayRefresh(
    () => day,
    () => calls++
  );
  check();
  assert.equal(calls, 0);
  day = "2026-10-15";
  check();
  check();
  assert.equal(calls, 1);
  day = "2026-10-17";
  check();
  assert.equal(calls, 2);
});
