"use strict";
const {createHash,createHmac}=require('node:crypto');
const API='https://seiwajyuku-platform-api-287369-8-1453587887.sh.run.tcloudbase.com';
const FUNCTION='readingSyncDispatch';
const NAMESPACE='shengheshu-d2g2zyyl99f6c6fc2';
const sha=v=>createHash('sha256').update(v).digest('hex');
const mac=(k,v)=>createHmac('sha256',k).update(v).digest();

function signed(url,payload,key,now=Math.floor(Date.now()/1000)){
 if(typeof key!=='string'||key.length<32)throw Error('SIGNING_NOT_CONFIGURED');
 const target=new URL(url);
 if(target.protocol!=='https:'||target.username||target.password||target.search||target.hash)throw Error('INVALID_TARGET');
 const body=JSON.stringify(payload);
 const signature=createHmac('sha256',key).update(['POST',target.pathname,String(now),sha(body)].join('\n')).digest('hex');
 return{body,headers:{'Content-Type':'application/json','X-Reading-Sync-Time':String(now),'X-Reading-Sync-Signature':signature}};
}

async function call(url,payload,key){
 const request=signed(url,payload,key);
 const response=await fetch(url,{method:'POST',...request,redirect:'error',signal:AbortSignal.timeout(55000)});
 if(!response.ok)throw Error('UPSTREAM_NOT_VERIFIED');
 const result=await response.json();
 if(result.success!==true||!result.data)throw Error('UPSTREAM_NOT_VERIFIED');
 return result.data;
}

async function enqueue(event,env=process.env,now=Math.floor(Date.now()/1000)){
 const id=env.TENCENTCLOUD_SECRETID,key=env.TENCENTCLOUD_SECRETKEY,token=env.TENCENTCLOUD_SESSIONTOKEN;
 if(!id||!key||!token)throw Error('RUNTIME_IDENTITY_UNAVAILABLE');
 const body=JSON.stringify({FunctionName:FUNCTION,Namespace:NAMESPACE,InvocationType:'Event',Qualifier:'$LATEST',ClientContext:JSON.stringify(event)});
 const host='scf.tencentcloudapi.com',type='application/json; charset=utf-8',day=new Date(now*1000).toISOString().slice(0,10);
 const scope=`${day}/scf/tc3_request`;
 const canonical=`POST\n/\n\ncontent-type:${type}\nhost:${host}\n\ncontent-type;host\n${sha(body)}`;
 const signature=mac(mac(mac(mac('TC3'+key,day),'scf'),'tc3_request'),`TC3-HMAC-SHA256\n${now}\n${scope}\n${sha(canonical)}`).toString('hex');
 const response=await fetch('https://'+host+'/',{method:'POST',body,redirect:'error',signal:AbortSignal.timeout(8000),headers:{'Content-Type':type,'X-TC-Action':'Invoke','X-TC-Version':'2018-04-16','X-TC-Region':'ap-shanghai','X-TC-Timestamp':String(now),'X-TC-Token':token,Authorization:`TC3-HMAC-SHA256 Credential=${id}/${scope}, SignedHeaders=content-type;host, Signature=${signature}`}});
 const data=await response.json();
 if(!response.ok||data.Response?.Error||!data.Response?.RequestId)throw Error('CONTINUATION_NOT_ACCEPTED');
}

function workerUrl(raw){
 const url=new URL(raw);
 if(url.protocol!=='https:'||!/^seiwajyuku-reading-sync-[a-z0-9-]+\.sh\.run\.tcloudbase\.com$/.test(url.hostname)||url.port||url.username||url.password||url.search||url.hash||!['','/'].includes(url.pathname))throw Error('WORKER_NOT_CONFIGURED');
 return url.origin+'/collect';
}

