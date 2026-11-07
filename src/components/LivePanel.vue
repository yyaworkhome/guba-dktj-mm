<script setup lang="ts">
import {ref,onMounted,onUnmounted,computed} from 'vue';
import {Download,ExternalLink,RefreshCw} from 'lucide-vue-next';
import {request,download} from '../api';
import StockSearch,{type SearchStock} from './StockSearch.vue';
import LiveHistory from './LiveHistory.vue';
import QuotePanel from './QuotePanel.vue';
import type {LiveAnalysis} from '../types';
const props=defineProps<{initialInstrument?:string;home?:boolean}>();
const emit=defineEmits<{select:[id:string]}>();
const iid=ref(props.initialInstrument||'XSHE:300059'),name=ref(''),content=ref<'titles'|'body'>('titles'),days=ref(3);
const data=ref<LiveAnalysis|null>(null),busy=ref(false),loading=ref(false),error=ref(''),notice=ref(''),exporting=ref(false),history=ref<InstanceType<typeof LiveHistory>|null>(null);
let version=0,disposed=false;
const exchanges:Record<string,string>={XSHE:'深交所',XSHG:'上交所',XBSE:'北交所'};
const selectedName=computed(()=>name.value||data.value?.name||iid.value);
const code=computed(()=>iid.value.split(':')[1]);
const names:Record<string,string>={bullish:'看多',bearish:'看空',neutral:'未识别'};
const quality=computed(()=>!data.value?.effective_posts?'暂无样本':data.value.interpretation?.quality_reasons.length?'样本覆盖不足':data.value.interpretation?.headline||'已完成统计');
const time=(s:string|null|undefined)=>s?new Date(s).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未采集';
async function load(){const current=++version;loading.value=true;data.value=null;error.value='';try{const result=await request<LiveAnalysis>(`/live/analysis?${new URLSearchParams({instrument_id:iid.value,days:String(days.value),content:content.value})}`);if(current===version&&!disposed)data.value=result;}catch(e){if(current===version&&!disposed)error.value=e instanceof Error?e.message:'结果读取失败';}finally{if(current===version&&!disposed)loading.value=false;}}
async function choose(stock:SearchStock){iid.value=stock.id;name.value=stock.name;notice.value='';emit('select',stock.id);await load();}
async function period(n:number){days.value=n;notice.value='';await load();}
async function analyze(){if(busy.value)return;const current=++version;busy.value=true;error.value='';notice.value='';try{const result=await request<{analysis:LiveAnalysis;collection:{message:string}}>('/live/analyze',{method:'POST',body:JSON.stringify({instrument_id:iid.value,days:days.value,content:content.value,limit:5})},150000);if(current===version&&!disposed){data.value=result.analysis;notice.value=result.collection.message;await history.value?.load();}}catch(e){if(!disposed)error.value=e instanceof Error?e.message:'分析失败';}finally{if(!disposed)busy.value=false;}}
async function exportData(){if(!data.value)return;exporting.value=true;try{await download(`/live/export?${new URLSearchParams({instrument_id:iid.value,days:String(days.value),content:content.value})}`,`多空分析-${code.value}.xlsx`);}catch(e){error.value=e instanceof Error?e.message:'导出失败';}finally{exporting.value=false;}}
onMounted(load);onUnmounted(()=>{disposed=true;++version;});
</script>

