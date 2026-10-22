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
  vm.runInContext(compiled + "\nglobalThis.result = {state,file,form,preview,check,register,canExport,canManage,exportFile,exportClasses,eligible,setup,error,reload,show,detail,act,batchSummary,posting,stopRequested,refreshDetail,recover};", context);
  return context.result;
}

test("changing source information while a check is running discards the old preview", async () => {
  let finish;
  const ui = harness({ sourcePreview: () => new Promise(resolve => { finish = resolve; }) });
  ui.form.class_id = "test-class"; ui.form.kind = "DAILY_READING"; ui.form.source_note = "原表已核对"; ui.file.value = { name: "test.xlsx" };
  await nextTick();
  const pending = ui.check();
  ui.form.kind = "EXCELLENT_SHARE"; await nextTick();
  finish({ data: { fingerprint: "old", rows: [], error_count: 0 } }); await pending;
  assert.equal(ui.preview.value, undefined);
});

test("editing an optional note keeps a checked scoring preview",async()=>{
  const ui=harness();ui.form.kind='DAILY_READING';await nextTick();
  ui.preview.value={fingerprint:'facts-only'};ui.form.source_note='补充备注';await nextTick();
  assert.equal(ui.preview.value.fingerprint,'facts-only');
});

test("export staff can download without source-management permission", async () => {
  let downloaded = 0;
  const ui = harness({ sourceExport: async () => { downloaded++; return {}; } }, ["exports:normal", "members:read", "plans:credit_settlement_preview"]);
  assert.equal(ui.canExport.value, true); assert.equal(ui.canManage.value, false);
  await ui.exportFile(); assert.equal(downloaded, 1);
});

