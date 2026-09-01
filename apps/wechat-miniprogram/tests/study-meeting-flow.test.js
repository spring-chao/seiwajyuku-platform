const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const requestPath = require.resolve("../utils/request");
const studyMeetingUtils = require("../utils/study-meeting");
const pagePath = path.resolve(__dirname, "../pages/study-meeting/submit.js");

const meetingPlan = {
  learning_cycle_index: 1,
  cycle_index: 1,
  steps: [
    { step_no: 1, content: "本期流程", is_terminal: false, learning_content_keys: ["content-1"] },
    { step_no: 2, content: "空巴。", is_terminal: true, learning_content_keys: [] }
  ],
  learning_contents: [{
    content_key: "content-1",
    title: "本期必学内容",
    description: "全组学习",
    required: true,
    sort_order: 1,
    credit_points: null,
    content_access: { type: "NONE", label: null }
  }]
};

function instantiatePage(definition) {
  const page = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(next) {
      this.data = { ...this.data, ...next };
    }
  };
  for (const [name, value] of Object.entries(definition)) {
    if (typeof value === "function") page[name] = value.bind(page);
  }
  return page;
}

test("study meeting submits one complete fact without legacy course selection", async () => {
  const calls = [];
  const app = {
    globalData: {
      studyMeetingDraft: {
        group_org_unit_id: "group-1",
        member_ids: [11, 12],
        cross_group_member_ids: [21]
      }
    }
  };
  const request = async (url, options = {}) => {
    calls.push({ kind: "request", url, method: options.method || "GET", options });
    if (url.includes("/context?")) {
      return {
        data: {
          assignment: {
            class_name: "测试班",
            group_name: "测试组",
            current_cycle: { learning_cycle_index: 1 },
            meeting_plan: meetingPlan
          },
          evidence_enabled: true
        }
      };
    }
    if (url === "/api/v1/study-meetings") return { data: { id: 99 } };
    if (url === "/api/v1/study-meetings/99/submit") {
      return {
        data: {
          id: 99,
          status: "SUBMITTED",
          attendees: [
            { member_id: 11, attendance_type: "HOME_GROUP" },
            { member_id: 12, attendance_type: "HOME_GROUP" },
            { member_id: 21, attendance_type: "CROSS_GROUP" }
          ],
          courses: []
        }
      };
    }
    throw new Error(`unexpected request: ${url}`);
  };
  const uploadPhoto = async (url, filePath) => {
    calls.push({ kind: "upload", url, filePath });
    return { data: { uploaded: true } };
  };

  const previousRequestModule = require.cache[requestPath];
  const previousPage = global.Page;
  const previousGetApp = global.getApp;
  const previousWx = global.wx;
  let definition;
  try {
    require.cache[requestPath] = {
      id: requestPath,
      filename: requestPath,
      loaded: true,
      exports: { request, uploadPhoto }
    };
    global.getApp = () => app;
    global.Page = value => {
      definition = value;
    };
    global.wx = {
      showToast() {},
      redirectTo({ url }) {
        calls.push({ kind: "redirect", url });
      }
    };
    delete require.cache[pagePath];
    require(pagePath);

    const page = instantiatePage(definition);
    await page.loadContext();
    page.setData({
      learningContentResults: [
        { contentKey: "content-1", uiKey: "content-1-0", completed: true, required: true }
      ],
      allRequiredContentCompleted: true,
      photoPath: "temporary-photo.jpg"
    });
    await page.submit();

    assert.deepEqual(
      calls.map(item => `${item.kind}:${item.method || ""}:${item.url}`),
      [
        "request:GET:/api/v1/study-meetings/context?group_org_unit_id=group-1",
        "request:POST:/api/v1/study-meetings",
        "upload::/api/v1/study-meetings/99/evidence",
        "request:POST:/api/v1/study-meetings/99/submit",
        "redirect::/pages/study-meeting/result"
      ]
    );
    const create = calls.find(item => item.kind === "request" && item.url === "/api/v1/study-meetings");
    assert.equal(Object.keys(create.options.data).some(key => key.includes("course")), false);
    assert.equal(app.globalData.studyMeetingDraft, null);
    assert.equal(app.globalData.studyMeetingResult.status, "SUBMITTED");
  } finally {
    delete require.cache[pagePath];
    if (previousRequestModule) require.cache[requestPath] = previousRequestModule;
    else delete require.cache[requestPath];
    global.Page = previousPage;
    global.getApp = previousGetApp;
    global.wx = previousWx;
  }
});
