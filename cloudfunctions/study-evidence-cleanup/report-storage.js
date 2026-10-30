"use strict";
const {createHash,createHmac}=require('node:crypto');
const KEY=/^monthly-core\/production\/20[0-9]{2}-(0[1-9]|1[0-2])\/[a-f0-9]{64}\/(state\.json|[a-f0-9]{32}\.xlsx)$/;
const enc=v=>encodeURIComponent(v).replace(/[!'()*]/g,c=>'%'+c.charCodeAt(0).toString(16).toUpperCase());
const canonical=v=>Object.keys(v).sort().map(k=>`${enc(k)}=${enc(v[k])}`).join('&');
const hmac=(key,value)=>createHmac('sha1',key).update(value).digest('hex');
function signedObjectRequest(p,env=process.env,now=Math.floor(Date.now()/1000)) {
  if(!p||Object.keys(p).some(k=>!['operation','method','key','content_type','if_match'].includes(k))||p.operation!=='monthly-core-object'||!KEY.test(p.key||'')||!['GET','PUT','HEAD'].includes(p.method))throw new Error('invalid report object');
  const bucket=env.CLOUDBASE_STORAGE_BUCKET||'',region=env.CLOUDBASE_STORAGE_REGION||'';
  if(!/^[a-z0-9-]+-[0-9]+$/.test(bucket)||region!=='ap-shanghai')throw new Error('storage unavailable');
  const id=env.TENCENTCLOUD_SECRETID,secret=env.TENCENTCLOUD_SECRETKEY,token=env.TENCENTCLOUD_SESSIONTOKEN;
  if(!id||!secret||!token)throw new Error('runtime unavailable');
  const host=`${bucket}.cos.${region}.myqcloud.com`,headers={host};
  if(p.method==='PUT') {
    const type=p.key.endsWith('.json')?'application/json':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    if(p.content_type!==type||(p.if_match&&!/^"[a-f0-9]{32}(?:-[0-9]+)?"$/i.test(p.if_match))||p.if_match&&!p.key.endsWith('state.json'))throw new Error('invalid report condition');
    Object.assign(headers,{'content-type':type,'x-cos-acl':'private',...(p.if_match?{'if-match':p.if_match}:{'if-none-match':'*'})});
  } else if(p.if_match||p.content_type)throw new Error('invalid report request');
  const query={'x-cos-security-token':token},time=`${now};${now+90}`;
  const http=`${p.method.toLowerCase()}\n/${p.key}\n${canonical(query)}\n${canonical(headers)}\n`;
  const signature=hmac(hmac(secret,time),`sha1\n${time}\n${createHash('sha1').update(http).digest('hex')}\n`);
  Object.assign(query,{'q-sign-algorithm':'sha1','q-ak':id,'q-sign-time':time,'q-key-time':time,'q-header-list':Object.keys(headers).sort().join(';'),'q-url-param-list':'x-cos-security-token','q-signature':signature});
  delete headers.host;
  return {url:`https://${host}/${p.key}?${canonical(query)}`,headers,expires_in:90};
}
module.exports={signedObjectRequest};
