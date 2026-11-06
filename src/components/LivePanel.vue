<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { RefreshCw, Database, ExternalLink } from 'lucide-vue-next';
import { request } from '../api';
import LiveHistory from './LiveHistory.vue';
import LiveRankings from './LiveRankings.vue';

interface LiveData {
  instrument_id: string; name: string; code: string; content: string; source_url: string; collector_command: string;
  examples: { source_id: string; excerpt: string; url: string; label: string; published_at: string; first_seen_at: string; observed_at: string }[];
  as_of: string; last_post_observation: string | null; stale: boolean; days: number; range: string[];
  sampled_posts: number; effective_posts: number; authors: number; opinion_score: number | null; coverage: number;
  counts: Record<string, number>; percentages: Record<string, number | null>;
  comments: number; reads: number; removed: Record<string, number>;
  keywords: { text: string; count: number }[];
  trend: { date: string; posts: number; opinion_score: number | null }[];
  quote: { observed_at: string; bars: { date: string; open: number; close: number; is_current_session: boolean }[] } | null;
  status: { latest_run: { id: number; status: string; finished_at: string | null;
    details: Record<string, { status?: string; error?: string; accepted?: number; received?: number; requested?: number; detail_excluded?: unknown[] }> } | null };
  limitations: string[];
}
interface LiveStock { id: string; name: string; code: string; exchange: string; }
const props = defineProps<{initialInstrument?: string}>();
const stocks = ref<LiveStock[]>([]), instrumentId = ref(props.initialInstrument || 'XSHE:300059'), stockQuery = ref('');
const content = ref<'titles' | 'body'>('titles');
const filteredStocks = computed(() => stocks.value.filter(s => `${s.id} ${s.code} ${s.name}`.toLowerCase().includes(stockQuery.value.trim().toLowerCase())));
const selectedStock = computed(() => stocks.value.find(s => s.id === instrumentId.value));
const scopeName = computed(() => content.value === 'body' ? '正文' : '标题');
const data = ref<LiveData | null>(null), days = ref(3), busy = ref(false), error = ref('');
let version = 0;
const names: Record<string, string> = { bullish: '看多', bearish: '看空', neutral: '中性 / 未识别' };
const statusNames: Record<string, string> = { success: '采集成功', partial: '部分采集失败', failed: '采集失败', running: '采集中' };
const lastBar = computed(() => data.value?.quote?.bars.at(-1));
const historyPanel = ref<InstanceType<typeof LiveHistory> | null>(null);
const rankingPanel = ref<InstanceType<typeof LiveRankings> | null>(null);
async function refresh() { await Promise.all([load(), historyPanel.value?.load(), rankingPanel.value?.load()]); }
async function selectRanked(id: string) { instrumentId.value = id; stockQuery.value = ''; await load(); }
function time(s: string | null | undefined) {
  return s ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(s)) : '尚未采集';
}
async function load(n = days.value) {
  const id = ++version;
  days.value = n; busy.value = true; error.value = '';
  // Clear old data so changing a window cannot label an old result as new.
  data.value = null;
  try {
    const params = new URLSearchParams({days: String(n), instrument_id: instrumentId.value, content: content.value});
    const result = await request<LiveData>(`/live/analysis?${params}`);
    if (id === version) data.value = result;
  } catch (e) {
    if (id === version) error.value = e instanceof Error ? e.message : '读取失败';
  } finally { if (id === version) busy.value = false; }
}
onMounted(async () => {
  await load();
  try { stocks.value = await request<LiveStock[]>('/live/instruments'); }
  catch (e) { error.value = e instanceof Error ? e.message : '证券目录读取失败'; }
});
async function chooseStock(stock: LiveStock) { instrumentId.value = stock.id; await load(); }
async function chooseContent(value: 'titles' | 'body') { content.value = value; await load(); }
onUnmounted(() => { version++; });
</script>

