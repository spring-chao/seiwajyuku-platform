const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const appJs = fs.readFileSync(path.join(root, "app.js"), "utf8");
const runtimeConfigJs = fs.readFileSync(path.join(root, "config.runtime.js"), "utf8");
const devConfigJs = fs.readFileSync(path.join(root, "config.dev.js"), "utf8");
const requestJs = fs.readFileSync(path.join(root, "utils/request.js"), "utf8");
const homeJs = fs.readFileSync(path.join(root, "pages/home/index.js"), "utf8");
const homeWxml = fs.readFileSync(path.join(root, "pages/home/index.wxml"), "utf8");
const staffBindJs = fs.readFileSync(path.join(root, "pages/identity/staff-bind.js"), "utf8");
const personBindJs = fs.readFileSync(path.join(root, "pages/identity/bind.js"), "utf8");
const personBindWxml = fs.readFileSync(path.join(root, "pages/identity/bind.wxml"), "utf8");
const operationsJs = fs.readFileSync(path.join(root, "pages/operations/index.js"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "app.json"), "utf8"));

assert.match(appJs, /personSessionToken/);
assert.match(appJs, /require\("\.\/config\.runtime"\)/);
assert.match(runtimeConfigJs, /envVersion === "develop"/);
assert.match(runtimeConfigJs, /\.\/config\.dev/);
assert.match(devConfigJs, /127\.0\.0\.1:8000/);
assert.match(appJs, /setPersonSession/);
assert.match(requestJs, /personSessionToken \|\| app\.globalData\.memberSessionToken/);
assert.match(staffBindJs, /\/api\/v1\/wechat\/staff-bindings\/verify/);
assert.match(staffBindJs, /setPersonSession/);
assert.match(personBindJs, /\/api\/v1\/wechat\/person-bindings\/verify/);
assert.match(personBindJs, /phone_verification/);
assert.match(personBindWxml, /open-type="getPhoneNumber"/);
assert.doesNotMatch(homeWxml, /工作人员身份确认/);
assert.doesNotMatch(homeWxml, /使用已有后台账号确认工作人员身份/);
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
