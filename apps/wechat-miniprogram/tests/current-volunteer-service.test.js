const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { resolveVolunteerServices } = require("../utils/volunteer-services");

const ordinary = resolveVolunteerServices({ is_volunteer: false, roles: [] });
assert.equal(ordinary.isVolunteer, false);
assert.equal(ordinary.canManageStudyMeeting, false);
assert.deepEqual(ordinary.serviceAssignments, []);

const volunteer = resolveVolunteerServices({
  is_volunteer: true,
  roles: [
    {
      position_key: "volunteer_regional_service",
      position_name: "分中心服务志工",
      scope_name: "苏州分中心",
      capabilities: []
    },
    {
      position_key: "volunteer_group_counselor",
      position_name: "辅导员",
      scope_name: "卓越组",
      capabilities: ["STUDY_MEETING_MANAGE"]
    }
  ]
});
assert.equal(volunteer.isVolunteer, true);
assert.equal(volunteer.canManageStudyMeeting, true);
assert.equal(volunteer.serviceAssignments[1].scopeName, "卓越组");

const root = path.resolve(__dirname, "..");
const homeJs = fs.readFileSync(path.join(root, "pages/home/index.js"), "utf8");
const homeWxml = fs.readFileSync(path.join(root, "pages/home/index.wxml"), "utf8");
const homeWxss = fs.readFileSync(path.join(root, "pages/home/index.wxss"), "utf8");
const servicesJs = fs.readFileSync(path.join(root, "pages/services/index.js"), "utf8");
const servicesWxml = fs.readFileSync(path.join(root, "pages/services/index.wxml"), "utf8");

assert.match(homeJs, /\/api\/v1\/wechat\/volunteer-services/);
assert.doesNotMatch(homeJs, /\/api\/v1\/study-meetings\/context/);
assert.match(homeWxml, /wx:if="\{\{isVolunteer\}\}"/);
assert.match(homeWxml, /志工服务/);
assert.match(homeWxss, /\.entry-card-wide/);
assert.match(servicesJs, /\/api\/v1\/wechat\/volunteer-services/);
assert.doesNotMatch(servicesJs, /\/api\/v1\/study-meetings\/context/);
assert.match(servicesWxml, /志工服务/);
assert.doesNotMatch(homeWxml, /我的服务/);
assert.doesNotMatch(servicesWxml, /我的服务/);

console.log("current volunteer service mini-program tests passed");
