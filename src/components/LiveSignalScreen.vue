<script setup lang="ts">
import {ref,computed,watch,onMounted,onUnmounted} from 'vue';
import {request} from '../api';
type Row={id:string;name:string;opinion_score:number;directional_posts:number;effective_posts:number;authors:number;last_observation:string};
const props=defineProps<{days:number;content:'titles'|'body'}>();
const emit=defineEmits<{select:[id:string]}>();
const rows=ref<Row[]>([]),excluded=ref(0),threshold=ref(80),direction=ref('all'),busy=ref(false),error=ref(''),asOf=ref('');
let version=0;
const matches=computed(()=>rows.value.filter(r=>(direction.value!=='bearish'&&r.opinion_score>=threshold.value)||(direction.value!=='bullish'&&r.opinion_score<=100-threshold.value)));
const time=(s:string)=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
async function load(){const current=++version;rows.value=[];excluded.value=0;asOf.value='';error.value='';busy.value=true;
  try{const r=await request<{ranked:Row[];unranked:unknown[];as_of:string}>(`/live/rankings?days=${props.days}&content=${props.content}`);if(current===version){rows.value=r.ranked;excluded.value=r.unranked.length;asOf.value=r.as_of;}}
  catch(e){if(current===version)error.value=e instanceof Error?e.message:'读取失败';}
  finally{if(current===version)busy.value=false;}}
watch(()=>[props.days,props.content],load);onMounted(load);onUnmounted(()=>++version);
</script>
<template>
  <section class="panel signal-screen"><div class="panel-heading"><h2>方向集中度条件筛选 · 规则</h2><button class="secondary" :disabled="busy" @click="load">刷新筛选</button></div>
    <p class="subtle section-note">仅已达到样本门槛的股票参与。舆论分≥阈值表示方向样本偏多集中，≤100−阈值表示偏空集中；中性不在分母内，不代表全体帖子一致。无预测或交易含义。</p>
    <div class="signal-controls"><label>方向<select v-model="direction" aria-label="真实异动方向"><option value="all">偏多和偏空</option><option value="bullish">偏多集中</option><option value="bearish">偏空集中</option></select></label><label>集中阈值<select v-model="threshold" aria-label="真实异动阈值"><option v-for="n in [70,80,90]" :key="n" :value="n">{{n}}%</option></select></label></div>
    <p v-if="busy" role="status" class="section-note">正在筛选真实样本…</p><p v-else-if="error" role="alert" class="error-banner">{{error}}</p>
    <template v-else><p class="subtle section-note">满足样本门槛 {{rows.length}}只 · 未达标 {{excluded}}只 · 命中条件 {{matches.length}}只</p><p v-if="!matches.length" class="empty-note">当前没有满足此条件且样本达标的股票。可调整阈值、自然日窗口或查看下方覆盖情况。</p><article v-for="r in matches" :key="r.id"><button class="text-button" @click="emit('select',r.id)">{{r.name}}</button><p>{{r.opinion_score>=threshold?'偏多集中':'偏空集中'}} · 舆论分 {{r.opinion_score}} · 方向样本 {{r.directional_posts}} / 有效样本 {{r.effective_posts}} / 作者 {{r.authors}}</p><small>最近观察 {{time(r.last_observation)}}</small></article><p v-if="asOf" class="subtle section-note">查询截止 {{time(asOf)}} · 开发阈值未校准，跨日反转和价格背离未启用。</p></template>
  </section>
</template>
<style scoped>.signal-screen{margin-bottom:16px}.section-note{line-height:1.9}.signal-controls{display:flex;gap:16px;flex-wrap:wrap;margin-top:18px}.signal-controls label{display:grid;gap:8px;font-size:12px;color:var(--muted)}select{background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px;border-radius:5px}article{padding:16px 0;border-top:1px solid var(--border)}article p,small{font-size:12px;line-height:1.9;color:var(--muted)}@media(max-width:650px){.panel-heading{flex-wrap:wrap;gap:12px}}</style>
