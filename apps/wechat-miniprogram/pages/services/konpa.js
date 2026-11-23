const {request}=require('../../utils/request');
const {beginPrivateRequest,hidePrivatePage,showPrivatePage}=require('../../utils/page-session');
const STATUS_LABELS = {
 PENDING: '待确认', UNCONFIRMED: '未登记', REVIEWED: '已调整待确认',
 CONFIRMED: '已确认参加', ABSENT: '已确认未参加'
};

const CONFIRMATION_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

function shanghaiTimestamp(value, fallback) {
 const text = String(value || fallback || '').trim().replace(' ', 'T');
 return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : text + '+08:00');
}

function currentEvents(events, now) {
 const byClass = new Map();
 for (const event of events) {
  if (!event.class_org_unit_id || !event.event_id) continue;
  const date = /^\d{4}-\d{2}-\d{2}$/.test(event.event_date || '') ? event.event_date : '';
  const start = shanghaiTimestamp(event.scheduled_start_at, date && date + 'T00:00:00');
  const end = shanghaiTimestamp(event.scheduled_end_at, date && date + 'T23:59:59');
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start || end + CONFIRMATION_WINDOW_MS <= now) continue;

  const started = start <= now;
  const previous = byClass.get(event.class_org_unit_id);
  if (!previous || (started && (!previous.started || start > previous.start)) ||
    (!started && !previous.started && start < previous.start)) {
   byClass.set(event.class_org_unit_id, { event, start, started });
  }
 }
 return [...byClass.values()].sort((a, b) => b.start - a.start).map(item => item.event);
}

function participantRows(rows) {
 const pending = [];
 const confirmed = [];
 const declined = [];
 const unselected = [];
 for (const row of rows) {
  const attendingNow = row.latest_choice === true;
  const isConfirmed = row.status === 'CONFIRMED';
  const choice = typeof row.latest_choice === 'boolean' ? row.latest_choice : null;
  const section = attendingNow ? (isConfirmed ? '已确认参加' : '参加意向待确认')
   : choice === false ? '选择不参加' : '未选择';
  const item = { ...row, attendingNow, section, statusLabel: STATUS_LABELS[row.status] || row.status,
   selectedYes: choice === true, selectedNo: choice === false,
   choiceLabel: choice === null ? '当前未选择' : choice ? '当前选择：参加' : '当前选择：不参加',
   confirmLabel: row.choice ? '确认参加' : '确认不参加',
   canConfirm: typeof row.choice === 'boolean' && !['CONFIRMED','ABSENT'].includes(row.status) };
  if (!attendingNow) (choice === false ? declined : unselected).push(item);
  else if (isConfirmed) confirmed.push(item);
  else pending.push(item);
 }
 return [...pending, ...confirmed, ...declined, ...unselected];
}

const empty=()=>({events:[],rows:[],eventId:'',busy:false,error:'',canManage:false,estimatedCount:0,confirmedCount:0,deadline:'',dinnerLocked:false,dinnerCount:0,dinnerAdded:0,dinnerRemoved:0});
Page({
 data:empty(),
 onShow(){showPrivatePage(this);return this.loadEvents();},
 onHide(){hidePrivatePage(this,empty());},onUnload(){this.onHide();},
 async loadEvents(){
  const current=beginPrivateRequest(this,'events');this.setData({busy:true,error:''});
  try{const result=await request('/api/v1/wechat/checkin/konpa/events',{auth:true});if(current())this.setData({events:currentEvents(result.data.events||[],Date.now())});}
  catch(e){if(current())this.setData({error:e.message||'名单暂时无法加载'});}
  finally{if(current())this.setData({busy:false});}
 },
 selectEvent(e){if(this.data.busy)return;this.setData({eventId:e.currentTarget.dataset.id,rows:[]});return this.refresh();},
 async refresh(){
  const current=beginPrivateRequest(this,'review');const eventId=this.data.eventId;
  if(!eventId)return;
  this.setData({busy:true,error:''});
  try{const result=await request('/api/v1/wechat/checkin/konpa/'+encodeURIComponent(eventId),{auth:true});
   if(current()&&eventId===this.data.eventId){const data=result.data;
    this.setData({rows:participantRows(data.rows || []),canManage:data.can_manage===true,
     estimatedCount:data.estimated_count,confirmedCount:data.confirmed_count,
     dinnerLocked:Boolean(data.dinner_locked_at),dinnerCount:data.dinner_locked_count,dinnerAdded:data.dinner_added,dinnerRemoved:data.dinner_removed,
     deadline:new Date(Date.parse(data.auto_confirm_at)+8*60*60*1000).toISOString().slice(0,16).replace('T',' ')});}}
  catch(e){if(current())this.setData({error:e.message||'名单暂时无法加载'});}
  finally{if(current())this.setData({busy:false});}
 },
 async lockDinner(){
  if(this.data.busy||!this.data.canManage||this.data.dinnerLocked)return;
  const current=beginPrivateRequest(this,'dinner');this.setData({busy:true,error:''});
  try{await request('/api/v1/wechat/checkin/konpa-dinner/'+encodeURIComponent(this.data.eventId),{method:'POST',auth:true,data:{}});if(current())await this.refresh();}
  catch(e){if(current())this.setData({error:e.message||'锁定未完成，请刷新核对'});}
  finally{if(current())this.setData({busy:false});}
 },
 async update(e){
  if(this.data.busy)return;
  const row=this.data.rows.find(r=>r.registration_id===e.currentTarget.dataset.id);if(!row)return;
  const action=e.currentTarget.dataset.action;
  if(action==='set' ? (!this.data.canManage && !row.can_set_intent)
   : action!=='confirm' || !this.data.canManage || !row.canConfirm)return;
  const payload={registration_id:row.registration_id,expected_revision:row.revision};
  if(action==='set')payload.choice=e.currentTarget.dataset.choice==='yes';
  const current=beginPrivateRequest(this,'update');const eventId=this.data.eventId;
  this.setData({busy:true,error:''});
  try{await request('/api/v1/wechat/checkin/konpa/'+encodeURIComponent(eventId)+'/'+action,{method:'POST',auth:true,data:payload});
   if(current())await this.refresh();}
  catch(error){if(current())this.setData({error:error.message||'处理未完成，请刷新核对'});}
  finally{if(current())this.setData({busy:false});}
 }
});
