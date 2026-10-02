const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function harness(bytes, respond) {
  const calls = [];
  const app = { globalData: { apiBaseUrl: "https://example.test/platform", memberSessionToken: "synthetic-session" } };
  const module = { exports: {} };
  let reader;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/request.js"), "utf8"), {
    module, getApp: () => app, Uint8Array,
    wx: {
      getFileSystemManager: () => ({ readFile(options) { reader = options; } }),
      request(options) { calls.push(options); if (respond) respond(options); },
      uploadFile() { throw new Error("Requires an upload domain"); }
    }
  });
  return { app, calls, upload: module.exports.uploadPhoto,
    read() { reader.success({ data: Uint8Array.from(bytes).buffer }); } };
}

test("multipart request preserves exact binary bytes and uses the existing authenticated endpoint", async () => {
  const bytes = [137, 80, 78, 71, 0, 255, 13, 10, 128];
  const h = harness(bytes, options => options.success({ statusCode: 200, data: { success: true } }));
  const pending = h.upload("/api/v1/study-meetings/7/evidence", "synthetic-photo.png");
  h.read();
  await pending;
  assert.equal(h.calls.length, 1);
  const request = h.calls[0];
  assert.equal(request.url, "https://example.test/platform/api/v1/study-meetings/7/evidence");
  assert.equal(request.header.Authorization, "Bearer synthetic-session");
  const body = Buffer.from(request.data);
  const split = body.indexOf(Buffer.from("\r\n\r\n")) + 4;
  assert.deepEqual([...body.subarray(split, split + bytes.length)], bytes);
  const boundary = request.header["content-type"].split("boundary=")[1];
  assert.equal(body.subarray(split + bytes.length).toString(), `\r\n--${boundary}--\r\n`);
  assert.match(body.subarray(0, split).toString(), /name="photo"; filename="photo.png"/);
});

test("account switch during file read never sends the old account photo", async () => {
  const h = harness([255, 216, 255]);
  const pending = h.upload("/evidence", "synthetic.jpg");
  h.app.globalData.memberSessionToken = "other-session";
  h.read();
  await assert.rejects(pending, /绑定已变更/);
  assert.equal(h.calls.length, 0);
});

test("upload preserves permission errors and rejects an empty photo", async () => {
  const h = harness([255, 216, 255], options => options.success({ statusCode: 403, data: { detail: "无权上传合影" } }));
  const pending = h.upload("/evidence", "synthetic.jpg"); h.read();
  await assert.rejects(pending, error => error.statusCode === 403 && error.message === "无权上传合影");
  const empty = harness([]);
  const invalid = empty.upload("/evidence", "empty.jpg"); empty.read();
  await assert.rejects(invalid, /不超过5MB/);
  assert.equal(empty.calls.length, 0);
});
