import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const cleanup = require("./index.js");
const bridge = require("./storage-bridge.js");
const key = `study-meetings/production/2026/10/${"a".repeat(32)}.jpg`;
const runtime = {
  CLOUDBASE_STORAGE_BUCKET: "7368-synthetic-123",
  CLOUDBASE_STORAGE_REGION: "ap-shanghai",
  TENCENTCLOUD_SECRETID: "synthetic-id",
  TENCENTCLOUD_SECRETKEY: "synthetic-secret",
  TENCENTCLOUD_SESSIONTOKEN: "synthetic+token/=",
};

test('monthly report objects are private, isolated and conditional writes are signed',()=>{
  const storage=require('./report-storage');
  const key='monthly-core/production/2026-10/'+'a'.repeat(64)+'/state.json';
  const result=storage.signedObjectRequest({operation:'monthly-core-object',method:'PUT',key,content_type:'application/json',if_match:'"'+'b'.repeat(32)+'"'},runtime,1700000000);
  assert.equal(result.headers['x-cos-acl'],'private');assert.equal(result.headers['if-match'],'"'+'b'.repeat(32)+'"');
  for(const invalid of ['study-meetings/production/2026/10/'+ 'a'.repeat(32)+'.jpg',key.replace('2026-10','2026-99'),key.replace('/state.json','/../state.json')])
    assert.throws(()=>storage.signedObjectRequest({operation:'monthly-core-object',method:'GET',key:invalid},runtime));
  assert.throws(()=>storage.signedObjectRequest({operation:'monthly-core-object',method:'DELETE',key},runtime));
});

test('monthly dispatch is limited to its fixed function and report identifier',()=>{
  const dispatcher=require('./report-dispatch');
  const body=JSON.parse(dispatcher.signedInvoke({month:'2026-10',report_id:'a'.repeat(64)},0,runtime,1700000000).body);
  assert.equal(body.FunctionName,'studyEvidenceCleanup');assert.equal(body.InvocationType,'Event');
  assert.deepEqual(JSON.parse(body.ClientContext),{Type:'MonthlyCoreReport',month:'2026-10',report_id:'a'.repeat(64),hops:0});
  assert.throws(()=>dispatcher.signedInvoke({month:'2026-13',report_id:'a'.repeat(64)},0,runtime));
  assert.throws(()=>dispatcher.signedInvoke({},100,runtime));
});

test('monthly schedule only queues a sweep and does not run cleanup or posting',async()=>{
  const previous={...process.env},saved=globalThis.fetch,calls=[];
  Object.assign(process.env,runtime,{PLATFORM_API_CLEANUP_URL:'https://api.example.invalid/api/v1/internal/study-evidence',STUDY_EVIDENCE_CLEANUP_TOKEN:'synthetic-token-'.repeat(4)});
  globalThis.fetch=async(url,options)=>{calls.push([url,options]);return{ok:true,json:async()=>({Response:{RequestId:'synthetic'}})};};
  try{
    assert.equal((await cleanup.main({Type:'Timer',TriggerName:'monthlyCoreReports'},{})).queued,true);
    assert.equal(calls.length,1);assert.equal(JSON.parse(JSON.parse(calls[0][1].body).ClientContext).Type,'MonthlyCoreReportSweep');
  }finally{globalThis.fetch=saved;for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];Object.assign(process.env,previous);}
});

test("fixed per-job async Invoke signature matches the official Tencent Python SDK fixture",()=>{
  const dispatch=require('./credit-posting-dispatch');
  const request=dispatch.signedInvoke({job_id:7,fingerprint:'c'.repeat(64)},0,{...runtime,TENCENTCLOUD_SESSIONTOKEN:'synthetic-token'},1700000000);
  assert.ok(request.headers.Authorization.endsWith('Signature=31795742779248589b08f08d5dd43a8b945933968f70f36659049639cfd960d0'));
  const body=JSON.parse(request.body);
  assert.equal(body.InvocationType,'Event');assert.equal(body.Qualifier,'$LATEST');
  assert.throws(()=>dispatch.signedInvoke({job_id:0,fingerprint:'c'.repeat(64)},0,runtime,1700000000));
  assert.throws(()=>dispatch.signedInvoke({job_id:7,fingerprint:'c'.repeat(64)},5000,runtime,1700000000));
});

