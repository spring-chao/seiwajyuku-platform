const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const { enrollmentRulesDocument } = require("../utils/enrollment-rules");
const root = path.resolve(__dirname, "..");

function harness(wx = {}) {
  let definition;
  vm.runInNewContext(fs.readFileSync(path.join(root, "pages/enrollment/index.js"), "utf8"), {
    getApp: () => ({ globalData: {} }), Page: value => { definition = value; },
    require: () => ({ enrollmentRulesDocument }), wx
  });
  return { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    setData(patch) { for (const [key, value] of Object.entries(patch)) {
      const parts = key.split(".");
      if (parts.length === 1) this.data[key] = value;
      else this.data[parts[0]][parts[1]] = value;
    } }
  };
}

function metadata(scope, locked = true, ready = true) {
  return { target_shuku_locked: locked, target_shuku_org_unit_id: scope,
    target_shuku_name: scope === "org-suzhou" ? "苏州塾" : "无锡塾",
    brand_region_name: scope === "org-suzhou" ? "苏州" : "无锡",
    business_config_status: ready ? "READY" : "BUSINESS_CONFIG_REQUIRED",
    joining_rules: ["合成测试规则"],
    contacts: [{ name: scope, phone: "SYNTHETIC-" + scope }],
    service_address: "SYNTHETIC-ADDRESS-" + scope
  };
}

for (const scene of ["synthetic-legacy-scene", "e2_ABCDEFGHIJKLMNOPQRSTUV"]) {
  test(`scanning a dedicated code opens the locked form directly: ${scene}`, async () => {
    const page = harness();
    const calls = [];
    page.request = async requestPath => {
      calls.push(requestPath);
      return { data: metadata("org-suzhou") };
    };
    const load = page.loadForm.bind(page);
    let loaded;
    page.loadForm = () => { loaded = load(); return loaded; };
    page.onLoad({ scene: encodeURIComponent(scene) });
    await loaded;
    assert.deepEqual(calls, [`/api/v1/public/enrollment/${encodeURIComponent(scene)}`]);
    assert.equal(page.data.state, "ready");
    assert.equal(page.data.brandRegionName, "苏州");
    assert.equal(page.data.targetShukuLocked, true);
    assert.equal(page.data.form.target_shuku_org_unit_id, "org-suzhou");
  });
}

test("a dedicated scene opens the form immediately and cannot change target", async () => {
  const page = harness();
  page.data.token = "synthetic-entry";
  page.data.form.name = "尚未提交的测试输入";
  page.request = async () => ({ data: metadata("org-suzhou") });
  await page.loadForm();
  assert.equal(page.data.state, "ready");
  assert.equal(page.data.brandRegionName, "苏州");
  assert.equal(page.data.form.target_shuku_org_unit_id, "org-suzhou");
  page.changeTargetShuku();
  page.handleTargetShukuSelect({ currentTarget: { dataset: { id: "org-wuxi", name: "无锡塾" } } });
  assert.equal(page.data.state, "ready");
  assert.equal(page.data.form.target_shuku_org_unit_id, "org-suzhou");
  assert.equal(page.data.form.name, "尚未提交的测试输入");
});

test("generic entry retains selection and new target uses only its own contact", async () => {
  const copied = [];
  const page = harness({ setClipboardData: value => copied.push(value.data) });
  let data = { target_shuku_locked: false, target_shuku_org_unit_id: null };
  page.request = async () => ({ data });
  await page.loadForm();
  assert.equal(page.data.state, "selecting");
  data = metadata("org-suzhou", false);
  await page.loadForm("org-suzhou");
  page.toggleRules();
  page.changeTargetShuku();
  page.copyContactPhone({ currentTarget: { dataset: { index: 0 } } });
  assert.equal(copied.length, 0);
  assert.equal(page.data.rulesAcknowledged, false);
  data = metadata("org-wuxi", false);
  await page.loadForm("org-wuxi");
  page.copyContactPhone({ currentTarget: { dataset: { index: 0 } } });
  assert.deepEqual(copied, ["SYNTHETIC-org-wuxi"]);
  assert.equal(page.data.brandRegionName, "无锡");
  data = metadata("org-wuxi", false, false);
  await page.loadForm("org-wuxi");
  page.copyContactPhone({ currentTarget: { dataset: { index: 0 } } });
  assert.equal(copied.length, 1);
  assert.equal(page.data.joiningDocument.contacts.length, 0);
});

test("purpose and contact layout follows the confirmed bottom section order", () => {
  const wxml = fs.readFileSync(path.join(root, "pages/enrollment/index.wxml"), "utf8");
  const ordered = ["04", "05 · 重要说明", "这份资料会用于", "申请前，请先了解", "咨询与联系", "rules-detail-entry", "06 · 隐私确认", "submit-area"];
  let previous = -1;
  for (const text of ordered) { const position = wxml.indexOf(text); assert.ok(position > previous, text); previous = position; }
  assert.match(wxml, /wx:if="\{\{!targetShukuLocked\}\}" class="target-shuku-card"/);
  assert.match(wxml, /disabled="\{\{submitting \|\| !rulesAcknowledged \|\| !privacyConsent\}\}"/);
});
