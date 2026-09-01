import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";

const nodeRequire = createRequire(import.meta.url);
const studyMeetingUtils = nodeRequire("../apps/wechat-miniprogram/utils/study-meeting.js");

const meetingPlan = {
  learning_cycle_index: 5,
  cycle_index: 5,
  steps: [
    { step_no: 1, content: "企业经营者分享", is_terminal: false, learning_content_keys: ["content-1"] },
    { step_no: 2, content: "空巴。", is_terminal: true, learning_content_keys: [] },
    { step_no: 3, content: "不应显示的后续节点", is_terminal: false, learning_content_keys: [] }
  ],
  learning_contents: [{
    content_key: "content-1",
    task_type: "VIDEO_LEARNING",
    title: "关于核算表分析&任务单的制作",
    description: "全组观看并研讨",
    required: true,
    sort_order: 1,
    credit_points: 40,
    content_access: { type: "NONE", label: null }
  }]
};

function harness() {
  const calls = [];
  const app = { globalData: { studyMeetingDraft: { group_org_unit_id: "group-test", member_ids: [1], cross_group_member_ids: [] } } };
  const wx = {
    showToast: payload => calls.push({ toast: payload.title }),
    redirectTo: payload => calls.push({ navigation: payload.url }),
    chooseMedia: options => { assert.equal(options.count, 1); options.success({ tempFiles: [{ tempFilePath: "/tmp/source.jpg" }] }); },
    compressImage: options => { calls.push({ compressed: true }); options.success({ tempFilePath: "/tmp/small.jpg" }); },
    getFileSystemManager: () => ({ getFileInfo: options => options.success({ size: 2048 }) })
  };
  let page;
  let failSubmit = false;
  const request = async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith("/submit") && failSubmit) { failSubmit = false; throw new Error("retry"); }
    if (path.endsWith("/submit")) return { data: { id: 42, status: "SUBMITTED", courses: [], evidence: { id: 1 } } };
    if (path.includes("/context?")) return {
      data: {
        assignment: {
          class_name: "测试班",
          group_name: "测试组",
          current_cycle: { learning_cycle_index: 5 },
          meeting_plan: meetingPlan
        },
        evidence_enabled: true
      }
    };
    return { data: { id: 42, status: "DRAFT", evidence: { id: 1 } } };
  };
  vm.runInNewContext(readFileSync(new URL("../apps/wechat-miniprogram/pages/study-meeting/submit.js", import.meta.url), "utf8"), {
    getApp: () => app, Page: definition => { page = definition; },
    require: modulePath => modulePath.includes("study-meeting")
      ? studyMeetingUtils
      : { request, uploadPhoto: async path => calls.push({ upload: path }) },
    wx, Date, Promise
  });
  page.setData = value => Object.assign(page.data, value);
  return { page, app, calls, failNextSubmit: () => { failSubmit = true; } };
}

test("meeting content is confirmed locally and submit payload has no legacy course fields", async () => {
  const { page, calls } = harness();
  await page.loadContext();
  assert.equal(page.data.learningContentResults.length, 1);
  assert.equal(page.data.allRequiredContentCompleted, false);
  page.toggleLearningContent({ currentTarget: { dataset: { contentKey: "content-1" } } });
  assert.equal(page.data.allRequiredContentCompleted, true);
  page.data.photoPath = "/tmp/small.jpg";
  page.data.evidenceEnabled = true;
  await page.submit();
  const create = calls.find(item => item.path === "/api/v1/study-meetings");
  assert.ok(create);
  assert.equal(Object.keys(create.options.data).some(key => key.includes("course")), false);
  assert.equal(Object.keys(create.options.data).includes("has_course"), false);
});

test("one photo is compressed; retry reuses server draft and does not reupload", async () => {
  const { page, calls, app, failNextSubmit } = harness();
  page.data.evidenceEnabled = true;
  await page.loadContext();
  page.toggleLearningContent({ currentTarget: { dataset: { contentKey: "content-1" } } });
  await page.choosePhoto();
  assert.equal(page.data.photoPath, "/tmp/small.jpg");
  assert.equal(calls.filter(item => item.compressed).length, 1);
  failNextSubmit();
  await page.submit();
  assert.equal(app.globalData.studyMeetingDraft.sessionId, 42);
  await page.submit();
  assert.equal(calls.filter(item => item.path === "/api/v1/study-meetings").length, 1);
  assert.equal(calls.filter(item => item.upload).length, 1);
  assert.equal(app.globalData.studyMeetingResult.status, "SUBMITTED");
  assert.equal(app.globalData.studyMeetingDraft, null);
});

test("concurrent taps submit once and points are never sent by the leader", async () => {
  const { page, calls } = harness();
  page.data.evidenceEnabled = true;
  page.data.photoPath = "/tmp/small.jpg";
  await page.loadContext();
  page.toggleLearningContent({ currentTarget: { dataset: { contentKey: "content-1" } } });
  await Promise.all([page.submit(), page.submit()]);
  const creates = calls.filter(item => item.path === "/api/v1/study-meetings");
  assert.equal(creates.length, 1);
  assert.equal(Object.keys(creates[0].options.data).some(key => key.includes("course")), false);
  assert.equal(Object.keys(creates[0].options.data).some(key => key.includes("credit")), false);
});