test("a posting event only runs its one confirmed job and stops when finished", async () => {
  const previousFetch=globalThis.fetch;
  const savedUrl=process.env.PLATFORM_API_CLEANUP_URL, savedToken=process.env.STUDY_EVIDENCE_CLEANUP_TOKEN;
  process.env.PLATFORM_API_CLEANUP_URL='https://api.example.invalid/api/v1/internal/study-evidence';
  process.env.STUDY_EVIDENCE_CLEANUP_TOKEN='synthetic-posting-worker-token-123456789';
  const calls=[];
  globalThis.fetch=async(url,options)=>{calls.push([url,options]);return{ok:true,text:async()=>JSON.stringify({success:true,data:{processed:2,completed:1,needs_review:0,more:false}})};};
  try {
    const result=await cleanup.main({Type:'CreditPostingJob',job_id:8,fingerprint:'a'.repeat(64),hops:0},{});
    assert.equal(result.ok,true);assert.equal(calls.length,1);
    assert.equal(new URL(calls[0][0]).pathname,'/api/v1/internal/credit-posting-jobs');
    assert.deepEqual(JSON.parse(calls[0][1].body),{job_id:8,fingerprint:'a'.repeat(64)});assert.equal(result.completed,1);
  } finally {
    globalThis.fetch=previousFetch;
    if(savedUrl===undefined)delete process.env.PLATFORM_API_CLEANUP_URL;else process.env.PLATFORM_API_CLEANUP_URL=savedUrl;
    if(savedToken===undefined)delete process.env.STUDY_EVIDENCE_CLEANUP_TOKEN;else process.env.STUDY_EVIDENCE_CLEANUP_TOKEN=savedToken;
  }
});

test("unfinished posting work enqueues exactly one continuation of the same job",async()=>{
  const savedFetch=globalThis.fetch, previous={...process.env};
  Object.assign(process.env,runtime,{PLATFORM_API_CLEANUP_URL:'https://api.example.invalid/api/v1/internal/study-evidence',STUDY_EVIDENCE_CLEANUP_TOKEN:'synthetic-posting-token-'.repeat(2)});
  const calls=[];
  globalThis.fetch=async(url,options)=>{
    calls.push([url,options]);
    return url.includes('scf.tencentcloudapi.com')?{ok:true,json:async()=>({Response:{RequestId:'synthetic-request'}})}:
      {ok:true,text:async()=>JSON.stringify({success:true,data:{processed:2,completed:0,needs_review:0,more:true}})};
  };
  try{
    const result=await cleanup.main({Type:'CreditPostingJob',job_id:9,fingerprint:'b'.repeat(64),hops:3},{});
    assert.equal(result.continued,true);assert.equal(calls.length,2);
    const next=JSON.parse(calls[1][1].body);
    assert.equal(next.InvocationType,'Event');assert.equal(next.FunctionName,'studyEvidenceCleanup');
    assert.equal(next.Namespace,'shengheshu-d2g2zyyl99f6c6fc2');
    assert.deepEqual(JSON.parse(next.ClientContext),{Type:'CreditPostingJob',job_id:9,fingerprint:'b'.repeat(64),hops:4});
    assert.ok(!JSON.stringify(result).includes(runtime.TENCENTCLOUD_SECRETKEY));
  }finally{
    globalThis.fetch=savedFetch;
    for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];
    Object.assign(process.env,previous);
  }
});

test("authenticated click dispatch returns async acknowledgement without posting inline",async()=>{
  const savedFetch=globalThis.fetch, previous={...process.env};const calls=[];
  Object.assign(process.env,runtime,{STUDY_EVIDENCE_STORAGE_BRIDGE_ENABLED:'true',STUDY_EVIDENCE_CLEANUP_TOKEN:'synthetic-posting-token-'.repeat(2)});
  globalThis.fetch=async(url,options)=>{calls.push([url,options]);return{ok:true,json:async()=>({Response:{RequestId:'synthetic-async-ack'}})};};
  const event={httpMethod:'POST',path:'/study-evidence-storage',headers:{},body:JSON.stringify({operation:'credit-posting-dispatch',job_id:7,fingerprint:'c'.repeat(64)})};
  try{
    assert.equal((await cleanup.main(event,{})).statusCode,401);assert.equal(calls.length,0);
    event.headers['x-study-evidence-cleanup-token']=process.env.STUDY_EVIDENCE_CLEANUP_TOKEN;
    const result=await cleanup.main(event,{});assert.equal(result.statusCode,202);assert.deepEqual(JSON.parse(result.body),{accepted:true});
    assert.equal(calls.length,1);assert.equal(calls[0][0],'https://scf.tencentcloudapi.com/');
    assert.equal(JSON.parse(calls[0][1].body).InvocationType,'Event');
  }finally{
    globalThis.fetch=savedFetch;
    for(const key of Object.keys(process.env))if(!(key in previous))delete process.env[key];
    Object.assign(process.env,previous);
  }
});

