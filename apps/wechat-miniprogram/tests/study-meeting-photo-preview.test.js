const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  const app = { globalData: { personSessionToken: "synthetic-a", studyMeetingDraft: {
    group_org_unit_id: "synthetic-group", member_ids: [1], cross_group_member_ids: []
  } } };
  const state = { picker: null, compress: null, size: 2048, uploadCount: 0, createCount: 0,
    submitCount: 0, redirects: [], previews: [], toasts: [], session: null };
  const context = { data: { evidence_enabled: true, assignment: {
    current_cycle: { learning_cycle_index: 4 }, meeting_plan: { learning_cycle_index: 4,
      steps: [{ step_no: 1, content: "空巴。", is_terminal: true }], learning_contents: [] }
  } } };
  const request = async (url, options = {}) => {
    if (url.includes("/context?")) return context;
    if (url === "/api/v1/study-meetings") {
      state.createCount++;
      state.session = { id: 42, status: "DRAFT", evidence: null };
      return { data: state.session };
    }
    if (url.endsWith("/submit")) {
      state.submitCount++;
      if (state.submit) return state.submit.promise;
      state.session.status = "SUBMITTED";
    }
    assert.ok(options.auth);
    return { data: state.session };
  };
  const uploadPhoto = async () => {
    state.uploadCount++;
    if (state.upload) await state.upload.promise;
    state.session.evidence = { id: 7 };
    return { data: state.session.evidence };
  };
  const guard = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, "utils/page-session.js"), "utf8"), {
    module: guard, getApp: () => app
  });
  let page;
  vm.runInNewContext(fs.readFileSync(path.join(root, "pages/study-meeting/submit.js"), "utf8"), {
    Page: value => { page = value; }, getApp: () => app,
    require: name => name.endsWith("page-session") ? guard.exports : name.endsWith("study-meeting")
      ? require("../utils/study-meeting") : { request, uploadPhoto },
    wx: {
      chooseMedia: options => { state.picker = options; },
      compressImage: options => { if (state.deferCompression) state.compress = options;
        else if (state.compressionError) options.fail({ errMsg: "compress:fail" });
        else options.success({ tempFilePath: options.src + ".compressed.jpg" }); },
      getFileSystemManager: () => ({ getFileInfo: options => options.success({ size: state.size }) }),
      previewImage: options => state.previews.push(options),
      showToast: options => state.toasts.push(options),
      redirectTo: options => state.redirects.push(options)
    }
  });
  page.setData = patch => Object.assign(page.data, patch);
  page._shown = true;
  page._privateVisible = true;
  const pick = name => state.picker.success({ tempFiles: [{ tempFilePath: name || "synthetic-photo" }] });
  return { app, page, state, pick };
}

for (const returnOrder of ["before picker callback", "after picker callback", "during compression"]) {
  test(`camera/album preview survives onHide with onShow ${returnOrder}`, async () => {
    const { page, state, app, pick } = harness();
    await page.loadContext();
    const pending = page.choosePhoto();
    page.onHide();
    assert.equal(page.data.photoPath, "");
    assert.equal(page.data.assignment, null);
    if (returnOrder === "before picker callback") { page.onShow(); await flush(); }
    if (returnOrder === "during compression") state.deferCompression = true;
    pick();
    if (returnOrder === "during compression") {
      await flush();
      page.onShow(); await flush();
      assert.equal(page.data.choosingPhoto, true);
      state.compress.success({ tempFilePath: "synthetic-photo.compressed.jpg" });
    }
    await pending;
    if (returnOrder === "after picker callback") {
      assert.equal(page.data.photoPath, "", "private UI remains cleared until return");
      page.onShow(); await flush();
    }
    assert.equal(page.data.photoPath, "synthetic-photo.compressed.jpg");
    assert.equal(app.globalData.studyMeetingDraft.photoPath, page.data.photoPath);
    assert.equal(page.data.choosingPhoto, false);
    assert.equal(page.data.photoUploadState, "selected");
    assert.equal(state.uploadCount, 0, "selection must not claim or perform an upload");
  });
}

test("large-image preview and ordinary background return restore the same photo after context authorization", async () => {
  const { page, state, pick } = harness();
  await page.loadContext();
  const pending = page.choosePhoto(); pick(); await pending;
  page.photoLoaded({ currentTarget: { dataset: { path: page.data.photoPath } } });
  assert.equal(page.data.photoLoaded, true);
  page.previewPhoto();
  assert.equal(state.previews[0].current, page.data.photoPath);
  page.onHide();
  assert.equal(page.data.photoPath, "");
  page.onShow(); await flush();
  assert.equal(page.data.photoPath, state.previews[0].current);
  assert.equal(page.data.meetingPlanReady, true);
});

for (const boundary of ["picker", "compression"]) {
  for (const interruption of ["account switch", "unload", "new draft"]) {
    test(`${interruption} during ${boundary} discards late photo without restoring private UI`, async () => {
      const { page, app, state, pick } = harness();
      await page.loadContext();
      state.deferCompression = boundary === "compression";
      const oldDraft = app.globalData.studyMeetingDraft;
      const pending = page.choosePhoto();
      page.onHide();
      if (boundary === "compression") { pick(); await flush(); }
      if (interruption === "account switch") {
        app.globalData.personSessionToken = "synthetic-b";
        app.globalData.studyMeetingDraft = null;
      } else if (interruption === "unload") page.onUnload();
      else app.globalData.studyMeetingDraft = { group_org_unit_id: "new-group" };
      if (boundary === "compression") state.compress.success({ tempFilePath: "stale.jpg" });
      else pick();
      await pending;
      assert.equal(page.data.photoPath, "");
      assert.equal(oldDraft.photoPath, undefined);
      assert.equal(state.uploadCount, 0);
    });
  }
}