async function run(event,{env=process.env,request=call,dispatch=enqueue,today,periodModule}={}){
 if(!['Timer','ReadingSyncContinuation','ReadingSyncManual'].includes(event.Type))throw Error('INVALID_EVENT');
 const {periods,shanghaiToday}=periodModule||await import('./periods.mjs');
 const runDate=event.runDate||today||shanghaiToday();
 const kind=event.kind||(event.TriggerName==='readingSyncMonthly'?'monthly':event.TriggerName==='readingSyncWeekly'?'weekly':null);
 const intervals=periods(kind,runDate);
 if(!intervals.length)return{skipped:true,reason:'MONTHLY_OWNS_THIS_DATE'};
 const index=event.index??0,attempt=event.attempt??0,polls=event.polls??0;
 if(!Number.isInteger(index)||index<0||index>=44||!Number.isInteger(attempt)||attempt<0||attempt>2||!Number.isInteger(polls)||polls<0||polls>30)throw Error('INVALID_CONTINUATION');
 const tasks=await request(API+'/api/v1/internal/reading-sync/plan',{intervals},env.READING_SYNC_SIGNING_KEY);
 if(!Array.isArray(tasks)||tasks.length!==22*intervals.length)throw Error('PLAN_INCOMPLETE');
 const task=tasks[index];
 const next=async()=>{if(index+1<tasks.length)await dispatch({Type:'ReadingSyncContinuation',kind,runDate,index:index+1},env);};
 if(task.skip){await next();return{class_id:task.class_id,start:task.start,end:task.end,status:'SKIPPED_VERIFIED_SUCCESS'};}
 const selected={class_id:task.class_id,source_meeting_id:task.source_meeting_id,start:task.start,end:task.end};
 let result,digest=event.file_sha256;
 try{
  if(event.import_id){
   result=await request(API+'/api/v1/internal/reading-sync/status',{...selected,import_id:event.import_id},env.READING_SYNC_SIGNING_KEY);
  }else{
   if(digest){
    result=await request(API+'/api/v1/internal/reading-sync/lookup',{...selected,file_sha256:digest},env.READING_SYNC_SIGNING_KEY);
    if(!result.import_id)result=null;
   }
   if(!result){
   const collected=await request(workerUrl(env.READING_WORKER_URL),selected,env.READING_WORKER_SIGNING_KEY);
   if(collected.class_id!==selected.class_id||collected.source_meeting_id!==selected.source_meeting_id||collected.start!==selected.start||collected.end!==selected.end||!collected.workbook_base64)throw Error('COLLECTED_SOURCE_CHANGED');
   digest=collected.file_sha256;
   if(!/^[a-f0-9]{64}$/.test(digest||''))throw Error('WORKBOOK_DIGEST_MISSING');
   result=await request(API+'/api/v1/internal/reading-sync/imports',{...selected,workbook_base64:collected.workbook_base64},env.READING_SYNC_SIGNING_KEY);
   }
  }
  if(result.status==='SOURCE_SAVED_POSTING_NOT_CREATED')result=await request(API+'/api/v1/internal/reading-sync/resume',{...selected,import_id:result.import_id},env.READING_SYNC_SIGNING_KEY);
 }catch{
  if(attempt<2){await dispatch({Type:'ReadingSyncContinuation',kind,runDate,index,attempt:attempt+1,import_id:event.import_id,file_sha256:digest,polls},env);return{...selected,status:'RETRY_PENDING'};}
  await request(API+'/api/v1/internal/reading-sync/outcome',{...selected,error_code:'SOURCE_OR_POSTING_NOT_VERIFIED'},env.READING_SYNC_SIGNING_KEY);
  await next();return{...selected,status:'FAILED',error_code:'SOURCE_OR_POSTING_NOT_VERIFIED'};
 }
 if(!result.posting_completed&&!result.source_only&&result.job_status!=='NEEDS_REVIEW'&&polls<30){
  await new Promise(resolve=>setTimeout(resolve,10000));
  await dispatch({Type:'ReadingSyncContinuation',kind,runDate,index,import_id:result.import_id,polls:polls+1},env);
 }else await next();
 return{...selected,...result};
}

// Let SCF record a failed invocation and apply its retry policy. Do not turn a
// dispatch/plan failure into a successful invocation containing a FAILED word.
exports.main=async event=>{try{return await run(event);}catch{throw Error('READING_SYNC_DISPATCH_NOT_VERIFIED');}};
exports.run=run;
exports.signed=signed;
exports.workerUrl=workerUrl;
