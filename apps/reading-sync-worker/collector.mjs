import {createHash} from 'node:crypto';
import {readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';

export const origin='https://study.daoshenghefu.com';
const management=origin+'/admin/learnshs/readingmeeting/readingmeetingmanagement';
const byName=(page,name)=>page.getByRole('button',{name,exact:true});

export async function login(page,{username,password}) {
  if(!username||!password)throw Error('CREDENTIALS_MISSING');
  await page.goto(management,{waitUntil:'domcontentloaded',timeout:45000});
  await page.locator('input').first().waitFor({timeout:30000});
  if(new URL(page.url()).origin!==origin)throw Error('UNEXPECTED_ORIGIN');
  if(new URL(page.url()).pathname.includes('/login')){
    await page.getByPlaceholder('请输入账号',{exact:true}).fill(username);
    await page.getByPlaceholder('请输入密码',{exact:true}).fill(password);
    await byName(page,'登录').click();
    await page.waitForURL(u=>u.origin===origin&&!u.pathname.includes('/login'),{timeout:30000});
    await page.goto(management,{waitUntil:'domcontentloaded',timeout:45000});
  }
  await page.getByRole('textbox',{name:'读书会名称：',exact:true}).waitFor({timeout:30000});
}

async function downloadInventory(page) {
  const loaded=page.waitForResponse(r=>new URL(r.url()).pathname==='/file/ExportRecord/list',{timeout:30000});
  await page.locator('i.ri-download-line').click();
  await loaded;
  const dialog=page.getByRole('dialog');
  await dialog.waitFor();
  // Waiting for the total makes an initially empty loading table observable.
  await dialog.getByText(/共\s*\d+\s*条/).waitFor();
  const text=await dialog.innerText();
  return {dialog,total:Number(text.match(/共\s*(\d+)\s*条/)[1])};
}

export async function collect(page,{sourceTitle,searchTerm=sourceTitle,interval,directory,report=()=>{}}) {
  if(!sourceTitle||/补课|补卡|塾友之家|工作人员/.test(sourceTitle))throw Error('UNAPPROVED_MEETING');
  const initialized=page.waitForResponse(r=>new URL(r.url()).pathname==='/reading/bcBook/getBcBookPage',{timeout:30000});
  await page.goto(management,{waitUntil:'domcontentloaded',timeout:45000});
  await initialized;
  report('search');
  await page.getByRole('textbox',{name:'读书会名称：',exact:true}).fill(searchTerm);
  const queried=page.waitForResponse(r=>{
    if(new URL(r.url()).pathname!=='/reading/bcBook/getBcBookPage')return false;
    const raw=r.request().postData()||new URL(r.url()).search;
    try{return decodeURIComponent(raw).includes(searchTerm);}catch{return raw.includes(searchTerm);}
  },{timeout:30000});
  await byName(page,'查询').click();
  await queried;
  const exact=page.getByRole('cell',{name:sourceTitle,exact:true});
  await exact.first().waitFor({timeout:30000});
  if(await exact.count()!==1){const error=Error('AMBIGUOUS_MEETING');error.probe=await exact.evaluateAll(es=>es.map(e=>({text:e.textContent,row:e.closest('tr')?.textContent})));throw error;}
  report('open'); await exact.getByText(sourceTitle,{exact:true}).click();
  await page.getByRole('heading',{name:sourceTitle,exact:true}).waitFor({timeout:30000});
  report('statistics'); await page.getByRole('tab',{name:'打卡统计',exact:true}).click();
  await page.getByRole('tab',{name:'自定义',exact:true}).click();
  const start=page.getByRole('textbox',{name:'开始时间',exact:true});
  const end=page.getByRole('textbox',{name:'结束时间',exact:true});
  await start.fill(interval.start); await start.press('Enter');
  await end.fill(interval.end); await end.press('Enter');
  await page.getByRole('heading',{name:'全部完成率',exact:true}).click();
  const statistics=page.waitForResponse(r=>new URL(r.url()).pathname==='/reading/bcBookStat/list',{timeout:30000});
  await byName(page,'查询').first().click();
  const queriedStatistics=await statistics;
  const rangeRequest=queriedStatistics.request().postData()||new URL(queriedStatistics.url()).search;
  let range;
  try{range=JSON.parse(rangeRequest);}catch{range=Object.fromEntries(new URLSearchParams(rangeRequest));}
  if(Date.parse(range.startTime)!==Date.parse(interval.start+'T00:00:00.000+08:00')||Date.parse(range.endTime)!==Date.parse(interval.end+'T23:59:59.999+08:00'))throw Error('QUERY_RANGE_UNCONFIRMED');
  const statisticsData=await queriedStatistics.json();
  const total=statisticsData.data?.total;
  if(await start.inputValue()!==interval.start||await end.inputValue()!==interval.end)throw Error('QUERY_RANGE_CHANGED');
  const sourceMeetingId=new URL(page.url()).searchParams.get('id');
  if(!sourceMeetingId)throw Error('MEETING_ID_MISSING');
  report('export');
  const before=await downloadInventory(page);
  await before.dialog.getByRole('button',{name:'确定',exact:true}).click();
  await page.locator('button.export-btn').click();
  const exported=page.waitForResponse(r=>new URL(r.url()).pathname==='/reading/bcBookStat/exportData',{timeout:30000});
  await page.getByRole('dialog',{name:'是否确认导出？'}).getByRole('button',{name:'确定',exact:true}).click();
  await exported;
  const after=await downloadInventory(page);
  if(after.total!==before.total+1)throw Error('EXPORT_TASK_ASSOCIATION_UNCERTAIN');
  const row=after.dialog.getByRole('row').filter({hasText:'读书会打卡统计列表'}).first();
  await row.getByText('下载',{exact:true}).waitFor({timeout:60000});
  const exportRecord=await row.innerText();
  report('download');
  const received=page.waitForEvent('download',{timeout:60000}).then(d=>({download:d}),()=>({error:true}));
  await row.getByText('下载',{exact:true}).click();
  const result=await received;
  if(result.error)throw Error('DOWNLOAD_TIMEOUT');
  await mkdir(directory,{recursive:true});
  const file=join(directory,`${sourceMeetingId}_${interval.start}_${interval.end}.xlsx`);
  await result.download.saveAs(file);
  const content=await readFile(file);
  if(content.length<4||content[0]!==0x50||content[1]!==0x4b)throw Error('INVALID_WORKBOOK');
  await after.dialog.getByRole('button',{name:'确定',exact:true}).click();
  return {file,sourceMeetingId,sourceTitle,interval,fileSha256:createHash('sha256').update(content).digest('hex'),expectedRows:Number.isSafeInteger(total)&&total>=0?total:null,exportRecord};
}
