<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue';
import {request} from '../api';
export type CommunityResult={source:string;effective_posts:number;authors:number;counts:Record<string,number>;percentages:Record<string,number|null>;opinion_score:number|null;observed_at:string;tags:Record<string,number>;excluded:Record<string,number>;eligible:boolean;reasons:string[];method:string;limitation:string;examples:{id:string;label:string;included:boolean;reason:string;excerpt:string;url:string}[]};
export type CommunityAnalysis={id:number;model:string;prompt_version:string;status:string;classified_at:string;result:CommunityResult&{error?:string}};
type Probe={id:number;status:string;sample_count:number;author_count:number;verified_publication_count:number;observed_at:string;details:{note?:string;error?:string};samples:{id:string;text:string;source_time:string;url:string}[]};
type Source={source:string;name:string;url:string;latest:Probe|null;attempts:number;reachable_rate:number|null;sample_success_rate:number|null;attempted_stocks:number;covered_stocks:number;sample_observations:number;analysis:CommunityAnalysis|null;last_success:CommunityAnalysis|null};
type Status={sources:Source[];universe_size:number|null;method:string};
type AI={configured:boolean;model?:string;calls_today?:number;daily_call_limit?:number;error:string;cooldown?:boolean};
const props=defineProps<{instrumentId:string;disabled?:boolean}>();
const emit=defineEmits<{results:[items:{source:string;analysis:CommunityAnalysis|null}[]]}>();
const data=ref<Status|null>(null),ai=ref<AI|null>(null),error=ref(''),busy=ref('');let disposed=false;
const names:Record<string,string>={samples:'取得可读帖子',entry_only:'入口已验证 · 无可用静态帖子',blocked:'来源拒绝或要求验证',network_failed:'网络失败',format_failed:'页面校验失败',http_failed:'HTTP失败',running:'处理中',success:'分类完成',auth_failed:'模型密钥校验失败',balance_failed:'模型余额不足',rate_limited:'模型限流',provider_failed:'模型服务失败',invalid_response:'模型结果校验失败'};
const label:Record<string,string>={bullish:'看多',bearish:'看空',neutral:'中性 / 未识别'};
const time=(s:string)=>new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const rate=(n:number|null)=>n===null?'—':`${n}%`;
async function load(){const [sources,config]=await Promise.all([request<Status>(`/live/community/sources?instrument_id=${encodeURIComponent(props.instrumentId)}`),request<AI>('/live/community/ai')]);if(disposed)return;data.value=sources;ai.value=config;emit('results',sources.sources.map(s=>({source:s.source,analysis:s.last_success})));}
async function perform(source:string,task:()=>Promise<unknown>){if(busy.value)return;busy.value=source;error.value='';try{await task();}catch(e){if(!disposed)error.value=e instanceof Error?e.message:'来源处理失败';}finally{if(!disposed){busy.value='';await load().catch(()=>{});}}}
async function probe(source:string){await perform(source,()=>request('/live/community/probe',{method:'POST',body:JSON.stringify({source,instrument_id:props.instrumentId})},45000));}
async function classify(source:Source,mode:'ai'|'rules'){await perform(source.source,()=>request('/live/community/analyze',{method:'POST',body:JSON.stringify({probe_id:source.latest?.id,mode})},90000));}
async function run(source:string){await perform(source,async()=>{
  const config=await request<AI>('/live/community/ai');ai.value=config;
  if(!config.configured||config.cooldown)throw new Error(config.error||'模型尚未配置');
  const observation=await request<{observation:Probe}>('/live/community/probe',{method:'POST',body:JSON.stringify({source,instrument_id:props.instrumentId})},45000);
  if(observation.observation.status!=='samples')throw new Error(observation.observation.details.error||observation.observation.details.note||'该来源未取得可读帖子');
  const result=await request<{analysis:CommunityAnalysis}>('/live/community/analyze',{method:'POST',body:JSON.stringify({probe_id:observation.observation.id,mode:'ai'})},90000);
  if(result.analysis.status!=='success')throw new Error(result.analysis.result.error||names[result.analysis.status]||'模型分类失败');
});if(error.value)throw new Error(error.value);}
defineExpose({run});
onMounted(()=>load().catch(e=>error.value=e.message));onUnmounted(()=>{disposed=true;});
</script>

