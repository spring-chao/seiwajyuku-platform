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

test("activity records no longer depend on legacy sync health", () => {
  assert.match(apiSource, /\| "STALE"/);
  assert.doesNotMatch(activitySource, /getAttendanceSyncStatus|syncAlert|签到自动同步/);
  assert.match(activitySource, /查看活动、场次及签到记录/);
});
