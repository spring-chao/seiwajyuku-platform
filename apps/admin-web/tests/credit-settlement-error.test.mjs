import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/utils/creditSettlementError.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext }
}).outputText;
const { creditSettlementErrorMessage: message } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('network failures identify the failed section and do not claim an empty ledger', () => {
  assert.match(message({ code: 'ERR_NETWORK', message: 'Network Error' }, '学分账本读取失败'), /学分账本读取失败.*无法连接学分接口/);
  assert.match(message(new Error('学员 ID 必须为正整数'), '读取失败'), /^学员 ID 必须为正整数$/);
  assert.equal(message(new Error('请选择班级，并填写至少4字的来源确认说明'),'检查失败'),'请选择班级，并填写至少4字的来源确认说明');
  assert.doesNotMatch(message(new Error('private raw exception'),'检查失败'),/private/);
});
test('a partial schema upgrade has a specific explanation', () => {
  assert.match(message({ response: { status: 503, data: { detail: { code: 'CREDIT_LEDGER_SCHEMA_UNAVAILABLE' } } } }, '账本读取失败'), /期间结构尚未就绪/);
  assert.match(message({ response: { status: 503, data: {} } }, '账本读取失败'), /HTTP 503.*服务尚未就绪/);
});
test('Axios timeouts are distinguished from network failures without claiming rollback',()=>{
  for(const code of ['ECONNABORTED','ETIMEDOUT']) {
    const result=message({code,message:'timeout of 180000ms exceeded'},'保存来源未收到结果');
    assert.match(result,/响应超时.*可能已保存/);
    assert.doesNotMatch(result,/无法连接|检查网络|180000/);
  }
});
test('HTTP errors retain useful status while traces and transport config stay private', () => {
  for (const status of [401, 403, 404, 500, 502, 504]) {
    assert.match(message({ response: { status, data: { detail: 'Traceback password token secret' } }, config: { Authorization: 'secret' } }, '账本读取失败'), new RegExp(`HTTP ${status}`));
  }
  for (const detail of ['<html>gateway</html>', 'Traceback password token secret', [{ msg: 'private' }]]) {
    assert.doesNotMatch(message({ response: { status: 400, data: { detail } } }, '读取失败'), /html|Traceback|password|token|secret|private/);
  }
  assert.match(message({ response: { status: 400, data: { detail: '学分类别无效' } } }, '读取失败'), /学分类别无效/);
});

test('read-only export timeout never implies a saved business mutation', () => {
  const result=message({code:'ECONNABORTED'},'导出未完成',true);
  assert.match(result,/导出响应超时.*不会修改学分/);
  assert.doesNotMatch(result,/可能已保存|核对处理结果/);
});
