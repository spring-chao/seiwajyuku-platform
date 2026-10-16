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
function harness(request) {
  const calls = [], errors = [];
  const context = vm.createContext({ computed, ref, watch, Blob, defineProps: () => ({
    filters: { class_org_unit_id: "class-a", status: "ACTIVE", keyword: "合成" }, total: 125
  }), http: { request: async (...args) => { calls.push(args); return request?.(...args) || new Blob(); } },
  ElMessage: { error: value => errors.push(value) }, URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} },
  document: { createElement: () => ({ click() {} }) }, setTimeout: () => {} });
  vm.runInContext(compiled + "\nglobalThis.result = { layout, selected, visible, busy, available, download };", context);
  return { ui: context.result, calls, errors };
}

test("group layout drops personal columns and exports the selected list filters", async () => {
  const { ui, calls } = harness();
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
  const { ui, calls, errors } = harness(() => new Promise((resolve, reject) => { finish = reject; }));
  ui.visible.value = true;
  const pending = ui.download(); await ui.download();
  assert.equal(calls.length, 1);
  finish({ response: { data: new Blob([JSON.stringify({ detail: "导出超过5000人，请缩小筛选范围" })]) } });
  await pending;
  assert.match(errors[0], /5000/);
  assert.equal(ui.visible.value, true);
  assert.equal(ui.busy.value, false);
});
