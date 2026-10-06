const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const learningJs = fs.readFileSync(path.join(root, "pages/learning/index.js"), "utf8");
const learningWxml = fs.readFileSync(path.join(root, "pages/learning/index.wxml"), "utf8");
const profileWxml = fs.readFileSync(path.join(root, "pages/profile/index.wxml"), "utf8");

assert.match(learningJs, /\/api\/v1\/wechat\/learning-summary/);
assert.match(learningJs, /\/api\/v1\/wechat\/credit-summary/);
assert.match(learningJs, /summary\.total_points/);
assert.match(learningJs, /summary\.current_learning/);
assert.match(learningJs, /historyPath\(\{ kind: "learning"/);
assert.match(learningJs, /historyResult\.value\.records/);
assert.match(learningJs, /openLearningHistory/);
assert.match(learningJs, /\/api\/v1\/study-meetings\/context/);
assert.match(learningJs, /canManageStudyMeeting/);
assert.doesNotMatch(learningJs, /const currentLearning = assignments/);
assert.doesNotMatch(learningJs, /item\.current_cycle\.learning_cycle_index/);

assert.match(learningWxml, /wx:for="\{\{recentLearning\}\}"/);
assert.match(learningJs, /uiKey:/);
assert.match(learningWxml, /wx:key="uiKey"/);
assert.match(learningWxml, /item\.occurredAtLabel/);
assert.match(learningWxml, /本年度/);
assert.match(learningWxml, /暂无正式入账记录/);
assert.doesNotMatch(learningWxml, /正式学分统计正在建设中/);
assert.doesNotMatch(learningWxml, /source_id/);
assert.doesNotMatch(profileWxml, /正式学分统计正在建设中/);
assert.match(profileWxml, /查看我的学分/);

console.log("learning summary mini-program tests passed");
