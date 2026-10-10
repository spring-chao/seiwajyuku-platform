const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function harness(respond) {
  const calls = [], initializations = [];
  const app = { globalData: { apiTransport: "cloudrun", cloudbaseEnvironment: "synthetic-env",
    cloudrunService: "synthetic-service", personSessionToken: "synthetic-person" } };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/request.js"), "utf8"), {
    module, getApp: () => app, wx: {
      request() { throw new Error("Unexpected public network fallback"); },
      cloud: { init(options) { initializations.push(options); },
        callContainer(options) { calls.push(options); return respond(options); } }
    }
  });
  return { calls, initializations, app, request: module.exports.request };
}

test("cloud transport preserves member auth, query and body and pins the configured service", async () => {
  const h = harness(async () => ({ statusCode: 200, data: { ok: true } }));
  await h.request("/api/v1/wechat/checkin/context?event_id=synthetic", { auth: true });
  await h.request("/api/v1/wechat/checkin/confirm", { method: "POST", auth: true,
    data: { event_id: "synthetic" }, header: { "X-WX-SERVICE": "other-service" } });
  assert.equal(h.initializations.length, 1);
  assert.equal(h.calls[0].config.env, "synthetic-env");
  assert.equal(h.calls[0].path, "/api/v1/wechat/checkin/context?event_id=synthetic");
  assert.equal(h.calls[1].method, "POST");
  assert.equal(h.calls[1].data.event_id, "synthetic");
  assert.equal(h.calls[1].header.Authorization, "Bearer synthetic-person");
  assert.equal(h.calls[1].header["X-WX-SERVICE"], "synthetic-service");
});

test("cloud transport retains permission failures and never falls back after a network error", async () => {
  const forbidden = harness(async () => ({ statusCode: 403, data: { detail: "无权签到" } }));
  await assert.rejects(forbidden.request("/checkin"), e => e.statusCode === 403 && e.message === "无权签到");
  const offline = harness(async () => { throw { errMsg: "synthetic private link unavailable" }; });
  await assert.rejects(offline.request("/checkin"), /synthetic private link unavailable/);
  assert.equal(offline.calls.length, 1);
  offline.app.globalData.cloudrunService = "";
  await assert.rejects(offline.request("/checkin"), /云服务连接尚未就绪/);
  assert.equal(offline.calls.length, 1);
});
