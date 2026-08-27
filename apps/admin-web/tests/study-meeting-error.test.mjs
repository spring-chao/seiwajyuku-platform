import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/utils/studyMeetingError.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext }
}).outputText;
const { studyMeetingErrorMessage: message } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('actionable HTTP categories replace a generic refresh error', () => {
  for (const [status, expected] of [[401, '登录'], [403, '权限'], [404, '版本'], [500, '迁移'], [503, '就绪']]) {
    const result = message({ response: { status, data: {} } });
    assert.ok(result.includes(`HTTP ${status}`));
    assert.ok(result.includes(expected));
  }
  assert.match(message({}), /API 地址/);
});
test('business errors stay useful; raw traceback, HTML and transport config are not displayed', () => {
  assert.match(message({ response: { status: 400, data: { detail: '课程已被其他人修改' } } }, '课程修正'), /课程已被其他人修改/);
  for (const data of ['<html>gateway</html>', { detail: 'Traceback: database password' }, { detail: [{ msg: 'validation' }] }]) {
    const result = message({ response: { status: 500, data }, config: { Authorization: 'secret' } });
    assert.doesNotMatch(result, /Traceback|password|secret|<html>/);
  }
});
