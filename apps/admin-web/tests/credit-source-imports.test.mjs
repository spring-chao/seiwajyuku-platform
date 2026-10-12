import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { computed, reactive, ref, watch, nextTick } from "vue";

const component = readFileSync(new URL("../src/views/seiwajyuku/components/CreditSourceImports.vue", import.meta.url), "utf8");
const script = component.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import[\s\S]*?from ["'][^"']+["'];\r?\n/gm, "");
const compiled = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
function harness(api = {}, permissions = ["members:read", "plans:credit_settlement_manage", "plans:credit_settlement_preview", "plans:credit_settlement_approve", "plans:credit_settlement_post", "exports:normal"]) {
  const storage = new Map();
  const context = vm.createContext({ computed, reactive, ref, watch, onMounted: () => {}, defineEmits: () => () => {}, useUserStoreHook: () => ({ permissions }), creditSettlementErrorMessage: e => e.message, ElMessage: { success() {} }, ElMessageBox: { confirm: async () => {} }, localStorage: { getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v) }, URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} }, document: { createElement: () => ({ click() {} }) }, setTimeout: () => {}, ...api });
  vm.runInContext(compiled + "\nglobalThis.result = {state,file,form,preview,check,register,canExport,canManage,exportFile,eligible,setup,error};", context);
  return context.result;
}

test("changing source information while a check is running discards the old preview", async () => {
  let finish;
  const ui = harness({ sourcePreview: () => new Promise(resolve => { finish = resolve; }) });
  ui.form.class_id = "test-class"; ui.form.kind = "DAILY_READING"; ui.form.source_note = "原表已核对"; ui.file.value = { name: "test.xlsx" };
  await nextTick();
  const pending = ui.check();
  ui.form.source_note = "更正后的确认说明"; await nextTick();
  finish({ data: { fingerprint: "old", rows: [], error_count: 0 } }); await pending;
  assert.equal(ui.preview.value, undefined);
});

test("export staff can download without source-management permission", async () => {
  let downloaded = 0;
  const ui = harness({ sourceExport: async () => { downloaded++; return {}; } }, ["exports:normal", "members:read", "plans:credit_settlement_preview"]);
  assert.equal(ui.canExport.value, true); assert.equal(ui.canManage.value, false);
  await ui.exportFile(); assert.equal(downloaded, 1);
});

test("batch review controls retain independent review and do not offer failed-batch retry", () => {
  const ui = harness();
  const row = { id: 1, batches: [{ status: "PENDING_APPROVAL", proposed_entry_count: 1, created_by_current_actor: true }] };
  ui.state.value = { imports: [row] };
  assert.equal(ui.eligible(row, "approve"), false);
  row.batches[0].created_by_current_actor = false;
  assert.equal(ui.eligible(row, "approve"), true);
  row.batches[0].status = "PARTIAL_FAILED";
  assert.equal(ui.eligible(row, "post"), false);
});

test("lost setup response keeps a durable no-replay journal", async () => {
  let calls = 0;
  const ui = harness({ sourceSetup: async () => { calls++; throw new Error("结果待核验"); } });
  ui.state.value = { setup_allowed: true, release_commit: "a".repeat(40) };
  await ui.setup(); await ui.setup();
  assert.equal(calls, 1); assert.match(ui.error.value, /不重复执行/);
});
