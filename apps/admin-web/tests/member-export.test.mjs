import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { computed, ref, watch, nextTick } from "vue";

const component = readFileSync(new URL("../src/views/seiwajyuku/components/MemberExport.vue", import.meta.url), "utf8");
const script = component.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  .replace(/^import[\s\S]*?from ["'][^"']+["'];\r?\n/gm, "");
const compiled = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const catalog = [
  ...['member_code', 'name', 'management_org_name', 'class_name', 'group_name', 'status', 'birthday', 'phone_masked'].map(key => ({
    key, label: key, category: '基本资料', available: true,
    personal: !['name', 'management_org_name', 'class_name', 'group_name'].includes(key)
  })),
  { key: 'company_address', label: '公司地址', category: '企业资料', available: true, personal: true, requires_purpose: true },
  { key: 'phone', label: '完整手机号', category: '受保护字段', available: false, personal: true, reason: '请使用敏感导出' }
];
function harness(request) {
  const calls = [], errors = [];
  const context = vm.createContext({ computed, ref, watch, Blob, defineProps: () => ({
    filters: { class_org_unit_id: "class-a", status: "ACTIVE", keyword: "合成" }, total: 125
  }), http: { request: async (...args) => { calls.push(args); return request ? request(...args) : args[1].endsWith('/fields') ? { data: catalog } : new Blob(); } },
  ElMessage: { error: value => errors.push(value) }, URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} },
  document: { createElement: () => ({ click() {} }) }, setTimeout: () => {} });
  vm.runInContext(compiled + "\nglobalThis.result = { layout, selected, visible, busy, available, fields, loading, loadError, purpose, needsPurpose, allSelected, toggleAll, open, download };", context);
  return { ui: context.result, calls, errors };
}

test("group layout drops personal columns and exports the selected list filters", async () => {
  const { ui, calls } = harness();
  await ui.open(); calls.length = 0;
  ui.layout.value = "group"; await nextTick();
  assert.equal(ui.selected.value.includes("member_code"), false);
  assert.equal(ui.available.value.some(field => field.key === "phone_masked"), false);
  ui.visible.value = true; await ui.download();
  assert.equal(calls[0][0], "get");
  assert.equal(calls[0][2].params.class_org_unit_id, "class-a");
  assert.equal(calls[0][2].params.keyword, "合成");
  assert.equal(calls[0][2].params.layout, "group");
  assert.equal(calls[0][2].params.page_size, undefined);
  assert.equal(ui.visible.value, false);
});

test("a pending download cannot be duplicated; backend range error remains visible", async () => {
  let finish;
  const { ui, calls, errors } = harness((...args) => args[1].endsWith('/fields') ? { data: catalog } : new Promise((resolve, reject) => { finish = reject; }));
  await ui.open(); calls.length = 0;
  ui.visible.value = true;
  const pending = ui.download(); await ui.download();
  assert.equal(calls.length, 1);
  finish({ response: { data: new Blob([JSON.stringify({ detail: "导出超过5000人，请缩小筛选范围" })]) } });
  await pending;
  assert.match(errors[0], /5000/);
  assert.equal(ui.visible.value, true);
  assert.equal(ui.busy.value, false);
});

test("all catalog fields stay visible and select-all skips protected and incompatible fields", async () => {
  const { ui } = harness();
  await ui.open();
  assert.equal(ui.fields.value.some(field => field.key === 'phone'), true);
  ui.toggleAll();
  assert.equal(ui.allSelected.value, true);
  assert.equal(ui.selected.value.includes('birthday'), true);
  assert.equal(ui.selected.value.includes('phone'), false);
  ui.toggleAll(); assert.equal(ui.selected.value.length, 0);
  ui.layout.value = 'group'; await nextTick();
  ui.toggleAll();
  assert.equal(ui.selected.value.includes('birthday'), false);
  assert.equal(ui.selected.value.includes('company_address'), false);
});

test("enterprise selection requires purpose and sends it with the chosen fields", async () => {
  const { ui, calls, errors } = harness();
  await ui.open(); calls.length = 0;
  ui.selected.value = ['name', 'company_address'];
  await ui.download();
  assert.equal(calls.length, 0);
  assert.match(errors[0], /用途/);
  ui.purpose.value = ' 学员资料整理 ';
  await ui.download();
  assert.equal(calls[0][2].params.purpose, '学员资料整理');
  assert.equal(calls[0][2].params.fields, 'name,company_address');
});

test("catalog failure prevents downloads and retry reloads available fields", async () => {
  let failed = true;
  const { ui, calls } = harness(() => {
    if (failed) throw new Error('network');
    return { data: catalog };
  });
  await ui.open();
  assert.match(ui.loadError.value, /加载失败/);
  await ui.download(); assert.equal(calls.length, 1);
  failed = false; await ui.open();
  assert.equal(ui.loadError.value, '');
  assert.ok(ui.selected.value.length > 0);
});
