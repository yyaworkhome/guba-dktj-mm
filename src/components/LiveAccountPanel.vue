<script setup lang="ts">
import {ref, watch, onMounted, onUnmounted} from 'vue';
import {request, post} from '../api';
import StockSearch,{type SearchStock} from './StockSearch.vue';
type Stock = {id:string; name:string; code:string};
type Sample = {opinion_score:number|null;effective_posts:number;authors:number;stale:boolean;
  last_post_observation:string|null;percentages:Record<string,number|null>;reasons:string[]};
type WatchRow = Stock & {supported:boolean;analysis:Sample|null};
type Rule = {id:number;instrument_id:string;name:string;content:string;days:number;metric:string;threshold:number;
  value:number|null;raw_value:number|null;ready:boolean;triggered:boolean;reasons:string[];last_observation:string|null};
const props=defineProps<{username:string;view:'alerts'|'portfolio'}>();
const emit=defineEmits<{login:[];select:[id:string]}>();
const stocks=ref<Stock[]>([]),selected=ref('XSHE:300059'),content=ref('titles'),days=ref(3);
const metric=ref('bullish_pct'),threshold=ref(80),rules=ref<Rule[]>([]),items=ref<WatchRow[]>([]);
const busy=ref(false),saving=ref(false),error=ref(''),note=ref('');
const names:Record<string,string>={bullish_pct:'看多占比',bearish_pct:'看空占比',opinion_score:'舆论分'};
let version=0,disposed=false;
const time=(s:string|null)=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚无';
function selectStock(s:SearchStock){if(!stocks.value.some(i=>i.id===s.id))stocks.value.push(s);selected.value=s.id;}
async function load(){
  const current=++version;rules.value=[];items.value=[];error.value='';
  if(!props.username){busy.value=false;return;}
  busy.value=true;
  try{
    if(props.view==='alerts'){
      const rows=await request<Rule[]>('/live/alerts');
      if(current===version&&!disposed)rules.value=rows;
    }else{
      const data=await request<{items:WatchRow[]}>(`/live/watchlist?days=${days.value}&content=${content.value}`);
      if(current===version&&!disposed)items.value=data.items;
    }
  }catch(e){if(current===version&&!disposed)error.value=e instanceof Error?e.message:'读取失败';}
  finally{if(current===version&&!disposed)busy.value=false;}
}
async function save(){
  if(!props.username){emit('login');return;}
  const owner=props.username;saving.value=true;error.value='';note.value='';
  try{
    if(props.view==='alerts')await post('/live/alerts',{instrument_id:selected.value,content:content.value,days:days.value,metric:metric.value,threshold:threshold.value});
    else await post('/live/watchlist',{instrument_id:selected.value},'PUT');
    if(owner!==props.username||disposed)return;
    note.value=props.view==='alerts'?'真实预警已保存；仅站内查询状态，不发送推送。':'已加入统一账号自选。';
    await load();
  }catch(e){if(owner===props.username&&!disposed)error.value=e instanceof Error?e.message:'保存失败';}
  finally{if(!disposed)saving.value=false;}
}
async function remove(id:string|number){
  const owner=props.username;saving.value=true;error.value='';note.value='';
  try{
    await request(props.view==='alerts'?`/live/alerts/${id}`:`/watchlist?instrument_id=${encodeURIComponent(id)}`,{method:'DELETE'});
    if(owner===props.username&&!disposed)await load();
  }catch(e){if(owner===props.username&&!disposed)error.value=e instanceof Error?e.message:'移除失败';}
  finally{if(!disposed)saving.value=false;}
}
watch(()=>[props.username,props.view,days.value,content.value],()=>{note.value='';load();});
onMounted(async()=>{try{stocks.value=await request<Stock[]>('/live/instruments');}catch(e){error.value=e instanceof Error?e.message:'目录读取失败';}await load();});
onUnmounted(()=>{disposed=true;++version;});
</script>
<template>
  <section class="panel real-account">
    <div class="panel-heading"><h2>{{view==='alerts'?'真实采样预警':'真实自选对比'}}</h2><button class="secondary" :disabled="busy||saving" @click="load">刷新状态</button></div>
    <p class="subtle section-note">{{view==='alerts'?'按当前保存的标题或正文样本判断阈值。有效样本≥20、作者≥10、方向样本≥10且观察未超过24小时才判断触发；不足时显示待积累。':'同一账号自选共享。可搜索任意沪深北A股加入；无样本时进入个股页采集。不支持的对象列出原因，不借用模拟分数。'}} 状态刷新只读取已保存样本，不发送后台通知。</p>
    <StockSearch v-if="username" @select="selectStock" />
    <p v-if="!username" class="empty-note">登录本系统账号后保存和查询。<button class="text-button" @click="emit('login')">登录 / 注册</button></p>
    <form v-else class="real-controls" @submit.prevent="save">
      <label>股票<select v-model="selected" required aria-label="真实账号股票"><option v-for="s in stocks" :key="s.id" :value="s.id">{{s.name}} {{s.code}}</option></select></label>
      <label>内容<select v-model="content" aria-label="真实账号内容"><option value="titles">标题采样</option><option value="body">正文采样</option></select></label>
      <label>窗口<select v-model="days" aria-label="真实账号窗口"><option v-for="n in [1,3,5,10,20]" :key="n" :value="n">近{{n}}自然日</option></select></label>
      <template v-if="view==='alerts'"><label>指标<select v-model="metric" aria-label="真实预警指标"><option v-for="(name,key) in names" :key="key" :value="key">{{name}}</option></select></label><label>大于或等于<input v-model.number="threshold" type="number" min="0" max="100" step="0.1" required aria-label="真实预警阈值" /></label></template>
      <button class="primary" :disabled="saving||!stocks.length">{{saving?'保存中…':view==='alerts'?'保存真实预警':'添加到自选'}}</button>
    </form>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p><p v-if="note" role="status" class="section-note">{{note}}</p><p v-if="busy" role="status" class="section-note">正在读取账号数据…</p>
    <template v-if="username&&!busy">
      <template v-if="view==='alerts'"><p v-if="!rules.length" class="empty-note">尚无真实预警条件，可在上方保存。</p><article v-for="r in rules" :key="r.id" class="real-row"><div><button class="text-button" @click="emit('select',r.instrument_id)">{{r.name}}</button><p>{{r.content==='body'?'正文':'标题'}} · 近{{r.days}}自然日 · {{names[r.metric]}} ≥ {{r.threshold}}</p><strong>{{!r.ready?'待积累 / 待更新':r.triggered?'已触发':'未触发'}}</strong><p v-if="r.ready">当前值 {{r.value}}</p><p v-else>{{r.reasons.join('；')}}</p><small>最近观察 {{time(r.last_observation)}}</small></div><button class="secondary" :disabled="saving" :aria-label="`删除真实预警${r.id}`" @click="remove(r.id)">删除</button></article></template>
      <template v-else><p v-if="!items.length" class="empty-note">尚无自选，可选择上方股票添加。</p><article v-for="r in items" :key="r.id" class="real-row"><div><button class="text-button" :disabled="!r.supported" @click="emit('select',r.id)">{{r.name}} {{r.code}}</button><template v-if="r.analysis"><p>舆论分 {{r.analysis.opinion_score??'—'}} · 有效样本 {{r.analysis.effective_posts}} · 作者 {{r.analysis.authors}}</p><p>看多 {{r.analysis.percentages.bullish??'—'}}% / 看空 {{r.analysis.percentages.bearish??'—'}}%</p><p v-if="r.analysis.reasons.length">样本状态：{{r.analysis.reasons.join('；')}}</p><small>最近观察 {{time(r.analysis.last_post_observation)}}</small></template><p v-else>该对象暂未接入真实股吧样本，可在大盘/板块栏目查询妙想行情。</p></div><button class="secondary" :disabled="saving" :aria-label="`移除真实自选${r.name}`" @click="remove(r.id)">移除</button></article></template>
    </template>
  </section>
</template>
<style scoped>
.real-account{min-width:0}.real-controls{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin:20px 0}.real-controls label{display:grid;gap:8px;font-size:12px;color:var(--muted)}select,input{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:5px;padding:10px;max-width:100%}input{width:100px}.real-row{border-top:1px solid var(--border);padding:18px 0;display:flex;justify-content:space-between;gap:14px;align-items:start}.real-row p,.real-row small{font-size:12px;color:var(--muted);line-height:1.9;margin-top:6px}.real-row strong{display:block;margin-top:9px;color:#e7b876}.real-row .text-button{padding:0}.section-note{line-height:1.9}@media(max-width:650px){.panel-heading{flex-wrap:wrap;gap:12px}.real-controls label{flex:1 1 120px;min-width:0}.real-controls>.primary{width:100%}}
</style>