<template>
  <div class="live-panel" :aria-busy="busy">
    <section class="panel live-intro">
      <div class="panel-heading">
        <div><p class="live-eyebrow"><Database :size="14" />真实采样 · 规则分类</p><h2>{{ selectedStock?.name || data?.name || '东方财富' }} <span class="subtle">{{ instrumentId }} · {{ scopeName }}口径</span></h2></div>
        <button class="secondary compact" :disabled="busy" @click="refresh()"><RefreshCw :size="14" :class="{ spin: busy }" />刷新已采集数据</button>
      </div>
      <p class="subtle live-description">已开放5只股票，标题与正文分别统计，行情为未复权日线。大盘、板块栏目可查询妙想真实行情，其综合情绪聚合仍待接入。</p>
      <label class="live-search-label" for="live-stock-search">输入个股代码或名称</label>
      <input id="live-stock-search" v-model="stockQuery" class="live-search" placeholder="如 000001、平安银行、600519" />
      <div class="live-stock-options" role="group" aria-label="选择真实采样股票">
        <button v-for="s in filteredStocks" :key="s.id" :class="['secondary', { selected: instrumentId === s.id }]" :aria-pressed="instrumentId === s.id" @click="chooseStock(s)">{{ s.name }} <small>{{ s.code }} · {{ s.exchange === 'XSHG' ? '上交所' : '深交所' }}</small></button>
      </div>
      <p v-if="!filteredStocks.length && stocks.length" class="subtle">未找到已开放股票。此入口只查询个股，000001对应深交所平安银行；上证指数行情可在妙想入口查询。</p>
      <div class="period-options live-periods" role="group" aria-label="内容口径">
        <button :class="{selected: content === 'titles'}" :aria-pressed="content === 'titles'" @click="chooseContent('titles')">标题采样</button>
        <button :class="{selected: content === 'body'}" :aria-pressed="content === 'body'" @click="chooseContent('body')">正文采样 · 每批最多5条</button>
      </div>
      <div class="period-options live-periods" role="group" aria-label="真实样本自然日范围">
        <button v-for="n in [1, 3, 5, 10, 20]" :key="n" :class="{ selected: days === n }" :aria-pressed="days === n" @click="load(n)">{{ n === 1 ? '今日' : `近${n}自然日` }}</button>
      </div>
      <p class="subtle">刷新只读取数据库；源站采集由本机采集命令运行，间隔至少10分钟。</p>
    </section>
    <p v-if="busy" class="panel loading-state" role="status">正在读取真实采样记录…</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }} · 未使用演示数据替代。</p>
    <template v-if="data">
      <section class="panel live-source">
        <div class="panel-heading"><h2>采集状态</h2><span class="live-badge">{{ data.status.latest_run ? statusNames[data.status.latest_run.status] : '尚无采集记录' }}</span></div>
        <p class="subtle">最近运行：{{ time(data.status.latest_run?.finished_at) }}（北京时间） · {{ scopeName }}最近观察：{{ time(data.last_post_observation) }}</p>
        <p v-if="data.stale" class="live-warning">样本尚未采集或已超过24小时，请更新采样后查看。</p>
        <template v-for="(source, key) in data.status.latest_run?.details" :key="key"><p v-if="key !== '_meta'" class="subtle">
          {{ key === 'posts' ? `股吧${scopeName}` : key === 'quotes' ? '日线行情' : '处理状态' }}：{{ source.status === 'ok' ? '正常' : (source.error || '失败') }}
          <span v-if="source.received !== undefined"> · 列表 {{ source.received }} 条，纳入 {{ source.accepted }} 条</span>
          <span v-if="source.requested !== undefined"> · 正文候选 {{ source.requested }} 条，详情排除 {{ source.detail_excluded?.length || 0 }} 条</span>
        </p></template>
        <p v-if="data.status.latest_run && data.status.latest_run.status !== 'success'" class="live-warning">本次采集尚未全部成功。下方如有数据，为已保存的观察记录，请核对各自时间。</p>
        <a class="text-button live-source-link" :href="data.source_url" target="_blank" rel="noopener noreferrer">查看源股吧 <ExternalLink :size="12" /></a>
      </section>
      <div v-if="!data.effective_posts" class="panel empty-note"><p>此股票、口径及自然日范围暂无有效样本。可扩大范围或先运行采集；不会补入其他股票、标题或模拟数据。</p><details class="live-method"><summary>本机采集命令</summary><code>{{ data.collector_command }}</code></details></div>
      <section class="live-grid">
        <div class="panel live-score"><p class="subtle">舆论情绪分 · 0–100</p><strong>{{ data.opinion_score ?? '—' }}</strong><p>仅舆论维度 · 权重覆盖 {{ data.coverage }}%</p><p class="subtle">方向明确样本 {{ data.counts.bullish + data.counts.bearish }} 条 / 有效{{ scopeName }} {{ data.effective_posts }} 条；中性不进入分数分母。</p><p class="subtle">此分数尚不包含热度、赚钱效应、资金与变化速度。</p></div>
        <div class="panel"><h2>{{ scopeName }}方向分布</h2><div v-for="key in ['bullish', 'bearish', 'neutral']" :key="key" :class="['live-distribution', key]"><span>{{ names[key] }}</span><div class="live-track"><i :style="{ width: `${data.percentages[key] || 0}%` }"></i></div><b>{{ data.percentages[key] === null ? '—' : `${data.percentages[key]}%` }}</b><small>{{ data.counts[key] }} 条</small></div><p class="subtle">规则未识别的方向归入中性，尚未接入AI。</p></div>
      </section>
      <section class="panel live-stats" aria-label="样本与互动统计">
        <div><span>采集{{ scopeName }}</span><strong>{{ data.sampled_posts }}</strong></div><div><span>有效{{ scopeName }}</span><strong>{{ data.effective_posts }}</strong></div><div><span>有效作者</span><strong>{{ data.authors }}</strong></div><div><span>累计评论</span><strong>{{ data.comments.toLocaleString() }}</strong></div><div><span>累计阅读</span><strong>{{ data.reads.toLocaleString() }}</strong></div>
      </section>
      <section class="live-grid">
        <div class="panel"><h2>日线行情观察</h2><template v-if="lastBar"><p class="live-price">{{ lastBar.close.toFixed(2) }} <small>元 · {{ lastBar.date }}</small></p><p class="subtle">当日开盘 {{ lastBar.open.toFixed(2) }} · 未复权<br />观察时间 {{ time(data.quote?.observed_at) }}<br />{{ lastBar.is_current_session ? '包含当前交易日数据，盘中可能未收盘。' : '当前仅展示最近保存的历史日线。' }}</p></template><p v-else class="empty-note">尚无可用行情观察。</p><p class="subtle live-description">暂不计算价格背离及真实回测，等待持续积累可用时点一致的情绪快照。</p></div>
        <div class="panel"><h2>行为标签词频</h2><div class="live-tags"><span v-for="tag in data.keywords" :key="tag.text">{{ tag.text }} <b>{{ tag.count }}</b></span><p v-if="!data.keywords.length" class="subtle">未识别到预设行为词。</p></div><p class="subtle">仅统计当前口径中出现的规则标签，不等同于完整语义判断。</p></div>
      </section>
      <section class="panel"><h2>按发帖日期分组</h2><p class="subtle">{{ data.range.join(' 至 ') }} · 按当前已采集内容的发布时间分组，非当日实时信号；空缺日期不补零</p><div class="live-history"><div v-for="point in data.trend" :key="point.date"><span>{{ point.date }}</span><span>{{ point.posts }} 条有效{{ scopeName }}</span><b>舆论分 {{ point.opinion_score ?? '—' }}</b></div></div></section>
      <section v-if="data.examples.length" class="panel"><h2>样本溯源 · 最近5条有效{{ scopeName }}</h2><p class="subtle">以下为文本摘录，首次发现与修订观察时间均可核对。</p><article v-for="sample in data.examples" :key="sample.source_id" class="live-example"><div class="panel-heading"><a :href="sample.url" target="_blank" rel="noopener noreferrer">源帖 {{ sample.source_id }} ↗</a><span>{{ names[sample.label] }}</span></div><p class="live-excerpt">{{ sample.excerpt }}</p><small>发布 {{ time(sample.published_at) }} · 首次发现 {{ time(sample.first_seen_at) }} · 修订观察 {{ time(sample.observed_at) }}</small></article></section>
      <details class="panel live-method"><summary>查看采样与统计口径</summary><ul><li v-for="note in data.limitations" :key="note">{{ note }}</li></ul><p class="subtle">内容去重移除 {{ data.removed.duplicate_content || 0 }} 条；作者上限移除 {{ data.removed.author_cap || 0 }} 条。每位作者每天最多计3条，互动量不参与方向加权。</p></details>
    </template>
    <LiveHistory ref="historyPanel" :instrument-id="instrumentId" :content="content" />
    <LiveRankings ref="rankingPanel" :days="days" :content="content" @select="selectRanked" />
  </div>
