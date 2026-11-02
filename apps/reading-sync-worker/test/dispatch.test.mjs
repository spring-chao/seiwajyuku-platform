import {test} from 'node:test';
import assert from 'node:assert/strict';
import dispatcher from '../../../cloudfunctions/reading-sync-dispatch/index.js';
import * as periodModule from '../periods.mjs';
const env={READING_SYNC_SIGNING_KEY:'synthetic-api-key-'+'a'.repeat(40),READING_WORKER_SIGNING_KEY:'synthetic-worker-key-'+'b'.repeat(40),READING_WORKER_URL:'https://seiwajyuku-reading-sync-test.sh.run.tcloudbase.com'};
const event={Type:'ReadingSyncContinuation',kind:'weekly',runDate:'2026-11-09',index:0};
const task={class_id:'synthetic-class',source_meeting_id:'synthetic-meeting',start:'2026-11-02',end:'2026-11-08',skip:false};
const tasks=Array.from({length:22},(_,i)=>({...task,class_id:'synthetic-class-'+i}));
const done={import_id:1,status:'SUCCESS',posting_completed:true,job_status:'SUCCEEDED'};
test('month start Monday belongs exclusively to monthly trigger',async()=>{
 const result=await dispatcher.run({Type:'Timer',TriggerName:'readingSyncWeekly'},{env,periodModule,today:'2026-06-01',request:()=>assert.fail('must skip')});
 assert.equal(result.skipped,true);
});
test('one class is collected and imported, then the next class is explicitly dispatched',async()=>{
 const sent=[];
 const request=async(url,payload)=>{
  if(url.endsWith('/plan'))return tasks;
  if(url.endsWith('/collect'))return{...payload,file_sha256:'a'.repeat(64),workbook_base64:'synthetic'};
  assert.ok(url.endsWith('/imports'));return done;
 };
 const result=await dispatcher.run(event,{env,periodModule,request,dispatch:async e=>sent.push(e)});
 assert.equal(result.status,'SUCCESS');assert.equal(sent.length,1);assert.equal(sent[0].index,1);
});
test('uncertain upload looks up the saved file before downloading or uploading again',async()=>{
 const calls=[];
 const request=async(url,payload)=>{
  calls.push(url);
  if(url.endsWith('/plan'))return tasks;
  if(url.endsWith('/lookup'))return done;
  assert.fail('must not collect or reimport a saved source');
 };
 const result=await dispatcher.run({...event,file_sha256:'a'.repeat(64)},{env,periodModule,request,dispatch:async()=>{}});
 assert.equal(result.status,'SUCCESS');assert.equal(calls.length,2);
});
test('a verified success is skipped without reading or writing sources',async()=>{
 const result=await dispatcher.run(event,{env,periodModule,request:async url=>{assert.ok(url.endsWith('/plan'));return tasks.map(t=>({...t,skip:true}));},dispatch:async()=>{}});
 assert.equal(result.status,'SKIPPED_VERIFIED_SUCCESS');
});
test('exhausted collection failure is recorded before advancing and never marked complete',async()=>{
 const calls=[];
 const request=async(url,payload)=>{
  calls.push(url);
  if(url.endsWith('/plan'))return tasks;
  if(url.endsWith('/outcome'))return{status:'FAILED'};
  throw Error('synthetic timeout');
 };
 const result=await dispatcher.run({...event,attempt:2},{env,periodModule,request,dispatch:async()=>{}});
 assert.equal(result.status,'FAILED');assert.ok(calls.some(p=>p.endsWith('/outcome')));
});
test('secrets cannot be forwarded to arbitrary worker hosts or redirects',()=>{
 for(const url of ['https://evil.invalid','https://seiwajyuku-reading-sync-test.sh.run.tcloudbase.com.evil.invalid','http://seiwajyuku-reading-sync-test.sh.run.tcloudbase.com','https://seiwajyuku-reading-sync-test.sh.run.tcloudbase.com/?token=bad'])assert.throws(()=>dispatcher.workerUrl(url));
 const a=dispatcher.signed('https://example.invalid/one',{n:1},env.READING_SYNC_SIGNING_KEY,1);
 const b=dispatcher.signed('https://example.invalid/two',{n:1},env.READING_SYNC_SIGNING_KEY,1);
 assert.notEqual(a.headers['X-Reading-Sync-Signature'],b.headers['X-Reading-Sync-Signature']);
 assert.ok(!JSON.stringify(a.headers).includes(env.READING_SYNC_SIGNING_KEY));
});
