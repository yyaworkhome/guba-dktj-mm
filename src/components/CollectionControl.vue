<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {request} from '../api';
const props=defineProps<{instrumentId:string;name:string;content:'titles'|'body';username:string}>();
const emit=defineEmits<{login:[];collected:[]}>();
type State={enabled:boolean;locked:boolean;next_allowed_at:string|null};
type Run={id:number;status:string;finished_at:string;details:Record<string,{accepted?:number;new_revisions?:number;error?:string;bars?:number}>};
const state=ref<State|null>(null),busy=ref(false),error=ref(''),result=ref<Run|null>(null),clock=ref(Date.now());
let version=0,disposed=false,timer:ReturnType<typeof setInterval>;
const remaining=computed(()=>state.value?.next_allowed_at?Math.max(0,Math.ceil((Date.parse(state.value.next_allowed_at)-clock.value)/1000)):0);
const time=(s:string)=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
async function load(){const id=++version;try{const r=await request<State>(`/live/collection?instrument_id=${encodeURIComponent(props.instrumentId)}`);if(id===version&&!disposed)state.value=r;}catch(e){if(id===version&&!disposed)error.value=e instanceof Error?e.message:'采集状态读取失败';}}
async function collect(){if(!props.username){emit('login');return;}const iid=props.instrumentId,content=props.content;busy.value=true;error.value='';result.value=null;
  try{const r=await request<Run>('/live/collection',{method:'POST',body:JSON.stringify({instrument_id:iid,content,limit:3})},150000);if(!disposed&&iid===props.instrumentId&&content===props.content){result.value=r;emit('collected');}}
  catch(e){if(!disposed)error.value=(e instanceof Error?e.message:'采集失败')+' 若请求超时，服务端可能仍在运行；请先刷新状态，勿反复提交。';}
  finally{if(!disposed){busy.value=false;await load();}}}
watch(()=>[props.instrumentId,props.content],()=>{result.value=null;error.value='';state.value=null;load();});
onMounted(()=>{load();timer=setInterval(()=>clock.value=Date.now(),1000);});onUnmounted(()=>{disposed=true;++version;clearInterval(timer);});
</script>
<template>
  <section class="collection-control">
    <div class="panel-heading"><h3>更新真实样本</h3><button class="secondary compact" :disabled="busy" @click="load">刷新采集状态</button></div>
    <p>当前对象：{{name}} {{instrumentId}} · {{content==='titles'?'标题':'正文（最多3条详情）'}}。执行后保存样本与历史快照，再更新下方分析；同一股票两种口径共用10分钟间隔。</p>
    <p v-if="state&&!state.enabled" class="subtle">本服务未开放页面采集。标准本机启动脚本会启用；远程部署由管理员配置服务端采集任务。仍可查询已保存样本。</p>
    <template v-else-if="state"><button class="primary" :disabled="busy||state.locked||remaining>0" @click="collect">{{busy?'正在采集，请等待…':remaining>0?`可再次采集：${Math.floor(remaining/60)}分${remaining%60}秒`:state.locked?'已有采集任务或遗留锁':'采集这只股票'}}</button><p v-if="busy" role="status">正在请求公开股吧和日线，通常需要数秒，正文可能更久。离开页面不会撤销已发起的采集。</p><p v-if="!username" class="subtle">采集前需登录本系统账号，无需东方财富账号密码。</p></template>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p>
    <div v-if="result" class="collection-result" role="status"><strong>{{result.status==='success'?'采集完成':result.status==='partial'?'部分采集成功':'采集失败'}} · 运行 #{{result.id}}</strong><p>纳入 {{result.details.posts?.accepted??0}} 条 · 新增修订 {{result.details.posts?.new_revisions??0}} 条 · 保存日线 {{result.details.quotes?.bars??0}} 条</p><p v-if="result.details.posts?.error">股吧：{{result.details.posts.error}}</p><p v-if="result.details.quotes?.error">行情：{{result.details.quotes.error}}</p><p>结束时间 {{time(result.finished_at)}}；查询窗口无帖子时仍会显示缺失，不补模拟数据。</p></div>
  </section>
</template>
<style scoped>.collection-control{border-top:1px solid var(--border);margin-top:20px;padding-top:20px}.collection-control p{font-size:12px;line-height:1.9;color:var(--muted);margin:10px 0}.collection-control h3{font-size:15px}.collection-result{border:1px solid var(--border);border-radius:6px;padding:14px;margin-top:14px}.collection-result strong{font-size:13px;color:var(--accent)}@media(max-width:650px){.panel-heading{flex-wrap:wrap;gap:12px}.collection-control>.primary{width:100%}}</style>
