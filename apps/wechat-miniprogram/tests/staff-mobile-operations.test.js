const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const appJs = fs.readFileSync(path.join(root, "app.js"), "utf8");
const requestJs = fs.readFileSync(path.join(root, "utils/request.js"), "utf8");
const homeJs = fs.readFileSync(path.join(root, "pages/home/index.js"), "utf8");
const homeWxml = fs.readFileSync(path.join(root, "pages/home/index.wxml"), "utf8");
const staffBindJs = fs.readFileSync(path.join(root, "pages/identity/staff-bind.js"), "utf8");
const operationsJs = fs.readFileSync(path.join(root, "pages/operations/index.js"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "app.json"), "utf8"));

assert.match(appJs, /personSessionToken/);
assert.match(appJs, /setPersonSession/);
assert.match(requestJs, /personSessionToken \|\| app\.globalData\.memberSessionToken/);
assert.match(staffBindJs, /\/api\/v1\/wechat\/staff-bindings\/verify/);
assert.match(staffBindJs, /setPersonSession/);
assert.match(homeJs, /\/api\/v1\/wechat\/operations\/workbench/);
assert.match(homeWxml, /移动运营/);
assert.match(operationsJs, /\/api\/v1\/wechat\/operations\/workbench/);
assert.deepEqual(
  [
    "pages/identity/staff-bind",
    "pages/operations/index",
    "pages/operations/today-actions",
    "pages/operations/member-search",
    "pages/operations/followups",
    "pages/operations/study-meetings"
  ].every(page => manifest.pages.includes(page)),
  true
);

console.log("staff mobile operations mini-program tests passed");
