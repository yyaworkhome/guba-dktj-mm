<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { RefreshCw, ArrowRight } from 'lucide-vue-next';
import { request } from '../api';
import type { LiveAnalysis } from '../types';
const props = defineProps<{ instrumentId: string; name: string; content: 'titles'|'body'; days: number }>();
const emit = defineEmits<{ analyzed: [data: LiveAnalysis]; running: [value: boolean] }>();
type State = { enabled: boolean; locked: boolean; next_allowed_at: string|null };
type Run = { id: number; status: string; details: Record<string, { accepted?: number; error?: string; bars?: number }> };
type Result = { analysis: LiveAnalysis; collection: { mode: string; message: string; run: Run|null } };
const state = ref<State|null>(null), busy = ref(false), error = ref(''), result = ref<Result['collection']|null>(null);
const clock = ref(Date.now());
let version = 0, disposed = false, timer: ReturnType<typeof setInterval>;
const remaining = computed(() => state.value?.next_allowed_at ? Math.max(0, Math.ceil((Date.parse(state.value.next_allowed_at)-clock.value)/1000)) : 0);
async function load() {
  const id = ++version;
  try {
    const r = await request<State>(`/live/collection?instrument_id=${encodeURIComponent(props.instrumentId)}`);
    if (id === version && !disposed) state.value = r;
  } catch (e) { if (id === version && !disposed) error.value = e instanceof Error ? e.message : '采集状态读取失败'; }
}
async function analyze() {
  if (busy.value) return;
  const iid = props.instrumentId, content = props.content, days = props.days;
  busy.value = true; emit('running', true); error.value = ''; result.value = null;
  try {
    const response = await request<Result>('/live/analyze', {
      method: 'POST', body: JSON.stringify({ instrument_id: iid, content, days, limit: 5 }),
    }, 150000);
    if (!disposed && iid === props.instrumentId && content === props.content && days === props.days) {
      result.value = response.collection;
      emit('analyzed', response.analysis);
    }
  } catch (e) {
    if (!disposed) error.value = (e instanceof Error ? e.message : '分析失败') + ' 如请求超时，服务端可能仍在采集，请先刷新状态。';
  } finally {
    if (!disposed) { busy.value = false; emit('running', false); await load(); }
  }
}
watch(() => [props.instrumentId, props.content, props.days], () => { result.value = null; error.value = ''; load(); });
onMounted(() => { load(); timer = setInterval(() => clock.value = Date.now(), 1000); });
onUnmounted(() => { disposed = true; ++version; clearInterval(timer); });
</script>
<template>
  <section class="collection-control" aria-label="开始真实分析">
    <div class="analysis-action">
      <div><h3>分析 {{name}} · {{content==='titles'?'标题':'正文'}} · 近{{days}}自然日</h3><p>读取股吧 → 去重与规则分类 → 保存快照 → 展示结果。本机分析无需登录；自选和预警需登录保存。</p></div>
      <button class="primary" :disabled="busy" @click="analyze"><RefreshCw v-if="busy" :size="16" class="spin" />{{busy?'正在采集并分析…':'开始真实分析'}}<ArrowRight v-if="!busy" :size="16" /></button>
    </div>
    <p v-if="busy" role="status">正在处理 {{name}}。标题通常数秒，正文最多请求5条详情；请等待结果，重复点击不会加速。</p>
    <p v-if="state?.enabled && remaining>0" class="subtle">源站采样冷却还剩{{Math.floor(remaining/60)}}分{{remaining%60}}秒；仍可立即分析已保存的同口径样本。</p>
    <p v-else-if="state?.locked" class="subtle">另有采集任务正在运行；点击分析可查看已保存结果。</p>
    <p v-else-if="state && !state.enabled" class="subtle">此服务仅分析已保存样本。更新采样需通过标准本机启动脚本启用采集。</p>
    <button class="text-button" :disabled="busy" @click="load">刷新采集状态</button>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p>
    <div v-if="result" class="collection-result" role="status">
      <strong>{{result.message}}</strong>
      <p v-if="result.mode==='cooldown' && !state?.locked">如当前口径尚无样本，需要等待冷却结束后再次分析；标题与正文共用采样间隔。</p>
      <p v-if="result.run">运行 #{{result.run.id}} · 纳入 {{result.run.details.posts?.accepted??0}} 条{{content==='titles'?'标题':'正文'}} · 保存日线 {{result.run.details.quotes?.bars??0}} 条</p>
      <p v-if="result.run?.details.posts?.error">股吧：{{result.run.details.posts.error}}</p>
      <p v-if="result.run?.details.quotes?.error">行情：{{result.run.details.quotes.error}}</p>
    </div>
  </section>
</template>
<style scoped>
.collection-control{border-top:1px solid var(--border);margin-top:20px;padding-top:20px}
.analysis-action{display:flex;align-items:center;justify-content:space-between;gap:20px}
.analysis-action>.primary{flex-shrink:0}.collection-control p{font-size:12px;line-height:1.9;color:var(--muted);margin:10px 0}
.collection-control h3{font-size:15px}.collection-result{border:1px solid var(--border);border-radius:6px;padding:14px;margin-top:14px}
.collection-result strong{font-size:13px;color:var(--accent)}
@media(max-width:650px){.analysis-action{align-items:stretch;flex-direction:column;gap:8px}.analysis-action>.primary{justify-content:center}}
</style>
