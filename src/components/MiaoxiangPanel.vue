<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, computed } from 'vue';
import { Search, Download, RefreshCw } from 'lucide-vue-next';
import { request, download } from '../api';

type QueryKind = 'data' | 'screen';
type Entry = { id: number; kind: QueryKind; query: string; status: string; observed_at: string };
type Table = { title: string; entity: string; source_type: string; total_rows: number;
  columns: {key: string; label: string; date: string}[]; rows: string[][] };
type Result = Entry & { cached: boolean; source: string; conditions: string; tables?: Table[];
  entities?: {name: string; code: string; type: string}[]; notes?: string[]; truncated?: boolean; error?: string };
const props = defineProps<{ username: string }>();
const emit = defineEmits<{ login: [] }>();
const kind = ref<QueryKind>('data'), query = ref('东方财富300059最新价');
const status = ref<{ configured: boolean } | null>(null), history = ref<Entry[]>([]);
const result = ref<Result | null>(null), error = ref(''), busy = ref(false), exporting = ref(false);
const failedDraft = ref(false);
let version = 0, disposed = false;
const names = { data: '行情 / 财务', screen: '板块 / 条件筛选' };
const examples = computed(() => kind.value === 'data'
  ? ['东方财富300059最新价', '上证指数000001.SH最新收盘价', '宁德时代300750最新换手率和成交额']
  : ['半导体行业涨跌幅前10名', '沪深300成分股涨跌幅前10名', '机器人概念成交额前10名']);
const time = (value: string) => new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
const edited = computed(() => result.value && (result.value.query !== query.value.trim() || result.value.kind !== kind.value));
function failure(e: unknown) { error.value = e instanceof Error ? e.message : '查询失败，请稍后重试。'; }
async function loadHistory() {
  const user = props.username;
  if (!user) { history.value = []; return; }
  const rows = await request<Entry[]>('/mx/history');
  if (!disposed && user === props.username) history.value = rows;
}
async function run() {
  if (!props.username) { emit('login'); return; }
  if (busy.value) return;
  const current = ++version;
  error.value = ''; result.value = null; failedDraft.value = false; busy.value = true;
  try {
    const data = await request<Result>('/mx/query', {method: 'POST', body: JSON.stringify({kind: kind.value, query: query.value})}, 45000);
    if (current === version && !disposed) result.value = data;
  } catch (e) {
    if (current === version && !disposed) { failure(e); failedDraft.value = true; }
  } finally {
    if (current === version && !disposed) {
      busy.value = false;
      try { await loadHistory(); } catch (e) { failure(e); }
    }
  }
}
async function open(entry: Entry) {
  if (busy.value) return;
  const current = ++version;
  busy.value = true; error.value = ''; result.value = null; failedDraft.value = false;
  try {
    const data = await request<Result>(`/mx/history/${entry.id}`);
    if (current === version && !disposed) { result.value = data; query.value = data.query; kind.value = data.kind; }
  } catch (e) { if (current === version && !disposed) failure(e); }
  finally { if (current === version && !disposed) busy.value = false; }
}
async function exportResult() {
  if (!result.value) return;
  exporting.value = true;
  try { await download(`/mx/history/${result.value.id}/export`, `miaoxiang-${result.value.id}.xlsx`); }
  catch (e) { failure(e); }
  finally { exporting.value = false; }
}
watch(() => props.username, async () => {
  ++version; result.value = null; history.value = []; error.value = ''; busy.value = false;
  try { await loadHistory(); } catch (e) { failure(e); }
});
onMounted(async () => {
  try { status.value = await request('/mx/status'); await loadHistory(); }
  catch (e) { failure(e); }
});
onUnmounted(() => { disposed = true; ++version; });
</script>

