import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { ref, computed, reactive, watch } from 'vue';

const errorModule = ts.transpileModule(readFileSync(new URL('../src/utils/studyMeetingError.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
}).outputText;
const errors = await import(`data:text/javascript;base64,${Buffer.from(errorModule).toString('base64')}`);

function harness(request) {
  const source = readFileSync(new URL('../src/views/seiwajyuku/study-meetings.vue', import.meta.url), 'utf8');
  const script = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
    .replaceAll('import.meta.env.DEV', 'false').replaceAll('import.meta.env.VITE_API_BASE_URL', '""');
  const compiled = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
    .outputText.replace(/^import [\s\S]*?;\r?\n/gm, '');
  const calls = [], saved = [], messages = [];
  const context = vm.createContext({ ref, computed, reactive, watch, ...errors,
    defineOptions() {}, onMounted() {}, onBeforeUnmount() {}, setTimeout: fn => fn(),
    useUserStoreHook: () => ({ permissions: ['exports:normal', 'plans:credit_settlement_preview'] }),
    ElMessage: { success: value => messages.push(value), error: value => messages.push(value) },
    URL: { createObjectURL: () => 'blob:synthetic-workbook', revokeObjectURL() {} },
    document: { createElement: () => { const anchor = { click: () => saved.push(anchor.download) }; return anchor; } },
    exportMeetingRecords: params => { calls.push(params); return request(params); }
  });
  vm.runInContext(compiled + '\nglobalThis.state = {exporting, exportError, exportLayout, filters}; globalThis.download = downloadRecords;', context);
  return { state: context.state, run: context.download, calls, saved, messages };
}

const workbook = () => new Blob([new Uint8Array([0x50, 0x4b, 3, 4, 0])]);

test('filtered export prevents duplicate submits and keeps the requested filename while controls change', async () => {
  let resolve;
  const h = harness(() => new Promise(r => { resolve = r; }));
  h.state.filters.class_org_unit_id = 'synthetic-class';
  h.state.filters.meeting_date_from = '2026-10-01';
  const first = h.run();
  await h.run();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].class_org_unit_id, 'synthetic-class');
  assert.equal(h.calls[0].meeting_date_from, '2026-10-01');
  h.state.exportLayout.value = 'member';
  resolve(workbook()); await first;
  assert.deepEqual(h.saved, ['小组学习会-小组汇总.xlsx']);
  assert.equal(h.state.exporting.value, false);
});

test('single-meeting export ignores list filters and uses member layout', async () => {
  const h = harness(async () => workbook());
  h.state.filters.class_org_unit_id = 'another-class';
  await h.run(42);
  assert.equal(JSON.stringify(h.calls[0]), JSON.stringify({session_id: 42, layout: 'member'}));
  assert.deepEqual(h.saved, ['学习会-42-人员学分明细.xlsx']);
});

test('failed downloads leave an actionable error and re-enable retry without saving a broken file', async () => {
  let attempts = 0;
  const h = harness(async () => {
    if (++attempts === 1) throw {code: 'ECONNABORTED'};
    return workbook();
  });
  await h.run();
  assert.match(h.state.exportError.value, /超时/);
  assert.equal(h.state.exporting.value, false);
  assert.equal(h.saved.length, 0);
  await h.run();
  assert.equal(h.state.exportError.value, '');
  assert.equal(h.saved.length, 1);
  const broken = harness(async () => new Blob(['<html>gateway error</html>']));
  await broken.run();
  assert.match(broken.state.exportError.value, /有效的 Excel/);
  assert.equal(broken.saved.length, 0);
});