<template>
  <div class="analysis-workspace" :aria-busy="busy||loading">
    <div :class="['task-band',{single:!home}]">
      <section class="panel stock-command">
        <h1>输入股票，查看多空情绪</h1>
        <StockSearch :disabled="busy" @select="choose"/>
        <div class="selected-security"><strong>{{selectedName}}</strong><span>{{code}} · {{exchanges[iid.split(':')[0]]}}</span></div>
        <div class="analysis-controls"><div class="window-tabs" role="group" aria-label="统计周期（自然日）"><button v-for="n in [1,3,5,10,20]" :key="n" :disabled="busy" :class="{active:days===n}" :aria-pressed="days===n" @click="period(n)">{{n===1?'今日':`近${n}日`}}</button></div><button class="primary analyze-primary" :disabled="busy||loading" @click="analyze"><RefreshCw v-if="busy" :size="17" class="spin"/>{{busy?'分析中…':'开始分析'}}</button></div>
        <div class="analysis-options"><label>采样口径<select v-model="content" :disabled="busy" @change="notice='';load()"><option value="titles">标题采样</option><option value="body">正文采样（每批最多5条）</option></select></label><button class="text-button" :disabled="busy||loading" @click="load">读取已保存结果</button></div>
      </section>
      <slot v-if="home" name="scan"/>
    </div>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p>
    <p v-if="notice" class="operation-notice" role="status">{{notice}}</p>
    <p v-if="busy" class="operation-notice" role="status">正在采集 {{selectedName}} 的{{content==='titles'?'标题':'正文'}}并保存分析快照…</p>
    <section class="panel analysis-summary" aria-label="多空分析结果" aria-live="polite">
      <div class="summary-security"><h2>{{data?.name||selectedName}}</h2><p>{{code}} · {{exchanges[iid.split(':')[0]]}}</p></div>
      <div :class="['summary-verdict',{limited:data?.interpretation?.quality_reasons.length}]">{{busy?'正在分析':loading?'读取中…':quality}}<small v-if="data?.opinion_score!==null&&data?.opinion_score!==undefined">舆论分 {{data.opinion_score.toFixed(1)}} / 100</small></div>
      <div class="summary-numbers"><div><span>有效样本</span><strong>{{data?.effective_posts??'—'}}</strong></div><div v-for="key in ['bullish','bearish','neutral']" :key="key"><span>{{names[key]}}</span><strong :class="key==='bullish'?'bull':key==='bearish'?'bear':''">{{data?.counts[key]??'—'}}</strong></div></div>
      <div class="summary-time"><span>更新时间</span><time>{{time(data?.last_post_observation)}}</time></div>
    </section>
    <QuotePanel :key="iid" :instrument-id="iid" :disabled="busy" @updated="load"/>
    <slot v-if="home" name="rankings"/>
    <template v-if="data&&!busy">
      <div class="analysis-detail-heading"><h2>多空分析明细</h2><button class="secondary" :disabled="exporting" @click="exportData"><Download :size="15"/>{{exporting?'导出中…':'导出 Excel'}}</button></div>
      <div class="analysis-detail-grid">
        <section class="panel direction-panel"><h2>观点分布</h2><div v-for="key in ['bullish','bearish','neutral']" :key="key" :class="['direction-row',key]"><span>{{names[key]}}</span><div class="direction-track"><i :style="{width:(data.percentages[key]||0)+'%'}"/></div><strong>{{data.percentages[key]===null?'—':`${data.percentages[key]}%`}}</strong><small>{{data.counts[key]}}条</small></div><p class="subtle">占全部有效样本的比例。未识别方向不等于真实中性。</p></section>
        <section class="panel interpretation-panel"><h2>{{data.interpretation?.headline||'真实样本统计'}}</h2><p v-for="line in data.interpretation?.bullets" :key="line">{{line}}</p><div class="sample-totals"><span>作者 <strong>{{data.authors}}</strong></span><span>累计评论 <strong>{{data.comments.toLocaleString()}}</strong></span><span>累计阅读 <strong>{{data.reads.toLocaleString()}}</strong></span></div></section>
      </div>
      <LiveHistory ref="history" :instrument-id="iid" :content="content"/>
      <section class="panel evidence-panel"><div class="panel-heading"><h2>最近有效样本</h2><a :href="data.source_url" target="_blank" rel="noopener noreferrer" class="text-button">查看股吧 <ExternalLink :size="13"/></a></div><p v-if="!data.examples.length" class="subtle">暂无所选范围和口径的有效样本，可以扩大日期范围后分析。</p><article v-for="sample in data.examples" :key="sample.source_id"><div><span :class="sample.label==='bullish'?'bull':sample.label==='bearish'?'bear':'subtle'">{{names[sample.label]}}</span><small>{{time(sample.published_at)}}</small></div><p>{{sample.excerpt}}</p><a :href="sample.url" target="_blank" rel="noopener noreferrer">查看原帖</a></article></section>
      <details class="panel evidence-details"><summary>数据来源、采集状态与统计口径</summary><p>近{{data.days}}自然日 · {{data.range.join(' — ')}} · {{content==='titles'?'标题':'正文'}}采样</p><p v-if="data.status.latest_run">采集批次 #{{data.status.latest_run.id}} · {{data.status.latest_run.status}} · {{time(data.status.latest_run.finished_at)}}</p><template v-for="(source,key) in data.status.latest_run?.details" :key="key"><p v-if="key!=='_meta'">{{key==='posts'?'帖子':'行情'}}：{{source.status}} {{source.error}}</p></template><ul><li v-for="line in data.limitations" :key="line">{{line}}</li></ul></details>
    </template>
  </div>
</template>

