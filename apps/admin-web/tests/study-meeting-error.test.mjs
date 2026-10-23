import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/utils/studyMeetingError.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext }
}).outputText;
const { studyMeetingErrorMessage: message, studyMeetingExportErrorMessage: exportMessage, validateStudyMeetingWorkbook: validate } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('actionable HTTP categories replace a generic refresh error', () => {
  for (const [status, expected] of [[401, '登录'], [403, '权限'], [404, '版本'], [500, '迁移'], [503, '就绪']]) {
    const result = message({ response: { status, data: {} } });
    assert.ok(result.includes(`HTTP ${status}`));
    assert.ok(result.includes(expected));
  }
  assert.match(message({}), /网络/);
});

test('export timeout and cancellation are not reported as API address failures', async () => {
  for (const code of ['ECONNABORTED', 'ETIMEDOUT']) {
    const result = await exportMessage({ code });
    assert.match(result, /超时.*班级.*日期范围/);
    assert.doesNotMatch(result, /无法连接|API 地址/);
  }
  assert.match(await exportMessage({ code: 'ERR_CANCELED' }), /已取消/);
  assert.match(await exportMessage({ code: 'ERR_NETWORK' }), /网络/);
});

test('Excel download decodes bounded JSON Blob business errors without leaking server internals', async () => {
  assert.match(await exportMessage({ response: { status: 400, data: new Blob([JSON.stringify({ detail: '导出超过500场，请缩小日期范围' })], { type: 'application/json' }) } }), /HTTP 400.*500场/);
  for (const [status, detail] of [[403, '无权访问'], [500, 'Traceback password=secret'], [400, '<html>unsafe</html>']]) {
    const result = await exportMessage({ response: { status, data: new Blob([JSON.stringify({ detail })]) } });
    assert.match(result, new RegExp(`HTTP ${status}`));
    assert.doesNotMatch(result, /Traceback|password|secret|<html>/);
  }
  assert.match(await exportMessage({ response: { status: 504, data: new Blob(['<html>gateway</html>']) } }), /HTTP 504.*超时/);
});

test('an empty or HTML download is rejected before being saved as Excel', async () => {
  for (const blob of [new Blob([]), new Blob(['<html>gateway</html>']), new Blob(['{"detail":"error"}']), {}]) {
    await assert.rejects(validate(blob), /INVALID_STUDY_MEETING_WORKBOOK/);
  }
  await validate(new Blob([new Uint8Array([0x50, 0x4b, 3, 4, 0])]));
  assert.match(await exportMessage(new Error('INVALID_STUDY_MEETING_WORKBOOK')), /有效的 Excel/);
});
test('business errors stay useful; raw traceback, HTML and transport config are not displayed', () => {
  assert.match(message({ response: { status: 400, data: { detail: '课程已被其他人修改' } } }, '课程修正'), /课程已被其他人修改/);
  for (const data of ['<html>gateway</html>', { detail: 'Traceback: database password' }, { detail: [{ msg: 'validation' }] }]) {
    const result = message({ response: { status: 500, data }, config: { Authorization: 'secret' } });
    assert.doesNotMatch(result, /Traceback|password|secret|<html>/);
  }
});
