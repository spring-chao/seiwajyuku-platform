const {request}=require('../../utils/request');
const {beginPrivateRequest,hidePrivatePage,showPrivatePage}=require('../../utils/page-session');
const empty=()=>({events:[],rows:[],eventId:'',busy:false,error:'',canManage:false,estimatedCount:0,confirmedCount:0,deadline:'',dinnerLocked:false,dinnerCount:0,dinnerAdded:0,dinnerRemoved:0});
Page({
 data:empty(),
 onShow(){showPrivatePage(this);return this.loadEvents();},
 onHide(){hidePrivatePage(this,empty());},onUnload(){this.onHide();},
 async loadEvents(){
  const current=beginPrivateRequest(this,'events');this.setData({busy:true,error:''});
  try{const result=await request('/api/v1/wechat/checkin/konpa/events',{auth:true});if(current())this.setData({events:result.data.events||[]});}
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
    this.setData({rows:(data.rows||[]).map(r=>({...r,statusLabel:({PENDING:'待确认',UNCONFIRMED:'未登记',REVIEWED:'已调整待确认',CONFIRMED:'已确认参加',ABSENT:'已确认未参加'})[r.status]||r.status})),canManage:data.can_manage===true,
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
  if(this.data.busy||!this.data.canManage)return;
  const row=this.data.rows.find(r=>r.registration_id===e.currentTarget.dataset.id);if(!row)return;
  const action=e.currentTarget.dataset.action;
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