</template>

<style scoped>
.live-search-label { display: block; font-size: 12px; color: var(--muted); margin: 16px 0 7px; }
.live-search { width: 100%; max-width: 430px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 11px; color: var(--text); }
.live-stock-options { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.live-stock-options button { display: block; text-align: left; }
.live-stock-options small { display: block; margin-top: 4px; color: var(--muted); }
.live-example { border-top: 1px solid var(--border); padding: 15px 0; margin-top: 14px; }
.live-example a { color: var(--accent); font-size: 12px; }
.live-example small { color: var(--muted); font-size: 11px; }
.live-excerpt { margin: 10px 0; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; }
.live-method code { display: block; margin-top: 12px; overflow-wrap: anywhere; user-select: all; font-size: 12px; }
.live-panel { display: grid; gap: 16px; }
.live-eyebrow { color: var(--accent); display: flex; gap: 7px; align-items: center; margin-bottom: 9px; font-size: 12px; }
.live-description { margin-top: 12px; line-height: 1.8; }
.live-periods { margin: 18px 0 10px; flex-wrap: wrap; }
.live-grid { display: grid; grid-template-columns: 1fr 1.45fr; gap: 16px; }
.live-score strong { display: block; font-size: 56px; color: var(--accent); font-variant-numeric: tabular-nums; margin: 10px 0; }
.live-score p { line-height: 1.8; }
.live-source p { line-height: 1.9; margin-top: 5px; overflow-wrap: anywhere; }
.live-badge { color: var(--accent); font-size: 12px; flex-shrink: 0; }
.live-warning { color: #e7b876; font-size: 13px; }
.live-source-link { display: inline-flex; gap: 5px; align-items: center; padding-left: 0; }
.live-distribution { display: grid; grid-template-columns: 105px 1fr 52px 42px; align-items: center; gap: 10px; margin: 18px 0; font-size: 12px; }
.live-distribution b, .live-distribution small { text-align: right; }
.live-track { background: var(--surface-alt); height: 7px; border-radius: 4px; overflow: hidden; }
.live-track i { height: 100%; display: block; background: var(--muted); }
.bullish .live-track i { background: var(--bull); }
.bearish .live-track i { background: var(--bear); }
.live-stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 18px; }
.live-stats span { color: var(--muted); font-size: 12px; display: block; }
.live-stats strong { font-size: 24px; display: block; margin-top: 10px; font-variant-numeric: tabular-nums; }
.live-price { font-size: 30px; margin: 18px 0 10px; }
.live-price small { color: var(--muted); font-size: 12px; }
.live-tags { display: flex; gap: 12px; flex-wrap: wrap; margin: 22px 0; }
.live-tags span { background: var(--accent-bg); color: var(--accent); border-radius: 5px; padding: 9px 12px; }
.live-tags b { margin-left: 6px; }
.live-history { margin-top: 14px; }
.live-history div { display: flex; justify-content: space-between; gap: 10px; border-top: 1px solid var(--border); padding: 12px 0; font-size: 12px; }
.live-method summary { cursor: pointer; }
.live-method ul { padding-left: 18px; line-height: 1.9; color: var(--muted); font-size: 12px; }
@media(max-width: 760px) { .live-grid { grid-template-columns: 1fr; } .live-stats { grid-template-columns: repeat(2, 1fr); } .live-intro .panel-heading { align-items: flex-start; flex-direction: column; } .live-distribution { grid-template-columns: 94px 1fr 48px 30px; gap: 5px; } .live-source .panel-heading { flex-wrap: wrap; } }
</style>