test("boundedLimit keeps the scheduler batch within the service limit", () => {
  assert.equal(cleanup._test.boundedLimit(undefined), 500);
  assert.equal(cleanup._test.boundedLimit("17"), 17);
  assert.equal(cleanup._test.boundedLimit("9999"), 500);
  assert.equal(cleanup._test.boundedLimit("invalid"), 500);
});

test("COS signature fixture binds one private PUT and expires after 90 seconds", () => {
  const result = bridge.signedObjectRequest({ method: "PUT", key, content_type: "image/jpeg" }, runtime, 1700000000);
  const url = new URL(result.url);
  assert.equal(url.hostname, "7368-synthetic-123.cos.ap-shanghai.myqcloud.com");
  assert.equal(url.pathname, `/${key}`);
  assert.equal(url.searchParams.get("q-sign-time"), "1700000000;1700000090");
  assert.equal(url.searchParams.get("q-signature"), "2f2113fb150321c9311b8be15a5e5a145f09fc53");
  assert.equal(url.searchParams.get("q-header-list"), "content-type;host;if-none-match;x-cos-acl");
  assert.equal(url.searchParams.get("x-cos-security-token"), runtime.TENCENTCLOUD_SESSIONTOKEN);
  assert.deepEqual(result.headers, { "content-type": "image/jpeg", "x-cos-acl": "private", "if-none-match": "*" });
  assert.equal(result.expires_in, 90);
  assert.ok(!JSON.stringify(result).includes(runtime.TENCENTCLOUD_SECRETKEY));
});

test("storage signing rejects arbitrary resources, nonproduction keys and invalid media", () => {
  for (const payload of [
    { method: "PUT", key, content_type: "text/plain" },
    { method: "PUT", key, content_type: "image/png" },
    { method: "LIST", key },
    { method: "GET", key, bucket: "other" },
    { method: "GET", key: key.replace("production", "test") },
    { method: "GET", key: key.replace("/10/", "/13/") },
    { method: "DELETE", key: "study-meetings/production/../file.jpg" },
  ]) assert.throws(() => bridge.signedObjectRequest(payload, runtime));
  assert.throws(() => bridge.signedObjectRequest({ method: "GET", key }, { ...runtime, TENCENTCLOUD_SESSIONTOKEN: "" }));
  assert.throws(() => bridge.signedObjectRequest({ method: "GET", key }, { ...runtime, CLOUDBASE_STORAGE_REGION: "other" }));
  for (const method of ["GET", "HEAD", "DELETE"]) {
    const result = bridge.signedObjectRequest({ method, key }, runtime, 1700000000);
    assert.deepEqual(result.headers, {});
    assert.equal(new URL(result.url).searchParams.get("q-header-list"), "host");
  }
});

