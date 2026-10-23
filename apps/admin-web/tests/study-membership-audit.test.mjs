import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {computed,ref} from 'vue';

function harness(request,permissions=['org:manage','members:read']) {
  const s=readFileSync(new URL('../src/views/seiwajyuku/components/StudyMembershipAudit.vue',import.meta.url),'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const js=ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText.replace(/^import [\s\S]*?;\r?\n/gm,'');
  const calls=[];
  const c=vm.createContext({computed,ref,useUserStoreHook:()=>({permissions}),http:{request:async (...args)=>{calls.push(args);return request();}}});
  vm.runInContext(js+'\nglobalThis.run=load;globalThis.state={visible,loading,error,data};',c);
  return {run:c.run,state:c.state,calls};
}

test('membership audit reads the controlled endpoint once and exposes no mutation action',async()=>{
  let resolve;const pending=new Promise(r=>{resolve=r;});const h=harness(()=>pending);
  const first=h.run();await h.run();assert.equal(h.calls.length,1);
  assert.equal(h.calls[0][0],'get');assert.match(h.calls[0][1],/study-membership-audit$/);
  assert.equal(h.calls[0][2].params.limit,500);
  resolve({data:{checked_members:12,membership_issue_count:2}});await first;
  assert.equal(h.state.data.value.checked_members,12);assert.equal(h.state.loading.value,false);
});
test('missing permission performs no request and a failed refresh clears stale audit data',async()=>{
  const denied=harness(async()=>({data:{}}),['org:manage']);await denied.run();assert.equal(denied.calls.length,0);
  const failed=harness(async()=>{throw Error('synthetic transport failure');});failed.state.data.value={checked_members:99};
  await failed.run();assert.equal(failed.state.data.value,undefined);assert.equal(failed.state.loading.value,false);
  assert.match(failed.state.error.value,/没有修改任何归属/);
});
