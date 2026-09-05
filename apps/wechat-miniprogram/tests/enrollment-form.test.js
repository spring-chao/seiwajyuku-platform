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

console.log("enrollment form mini-program tests passed");