<template>
  <section class="panel community-panel" aria-label="多来源情绪分析">
    <div class="community-heading"><div><h2>多来源情绪分析</h2><p class="subtle">{{ai?.configured?`${ai.model} 已配置`:'AI 尚未配置'}} · 今日调用 {{ai?.calls_today??0}} / {{ai?.daily_call_limit??'—'}}</p></div><button class="secondary" :disabled="!!busy||disabled" @click="load().catch(e=>error=e.message)">刷新结果</button></div>
    <p v-if="ai?.error" class="subtle config-note">{{ai.error}}</p>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p>
    <p v-if="busy" class="operation-note" role="status">正在处理{{data?.sources.find(s=>s.source===busy)?.name}}的真实样本，结果会保存到本机…</p>
    <div class="source-grid">
      <article v-for="source in data?.sources" :key="source.source" class="source-card">
        <div class="source-title"><h3>{{source.name}}</h3><a :href="source.url" target="_blank" rel="noopener noreferrer">原始页面 ↗</a></div>
        <p class="source-state">{{source.latest?names[source.latest.status]:'尚未验证'}}</p>
        <dl class="source-metrics"><div><dt>可达率 / 样本成功率</dt><dd>{{rate(source.reachable_rate)}} / {{rate(source.sample_success_rate)}}</dd></div><div><dt>请求 / 覆盖股票</dt><dd>{{source.attempts}} 次 / {{source.covered_stocks}} 只</dd></div><div><dt>本次可读帖子 / 作者</dt><dd>{{source.latest?.sample_count??0}} / {{source.latest?.author_count??0}}</dd></div><div><dt>已核验完整发布时间</dt><dd>{{source.latest?.verified_publication_count??0}} 条</dd></div></dl>
        <p v-if="source.latest?.details.error" class="subtle">{{source.latest.details.error}}</p>
        <div class="source-actions"><button class="secondary" :disabled="!!busy||disabled" @click="probe(source.source)">验证与采集</button><button v-if="source.latest?.sample_count" class="primary" :disabled="!!busy||disabled||!ai?.configured||ai?.cooldown" @click="classify(source,'ai')">AI 情绪分析</button></div>
        <p v-if="source.analysis&&source.analysis.status!=='success'" class="source-failure">最近分类：{{names[source.analysis.status]||source.analysis.status}}。已保存的成功结果保留。</p>
        <template v-if="source.last_success">
          <div class="sentiment-result"><strong>{{source.last_success.result.opinion_score??'—'}}<small> / 100 舆论分</small></strong><span>{{source.last_success.model}} · {{source.last_success.prompt_version}}</span></div>
          <div class="sentiment-counts"><span v-for="key in ['bullish','bearish','neutral']" :key="key" :class="key==='bullish'?'bull':key==='bearish'?'bear':''">{{key==='neutral'?(source.last_success.model.startsWith('deepseek')?'中性':'未识别'):label[key]}} {{source.last_success.result.counts[key]}}</span></div>
          <p class="subtle">有效 {{source.last_success.result.effective_posts}} 条 · {{source.last_success.result.authors}} 位作者 · {{source.last_success.result.eligible?'达到研究样本门槛':source.last_success.result.reasons.join('；')}}</p>
          <p v-if="Object.keys(source.last_success.result.tags).length" class="tag-row"><span v-for="(count,tag) in source.last_success.result.tags" :key="tag">{{tag}} {{count}}</span></p>
          <details class="source-evidence"><summary>查看分类依据与排除项</summary><p class="subtle">排除统计：{{Object.entries(source.last_success.result.excluded).map(([k,v])=>`${k}: ${v}`).join('；')||'无'}}。时间 {{time(source.last_success.classified_at)}}</p><article v-for="sample in source.last_success.result.examples" :key="sample.id"><div>{{sample.included?label[sample.label]:'已排除'}} · {{sample.reason}}</div><p>{{sample.excerpt}}</p><a :href="sample.url" target="_blank" rel="noopener noreferrer">原帖 ↗</a></article></details>
        </template>
        <details v-if="source.latest" class="source-evidence"><summary>来源快照与口径</summary><p>{{time(source.latest.observed_at)}}</p><p>{{source.latest.details.note}}</p><article v-for="sample in source.latest.samples.slice(0,3)" :key="sample.id"><p>{{sample.text}}</p><small>页面时间原文：{{sample.source_time||'未提供'}}</small></article><button v-if="source.latest.sample_count" class="text-button" :disabled="!!busy||disabled" @click="classify(source,'rules')">仅运行关键词规则（非AI）</button></details>
      </article>
    </div>
    <p class="subtle source-footnote">{{data?.method}} 当前股票目录 {{data?.universe_size?.toLocaleString()??'未同步'}} 只。来源间不拼接成完整全市场指数；新来源的观察快照不用于发布时间趋势或历史收益回测。</p>
  </section>
