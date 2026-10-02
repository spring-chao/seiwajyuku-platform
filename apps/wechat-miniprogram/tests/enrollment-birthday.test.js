const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

test("enrollment rejects future and nonexistent birthdays while accepting leap days", () => {
  const context = { getApp: () => ({ globalData: {} }), Page() {} };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../pages/enrollment/index.js"), "utf8"), context);
  const valid = (date, max) => vm.runInContext(`validBirthday(${JSON.stringify(date)}, ${JSON.stringify(max)})`, context);
  assert.equal(valid("2000-02-29", "2026-10-02"), true);
  assert.equal(valid("2001-02-29", "2026-10-02"), false);
  assert.equal(valid("2026-04-31", "2026-10-02"), false);
  assert.equal(valid("2026-10-03", "2026-10-02"), false);
  assert.equal(valid("2026-10-02", "2026-10-02"), true);
  assert.equal(valid("1899-12-31", "2026-10-02"), false);
});
