import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/utils/creditRuleApplyJournal.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext }
}).outputText;
const { submitCreditRuleApplyOnce: submit, creditRuleApplyJournalKey: key } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const record = { commit: 'c'.repeat(40), fingerprint: 'a'.repeat(64), submitted_at: '2026-10-09T16:00:00Z' };

test('reserve is persisted before sending; an unknown result and a reload cannot replay', async () => {
  const values = new Map();
  const storage = { getItem: k => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
  let sent = 0;
  await assert.rejects(submit(storage, record, async () => {
    sent++;
    assert.deepEqual(JSON.parse(storage.getItem(key)), record);
    throw new Error('timeout after server commit');
  }), /timeout/);
  await assert.rejects(submit(storage, record, async () => { sent++; }), /已提交/);
  assert.equal(sent, 1);
});
test('persistence failure refuses to send the write', async () => {
  let sent = false;
  const storage = { getItem: () => null, setItem: () => { throw new Error('storage unavailable'); } };
  await assert.rejects(submit(storage, record, async () => { sent = true; }), /storage unavailable/);
  assert.equal(sent, false);
});
test('a successful request is still reserved and is never automatically repeated', async () => {
  let value = null;
  const storage = { getItem: () => value, setItem: (_, v) => { value = v; } };
  assert.deepEqual(await submit(storage, record, async () => ({ status: 'APPLIED' })), { status: 'APPLIED' });
  await assert.rejects(submit(storage, record, async () => assert.fail('replayed')), /已提交/);
});
