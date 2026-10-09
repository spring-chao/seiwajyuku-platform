const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

test("signed ticket reaches the dedicated engine without the platform session or container", async () => {
  const calls = [], module = { exports: {} };
  const transport = { apiTransport: "cloudrun", cloudbaseEnvironment: "synthetic-env",
    signinEngineFunction: "checkinStg2026100970716ba4",
    signinEngineApiBase: "https://synthetic-gateway.net/stg_signin_20261009_70716ba4/api",
    personSessionToken: "synthetic-platform-session" };
  let status = 200;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../utils/checkin.js"), "utf8"), {
    module, wx: { request() { throw new Error("Unexpected public request fallback"); }, cloud: {
      callContainer() { throw new Error("Platform must remain independent"); },
      async callFunction(options) { calls.push(options); return { result: { statusCode: status,
        body: JSON.stringify(status === 200 ? { ok: true, checked_at: "2026-10-09T12:00:00Z", sync_status: "PENDING" } : { ok: false, msg: "票据已过期" }) } }; }
    } }
  });
  const url = transport.signinEngineApiBase + "/native/v1/checkin/confirm";
  assert.equal(module.exports.engineConfirmUrl(url, "", transport), url);
  assert.equal(module.exports.engineConfirmUrl(url.replace("70716ba4", "70716ba5"), "", transport), "");
  const receipt = await module.exports.directConfirm(url, "synthetic.signed.ticket", transport);
  assert.equal(receipt.sync_status, "PENDING");
  assert.equal(calls[0].name, transport.signinEngineFunction);
  assert.equal(calls[0].data.path, "/native/v1/checkin/confirm");
  assert.equal(JSON.parse(calls[0].data.body).ticket, "synthetic.signed.ticket");
  assert.equal(calls[0].data.headers.Authorization, undefined);
  status = 403;
  await assert.rejects(module.exports.directConfirm(url, "expired.ticket", transport), e => e.engineTicketError === true && e.statusCode === 403);
  await assert.rejects(module.exports.directConfirm(url + "?redirect=other", "ticket", transport));
  assert.equal(calls.length, 2);
});
