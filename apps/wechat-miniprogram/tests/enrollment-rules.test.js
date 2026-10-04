const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const { enrollmentRulesDocument } = require("../utils/enrollment-rules");
const root = path.resolve(__dirname, "..");

function metadata(scope = "org-suzhou") {
  return { target_shuku_org_unit_id: scope, target_shuku_name: "苏州塾",
    target_shuku_options: [{ id: scope, name: "苏州塾", unit_code: "SYNTHETIC" }],
    business_config_status: "READY", fee_amount: "4800", fee_unit: "元/人/年",
    payment: { payee_name: "测试收款主体", bank_name: "测试银行", bank_account: "SYNTHETIC-ACCOUNT" },
    contacts: [{ name: "测试联系人", phone: "SYNTHETIC-CONTACT" }], service_address: "测试服务地址",
    joining_rules: ["通用加入规则"], joining_rules_document: {
      title: "苏州塾加入守则与缴费说明", scope_org_unit_id: "org-suzhou", introduction: "测试简介",
      summary: ["先阅读，再申请"], sections: [{ id: "study", title: "过渡学习", paragraphs: ["30天学习"], applicant: "must-not-cross" }],
      reading_note: "请阅读相关著作"
    }
  };
}

function pageHarness(name, wx = {}, channel = {}) {
  let definition;
  vm.runInNewContext(fs.readFileSync(path.join(root, "pages/enrollment", name + ".js"), "utf8"), {
    getApp: () => ({ globalData: {} }), Page: page => { definition = page; },
    require: () => ({ enrollmentRulesDocument }), wx
  });
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)),
    getOpenerEventChannel: () => channel,
    setData(patch) { for (const [key, value] of Object.entries(patch)) {
      const parts = key.split(".");
      if (parts.length === 1) this.data[key] = value;
      else this.data[parts[0]][parts[1]] = value;
    } }
  };
  return page;
}

test("public detail payload excludes form values, entry token and unrelated section fields", () => {
  const document = enrollmentRulesDocument({ ...metadata(), token: "synthetic-private-entry",
    form: { name: "synthetic-applicant", phone: "synthetic-private-phone" } });
  const text = JSON.stringify(document);
  for (const secret of ["synthetic-private-entry", "synthetic-applicant", "synthetic-private-phone", "must-not-cross"]) {
    assert.ok(!text.includes(secret));
  }
  assert.equal(document.payment.bankAccount, "SYNTHETIC-ACCOUNT");
  assert.equal(document.feeAmount, "4800");
});

test("a differently scoped document cannot leak Suzhou policy into another shuku", () => {
  const document = enrollmentRulesDocument(metadata("org-wuxi"));
  assert.equal(document.title, "加入守则与缴费说明");
  assert.equal(document.sections[0].paragraphs[0], "通用加入规则");
  assert.ok(!JSON.stringify(document).includes("30天学习"));
});

test("missing business configuration hides cached payment and contacts without inventing replacements", () => {
  const document = enrollmentRulesDocument({ ...metadata(), business_config_status: "BUSINESS_CONFIG_REQUIRED" });
  assert.equal(document.businessReady, false);
  assert.equal(document.payment.bankAccount, "");
  assert.equal(document.contacts.length, 0);
  assert.equal(document.feeAmount, "");
});

test("opening and returning from rules preserves applicant inputs and leaves consent unchecked", async () => {
  const events = [];
  const channel = { on: (_name, callback) => events.push(callback),
    emit: (_name, data) => events.forEach(callback => callback(data)) };
  let detail, navigation, back = 0;
  const form = pageHarness("index", { navigateTo: options => {
    navigation = options.url;
    detail = pageHarness("rules", { navigateBack: () => { back++; } }, channel);
    detail.onLoad();
    options.success({ eventChannel: channel });
  } });
  form.request = async () => ({ data: metadata() });
  form.data.form.name = "本地尚未提交的姓名";
  form.data.form.notes = "本地备注";
  form.data.token = "synthetic-entry-token";
  await form.loadForm();
  const before = JSON.stringify(form.data.form);
  form.openJoiningRules();
  assert.equal(navigation, "/pages/enrollment/rules");
  assert.equal(detail.data.document.title, "苏州塾加入守则与缴费说明");
  assert.equal(form.data.rulesAcknowledged, false);
  detail.returnToForm();
  assert.equal(back, 1);
  assert.equal(JSON.stringify(form.data.form), before);
  assert.equal(form.data.rulesAcknowledged, false);
  assert.equal(form.data.privacyConsent, false);
  form.toggleRules();
  assert.equal(form.data.rulesAcknowledged, true);
});

test("switching target resets acknowledgment and discards the previous detailed policy", async () => {
  const page = pageHarness("index");
  let data = metadata();
  page.request = async () => ({ data });
  await page.loadForm();
  page.toggleRules();
  page.changeTargetShuku();
  assert.equal(page.data.rulesAcknowledged, false);
  data = metadata("org-wuxi");
  delete data.joining_rules_document;
  data.payment.bank_account = "SYNTHETIC-WUXI";
  await page.loadForm("org-wuxi");
  assert.equal(page.data.formMeta.joining_rules_document, null);
  assert.equal(page.data.joiningDocument.title, "加入守则与缴费说明");
  assert.equal(page.data.joiningDocument.payment.bankAccount, "SYNTHETIC-WUXI");
});

test("detail page handles missing opener, section navigation and late messages after exit", () => {
  const direct = pageHarness("rules");
  direct.onLoad();
  assert.equal(direct.data.document, null);
  let receive;
  const page = pageHarness("rules", {}, { on: (_name, callback) => { receive = callback; } });
  page.onLoad();
  receive(enrollmentRulesDocument(metadata()));
  page.goToSection({ currentTarget: { dataset: { section: "study" } } });
  assert.equal(page.data.activeSection, "section-study");
  page.goToSection({ currentTarget: { dataset: { section: "other" } } });
  assert.equal(page.data.activeSection, "section-study");
  page.onUnload();
  receive({ scopeOrgUnitId: "other", sections: [], title: "late" });
  assert.equal(page.data.document.title, "苏州塾加入守则与缴费说明");
});
