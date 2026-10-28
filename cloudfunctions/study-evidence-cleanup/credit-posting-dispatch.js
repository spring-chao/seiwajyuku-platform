"use strict";

// Event invocations use the function's existing rotating runtime identity.
// Only this function/environment can be targeted; no URLs, actors or amounts
// are accepted. TC3 reference: cloud.tencent.com/document/api/213/30654.
const { createHash, createHmac } = require("node:crypto");
const sha = value => createHash("sha256").update(value).digest("hex");
const mac = (key,value) => createHmac("sha256",key).update(value).digest();
const HOST = "scf.tencentcloudapi.com";
const NAMESPACE = "shengheshu-d2g2zyyl99f6c6fc2";
const FUNCTION = "studyEvidenceCleanup";

function valid(payload) {
  return payload && Number.isSafeInteger(payload.job_id) && payload.job_id > 0 &&
    /^[a-f0-9]{64}$/.test(payload.fingerprint || "");
}

function signedInvoke(payload, hops = 0, env = process.env, now = Math.floor(Date.now()/1000)) {
  if (!valid(payload) || !Number.isInteger(hops) || hops < 0 || hops >= 5000) throw new Error("invalid posting event");
  const id=env.TENCENTCLOUD_SECRETID, key=env.TENCENTCLOUD_SECRETKEY, token=env.TENCENTCLOUD_SESSIONTOKEN;
  if (!id || !key || !token) throw new Error("runtime identity unavailable");
  const body=JSON.stringify({ FunctionName: FUNCTION, Namespace: NAMESPACE, InvocationType: "Event", Qualifier: "$LATEST",
    ClientContext: JSON.stringify({Type:"CreditPostingJob",job_id:payload.job_id,fingerprint:payload.fingerprint,hops}) });
  const type="application/json; charset=utf-8", date=new Date(now*1000).toISOString().slice(0,10);
  const scope=`${date}/scf/tc3_request`;
  const canonical=`POST\n/\n\ncontent-type:${type}\nhost:${HOST}\n\ncontent-type;host\n${sha(body)}`;
  const toSign=`TC3-HMAC-SHA256\n${now}\n${scope}\n${sha(canonical)}`;
  const signature=mac(mac(mac(mac("TC3"+key,date),"scf"),"tc3_request"),toSign).toString("hex");
  return {body,headers:{"content-type":type,"X-TC-Action":"Invoke","X-TC-Version":"2018-04-16","X-TC-Region":"ap-shanghai",
    "X-TC-Timestamp":String(now),"X-TC-Token":token,
    Authorization:`TC3-HMAC-SHA256 Credential=${id}/${scope}, SignedHeaders=content-type;host, Signature=${signature}`}};
}

async function enqueue(payload, hops=0) {
  const request=signedInvoke(payload,hops);
  const response=await fetch(`https://${HOST}/`,{method:"POST",headers:request.headers,body:request.body,redirect:"error",signal:AbortSignal.timeout(8000)});
  const data=await response.json();
  if (!response.ok || !data.Response || data.Response.Error || !data.Response.RequestId) throw new Error("posting event not accepted");
  return {accepted:true};
}

async function run(event, url, token, requestMaintenance) {
  if (!valid(event) || Object.keys(event).some(k=>!["Type","job_id","fingerprint","hops"].includes(k))) throw new Error("invalid posting event");
  const hops=event.hops ?? 0;
  if (!Number.isInteger(hops) || hops < 0 || hops >= 5000) throw new Error("posting continuation limit");
  const worker=new URL(url);worker.pathname="/api/v1/internal/credit-posting-jobs";
  const result=await requestMaintenance(worker.toString(),token,{job_id:event.job_id,fingerprint:event.fingerprint},50000);
  const data=result && result.data;
  if (!result || result.success!==true || !data || typeof data.more!=="boolean" ||
      !["processed","completed","needs_review"].every(k=>Number.isInteger(data[k])&&data[k]>=0)) throw new Error("posting result unavailable");
  if (data.busy) throw new Error("posting lease is active; retry event later");
  if (data.more) await enqueue(event,hops+1);
  return {ok:true,processed:data.processed,completed:data.completed,needs_review:data.needs_review,continued:data.more};
}

module.exports={enqueue,run,signedInvoke};
