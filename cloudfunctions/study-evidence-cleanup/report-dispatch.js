"use strict";
const {createHash,createHmac}=require('node:crypto');
const sha=v=>createHash('sha256').update(v).digest('hex'),mac=(k,v)=>createHmac('sha256',k).update(v).digest();
const HOST='scf.tencentcloudapi.com',NAMESPACE='shengheshu-d2g2zyyl99f6c6fc2',FUNCTION='studyEvidenceCleanup';
const valid=p=>p&&Object.keys(p).every(k=>['month','report_id'].includes(k))&&/^20[0-9]{2}-(0[1-9]|1[0-2])$/.test(p.month||'')&&/^[a-f0-9]{64}$/.test(p.report_id||'');
function signedInvoke(payload,hops=0,env=process.env,now=Math.floor(Date.now()/1000)) {
  const sweep=payload&&Object.keys(payload).length===0;
  if((!sweep&&!valid(payload))||!Number.isInteger(hops)||hops<0||hops>=100)throw new Error('invalid report event');
  const id=env.TENCENTCLOUD_SECRETID,key=env.TENCENTCLOUD_SECRETKEY,token=env.TENCENTCLOUD_SESSIONTOKEN;
  if(!id||!key||!token)throw new Error('runtime unavailable');
  const event={Type:sweep?'MonthlyCoreReportSweep':'MonthlyCoreReport',...payload,hops};
  const body=JSON.stringify({FunctionName:FUNCTION,Namespace:NAMESPACE,InvocationType:'Event',Qualifier:'$LATEST',ClientContext:JSON.stringify(event)});
  const type='application/json; charset=utf-8',date=new Date(now*1000).toISOString().slice(0,10),scope=`${date}/scf/tc3_request`;
  const canonical=`POST\n/\n\ncontent-type:${type}\nhost:${HOST}\n\ncontent-type;host\n${sha(body)}`;
  const signature=mac(mac(mac(mac('TC3'+key,date),'scf'),'tc3_request'),`TC3-HMAC-SHA256\n${now}\n${scope}\n${sha(canonical)}`).toString('hex');
  return {body,headers:{'content-type':type,'X-TC-Action':'Invoke','X-TC-Version':'2018-04-16','X-TC-Region':'ap-shanghai','X-TC-Timestamp':String(now),'X-TC-Token':token,Authorization:`TC3-HMAC-SHA256 Credential=${id}/${scope}, SignedHeaders=content-type;host, Signature=${signature}`}};
}
async function enqueue(payload,hops=0) {
  const request=signedInvoke(payload,hops);
  const response=await fetch(`https://${HOST}/`,{method:'POST',headers:request.headers,body:request.body,redirect:'error',signal:AbortSignal.timeout(8000)});
  const data=await response.json();
  if(!response.ok||!data.Response||data.Response.Error||!data.Response.RequestId)throw new Error('report event not accepted');
}
async function run(event,url,token,requestMaintenance) {
  const sweep=event.Type==='MonthlyCoreReportSweep';
  const payload=sweep?{}:{month:event.month,report_id:event.report_id};
  if(!sweep&&!valid(payload)||Object.keys(event).some(k=>!['Type','month','report_id','hops'].includes(k)))throw new Error('invalid report event');
  const hops=event.hops??0;
  if(!Number.isInteger(hops)||hops<0||hops>=100)throw new Error('report continuation limit');
  const endpoint=new URL(url);endpoint.pathname='/api/v1/internal/monthly-core-reports'+(sweep?'/sweep':'/run');
  const result=await requestMaintenance(endpoint.toString(),token,payload,50000),data=result&&result.data;
  if(!result||result.success!==true||!data||!['completed','failed'].every(k=>Number.isInteger(data[k])&&data[k]>=0))throw new Error('report result unavailable');
  if(sweep&&data.more&&!data.busy)await enqueue({},hops+1);
  return {ok:data.failed===0,completed:data.completed,failed:data.failed,continued:Boolean(sweep&&data.more&&!data.busy)};
}
module.exports={enqueue,run,signedInvoke};
