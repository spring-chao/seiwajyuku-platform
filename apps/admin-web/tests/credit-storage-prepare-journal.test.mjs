import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/utils/creditStoragePrepareJournal.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext }
}).outputText;
const { submitCreditStoragePrepareOnce: submit, creditStoragePrepareKey: key } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const record = { commit: 'c'.repeat(40), fingerprint: 'a'.repeat(64), submitted_at: '2026-10-11T00:00:00Z' };

test('unknown storage result survives reload and does not replay the migration', async () => {
  const values = new Map();
  const storage = { getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
  let sent = 0;
  await assert.rejects(submit(storage, '0064', record, async () => {
    sent++;
    assert.deepEqual(JSON.parse(storage.getItem(key('0064'))), record);
    throw new Error('response lost');
  }), /response lost/);
  await assert.rejects(submit(storage, '0064', record, async () => sent++), /已提交/);
  assert.equal(sent, 1);
  await submit(storage, '0065', record, async () => sent++);
  assert.equal(sent, 2);
});
test('storage persistence failure or unsupported step prevents dispatch', async () => {
  const storage = { getItem: () => null, setItem: () => { throw new Error('storage unavailable'); } };
  await assert.rejects(submit(storage, '0064', record, async () => assert.fail('sent')), /storage unavailable/);
  await assert.rejects(submit(storage, '0069', record, async () => assert.fail('sent')), /不支持/);
});