<style scoped>
.analysis-workspace{display:grid;gap:24px}.task-band{display:grid;grid-template-columns:minmax(0,1fr) 350px;gap:24px;align-items:stretch}.task-band.single{grid-template-columns:1fr}.stock-command{padding:30px;min-width:0}.stock-command h1{font-size:29px;font-weight:700;letter-spacing:0}.selected-security{display:flex;align-items:baseline;gap:14px;margin:26px 0 22px;flex-wrap:wrap}.selected-security strong{font-size:23px}.selected-security span{font-size:16px;color:var(--muted)}.analysis-controls{display:flex;gap:20px;justify-content:space-between;align-items:center}.window-tabs{display:flex;background:var(--surface-alt);border-radius:6px;padding:3px;flex:1;max-width:650px}.window-tabs button{flex:1;white-space:nowrap;padding:12px 10px;border-radius:5px;font-size:15px;font-weight:600}.window-tabs .active{background:var(--accent);color:#08251f}.analyze-primary{min-width:145px;min-height:48px;font-size:16px}.analysis-options{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:24px}.analysis-options label{display:flex;align-items:center;gap:10px;font-size:12px}.analysis-options select{padding:7px;font-size:12px}.analysis-options .text-button{font-size:12px}.operation-notice{font-size:13px;color:var(--accent);padding:14px 18px;border-left:3px solid var(--accent);background:var(--accent-bg)}.analysis-summary{display:flex;align-items:center;justify-content:space-between;gap:22px;padding:26px}.summary-security h2{font-size:24px}.summary-security p{color:var(--muted);font-size:13px;margin-top:7px}.summary-verdict{border-left:1px solid var(--border);padding-left:24px;font-size:21px;font-weight:650;color:var(--accent);max-width:250px}.summary-verdict.limited{color:var(--bull)}.summary-verdict small{display:block;color:var(--muted);font-size:12px;font-weight:400;margin-top:7px}.summary-numbers{display:flex;gap:34px;flex:1;justify-content:space-evenly}.summary-numbers span,.summary-time>span{display:block;color:var(--muted);font-size:13px}.summary-numbers strong{display:block;margin-top:8px;font-size:28px;font-variant-numeric:tabular-nums}.summary-time{border-left:1px solid var(--border);padding-left:22px;max-width:160px}.summary-time time{display:block;font-size:13px;margin-top:10px;line-height:1.7}.analysis-detail-heading{display:flex;align-items:center;justify-content:space-between;margin-top:10px}.analysis-detail-heading h2{font-size:20px}.analysis-detail-grid{display:grid;grid-template-columns:1fr 1.2fr;gap:24px}.direction-row{display:grid;grid-template-columns:60px 1fr 55px 45px;align-items:center;gap:12px;font-size:13px;margin:24px 0}.direction-track{height:7px;background:var(--surface-alt);border-radius:4px;overflow:hidden}.direction-track i{display:block;height:100%;background:var(--muted)}.bullish i{background:var(--bull)}.bearish i{background:var(--bear)}.direction-row strong,.direction-row small{text-align:right}.interpretation-panel>p{margin-top:14px;font-size:13px;color:var(--muted)}.sample-totals{display:flex;flex-wrap:wrap;gap:24px;border-top:1px solid var(--border);margin-top:22px;padding-top:20px;font-size:12px;color:var(--muted)}.sample-totals strong{color:var(--text);margin-left:7px}.evidence-panel article{border-top:1px solid var(--border);padding:18px 0;margin-top:14px}.evidence-panel article>div{display:flex;justify-content:space-between}.evidence-panel article p{margin:10px 0;overflow-wrap:anywhere}.evidence-panel article a{font-size:12px;color:var(--accent)}.evidence-panel article small{color:var(--muted)}.evidence-details{font-size:12px;color:var(--muted);line-height:1.9}.evidence-details summary{cursor:pointer;color:var(--text);font-size:14px}.evidence-details p{margin-top:10px}.evidence-details ul{padding-left:20px}.evidence-panel .panel-heading{gap:14px;flex-wrap:wrap}
@media(max-width:1150px){.task-band{grid-template-columns:minmax(0,1fr) 310px}.stock-command{padding:24px}.stock-command h1{font-size:25px}.summary-numbers{gap:20px}.summary-time{display:none}.analysis-controls{gap:12px}.analyze-primary{min-width:120px}.window-tabs button{padding:12px 8px;font-size:14px}}@media(max-width:900px){.task-band{grid-template-columns:1fr}.analysis-summary{flex-wrap:wrap;justify-content:start}.summary-numbers{flex-basis:100%;justify-content:space-between;border-top:1px solid var(--border);padding-top:20px}.analysis-detail-grid{grid-template-columns:1fr}}@media(max-width:550px){.analysis-workspace{gap:18px}.stock-command{padding:22px 18px}.stock-command h1{font-size:24px;line-height:1.45}.selected-security{margin:22px 0 18px;gap:8px}.selected-security strong{font-size:21px}.selected-security span{font-size:13px}.analysis-controls{flex-direction:column;align-items:stretch}.analyze-primary{width:100%;margin-top:4px}.window-tabs{width:100%}.window-tabs button{font-size:13px;padding:11px 5px}.analysis-options{gap:12px;flex-wrap:wrap;margin-top:16px}.analysis-options .text-button{padding:0}.analysis-summary{padding:22px 18px;gap:18px}.summary-security h2{font-size:22px}.summary-verdict{font-size:17px;padding-left:16px;max-width:170px}.summary-numbers strong{font-size:26px}.summary-numbers span{font-size:12px}.direction-row{gap:8px;grid-template-columns:52px 1fr 50px 36px}.evidence-panel{padding:20px}}
</style>
