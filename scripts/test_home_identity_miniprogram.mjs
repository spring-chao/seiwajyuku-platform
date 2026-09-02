import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

const nodeRequire = createRequire(import.meta.url);
const { resolveVolunteerServices } = nodeRequire('../apps/wechat-miniprogram/utils/volunteer-services.js');
const template = readFileSync(new URL('../apps/wechat-miniprogram/pages/home/index.wxml', import.meta.url), 'utf8');
const enrollmentCondition = template.match(/wx:if="{{([^}]+)}}" class="section-card enrollment-card"/)[1];
const studyCondition = template.match(/wx:if="{{([^}]+)}}" class="primary-button" bindtap="openStudyMeeting"/)[1];
const visible = (condition, data) => vm.runInNewContext(condition, data);
const failure = statusCode => Object.assign(new Error('test failure'), { statusCode });
const member = { member_id: 42, name_masked: '测*长', class_name: '测试班', study_group_name: '第一小组' };
const assignment = position_name => ({
  position_key: `test-${position_name}`,
  position_name,
  scope_level: 'GROUP',
  scope_type: 'UNIT',
  scope_org_unit_id: 'group-one',
  scope_name: '第一小组',
  capabilities: ['STUDY_MEETING_MANAGE']
});

function harness({ bound = false, assignments = [], meError, serviceError, meResponse, serviceResponse } = {}) {
  const calls = [];
  const app = { globalData: { memberSessionToken: bound ? 'synthetic-session' : '' }, clearMemberSession() { this.globalData.memberSessionToken = ''; } };
  const request = async path => {
    calls.push(path);
    if (path.endsWith('/me')) { if (meError) throw meError; return meResponse ? meResponse() : { data: { member } }; }
    if (path.endsWith('/volunteer-services')) {
      if (serviceError) throw serviceError;
      return serviceResponse
        ? serviceResponse()
        : { data: { is_volunteer: assignments.length > 0, roles: assignments } };
    }
    if (path.endsWith('/revoke')) return { success: true };
    return { data: { enrollment_entry: { handoff_token: 'synthetic-handoff' } } };
  };
  let page;
  const wx = { navigateTo: data => calls.push(data.url), showToast() {}, showModal: options => { page.modal = options; } };
  vm.runInNewContext(readFileSync(new URL('../apps/wechat-miniprogram/pages/home/index.js', import.meta.url), 'utf8'), {
    Page: definition => { page = definition; },
    getApp: () => app,
    require: modulePath => modulePath.includes('volunteer-services')
      ? { resolveVolunteerServices }
      : { request },
    wx
  });
  page.setData = values => Object.assign(page.data, values);
  return { page, app, calls };
}

test('unbound sees binding and enrollment; original enrollment route is preserved', async () => {
  const { page, calls } = harness();
  await page.loadHome();
  assert.equal(page.data.identityState, 'unbound');
  assert.equal(visible(enrollmentCondition, page.data), true);
  assert.equal(visible(studyCondition, page.data), false);
  page.openEnrollment();
  assert.ok(calls.includes('/pages/enrollment/index?token=synthetic-handoff'));
});

for (const label of ['组长', '辅导员']) {
  test(`${label} sees own group service, never applicant entry`, async () => {
    const { page, calls } = harness({ bound: true, assignments: [assignment(label)] });
    await page.loadHome();
    assert.equal(page.data.identityState, 'bound');
    assert.equal(page.data.displayRole, label);
    assert.match(page.data.displayScope, /第一小组/);
    assert.equal(visible(studyCondition, page.data), true);
    assert.equal(visible(enrollmentCondition, page.data), false);
    page.openEnrollment();
    assert.equal(calls.includes('/api/v1/public/portal'), false);
    assert.equal(calls.some(path => path.includes('/pages/enrollment/')), false);
    page.openStudyMeeting();
    assert.ok(calls.includes('/pages/study-meeting/index'));
  });
}

for (const serviceError of [undefined, failure(403), failure(404), failure(500)]) {
  test(`ordinary member or unavailable volunteer service ${serviceError?.statusCode || 'empty'} stays bound with neither action`, async () => {
    const { page, app, calls } = harness({ bound: true, serviceError });
    await page.loadHome();
    assert.equal(page.data.identityState, 'bound');
    assert.equal(page.data.member.member_id, 42);
    assert.ok(app.globalData.memberSessionToken);
    assert.equal(visible(enrollmentCondition, page.data), false);
    assert.equal(visible(studyCondition, page.data), false);
    page.openStudyMeeting();
    assert.equal(calls.includes('/pages/study-meeting/index'), false);
  });
}

test('successful revoke clears capabilities and restores applicant entry', async () => {
  const { page, app } = harness({ bound: true, assignments: [assignment('辅导员')] });
  await page.loadHome();
  page.unbind();
  await page.modal.success({ confirm: true });
  assert.equal(app.globalData.memberSessionToken, '');
  assert.equal(page.data.displayRole, '');
  assert.equal(page.data.bindingActionLabel, '重新绑定我的学员身份');
  assert.equal(visible(enrollmentCondition, page.data), true);
  assert.equal(visible(studyCondition, page.data), false);
});

test('401 clears revoked/expired session, network and server errors do not misidentify a member', async () => {
  for (const status of [401, undefined, 403, 404, 500]) {
    const { page, app } = harness({ bound: true, meError: failure(status) });
    await page.loadHome();
    assert.equal(page.data.identityState, status === 401 ? 'unbound' : 'unknown');
    assert.equal(Boolean(app.globalData.memberSessionToken), status !== 401);
    assert.equal(visible(enrollmentCondition, page.data), status === 401);
  }
});

test('identity resolves before context and slow old responses cannot resurrect revoked identity', async () => {
  let releaseService;
  let reachedService;
  const reached = new Promise(resolve => { reachedService = resolve; });
  const service = new Promise(resolve => { releaseService = resolve; });
  const { page } = harness({ bound: true, serviceResponse: () => { reachedService(); return service; } });
  const loading = page.loadHome();
  await reached;
  assert.equal(page.data.identityState, 'bound');
  assert.equal(visible(enrollmentCondition, page.data), false);
  page.unbind();
  await page.modal.success({ confirm: true });
  releaseService({ data: { is_volunteer: true, roles: [assignment('组长')] } });
  await loading;
  assert.equal(page.data.identityState, 'unbound');
  assert.equal(visible(studyCondition, page.data), false);
});

test('request helper carries HTTP status without leaking headers', async () => {
  let exported;
  const module = { exports: {} };
  vm.runInNewContext(readFileSync(new URL('../apps/wechat-miniprogram/utils/request.js', import.meta.url), 'utf8'), {
    getApp: () => ({ globalData: { apiBaseUrl: 'http://127.0.0.1:8000' } }), module,
    wx: { request: options => options.success({ statusCode: 401, data: { detail: '需要重新绑定' } }) }
  });
  exported = module.exports;
  await assert.rejects(exported.request('/api/v1/wechat/me'), error => error.statusCode === 401 && error.message === '需要重新绑定');
});
