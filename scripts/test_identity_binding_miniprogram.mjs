import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

function loadPage({ requestError } = {}) {
  const calls = [];
  const app = {
    globalData: {},
    setMemberSession(token) { calls.push({ session: token }); }
  };
  const request = async () => {
    calls.push({ request: true });
    if (requestError) throw requestError;
    return { data: { access_token: 'new-session', member: { name_masked: '李*', class_name: '测试班' } } };
  };
  let page;
  const wx = {
    login: options => options.success({ code: 'login-code' }),
    showToast: options => calls.push({ toast: options.title }),
    showModal: options => {
      calls.push({ modal: options });
      if (options.title === '身份已绑定' && options.success) options.success();
    },
    navigateBack: options => calls.push({ navigateBack: options.delta })
  };
  vm.runInNewContext(readFileSync(new URL('../apps/wechat-miniprogram/pages/identity/bind.js', import.meta.url), 'utf8'), {
    Page: definition => { page = definition; },
    getApp: () => app,
    require: () => ({ request }),
    wx
  });
  page.setData = values => Object.assign(page.data, values);
  return { page, calls };
}

test('binding conflicts are shown in an actionable modal', async () => {
  const { page, calls } = loadPage({
    requestError: Object.assign(new Error('当前微信已绑定其他学员，请先解绑后再绑定'), { statusCode: 400 })
  });
  page.data.name = '李四';
  page.data.phone = '13800000000';
  await page.bindIdentity();
  const modal = calls.find(item => item.modal)?.modal;
  assert.equal(modal.title, '无法绑定');
  assert.equal(modal.content, '当前微信已绑定其他学员，请先解绑后再绑定');
  assert.equal(calls.some(item => item.session), false);
});

test('successful binding stores the new session and confirms the masked member', async () => {
  const { page, calls } = loadPage();
  page.data.name = '李四';
  page.data.phone = '13800000000';
  await page.bindIdentity();
  assert.deepEqual(calls.find(item => item.session), { session: 'new-session' });
  assert.equal(calls.find(item => item.modal)?.modal.title, '身份已绑定');
  assert.deepEqual(calls.find(item => item.navigateBack), { navigateBack: 1 });
});
