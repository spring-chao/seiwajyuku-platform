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
  const context = vm.createContext({ computed, reactive, ref, watch, onMounted: () => {}, defineEmits: () => () => {}, useUserStoreHook: () => ({ permissions }), creditSettlementErrorMessage: e => e.message, ElMessage: { success() {}, error() {} }, ElMessageBox: { confirm: async () => {} }, localStorage: { getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v) }, URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} }, document: { createElement: () => ({ click() {} }) }, setTimeout: () => {}, ...api });
  vm.runInContext(compiled + "\nglobalThis.result = {state,file,form,preview,check,register,canExport,canManage,exportFile,exportClasses,eligible,setup,error,reload,show,detail};", context);
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

test("a source-read failure leaves the independently authorized export usable", async () => {
  const ui = harness({ sourceWorkbench: async () => { throw new Error('source timeout'); }, sourceExportOptions: async () => ({ data: { classes: [{ id:'test-class', name:'合成班级' }] } }) });
  await ui.reload();
  assert.equal(ui.exportClasses.value[0].id,'test-class');
  assert.match(ui.error.value,/source timeout/);
});

test("source details are loaded for the selected receipt instead of every list row", async () => {
  let id;
  const ui = harness({ sourceGet: async value => { id=value;return { data: { id:value,rows:[{excel_row:2}] } }; } });
  ui.state.value = { imports: [{id:7,rows:[],row_count:1}] };
  await ui.show({id:7});
  assert.equal(id,7);assert.equal(ui.detail.value.rows[0].excel_row,2);
});

test("missing source note shows immediate feedback without sending an import", async () => {
  const messages=[];let calls=0;
  const ui=harness({ sourcePreview:async()=>{calls++;}, ElMessage:{error:msg=>messages.push(msg),success(){}} });
  ui.form.class_id='class';ui.file.value={name:'test.xlsx'};
  await ui.check();
  assert.equal(calls,0);assert.equal(messages.length,1);assert.match(messages[0],/至少4字/);
});

test("reading preview with ignored rows can save the validated fingerprint", async () => {
  let fingerprint;
  const ui=harness({sourceRegister:async(_file,_form,fp)=>{fingerprint=fp;}, sourceWorkbench:async()=>({data:{storage_available:true}}),sourceExportOptions:async()=>({data:{classes:[]}})},['members:read','plans:credit_settlement_manage','plans:credit_activity_fact_manage','plans:credit_settlement_preview']);
  ui.form.class_id='class';ui.form.kind='DAILY_READING';ui.form.source_note='已核对原表';ui.file.value={name:'test.xlsx'};
  ui.state.value={storage_available:true,can_import_activity:true};await nextTick();
  ui.preview.value={fingerprint:'valid-selection',error_count:2,accepted_count:1,ignored_count:2,rows:[]};
  await ui.register();assert.equal(fingerprint,'valid-selection');
});
