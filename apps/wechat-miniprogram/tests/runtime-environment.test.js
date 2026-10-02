const assert = require("node:assert/strict");
const test = require("node:test");

function runtime(wx) {
  const path = require.resolve("../config.runtime");
  delete require.cache[path];
  global.wx = wx;
  return require(path);
}

test("phone preview, trial and release all use the production gateway", () => {
  for (const envVersion of ["develop", "trial", "release"]) {
    for (const platform of ["ios", "android"]) {
      const config = runtime({
        getAccountInfoSync: () => ({ miniProgram: { envVersion } }),
        getDeviceInfo: () => ({ platform })
      });
      assert.equal(config.environment, "PRODUCTION");
      assert.match(config.apiBaseUrl, /^https:\/\//);
    }
  }
});

test("only a develop build in the local developer tools uses the test API", () => {
  const config = runtime({
    getAccountInfoSync: () => ({ miniProgram: { envVersion: "develop" } }),
    getSystemInfoSync: () => ({ platform: "devtools" })
  });
  assert.equal(config.environment, "TEST");
  assert.equal(config.apiBaseUrl, "http://127.0.0.1:8000");
  assert.equal(runtime({ getAccountInfoSync: () => { throw new Error("unavailable"); } }).environment, "PRODUCTION");
});
