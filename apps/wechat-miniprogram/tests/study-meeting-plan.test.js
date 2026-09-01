const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  applyLearningContentResults,
  normalizeMeetingPlan,
  requiredLearningContentComplete,
  serializeLearningContentResults
} = require("../utils/study-meeting");

const root = path.resolve(__dirname, "..");

function samplePlan(cycleIndex = 5) {
  return {
    title: `第${cycleIndex}次小组学习会`,
    learning_cycle_index: cycleIndex,
    steps: [
      { step_no: 1, content: "观看指定视频并研讨", is_terminal: false, learning_content_keys: ["video-1"] },
      { step_no: 2, content: "空巴。", is_terminal: true, learning_content_keys: [] },
      { step_no: 3, content: "空巴后的错误节点", is_terminal: false, learning_content_keys: ["video-after"] }
    ],
    learning_contents: [
      {
        content_key: "video-1",
        title: cycleIndex === 5 ? "关于核算表分析&任务单的制作" : "本期无二维码必学视频",
        description: "全组观看并研讨",
        required: true,
        sort_order: 1,
        credit_points: 40,
        content_access: { type: "NONE", label: null }
      },
      {
        content_key: "video-after",
        title: "空巴后的内容不应显示",
        required: true,
        sort_order: 2,
        credit_points: 20,
        content_access: { type: "QR", label: "扫码打开学习内容" }
      }
    ]
  };
}

function loadIndexPage(response) {
  const requestPath = require.resolve("../utils/request");
  const pagePath = path.resolve(root, "pages/study-meeting/index.js");
  const previousRequest = require.cache[requestPath];
  const previousPage = global.Page;
  const previousGetApp = global.getApp;
  const previousWx = global.wx;
  const calls = [];
  const app = { globalData: { studyMeetingDraft: null } };
  let definition;
  require.cache[requestPath] = {
    id: requestPath,
    filename: requestPath,
    loaded: true,
    exports: { request: async () => response }
  };
  global.getApp = () => app;
  global.Page = value => { definition = value; };
  global.wx = {
    showToast: payload => calls.push({ toast: payload.title }),
    navigateTo: payload => calls.push({ navigation: payload.url })
  };
  delete require.cache[pagePath];
  require(pagePath);
  const page = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(next) { this.data = { ...this.data, ...next }; }
  };
  for (const [name, value] of Object.entries(definition)) {
    if (typeof value === "function") page[name] = value.bind(page);
  }
  return {
    page,
    app,
    calls,
    cleanup() {
      delete require.cache[pagePath];
      if (previousRequest) require.cache[requestPath] = previousRequest;
      else delete require.cache[requestPath];
      global.Page = previousPage;
      global.getApp = previousGetApp;
      global.wx = previousWx;
    }
  };
}

test("meeting plan stops at the first 空巴 and keeps only reachable content", () => {
  const plan = normalizeMeetingPlan(samplePlan());
  assert.ok(plan);
  assert.deepEqual(plan.steps.map(item => item.content), ["观看指定视频并研讨", "空巴。"]);
  assert.deepEqual(plan.learningContents.map(item => item.contentKey), ["video-1"]);
  assert.equal(plan.learningContents[0].creditLabel, "40学分课程");
  assert.equal(plan.learningContents[0].hasCredit, true);
});

test("QR resource is only a weak access hint and an uncredited video is not shown as zero credit", () => {
  const plan = normalizeMeetingPlan({
    learning_cycle_index: 32,
    steps: [
      { step_no: 1, content: "观看成功方程式49天讲解并研讨", is_terminal: false, learning_content_keys: ["success-formula"] },
      { step_no: 2, content: "扫码查看补充资料", is_terminal: false, learning_content_keys: ["qr-content"] },
      { step_no: 3, content: "空巴。", is_terminal: true, learning_content_keys: [] }
    ],
    learning_contents: [
      {
        content_key: "success-formula",
        title: "成功方程式49天讲解",
        required: true,
        credit_points: null,
        content_access: { type: "NONE", label: null }
      },
      {
        content_key: "qr-content",
        title: "补充学习内容",
        required: false,
        credit_points: null,
        content_access: { type: "QR", label: "扫码打开学习内容" }
      }
    ]
  });
  assert.ok(plan);
  const successFormula = plan.learningContents.find(item => item.contentKey === "success-formula");
  const qrContent = plan.learningContents.find(item => item.contentKey === "qr-content");
  assert.equal(successFormula.creditLabel, "本内容不单独计课程学分");
  assert.equal(successFormula.hasCredit, false);
  assert.equal(qrContent.hasResourceAccess, true);
  assert.equal(qrContent.resourceLabel, "扫码打开学习内容");
});

