const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
let definition;
let response;
let pending;
const requests = [];
const wx = { env: { USER_DATA_PATH: '/synthetic' }, getFileSystemManager: () => ({ writeFileSync() {} }) };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../pages/resources/index.js'), 'utf8'), {
  Page(value) { definition = value; }, wx,
  require() { return { request(url, options) { requests.push({url, options}); return pending || Promise.resolve(response); } }; }
});
async function run() {
  const page = { ...definition, data: { ...definition.data }, _loadVersion: 0, setData(value) { Object.assign(this.data, value); } };
  response = {data: {years: [{year:1,title:'第一年',months:[]},{year:2,title:'第二年',months:[]},{year:3,title:'第三年',months:[]}]}};
  await page.loadCatalog();
  for (const month of [11,12]) {
    response = {data: { label: `第${month}个月`, classTopic: '原课程', groupTopic:'', classQrs:[], groupQrs:[], extraTopics:[], extraQrs:[], resourceNote:'本月无学习视频二维码' }};
    await page.openMonth({currentTarget:{dataset:{year:3,month}}});
    assert.equal(page.data.blocks.flatMap(x=>x.qrs).length,0);
    assert.equal(page.data.note,'本月无学习视频二维码');
  }
  let resolve;
  pending = new Promise(done => { resolve = done; });
  const loading = page.committee();
  page.home();
  resolve({data:{videos:[]}});
  await loading;
  assert.equal(page.data.view,'home');
  assert.equal(page.data.baidu,null);
  pending = Promise.reject(new Error('岗位无权限'));
  await page.committee();
  assert.equal(page.data.videos.length,0);
  assert.equal(page.data.error,'岗位无权限');
  assert.ok(requests.every(x=>x.options.auth));
}
run().then(()=>console.log('resource navigation, empty months, authorization and stale request tests passed'));
