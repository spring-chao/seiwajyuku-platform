const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const js = fs.readFileSync(path.join(root, "pages/enrollment/index.js"), "utf8");
const wxml = fs.readFileSync(path.join(root, "pages/enrollment/index.wxml"), "utf8");

assert.match(wxml, /src="\/assets\/shenghe-brand-logo-jiangnan\.png"/);
assert.doesNotMatch(wxml, /shenghe-brand-logo\.png"/);
assert.doesNotMatch(wxml, /苏州盛和塾/);
assert.match(wxml, /提高心性 · 拓展经营/);
assert.match(wxml, /<view class="section-index">01<\/view>/);
assert.match(wxml, /<view class="section-index">04<\/view>/);
assert.match(wxml, /<view class="field-row">\s*<view class="field-label required">姓名<\/view>/);
assert.match(wxml, /class="hero-duration">约 3–5 分钟完成<\/view>/);

for (const label of [
  "性别",
  "政治面貌",
  "利润率",
  "计划学习年限",
  "业绩提升目标",
  "利润提升目标"
]) {
  assert.match(wxml, new RegExp(`field-label required\\">${label}`));
}

assert.match(wxml, /年销售额（万）/);
assert.match(wxml, /range="\{\{positionOptions\}\}"/);
for (const position of [
  "法人代表",
  "董事长",
  "合伙人",
  "股东",
  "总经理",
  "经营者夫妻",
  "经营者二代"
]) {
  assert.match(js, new RegExp(position));
}

assert.match(js, /const GROWTH_OPTIONS = \["1\.5倍", "2倍", "3倍", "5倍", "自定义"\]/);
assert.doesNotMatch(js, /const GROWTH_OPTIONS = .*暂不设定/);
assert.doesNotMatch(wxml, /暂不设定/);

for (const field of [
  "invoice_registered_address",
  "invoice_phone",
  "invoice_bank",
  "invoice_account"
]) {
  assert.doesNotMatch(js, new RegExp(`data-field="${field}"`));
  assert.doesNotMatch(wxml, new RegExp(`data-field="${field}"`));
}

assert.match(js, /\["gender", "性别"\]/);
assert.match(js, /\["political_status", "政治面貌"\]/);
assert.match(js, /\["profit_margin", "利润率"\]/);
assert.match(js, /\["goal_years", "计划学习年限"\]/);
assert.match(js, /\["revenue_growth_target", "业绩提升目标"\]/);
assert.match(js, /\["profit_growth_target", "利润提升目标"\]/);
assert.match(js, /state: "selecting"/);
assert.match(js, /target_shuku_org_unit_id=\$\{encodeURIComponent/);
assert.match(js, /selectedTargetShukuId/);
assert.match(js, /selectedTargetShukuName/);
assert.match(js, /handleTargetShukuSelect/);
assert.match(wxml, /请选择您准备加入的塾/);
assert.match(wxml, /target-shuku-options/);
assert.match(wxml, /target-shuku-option/);
assert.match(wxml, /下一步，填写资料/);
assert.doesNotMatch(wxml, /range="\{\{targetShukuOptions\}\}"/);
assert.doesNotMatch(wxml, /targetShukuOptionIds\.indexOf/);
assert.match(wxml, /BUSINESS_CONFIG_REQUIRED/);
assert.match(js, /joining_rules/);
assert.match(wxml, /（一）加入守则/);
assert.match(wxml, /（二）缴费说明/);
assert.match(wxml, /formMeta\.fee_amount/);
assert.match(wxml, /适用于首次入塾及年度续费/);
assert.match(wxml, /formMeta\.contacts/);
assert.match(js, /copyPaymentAccount/);
assert.match(js, /copyContactPhone/);
assert.match(wxml, /bindtap="copyPaymentAccount"/);
assert.match(wxml, /bindtap="copyContactPhone"/);
assert.doesNotMatch(wxml, /class="copy-button"/);
assert.doesNotMatch(wxml, /复制号码/);
assert.match(js, /state: "selecting", rulesAcknowledged: false/);
for (const businessValue of [
  "无锡稻合企业管理顾问有限公司",
  "512914112210201",
  "8110501011602342242",
  "1103054509100146820",
  "19984864833",
  "13776052728",
  "15380081186",
  "15366836286",
  "18051598063",
  "18021183718"
]) {
  assert.doesNotMatch(js, new RegExp(businessValue));
  assert.doesNotMatch(wxml, new RegExp(businessValue));
}

// Exercise the selection handler so the visible card state and submitted ID
// cannot drift apart again while the WXML remains deliberately declarative.
const pagePath = path.join(root, "pages/enrollment/index.js");
const previousPage = global.Page;
const previousGetApp = global.getApp;
let definition;
try {
  global.getApp = () => ({ globalData: {} });
  global.Page = value => { definition = value; };
  delete require.cache[require.resolve(pagePath)];
  require(pagePath);
  const page = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(next) {
      Object.entries(next).forEach(([key, value]) => {
        const parts = key.split(".");
        if (parts.length === 1) this.data[key] = value;
        else {
          this.data[parts[0]] = { ...this.data[parts[0]], [parts[1]]: value };
        }
      });
    }
  };
  for (const [name, value] of Object.entries(definition)) {
    if (typeof value === "function") page[name] = value.bind(page);
  }
  page.handleTargetShukuSelect({ currentTarget: { dataset: { id: "org-changzhou", name: "常州塾" } } });
  assert.equal(page.data.selectedTargetShukuId, "org-changzhou");
  assert.equal(page.data.selectedTargetShukuName, "常州塾");
  assert.equal(page.data.form.target_shuku_org_unit_id, "org-changzhou");
} finally {
  delete require.cache[require.resolve(pagePath)];
  global.Page = previousPage;
  global.getApp = previousGetApp;
}

console.log("enrollment form mini-program tests passed");