<template>
  <div class="mx-panel">
    <section class="panel">
      <div class="panel-heading"><h2>妙想数据查询</h2><span class="subtle">{{ status ? (status.configured ? '数据服务已配置' : '数据服务未配置') : '检查服务中…' }}</span></div>
      <p class="subtle mx-intro">查询个股、指数行情和板块成分，保留每次获取时间与接口解析条件。</p>
      <form @submit.prevent="run">
        <div class="mode-tabs" role="group" aria-label="妙想查询类型">
          <button v-for="(label, key) in names" :key="key" type="button" :class="{selected: kind === key}" :aria-pressed="kind === key" :disabled="busy" @click="kind = key">{{ label }}</button>
        </div>
        <label for="mx-query">输入查询问句</label>
        <div class="mx-search-row"><div class="mx-input"><Search :size="17" aria-hidden="true" /><input id="mx-query" v-model="query" :disabled="busy" required minlength="2" maxlength="160" placeholder="如：半导体行业涨跌幅前10名" /></div><button class="primary" :disabled="busy || status?.configured === false"><RefreshCw v-if="busy" :size="16" class="spin" />{{ busy ? '正在查询…' : (username ? '查询并保存' : '登录后查询') }}</button></div>
        <div class="mx-examples" aria-label="常用查询"><span class="subtle">试试</span><button v-for="example in examples" :key="example" type="button" class="text-button" :disabled="busy" @click="query = example">{{ example }}</button></div>
      </form>
      <p v-if="!username" class="subtle">登录本系统账号后，查询结果与历史记录按账号保存。<button class="text-button" @click="emit('login')">登录 / 注册</button></p>
      <p class="subtle mx-note">输入代码时请带名称或交易所，例如“上证指数000001.SH”或“平安银行000001.SZ”。请核对返回证券与解析条件；自然语言匹配可能存在偏差。</p>
      <p v-if="status?.configured === false" class="mx-warning">请在后端配置 MX_APIKEY 后重新打开本页。</p>
    </section>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="failedDraft" class="subtle">本次查询未成功。可从历史记录查看已保存的结果。</p>
    <section v-if="result" class="panel" aria-live="polite">
      <div class="panel-heading"><h2>{{ result.query }}</h2><button v-if="result.status === 'success'" class="secondary" :disabled="exporting" @click="exportResult"><Download :size="15" />{{ exporting ? '正在导出…' : '导出 Excel' }}</button></div>
      <p class="subtle mx-result-time">{{ result.source }} · 获取于 {{ time(result.observed_at) }}（北京时间）<br />{{ result.cached ? '复用十分钟内的已保存结果，时间未刷新。' : '已保存查询观察记录。' }} 获取时间不等于行情发生时间，请核对表格中的数据日期。</p>
      <p v-if="edited" class="mx-warning">输入条件已修改；下方仍是“{{ result.query }}”的历史结果，请重新查询。</p>
      <p v-if="result.status === 'failed'" class="mx-warning">{{ result.error }}</p>
      <p v-if="result.entities?.length" class="mx-entities"><span v-for="e in result.entities" :key="`${e.type}-${e.code}`">{{ e.name }} {{ e.code }} · {{ e.type }}</span></p>
      <p v-if="result.conditions" class="subtle mx-condition">接口解析条件：{{ result.conditions }}</p>
      <div v-for="(table, index) in result.tables" :key="index" class="mx-result-table">
        <h3>{{ table.title }}</h3><p class="subtle">{{ table.entity }} · {{ table.source_type }} · 已保存 {{ table.rows.length }} / {{ table.total_rows }} 行</p>
        <div class="mx-table-scroll" tabindex="0" :aria-label="table.title + '，可横向滚动'"><table><thead><tr><th v-for="column in table.columns" :key="column.key">{{ column.label }}<small v-if="column.date">{{ column.date }}</small></th></tr></thead><tbody><tr v-for="(row, i) in table.rows" :key="i"><td v-for="(cell, j) in row" :key="j">{{ cell || '—' }}</td></tr></tbody></table></div>
        <p v-if="!table.rows.length" class="subtle">接口返回零条匹配记录。</p>
      </div>
      <p v-if="result.truncated" class="mx-warning">结果已限量保存：最多8张表，每表50行、24列。导出范围与页面一致，请缩小查询范围获取完整结果。</p>
      <p v-for="note in result.notes" :key="note" class="subtle mx-note">{{ note }}</p>
    </section>
    <section v-if="username" class="panel">
      <div class="panel-heading"><h2>我的查询记录</h2><span class="subtle">最近20次 · 仅当前账号</span></div>
      <p v-if="!history.length" class="subtle mx-intro">暂无查询记录。首次查询成功后可在这里回看；失败记录也会保留。</p>
      <button v-for="entry in history" :key="entry.id" class="mx-history" :disabled="busy" @click="open(entry)"><span>{{ entry.query }}<small>{{ names[entry.kind] }} · {{ entry.status === 'success' ? '成功' : '失败' }}</small></span><time>{{ time(entry.observed_at) }}</time></button>
    </section>
    <p class="subtle mx-note">此处提供妙想返回的数据观察，不计算散户情绪分，也不混入情绪回测。板块筛选结果不是完整历史成分表；资金流向指标属于成交估算，不能直接识别主力身份。</p>
  </div>
