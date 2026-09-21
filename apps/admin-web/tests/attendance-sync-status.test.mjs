import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const apiSource = readFileSync(
  new URL("../src/api/seiwajyuku.ts", import.meta.url),
  "utf8"
);
const activitySource = readFileSync(
  new URL("../src/views/seiwajyuku/activities.vue", import.meta.url),
  "utf8"
);

test("stale attendance sync is a red interruption alert, never a green success", () => {
  assert.match(apiSource, /\| "STALE"/);
  assert.match(activitySource, /status\.state === "STALE"/);
  assert.match(activitySource, /type: "error" as const/);
  assert.match(activitySource, /签到自动同步已中断/);
});