for (const failure of ["cancel", "oversize", "empty", "compression"]) {
  test(`${failure} while replacing a photo preserves the previous chosen image`, async () => {
    const { page, state, app, pick } = harness();
    await page.loadContext();
    let pending = page.choosePhoto(); pick("first"); await pending;
    const original = page.data.photoPath;
    pending = page.choosePhoto();
    page.onHide();
    if (failure === "cancel") state.picker.fail({ errMsg: "chooseMedia:fail cancel" });
    else {
      state.size = failure === "oversize" ? 6 * 1024 * 1024 : failure === "empty" ? 0 : 2048;
      state.compressionError = failure === "compression";
      pick("second");
    }
    await pending;
    page.onShow(); await flush();
    assert.equal(page.data.photoPath, original);
    assert.equal(app.globalData.studyMeetingDraft.photoPath, original);
    assert.equal(page.data.choosingPhoto, false);
    assert.equal(Boolean(page.data.photoFeedback), failure !== "cancel");
  });
}

test("thumbnail failure is visible and prevents submission until a new image is selected", async () => {
  const { page, state, pick } = harness();
  await page.loadContext();
  let pending = page.choosePhoto(); pick(); await pending;
  page.photoLoadFailed({ currentTarget: { dataset: { path: page.data.photoPath } } });
  await page.submit();
  assert.equal(page.data.photoPreviewError, true);
  assert.equal(state.createCount, 0);
  pending = page.choosePhoto(); pick("replacement"); await pending;
  page.photoLoadFailed({ currentTarget: { dataset: { path: "synthetic-photo.compressed.jpg" } } });
  assert.equal(page.data.photoPreviewError, false, "stale image event must not poison replacement");
  page.photoLoaded({ currentTarget: { dataset: { path: page.data.photoPath } } });
  assert.equal(page.data.photoLoaded, true);
});

test("upload status waits for server acknowledgment; failed upload retains photo and retries one draft", async () => {
  const { page, state, app, pick } = harness();
  await page.loadContext();
  let pending = page.choosePhoto(); pick(); await pending;
  state.upload = deferred();
  pending = page.submit(); await flush();
  assert.equal(page.data.photoUploadState, "uploading");
  assert.equal(app.globalData.studyMeetingDraft.uploadedPhotoPath, undefined);
  assert.equal(state.submitCount, 0);
  state.upload.reject(new Error("synthetic network failure")); await pending;
  assert.equal(page.data.photoUploadState, "failed");
  assert.ok(page.data.photoFeedback.includes("已保留"));
  assert.ok(page.data.submitError.includes("尚未提交"));
  assert.ok(page.data.photoPath);
  assert.equal(page.data.submitting, false);
  state.upload = deferred(); state.submit = deferred();
  pending = page.submit(); await flush();
  state.upload.resolve(); await flush();
  assert.equal(page.data.photoUploadState, "uploaded");
  assert.equal(page.data.submitting, true);
  assert.equal(app.globalData.studyMeetingDraft.uploadedPhotoPath, page.data.photoPath);
  assert.equal(state.redirects.length, 0);
  state.submit.resolve({ data: { ...state.session, status: "SUBMITTED" } }); await pending;
  assert.equal(state.createCount, 1);
  assert.equal(state.uploadCount, 2);
  assert.equal(state.redirects.length, 1);
  assert.equal(app.globalData.studyMeetingDraft, null);
});

for (const replace of [false, true]) {
  test(`submission failure preserves upload acknowledgment; retry ${replace ? "with replacement uploads new photo" : "reuses uploaded photo"}`, async () => {
    const { page, state, pick } = harness();
    await page.loadContext();
    let pending = page.choosePhoto(); pick(); await pending;
    state.submit = deferred();
    pending = page.submit(); await flush();
    state.submit.reject(new Error("synthetic submit failure")); await pending;
    assert.equal(page.data.photoUploadState, "uploaded");
    assert.ok(page.data.submitError.includes("合影已上传"));
    assert.equal(state.redirects.length, 0);
    if (replace) {
      pending = page.choosePhoto(); pick("replacement"); await pending;
      assert.equal(page.data.photoUploadState, "selected");
    }
    state.submit = null;
    await page.submit();
    assert.equal(state.createCount, 1);
    assert.equal(state.uploadCount, replace ? 2 : 1);
    assert.equal(state.redirects.length, 1);
  });
}

test("hiding during upload never reports success or continues submission from a stale request", async () => {
  const { page, state, app, pick } = harness();
  await page.loadContext();
  let pending = page.choosePhoto(); pick(); await pending;
  state.upload = deferred();
  pending = page.submit(); await flush();
  page.onHide();
  state.upload.resolve(); await pending;
  assert.equal(page.data.photoPath, "");
  assert.equal(page.data.photoUploadState, "");
  assert.equal(state.submitCount, 0);
  assert.equal(app.globalData.studyMeetingDraft.uploadedPhotoPath, undefined);
  page.onShow(); await flush();
  assert.ok(page.data.photoPath);
  assert.equal(page.data.photoUploadState, "selected");
});

test("double picker taps open one native picker", async () => {
  const { page, state, pick } = harness();
  await page.loadContext();
  const first = page.choosePhoto();
  const picker = state.picker;
  await page.choosePhoto();
  assert.equal(state.picker, picker);
  pick(); await first;
  assert.equal(page.data.choosingPhoto, false);
});