test("a no-video meeting plan remains valid and can continue without a content gate", () => {
  const plan = normalizeMeetingPlan({
    learning_cycle_index: 28,
    steps: [
      { step_no: 1, content: "经营分析会实操观摩；", is_terminal: false, learning_content_keys: [] },
      { step_no: 2, content: "近期重点工作沟通交流；", is_terminal: false, learning_content_keys: [] },
      { step_no: 3, content: "空巴。", is_terminal: true, learning_content_keys: [] }
    ],
    learning_contents: []
  });
  assert.ok(plan);
  assert.equal(plan.learningContents.length, 0);
  assert.equal(requiredLearningContentComplete(plan.learningContents), true);
});

test("learning content completion is a local UI state and keeps no course key", () => {
  const plan = normalizeMeetingPlan(samplePlan());
  const results = applyLearningContentResults(plan.learningContents, []);
  assert.equal(requiredLearningContentComplete(results), false);
  results[0].completed = true;
  assert.equal(requiredLearningContentComplete(results), true);
  assert.deepEqual(serializeLearningContentResults(results), [
    { content_key: "video-1", completed: true }
  ]);
});

test("index renders current cycle and meeting plan, then preserves completion state", async () => {
  const harness = loadIndexPage({
    data: {
      selection_required: false,
      selected_group_org_unit_id: "group-1",
      assignment: {
        class_name: "测试班",
        group_name: "第一小组",
        current_cycle: { learning_cycle_index: 8 },
        meeting_plan: samplePlan(8)
      },
      assignments: []
    }
  });
  try {
    await harness.page.loadContext();
    assert.equal(harness.page.data.meetingPlanReady, true);
    assert.equal(harness.page.data.assignment.current_cycle.learning_cycle_index, 8);
    assert.deepEqual(harness.page.data.meetingSteps.map(item => item.content), ["观看指定视频并研讨", "空巴。"]);
    harness.page.toggleLearningContent({ currentTarget: { dataset: { contentKey: "video-1" } } });
    harness.page.openMembers();
    assert.deepEqual(harness.app.globalData.studyMeetingDraft.learning_content_results, [
      { content_key: "video-1", completed: true }
    ]);
    assert.equal(harness.calls[0].navigation, "/pages/study-meeting/members?groupId=group-1");
  } finally {
    harness.cleanup();
  }
});

test("index fails closed when current cycle has no meeting plan", async () => {
  const harness = loadIndexPage({
    data: {
      selection_required: false,
      selected_group_org_unit_id: "group-1",
      assignment: {
        class_name: "测试班",
        group_name: "第一小组",
        current_cycle: { learning_cycle_index: 30 },
        meeting_plan: null,
        meeting_plan_error: "当前学习周期内容配置尚未完成，请联系运营人员检查学习计划"
      },
      assignments: []
    }
  });
  try {
    await harness.page.loadContext();
    assert.equal(harness.page.data.meetingPlanReady, false);
    assert.match(harness.page.data.meetingPlanError, /配置尚未完成/);
    harness.page.openMembers();
    assert.equal(harness.calls.some(item => item.navigation), false);
  } finally {
    harness.cleanup();
  }
});

test("index fails closed when the plan is marked as a mapping conflict", async () => {
  const harness = loadIndexPage({
    data: {
      selection_required: false,
      selected_group_org_unit_id: "group-1",
      assignment: {
        class_name: "测试班",
        group_name: "第一小组",
        current_cycle: { learning_cycle_index: 10 },
        meeting_plan: null,
        meeting_plan_error: "当前学习周期小组学习会配置存在冲突，请联系运营人员检查学习计划"
      },
      assignments: []
    }
  });
  try {
    await harness.page.loadContext();
    assert.equal(harness.page.data.meetingPlanReady, false);
    assert.match(harness.page.data.meetingPlanError, /配置存在冲突/);
    assert.equal(harness.page.data.learningContents.length, 0);
  } finally {
    harness.cleanup();
  }
});

test("normal submit page no longer contains the legacy course picker", () => {
  const source = fs.readFileSync(path.join(root, "pages/study-meeting/submit.js"), "utf8");
  const template = fs.readFileSync(path.join(root, "pages/study-meeting/submit.wxml"), "utf8");
  for (const legacy of ["hasCourse", "visibleCourses", "selectedCourseKeys", "courseSearch", "handleCourseSwitch", "toggleCourse", "searchCourses"]) {
    assert.doesNotMatch(source, new RegExp(legacy));
    assert.doesNotMatch(template, new RegExp(legacy));
  }
  assert.doesNotMatch(template, /今天看了在线课程吗/);
  assert.match(template, /本期小组学习会/);
  assert.match(template, /learningContentResults/);
  assert.match(template, /catchtap="openLearningContent"/);
});
