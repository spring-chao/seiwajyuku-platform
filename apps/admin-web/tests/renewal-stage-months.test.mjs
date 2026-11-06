import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { computed, reactive, ref } from 'vue';

test('stage cards open the complete matching ledger, including next-year projections', () => {
  const source = readFileSync(new URL('../src/views/seiwajyuku/renewals.vue', import.meta.url), 'utf8');
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import[\s\S]*?from ["'][^"']+["'];\r?\n/gm, '');
  const compiled = ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({ computed, reactive, ref, onMounted() {}, defineOptions() {}, chinaDate: () => '2026-11-06',
    useRoute: () => ({query:{}}), useRouter: () => ({}), useUserStoreHook: () => ({permissions:[]}) });
  vm.runInContext(compiled+'\nglobalThis.ui={stageCycles,stageCounts,selectStage,secondaryView,visibleLedgerCycles,todayActions};',context);
  const ui=context.ui;
  ui.stageCycles.value=[{id:1,stage:{code:'FOLLOW_1'}},{id:2,stage:{code:'RENEW_2'}},{id:-3,is_projection:true,stage:{code:'OBSERVE_3'}}];
  ui.todayActions.value.items=[];
  for (const [stage,id] of [['FOLLOW_1',1],['RENEW_2',2],['OBSERVE_3',-3]]) {
    ui.selectStage(stage);
    assert.equal(ui.secondaryView.value,'LEDGER');
    assert.equal(ui.stageCounts.value[stage],1);
    assert.equal(ui.visibleLedgerCycles.value.length,1);
    assert.equal(ui.visibleLedgerCycles.value[0].id,id);
  }
});
