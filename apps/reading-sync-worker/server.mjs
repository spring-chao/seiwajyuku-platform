import http from 'node:http';
import {createHmac,createHash,timingSafeEqual} from 'node:crypto';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {chromium} from 'playwright';
import {login,collect} from './collector.mjs';

let active=false;
const equal=(a,b)=>Buffer.byteLength(a)===Buffer.byteLength(b)&&timingSafeEqual(Buffer.from(a),Buffer.from(b));
const send=(res,status,value)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};

http.createServer(async(req,res)=>{
 if(req.method==='GET'&&req.url==='/health/live')return send(res,200,{status:'ok',commit:process.env.APP_GIT_SHA||'unknown'});
 if(req.method!=='POST'||req.url!=='/collect')return send(res,404,{error:'NOT_FOUND'});
 let browser,directory,ownsActive=false;
 try{
  const key=process.env.READING_WORKER_SIGNING_KEY||'';
  const stamp=req.headers['x-reading-sync-time']||'';
  const signature=req.headers['x-reading-sync-signature']||'';
  if(key.length<32||!/^\d+$/.test(stamp)||Math.abs(Date.now()/1000-Number(stamp))>120||signature.length!==64)return send(res,401,{error:'UNAUTHORIZED'});
  const chunks=[];let length=0;
  for await(const part of req){length+=part.length;if(length>16384)return send(res,413,{error:'TOO_LARGE'});chunks.push(part);}
  const raw=Buffer.concat(chunks);
  const material=['POST','/collect',stamp,createHash('sha256').update(raw).digest('hex')].join('\n');
  if(!equal(createHmac('sha256',key).update(material).digest('hex'),signature))return send(res,401,{error:'UNAUTHORIZED'});
  const input=JSON.parse(raw.toString());
  const classes=JSON.parse(process.env.READING_SYNC_CLASS_MAP||'[]');
  if(classes.length!==22)return send(res,503,{error:'CONFIGURATION_INCOMPLETE'});
  const target=classes.find(c=>c.class_id===input.class_id&&c.source_meeting_id===input.source_meeting_id);
  if(!target)return send(res,403,{error:'OUT_OF_SCOPE'});
  if(!/^\d{4}-\d{2}-\d{2}$/.test(input.start)||!/^\d{4}-\d{2}-\d{2}$/.test(input.end)||input.start>input.end||input.start.slice(0,7)!==input.end.slice(0,7)||new Date(input.end)-new Date(input.start)>6*86400000)return send(res,400,{error:'INVALID_INTERVAL'});
  if(active)return send(res,409,{error:'WORKER_BUSY'});
  active=true;ownsActive=true;
  directory=await mkdtemp(join(tmpdir(),'reading-'));
  browser=await chromium.launch({headless:true});
  const page=await(await browser.newContext({acceptDownloads:true})).newPage();
  // Bound one request below the documented CloudRun HTTP timeout. A timed-out
  // export is never reported as complete; the caller records and retries it.
  page.setDefaultTimeout(10000);
  const result=await Promise.race([
   (async()=>{
    await login(page,{username:process.env.READING_PLATFORM_USERNAME,password:process.env.READING_PLATFORM_PASSWORD});
    const result=await collect(page,{sourceTitle:target.source_title,searchTerm:target.search_term||target.source_title,interval:{start:input.start,end:input.end},directory});
    if(result.sourceMeetingId!==target.source_meeting_id)throw Error('SOURCE_CHANGED');
    const raw=await readFile(result.file);
    return{class_id:target.class_id,source_meeting_id:result.sourceMeetingId,start:input.start,end:input.end,file_sha256:result.fileSha256,workbook_base64:raw.toString('base64')};
   })(),
   new Promise((_,reject)=>{const timer=setTimeout(()=>reject(Error('REQUEST_BUDGET')),50000);timer.unref();})
  ]);
  send(res,200,{success:true,data:result});
 }catch{
  // Never log request bodies, browser errors, credentials or workbook data.
  if(!res.headersSent)send(res,503,{error:'COLLECTION_NOT_VERIFIED'});
 }finally{
  if(browser)await browser.close().catch(()=>{});
  if(directory)await rm(directory,{recursive:true,force:true}).catch(()=>{});
  if(ownsActive)active=false;
 }
}).listen(Number(process.env.PORT||8000),'0.0.0.0');