test("HTTP invocations require the server token and never trigger scheduled cleanup", async () => {
  const values = { ...runtime, STUDY_EVIDENCE_CLEANUP_TOKEN: "synthetic-server-token-".repeat(3),
    STUDY_EVIDENCE_STORAGE_BRIDGE_ENABLED: "true" };
  const previous = Object.fromEntries(Object.keys(values).map(k => [k, process.env[k]]));
  const previousFetch = globalThis.fetch;
  Object.assign(process.env, values);
  globalThis.fetch = () => { throw new Error("HTTP signing must not run cleanup"); };
  const event = { httpMethod: "POST", path: "/study-evidence-storage", headers: {},
    body: JSON.stringify({ method: "PUT", key, content_type: "image/jpeg" }) };
  try {
    assert.equal((await cleanup.main({ ...event, httpMethod: "GET" }, {})).statusCode, 405);
    assert.equal((await cleanup.main(event, {})).statusCode, 401);
    assert.equal((await cleanup.main({ ...event, headers: { "x-study-evidence-cleanup-token": "wrong" } }, {})).statusCode, 401);
    event.headers["X-Study-Evidence-Cleanup-Token"] = values.STUDY_EVIDENCE_CLEANUP_TOKEN;
    assert.equal((await cleanup.main({ ...event, path: "/other" }, {})).statusCode, 404);
    const result = await cleanup.main(event, {});
    assert.equal(result.statusCode, 200);
    assert.equal(result.headers["Cache-Control"], "private, no-store");
    assert.equal(JSON.parse(result.body).expires_in, 90);
    for (const path of ["/", ""]) {
      assert.equal((await cleanup.main({ ...event, path }, {})).statusCode, 200);
      assert.equal((await cleanup.main({ ...event, path, headers: {} }, {})).statusCode, 401);
    }
    assert.ok(!result.body.includes(values.STUDY_EVIDENCE_CLEANUP_TOKEN));
    assert.equal((await cleanup.main({ ...event, body: "x".repeat(2049) }, {})).statusCode, 400);
    assert.equal((await cleanup.main({ ...event, body: "not json" }, {})).statusCode, 503);
    const encoded = { ...event, body: Buffer.from(event.body).toString("base64"), isBase64Encoded: true };
    assert.equal((await cleanup.main(encoded, {})).statusCode, 200);
    delete process.env.TENCENTCLOUD_SECRETKEY;
    assert.deepEqual(JSON.parse((await cleanup.main(event, {})).body), { error: "storage unavailable" });
    process.env.STUDY_EVIDENCE_STORAGE_BRIDGE_ENABLED = "false";
    assert.equal((await cleanup.main(event, {})).statusCode, 503);
  } finally {
    globalThis.fetch = previousFetch;
    for (const [k, v] of Object.entries(previous)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  }
});

test("boundedTimeout falls back from invalid values and caps long requests", () => {
  assert.equal(cleanup._test.boundedTimeout(undefined), 30000);
  assert.equal(cleanup._test.boundedTimeout("invalid"), 30000);
  assert.equal(cleanup._test.boundedTimeout("500"), 30000);
  assert.equal(cleanup._test.boundedTimeout("45000"), 45000);
  assert.equal(cleanup._test.boundedTimeout("999999"), 120000);
});

test("cleanupEndpointUrl accepts only the exact HTTPS internal route", () => {
  const previous = process.env.PLATFORM_API_CLEANUP_URL;
  try {
    process.env.PLATFORM_API_CLEANUP_URL =
      "https://api.example.invalid/api/v1/internal/study-evidence/";
    assert.equal(
      cleanup._test.cleanupEndpointUrl(),
      "https://api.example.invalid/api/v1/internal/study-evidence",
    );
    process.env.PLATFORM_API_CLEANUP_URL = "http://api.example.invalid/api/v1/internal/study-evidence";
    assert.throws(() => cleanup._test.cleanupEndpointUrl(), /HTTPS cleanup endpoint/);
    process.env.PLATFORM_API_CLEANUP_URL = "https://api.example.invalid/health";
    assert.throws(() => cleanup._test.cleanupEndpointUrl(), /HTTPS cleanup endpoint/);
  } finally {
    if (previous === undefined) delete process.env.PLATFORM_API_CLEANUP_URL;
    else process.env.PLATFORM_API_CLEANUP_URL = previous;
  }
});

test("timer completes cleanup and monthly refresh through two fixed authenticated requests", async () => {
  const previousFetch = globalThis.fetch;
  const previous = {
    url: process.env.PLATFORM_API_CLEANUP_URL,
    token: process.env.STUDY_EVIDENCE_CLEANUP_TOKEN,
    limit: process.env.STUDY_EVIDENCE_CLEANUP_LIMIT,
  };
  process.env.PLATFORM_API_CLEANUP_URL = "https://api.example.invalid/api/v1/internal/study-evidence";
  process.env.STUDY_EVIDENCE_CLEANUP_TOKEN = "x".repeat(64);
  process.env.STUDY_EVIDENCE_CLEANUP_LIMIT = "23";
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({
      success: true,
      data: url.endsWith("/study-evidence") ? { candidates: 3, deleted: 2, errors: 0 } :
        { enabled: true, scanned: 19, updated: 0, failed: 0, repair_required: 0, review_required: 0, private_detail: "discard" },
    }), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const result = await cleanup.main({}, {});
    assert.equal(result.ok, true);
    assert.deepEqual(result, { candidates: 3, deleted: 2, errors: 0, ok: true,
      monthly: { ok: true, enabled: true, scanned: 19, updated: 0, failed: 0, repair_required: 0, review_required: 0 } });
    assert.deepEqual(requests.map(r => r.url), [process.env.PLATFORM_API_CLEANUP_URL,
      "https://api.example.invalid/api/v1/internal/learning-cycle-monthly"]);
    for (const request of requests) {
      assert.equal(request.options.method, "POST");
      assert.equal(request.options.redirect, "error");
      assert.equal(request.options.headers["x-study-evidence-cleanup-token"], process.env.STUDY_EVIDENCE_CLEANUP_TOKEN);
    }
    assert.deepEqual(JSON.parse(requests[0].options.body), { limit: 23 });
    assert.deepEqual(JSON.parse(requests[1].options.body), {});
    assert.ok(!JSON.stringify(result).includes("discard"));
  } finally {
    globalThis.fetch = previousFetch;
    for (const [key, value] of Object.entries({
      PLATFORM_API_CLEANUP_URL: previous.url,
      STUDY_EVIDENCE_CLEANUP_TOKEN: previous.token,
      STUDY_EVIDENCE_CLEANUP_LIMIT: previous.limit,
    })) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

for (const failingTask of ["study-evidence", "learning-cycle-monthly"]) {
  test(`failure in ${failingTask} preserves the other maintenance call and returns no upstream details`, async () => {
    const previousFetch = globalThis.fetch;
    const previousUrl = process.env.PLATFORM_API_CLEANUP_URL;
    const previousToken = process.env.STUDY_EVIDENCE_CLEANUP_TOKEN;
    process.env.PLATFORM_API_CLEANUP_URL = "https://api.example.invalid/api/v1/internal/study-evidence";
    process.env.STUDY_EVIDENCE_CLEANUP_TOKEN = "x".repeat(64);
    const requests = [];
    globalThis.fetch = async url => {
      requests.push(url);
      if (url.endsWith(`/${failingTask}`)) throw new Error("secret-looking upstream details");
      return new Response(JSON.stringify({ success: true, data: url.endsWith("/study-evidence") ?
        { candidates: 0, deleted: 0, errors: 0 } :
        { enabled: true, scanned: 19, updated: 0, failed: 0, repair_required: 0, review_required: 0 } }));
    };
    try {
      const result = await cleanup.main({}, {});
      assert.equal(requests.length, 2);
      assert.equal(result.ok, false);
      assert.equal(result.errors, failingTask === "study-evidence" ? 1 : 0);
      assert.equal(result.monthly.ok, failingTask === "study-evidence");
      assert.ok(!JSON.stringify(result).includes("secret-looking"));
    } finally {
      globalThis.fetch = previousFetch;
      if (previousUrl === undefined) delete process.env.PLATFORM_API_CLEANUP_URL; else process.env.PLATFORM_API_CLEANUP_URL = previousUrl;
      if (previousToken === undefined) delete process.env.STUDY_EVIDENCE_CLEANUP_TOKEN; else process.env.STUDY_EVIDENCE_CLEANUP_TOKEN = previousToken;
    }
  });
}

test("midnight roster trigger is isolated from cleanup, dates and credit progression", async () => {
  const env={PLATFORM_API_CLEANUP_URL:"https://synthetic.example/api/v1/internal/study-evidence",STUDY_EVIDENCE_CLEANUP_TOKEN:"synthetic-roster-token-".repeat(3)};
  const previous=Object.fromEntries(Object.keys(env).map(key=>[key,process.env[key]]));const priorFetch=globalThis.fetch;const calls=[];
  Object.assign(process.env,env);
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,text:async()=>JSON.stringify({success:true,data:{enabled:true,updated:1,failed:0,state:"FROZEN"}})};};
  try {
    const result=await cleanup.main({Type:"Timer",TriggerName:"studyMeetingRosterDaily",Time:"synthetic-cannot-pick-date",Message:"class=anything"},{});
    assert.equal(result.ok,true);assert.equal(result.roster.state,"FROZEN");assert.equal(calls.length,1);
    assert.match(calls[0].url,/\/internal\/study-meeting-roster-monthly$/);assert.deepEqual(JSON.parse(calls[0].options.body),{});
    assert.ok(!JSON.stringify(result).includes(env.STUDY_EVIDENCE_CLEANUP_TOKEN));
  } finally {globalThis.fetch=priorFetch;for(const [key,value] of Object.entries(previous)){if(value===undefined)delete process.env[key];else process.env[key]=value;}}
});
