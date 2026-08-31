const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const requestPath = require.resolve("../utils/request");
const pagePath = path.resolve(__dirname, "../pages/study-meeting/submit.js");

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

test("study meeting submits one complete fact through the public mini-program flow", async () => {
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
    calls.push({ kind: "request", url, method: options.method || "GET" });
    if (url.includes("/context?")) {
      return {
        data: {
          assignment: {
            class_name: "测试班",
            group_name: "测试组",
            current_cycle: { learning_cycle_index: 1 }
          },
          courses: [
            {
              course_key: "course-1",
              course_name: "测试课程",
              status: "CONFIGURED",
              credit_points: 20
            }
          ],
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
          courses: [{ course_key: "course-1" }]
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
      hasCourse: true,
      selectedCourseKeys: ["course-1"],
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