</template>

<style scoped>
.community-panel{min-width:0}.community-heading{display:flex;justify-content:space-between;align-items:center;gap:14px}.community-heading h2{font-size:22px}.community-heading p{margin-top:8px}.source-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:24px}.source-card{border:1px solid var(--border);padding:20px;border-radius:8px;min-width:0;align-self:start}.source-title{display:flex;align-items:center;justify-content:space-between;gap:8px}.source-title h3{font-size:17px}.source-title a,.source-evidence a{font-size:12px;color:var(--accent)}.source-state{font-size:13px;margin:16px 0;color:var(--accent)}.source-metrics{font-size:12px}.source-metrics>div{display:flex;justify-content:space-between;gap:8px;margin:12px 0}.source-metrics dt{color:var(--muted)}.source-metrics dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}.source-actions{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}.source-actions button{font-size:12px;flex:1;white-space:nowrap}.source-footnote{margin-top:20px;font-size:12px;line-height:1.9}.config-note{margin-top:16px;font-size:12px}.operation-note{color:var(--accent);margin-top:16px;font-size:13px}.sentiment-result{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin:22px 0 12px}.sentiment-result strong{font-size:27px;font-variant-numeric:tabular-nums}.sentiment-result small,.sentiment-result>span{font-size:11px;color:var(--muted);font-weight:400}.sentiment-counts{display:flex;gap:12px;flex-wrap:wrap;font-size:13px;margin-bottom:12px}.source-evidence{font-size:12px;margin-top:18px;color:var(--muted);line-height:1.8}.source-evidence summary{cursor:pointer;color:var(--text)}.source-evidence p{margin:10px 0;overflow-wrap:anywhere}.source-evidence article{border-top:1px solid var(--border);margin-top:14px;padding-top:14px}.source-evidence article div{color:var(--text)}.source-failure{font-size:12px;color:var(--bull);margin:12px 0}.tag-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.tag-row span{font-size:11px;background:var(--surface-alt);padding:4px 8px;border-radius:4px}
@media(max-width:1100px){.source-grid{grid-template-columns:1fr}.source-metrics{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}.source-actions button{flex:none}.source-card{padding:20px}}@media(max-width:550px){.community-panel{padding:20px 16px}.community-heading{align-items:start}.community-heading h2{font-size:20px}.community-heading .secondary{font-size:12px;white-space:nowrap}.source-metrics{display:block}.source-actions button{flex:1}.source-card{padding:16px}.source-grid{gap:14px}}
</style>
