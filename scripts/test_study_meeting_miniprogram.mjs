import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

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
    return { data: { id: 42, status: "DRAFT", evidence: { id: 1 } } };
  };
  vm.runInNewContext(readFileSync(new URL("../apps/wechat-miniprogram/pages/study-meeting/submit.js", import.meta.url), "utf8"), {
    getApp: () => app, Page: definition => { page = definition; },
    require: () => ({ request, uploadPhoto: async path => calls.push({ upload: path }) }),
    wx, Date, Promise
  });
  page.setData = value => Object.assign(page.data, value);
  page.data.courses = ["a", "b", "c", "d"].map(course_key => ({ course_key, course_name: course_key, selected: false }));
  return { page, app, calls, failNextSubmit: () => { failSubmit = true; } };
}

test("multiple courses are selected, none clears the list, photo is mandatory", async () => {
  const { page, calls } = harness();
  page.handleCourseSwitch({ currentTarget: { dataset: { value: "true" } } });
  for (const key of ["a", "b", "c", "d"]) page.toggleCourse({ currentTarget: { dataset: { key } } });
  assert.equal(page.data.selectedCourseKeys.length, 4);
  await page.submit();
  assert.equal(calls.filter(item => item.path).length, 0);
  page.handleCourseSwitch({ currentTarget: { dataset: { value: "false" } } });
  assert.equal(page.data.selectedCourseKeys.length, 0);
});

test("one photo is compressed; retry reuses server draft and does not reupload", async () => {
  const { page, calls, app, failNextSubmit } = harness();
  page.data.evidenceEnabled = true;
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
  page.handleCourseSwitch({ currentTarget: { dataset: { value: "true" } } });
  page.toggleCourse({ currentTarget: { dataset: { key: "a" } } });
  await Promise.all([page.submit(), page.submit()]);
  const creates = calls.filter(item => item.path === "/api/v1/study-meetings");
  assert.equal(creates.length, 1);
  assert.deepEqual(Array.from(creates[0].options.data.course_keys), ["a"]);
  assert.equal(Object.keys(creates[0].options.data).some(key => key.includes("credit")), false);
});
