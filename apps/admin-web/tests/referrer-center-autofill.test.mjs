import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
const code = ts.transpileModule(readFileSync(new URL('../src/utils/referrerCenterAutofill.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {createReferrerCenterAutofill:create} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
function fixture() {
  const state={applicationId:1,referrer:'Synthetic Referrer',centerId:'',enabled:true};
  let finish; let calls=0; let hint=''; let loading=false;
  const control=create({current:()=>({...state}),lookup:()=>{calls++;return new Promise(resolve=>{finish=resolve;});},apply:center=>{state.centerId=center;},notify:(l,m)=>{loading=l;hint=m;}});
  return {state,control,finish:result=>finish(result),calls:()=>calls,hint:()=>hint,loading:()=>loading};
}
const matched={status:'MATCHED',referrer_org_unit_id:'center-a'};
test('uniquely matched center is a review-form suggestion and may be changed',async()=>{
  const f=fixture();const pending=f.control.run();assert.equal(f.loading(),true);
  f.finish(matched);await pending;assert.equal(f.state.centerId,'center-a');assert.match(f.hint(),/保存审核后生效/);
  f.state.centerId='center-b';f.control.manual();f.state.referrer='Another';f.control.edited();await f.control.run();
  assert.equal(f.state.centerId,'center-b');assert.equal(f.calls(),1);
});
test('saved/manual center and non-reviewable applications are preserved',async()=>{
  for(const preset of [{centerId:'saved-center'},{enabled:false}]) {
    const f=fixture();Object.assign(f.state,preset);await f.control.run();assert.equal(f.calls(),0);
  }
});
test('ambiguous or unavailable identity leaves a manual choice',async()=>{
  const f=fixture();const pending=f.control.run();f.finish({status:'MANUAL',referrer_org_unit_id:null});await pending;
  assert.equal(f.state.centerId,'');assert.match(f.hint(),/手动选择/);
});
test('late results cannot overwrite another name, manual selection or application',async()=>{
  for(const change of [f=>{f.state.referrer='Other';f.control.edited();},f=>{f.state.centerId='manual';f.control.manual();},f=>{f.state.applicationId=2;f.control.reset();},f=>{f.state.enabled=false;f.control.reset();}]) {
    const f=fixture();const pending=f.control.run();change(f);const expected=f.state.centerId;f.finish(matched);await pending;
    assert.equal(f.state.centerId,expected);assert.equal(f.loading(),false);
  }
});
test('editing an automatically filled referrer clears the former automatic center',async()=>{
  const f=fixture();const pending=f.control.run();f.finish(matched);await pending;
  f.state.referrer='Other';f.control.edited();assert.equal(f.state.centerId,'');assert.equal(f.hint(),'');
});
test('a lookup failure leaves manual selection available and clears the spinner',async()=>{
  let center='';let loading=false;let hint='';
  const control=create({current:()=>({applicationId:1,referrer:'Synthetic',centerId:center,enabled:true}),
    lookup:async()=>{throw new Error('network');},apply:value=>{center=value;},notify:(l,m)=>{loading=l;hint=m;}});
  await control.run();assert.equal(center,'');assert.equal(loading,false);assert.match(hint,/手动选择/);
});
