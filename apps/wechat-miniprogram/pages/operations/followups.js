const { request } = require("../../utils/request");
Page({
  data:{ loading:true, tasks:[], selectedTaskId:null, facts:"", nextAction:"", errorMessage:"", saving:false },
  onLoad(query){ if(query.task_id) this.setData({selectedTaskId:Number(query.task_id)}); },
  onShow(){this.load();},
  async load(){this.setData({loading:true,errorMessage:""});try{const response=await request("/api/v1/wechat/operations/followup-tasks",{auth:true});this.setData({tasks:response.data||[]});}catch(error){this.setData({errorMessage:error.message||"关怀任务暂时无法加载"});}finally{this.setData({loading:false});}},
  choose(event){this.setData({selectedTaskId:Number(event.currentTarget.dataset.id)});},
  input(event){this.setData({[event.currentTarget.dataset.field]:event.detail.value});},
  async submit(){const taskId=this.data.selectedTaskId;const facts=(this.data.facts||"").trim();if(!taskId||!facts){wx.showToast({title:"请选择可记录的任务并填写客观事实",icon:"none"});return;}this.setData({saving:true});try{await request(`/api/v1/wechat/operations/followup-tasks/${taskId}/records`,{method:"POST",auth:true,data:{channel:"WECHAT",contacted_at:new Date().toISOString(),outcome_code:"COMPLETED",objective_facts:facts,next_action:(this.data.nextAction||"").trim()||null}});wx.showToast({title:"跟进已记录",icon:"success"});this.setData({facts:"",nextAction:""});await this.load();}catch(error){wx.showToast({title:error.message||"记录失败",icon:"none"});}finally{this.setData({saving:false});}}
});
