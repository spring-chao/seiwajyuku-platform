import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
const script=html.match(/<script id="canonical-admin-domain">([\s\S]*?)<\/script>/)[1];
function destination(href) {
  let result=null;
  const location=new URL(href);
  location.replace=value=>{result=value;};
  vm.runInNewContext(script,{window:{location},URL});
  return result;
}
const old='https://shengheshu-d2g2zyyl99f6c6fc2-1453587887.tcloudbaseapp.com';
test('legacy admin bookmarks preserve route and query on the fixed HTTPS domain',()=>{
  assert.equal(destination(old+'/ops-platform?utm_source=group#/login'),'https://ops.jnshengheshu.cn/ops-platform/?utm_source=group#/login');
  assert.equal(destination(old+'/ops-platform/#/operations?month=2026-09'),'https://ops.jnshengheshu.cn/ops-platform/#/operations?month=2026-09');
});
test('canonical host cannot loop; local development and other hosted apps stay accessible',()=>{
  for(const url of ['https://ops.jnshengheshu.cn/ops-platform/#/login','http://localhost:8848/ops-platform/',old+'/signin/',old+'/ops-platform-other/',old+'/']) assert.equal(destination(url),null);
  assert.ok(html.indexOf('canonical-admin-domain')<html.indexOf('src="/src/main.ts"'));
});
