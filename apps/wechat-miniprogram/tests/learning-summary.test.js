const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const learningJs = fs.readFileSync(path.join(root, "pages/learning/index.js"), "utf8");
const learningWxml = fs.readFileSync(path.join(root, "pages/learning/index.wxml"), "utf8");
const profileWxml = fs.readFileSync(path.join(root, "pages/profile/index.wxml"), "utf8");

assert.match(learningJs, /\/api\/v1\/wechat\/learning-summary/);
assert.match(learningJs, /summary\.current_learning/);
assert.match(learningJs, /summary\.recent_learning/);
assert.match(learningJs, /\/api\/v1\/study-meetings\/context/);
assert.match(learningJs, /canManageStudyMeeting/);
assert.doesNotMatch(learningJs, /const currentLearning = assignments/);
assert.doesNotMatch(learningJs, /item\.current_cycle\.learning_cycle_index/);

assert.match(learningWxml, /wx:for="\{\{recentLearning\}\}"/);
assert.match(learningWxml, /item\.occurredAtLabel/);
assert.match(learningWxml, /正式学分统计正在建设中/);
assert.doesNotMatch(learningWxml, /暂无学分记录/);
assert.doesNotMatch(profileWxml, /暂无学分记录/);

console.log("learning summary mini-program tests passed");
