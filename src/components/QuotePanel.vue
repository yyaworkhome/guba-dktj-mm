<script setup lang="ts">
import {ref,computed,onMounted,onUnmounted} from 'vue';
import {request} from '../api';
type Technical={date:string;close:number;ma5:number;ma20:number;atr14:number;support10:number;resistance20:number;volume_ratio:number|null;regime:string;trade_reasons:string[]};
type Result={technical:Technical|null;reasons:string[];quote:{source:string;observed_at:string;bars:{date:string;close:number;is_current_session:boolean}[]}|null;refresh:{status:string;started_at:string;attempts:{source:string;status:string;error?:string}[]}|null;message?:string};
const props=defineProps<{instrumentId:string;disabled?:boolean}>();
const emit=defineEmits<{updated:[]}>();
const data=ref<Result|null>(null),busy=ref(false),error=ref(''),notice=ref('');let disposed=false;
const number=(n:number|null|undefined)=>n===null||n===undefined?'—':n.toFixed(2);
const time=(v:string)=>new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const bars=computed(()=>data.value?.quote?.bars||[]);
const line=computed(()=>{const rows=bars.value;if(rows.length<2)return '';const lo=Math.min(...rows.map(b=>b.close)),hi=Math.max(...rows.map(b=>b.close)),span=hi-lo||1;return rows.map((b,i)=>`${8+i/(rows.length-1)*484},${108-(b.close-lo)/span*96}`).join(' ');});
async function load(){try{const result=await request<Result>(`/live/quotes?instrument_id=${encodeURIComponent(props.instrumentId)}`);if(!disposed)data.value=result;}catch(e){if(!disposed)error.value=e instanceof Error?e.message:'行情读取失败';}}
async function refresh(){if(busy.value||props.disabled)return;busy.value=true;error.value='';notice.value='';try{const result=await request<Result>('/live/quotes',{method:'POST',body:JSON.stringify({instrument_id:props.instrumentId})},45000);if(!disposed){data.value=result;notice.value=result.message||'';emit('updated');}}catch(e){if(!disposed)error.value=e instanceof Error?e.message:'行情更新失败';}finally{if(!disposed)busy.value=false;}}
onMounted(load);onUnmounted(()=>{disposed=true;});
</script>
<template>
  <section class="panel quote-panel" aria-label="独立行情分析">
    <div class="panel-heading"><div><h2>行情趋势分析</h2><p class="subtle">行情与股吧情绪分别核验，采样受限时仍可查询价格数据</p></div><button class="secondary" :disabled="busy||disabled" @click="refresh">{{busy?'正在更新行情…':'更新行情（不采集股吧）'}}</button></div>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p><p v-if="notice" class="quote-notice" role="status">{{notice}}</p>
    <template v-if="data?.quote">
      <div class="quote-content"><div class="quote-regime"><span>价格趋势</span><h3>{{data.technical?.regime||'日线不足，暂不判断'}}</h3><p class="subtle">独立技术规则，不代表散户观点或上涨概率</p><dl><div><dt>最新保存日线</dt><dd>{{bars.at(-1)?.date}}</dd></div><div><dt>收盘参考</dt><dd>{{number(bars.at(-1)?.close)}} 元</dd></div><div><dt>完整日线基准</dt><dd>{{data.technical?.date||'待核验'}}</dd></div></dl></div>
      <div class="quote-chart"><svg v-if="line" viewBox="0 0 500 124" role="img" :aria-label="`${bars[0]?.date}至${bars.at(-1)?.date}未复权日线收盘趋势`"><path d="M8 12H492 M8 60H492 M8 108H492" class="chart-grid"/><polyline :points="line"/></svg><p v-else class="subtle">至少两根日线后展示价格曲线</p><div class="chart-dates"><span>{{bars[0]?.date}}</span><span>未复权收盘 · 含尚未收盘的当日日线时须核对</span><span>{{bars.at(-1)?.date}}</span></div></div></div>
      <div v-if="data.technical" class="quote-metrics"><div v-for="[label,value] in [['MA5',data.technical.ma5],['MA20',data.technical.ma20],['ATR14',data.technical.atr14],['10日低点',data.technical.support10],['前20日高点',data.technical.resistance20],['成交量/前20日均量',data.technical.volume_ratio]]" :key="String(label)"><span>{{label}}</span><strong>{{number(value as number|null)}}</strong></div></div>
      <p class="quote-source subtle">{{data.quote.source}} · 获取 {{time(data.quote.observed_at)}} · {{bars.length}}根日线</p>
    </template><p v-else class="quote-empty subtle">尚未保存真实日线，点击“更新行情”获取。不会用演示价格填充。</p>
    <p v-for="reason in [...(data?.reasons||[]),...(data?.technical?.trade_reasons||[])]" :key="reason" class="quote-reason">{{reason}}</p>
    <details v-if="data?.refresh"><summary>行情来源状态</summary><p>最近请求 {{time(data.refresh.started_at)}} · {{data.refresh.status==='success'?'成功':'未成功，旧数据仍保留'}}</p><p v-for="attempt in data.refresh.attempts" :key="attempt.source">{{attempt.source}}：{{attempt.status==='ok'?'成功':attempt.error}}</p></details>
  </section>
</template>
<style scoped>
.quote-panel>.panel-heading{align-items:center;gap:18px;flex-wrap:wrap}.panel-heading p{margin-top:8px;font-size:12px}.quote-content{display:grid;grid-template-columns:300px minmax(0,1fr);gap:34px;margin-top:26px}.quote-regime>span{font-size:12px;color:var(--muted)}.quote-regime h3{font-size:24px;color:var(--accent);margin:10px 0}.quote-regime>p{font-size:12px;line-height:1.8}.quote-regime dl{display:grid;gap:12px;font-size:12px;margin-top:20px}.quote-regime dl>div{display:flex;justify-content:space-between;gap:14px}.quote-regime dt{color:var(--muted)}.quote-regime dd{margin:0}.quote-chart{min-width:0;align-self:center}.quote-chart svg{width:100%;height:170px;overflow:visible}.quote-chart polyline{stroke:var(--accent);stroke-width:2;fill:none;vector-effect:non-scaling-stroke}.chart-grid{stroke:var(--border);stroke-width:.6;fill:none}.chart-dates{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:11px;flex-wrap:wrap}.quote-metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:20px;border-top:1px solid var(--border);margin-top:26px;padding-top:22px}.quote-metrics span{font-size:12px;color:var(--muted)}.quote-metrics strong{display:block;font-size:22px;margin-top:10px}.quote-source{font-size:12px;margin-top:22px}.quote-notice,.quote-reason{font-size:12px;line-height:1.9;margin-top:14px}.quote-notice{color:var(--accent)}.quote-reason{color:var(--bull)}.quote-empty{font-size:13px;line-height:1.9;margin-top:24px}.quote-panel details{color:var(--muted);font-size:12px;line-height:1.9;margin-top:16px}.quote-panel summary{cursor:pointer}.quote-panel details p{margin-top:8px}@media(max-width:900px){.quote-content{grid-template-columns:240px minmax(0,1fr)}.quote-metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:550px){.quote-panel{padding:20px}.quote-content{grid-template-columns:1fr;gap:20px}.panel-heading>.secondary{width:100%}.quote-chart svg{height:130px}.quote-metrics{grid-template-columns:repeat(2,1fr)}.chart-dates span:nth-child(2){order:3;flex-basis:100%;line-height:1.8}}
</style>