test("source confirmation requires posting rights and accepts the source creator", () => {
  const ui = harness();
  const row = { id: 1, batches: [{ status: "PENDING_APPROVAL", proposed_entry_count: 1, created_by_current_actor: true }] };
  ui.state.value = { imports: [row] };
  assert.equal(ui.eligible(row), true);
  row.batches[0].status = "PARTIAL_FAILED";
  assert.equal(ui.eligible(row), true);
  const limited = harness({}, ["members:read", "plans:credit_settlement_manage", "plans:credit_settlement_preview"]);
  row.batches[0].status = "DRY_RUN"; limited.state.value = {imports:[row]};
  assert.equal(limited.eligible(row), false);
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

test("an empty optional source note allows checking the workbook", async () => {
  const messages=[];let calls=0;
  const ui=harness({ sourcePreview:async(_file,form)=>{calls++;assert.equal(form.source_note,'');return{data:{rows:[]}};}, ElMessage:{error:msg=>messages.push(msg),success(){}} });
  ui.form.class_id='class';ui.form.kind='DAILY_READING';ui.file.value={name:'test.xlsx'};
  await ui.check();
  assert.equal(calls,1);assert.equal(messages.length,0);
});

test("reading preview with ignored rows can save the validated fingerprint", async () => {
  let fingerprint;
  const ui=harness({sourceRegister:async(_file,_form,fp)=>{fingerprint=fp;}, sourceWorkbench:async()=>({data:{storage_available:true}}),sourceExportOptions:async()=>({data:{classes:[]}})},['members:read','plans:credit_settlement_manage','plans:credit_activity_fact_manage','plans:credit_settlement_preview']);
  ui.form.class_id='class';ui.form.kind='DAILY_READING';ui.form.source_note='已核对原表';ui.file.value={name:'test.xlsx'};
  ui.state.value={storage_available:true,can_import_activity:true};await nextTick();
  ui.preview.value={fingerprint:'valid-selection',error_count:2,accepted_count:1,ignored_count:2,rows:[]};
  await ui.register();assert.equal(fingerprint,'valid-selection');
});

for (const found of [true,false]) test(`lost source-save reply checks committed receipt and never resubmits: found=${found}`,async()=>{
  let writes=0,lookups=0,opens=0;
  const ui=harness({sourceRegister:async()=>{writes++;throw new Error('save timeout');},
    sourceFindSaved:async(fp,cls,kind)=>{lookups++;assert.equal(fp,'checked');assert.equal(cls,'class');assert.equal(kind,'DAILY_READING');return{data:{id:found?31:null}};},
    sourceGet:async id=>{opens++;return{data:{id}};},sourceWorkbench:async()=>({data:{imports:[]}}),sourceExportOptions:async()=>({data:{classes:[]}})},['members:read','plans:credit_settlement_manage','plans:credit_activity_fact_manage','plans:credit_settlement_preview']);
  ui.form.class_id='class';ui.form.kind='DAILY_READING';ui.file.value={name:'test.xlsx'};await nextTick();
  ui.state.value={storage_available:true,can_import_activity:true};ui.preview.value={fingerprint:'checked'};
  await ui.register();assert.equal(writes,1);assert.equal(lookups,1);assert.equal(opens,found?1:0);
  if(found){assert.equal(ui.preview.value,undefined);assert.equal(ui.detail.value.id,31);}
  else{assert.equal(ui.preview.value.fingerprint,'checked');assert.match(ui.error.value,/save timeout/);}
});
test('a saved receipt with a failed detail read reports saved status and keeps its preview cleared',async()=>{
  const ui=harness({sourceRegister:async()=>({data:{id:31,idempotent:false}}),sourceGet:async()=>{throw new Error('detail timeout');},sourceWorkbench:async()=>({data:{imports:[]}}),sourceExportOptions:async()=>({data:{classes:[]}})},['members:read','plans:credit_settlement_manage','plans:credit_activity_fact_manage','plans:credit_settlement_preview']);
  ui.form.class_id='class';ui.form.kind='DAILY_READING';ui.file.value={name:'test.xlsx'};await nextTick();
  ui.state.value={storage_available:true,can_import_activity:true};ui.preview.value={fingerprint:'checked'};
  await ui.register();assert.equal(ui.preview.value,undefined);assert.match(ui.error.value,/detail timeout/);
});


test("confirm uses the refreshed displayed settlement and does not call review actions",async()=>{
  const calls=[];let release;
  const source={id:9,class_name:'合成班级',source_kind:'DAILY_READING',period_start:'2026-10-09',period_end:'2026-10-10',member_count:2,
    batches:[{status:'DRY_RUN',proposed_entry_count:2}],settlement_fingerprint:'fresh',settlement_summary:{entry_count:2,proposed_points:'2.00',posted_points:'0.00'}};
  const ui=harness({sourceGet:async()=>({data:source}),sourceAction:async(...args)=>{calls.push(args);await new Promise(r=>release=r);return{data:source};},sourceWorkbench:async()=>({data:{imports:[source]}}),sourceExportOptions:async()=>({data:{classes:[]}})});
  ui.state.value={imports:[source]};
  const first=ui.act(source,'confirm');
  await new Promise(r=>setImmediate(r));
  await ui.act(source,'confirm');
  assert.equal(calls.length,1);assert.deepEqual(Array.from(calls[0]),[9,'confirm','fresh',false]);
  release();await first;
  assert.equal((ui.batchSummary(source).match(/待确认入账/g)||[]).length,1);
});

const progressSource = (posted, total = 8) => ({id:18,class_name:'合成班级',source_kind:'DAILY_READING',member_count:8,
  batches:[{status:posted===total?'POSTED':'DRY_RUN',proposed_entry_count:total}],settlement_fingerprint:'same-frozen-items',
  settlement_summary:{entry_count:total,proposed_points:String(total),posted_points:String(posted)},
  posting_progress:{state:posted===total?'COMPLETE':'READY',posted_entries:posted,total_entries:total,remaining_entries:total-posted,completed:posted===total}});

test("one confirmation continues short steps and exposes durable progress",async()=>{
  let calls=0;
  const start=progressSource(0);
  const ui=harness({sourceGet:async()=>({data:start}),sourceAction:async()=>({data:progressSource(Math.min(++calls*3,8))}),sourceWorkbench:async()=>({data:{imports:[progressSource(8)]}}),sourceExportOptions:async()=>({data:{classes:[]}})});
  ui.state.value={imports:[start]};await ui.act(start,'confirm');
  assert.equal(calls,3);assert.equal(ui.detail.value.posting_progress.posted_entries,8);assert.equal(ui.posting.value,false);assert.equal(ui.error.value,'');
});

test("lost reply refreshes the receipt and reports completion instead of replaying",async()=>{
  let reads=0,calls=0;const start=progressSource(0);
  const ui=harness({sourceGet:async()=>({data:++reads===1?start:progressSource(8)}),sourceAction:async()=>{calls++;throw new Error('timeout');},sourceWorkbench:async()=>({data:{imports:[progressSource(8)]}}),sourceExportOptions:async()=>({data:{classes:[]}})});
  ui.state.value={imports:[start]};await ui.act(start,'confirm');
  assert.equal(calls,1);assert.equal(ui.error.value,'');assert.equal(ui.detail.value.posting_progress.completed,true);
});

test("an unfinished lost reply shows progress and never automatically retries an uncertain write",async()=>{
  let reads=0,calls=0;const start=progressSource(0),partial=progressSource(3);partial.batches[0].status='POSTING';partial.posting_progress.state='POSTING';
  const ui=harness({sourceGet:async()=>({data:++reads===1?start:partial}),sourceAction:async()=>{calls++;throw new Error('timeout');},sourceWorkbench:async()=>({data:{imports:[partial]}}),sourceExportOptions:async()=>({data:{classes:[]}})});
  ui.state.value={imports:[start]};await ui.act(start,'confirm');
  assert.equal(calls,1);assert.match(ui.error.value,/已入账3\/8笔/);assert.match(ui.error.value,/无需重新上传/);assert.equal(ui.posting.value,false);
});

test("stopping between steps preserves progress and the refreshed dialog stays resumable",async()=>{
  const start=progressSource(0);let calls=0,ui;
  ui=harness({sourceGet:async()=>({data:start}),sourceAction:async()=>{calls++;ui.stopRequested.value=true;return{data:progressSource(3)};},sourceWorkbench:async()=>({data:{imports:[start]}}),sourceExportOptions:async()=>({data:{classes:[]}})});
  ui.state.value={imports:[start]};await ui.act(start,'confirm');
  assert.equal(calls,1);assert.equal(ui.detail.value.posting_progress.posted_entries,3);assert.equal(ui.eligible(ui.detail.value),true);
});