</template>

<style scoped>
.mx-panel { display: grid; gap: 16px; min-width: 0; }
.mx-panel .panel { min-width: 0; }
.mx-intro { margin: 12px 0 20px; line-height: 1.8; }
.mx-panel form { margin: 18px 0; }
.mx-panel label { display: block; margin: 20px 0 9px; font-size: 13px; }
.mx-search-row { display: flex; gap: 12px; }
.mx-input { flex: 1; min-width: 0; display: flex; align-items: center; padding: 0 12px; gap: 10px; border: 1px solid var(--border); background: var(--bg); border-radius: 6px; }
.mx-input svg { flex-shrink: 0; color: var(--muted); }
.mx-input input { width: 100%; min-width: 0; color: var(--text); background: none; border: 0; padding: 13px 0; }
.mx-examples { display: flex; align-items: baseline; gap: 8px 14px; flex-wrap: wrap; margin-top: 13px; font-size: 12px; }
.mx-examples .text-button { padding: 0; text-align: left; }
.mx-note, .mx-result-time, .mx-condition { font-size: 12px; line-height: 1.9; margin-top: 12px; overflow-wrap: anywhere; }
.mx-warning { color: #e7b876; line-height: 1.9; font-size: 13px; margin-top: 12px; }
.mx-result-time { margin-bottom: 16px; }
.mx-entities { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; }
.mx-result-table { margin-top: 24px; }
.mx-result-table h3 { font-size: 15px; margin-bottom: 8px; overflow-wrap: anywhere; }
.mx-result-table > p { font-size: 12px; margin: 8px 0 12px; }
.mx-table-scroll { overflow-x: auto; max-width: 100%; border-top: 1px solid var(--border); }
.mx-table-scroll table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mx-table-scroll th, .mx-table-scroll td { padding: 13px 14px; text-align: left; border-bottom: 1px solid var(--border); min-width: 100px; max-width: 280px; overflow-wrap: anywhere; }
.mx-table-scroll th { color: var(--muted); font-weight: 500; white-space: nowrap; }
.mx-table-scroll th small { display: block; margin-top: 5px; }
.mx-history { width: 100%; text-align: left; display: flex; justify-content: space-between; gap: 16px; padding: 16px 0; border-bottom: 1px solid var(--border); font-size: 13px; line-height: 1.8; }
.mx-history small { display: block; color: var(--muted); font-size: 11px; margin-top: 3px; }
.mx-history time { color: var(--muted); flex-shrink: 0; font-size: 12px; }
@media(max-width: 760px) {
  .mx-search-row { flex-direction: column; }
  .mx-search-row .primary { justify-content: center; }
  .panel-heading { flex-wrap: wrap; gap: 12px; }
  .mx-history { flex-direction: column; gap: 5px; }
}
</style>
