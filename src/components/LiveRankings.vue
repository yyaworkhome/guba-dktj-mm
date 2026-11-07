<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { request } from '../api';
type Row = {id: string; name: string; code: string; rank?: number; opinion_score: number | null;
  bullish_pct: number | null; bearish_pct: number | null; effective_posts: number; authors: number;
  directional_posts: number; last_observation: string | null; reasons: string[]};
type Ranks = {ranked: Row[]; unranked: Row[]; scope: string; scope_size:number; limitations: string[]; as_of: string};
const props = defineProps<{days: number; content: 'titles' | 'body'}>();
const emit = defineEmits<{select: [id: string]}>();
const data = ref<Ranks | null>(null), sort = ref('opinion_score'), busy = ref(false), error = ref('');
let version = 0;
const names: Record<string,string> = {opinion_score:'舆论分',bullish_pct:'看多占比',bearish_pct:'看空占比',effective_posts:'有效样本量'};
const time = (s: string | null) => s ? new Date(s).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '尚无';
async function load() {
  const id = ++version;
  busy.value = true; data.value = null; error.value = '';
  try {
    const result = await request<Ranks>(`/live/rankings?${new URLSearchParams({days:String(props.days), content:props.content, sort:sort.value})}`);
    if (id === version) data.value = result;
  } catch(e) { if (id === version) error.value = e instanceof Error ? e.message : '读取失败'; }
  finally { if (id === version) busy.value = false; }
}
watch(() => [props.days,props.content,sort.value], load);
onMounted(load);
onUnmounted(() => ++version);
defineExpose({load});
</script>
<template>
  <section class="panel live-rankings">
    <div class="panel-heading"><h2>真实样本观察榜<span v-if="data"> · {{data.scope_size}}股</span></h2><label>排序 <select v-model="sort" aria-label="真实榜单排序"><option v-for="(name,key) in names" :value="key" :key="key">{{ name }}</option></select></label></div>
    <p class="subtle rank-note">近{{ days }}自然日 · {{ content === 'body' ? '正文' : '标题' }}口径 · 已采集股票与初始观察样本，不是全市场排行。入榜需至少20条有效样本、10位作者、10条方向样本，且观察未超过24小时。</p>
    <p v-if="busy" class="rank-note" role="status">正在读取真实样本榜…</p>
    <p v-else-if="error" class="rank-warning" role="alert">{{ error }} · 未使用演示排名替代。</p>
    <template v-else-if="data">
      <p v-if="!data.ranked.length" class="rank-warning">当前没有股票满足入榜条件。下方列出覆盖情况，不生成排名。</p>
      <div v-else class="rank-scroll" tabindex="0" aria-label="真实样本榜，可横向滚动"><table><thead><tr><th>名次 / 股票</th><th>舆论分</th><th>看多 / 看空</th><th>样本 / 作者 / 方向</th><th>最近观察（北京时间）</th></tr></thead><tbody><tr v-for="r in data.ranked" :key="r.id"><td><button class="text-button" @click="emit('select',r.id)">{{ r.rank }} · {{ r.name }} {{ r.code }}</button></td><td>{{ r.opinion_score ?? '—' }}</td><td>{{ r.bullish_pct ?? '—' }}% / {{ r.bearish_pct ?? '—' }}%</td><td>{{ r.effective_posts }} / {{ r.authors }} / {{ r.directional_posts }}</td><td>{{ time(r.last_observation) }}</td></tr></tbody></table></div>
      <details :open="!data.ranked.length" class="rank-coverage"><summary>未入榜覆盖情况 · {{ data.unranked.length }}只</summary><div v-for="r in data.unranked" :key="r.id" class="rank-row"><button class="text-button" @click="emit('select', r.id)">{{ r.name }} {{ r.code }}</button><p>{{ r.reasons.join('；') }}</p><small>最近观察 {{ time(r.last_observation) }}</small></div></details>
      <p class="subtle rank-note">查询截止 {{ time(data.as_of) }}。最低门槛是待校准的开发规则；各股采集时点可能不同。样本量不是新增发帖速度，不据此推断情绪升温或收益。</p>
    </template>
  </section>
</template>
<style scoped>
.live-rankings { min-width: 0; }
label { color: var(--muted); font-size: 12px; }
select { padding: 7px 9px; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 5px; margin-left: 5px; }
.rank-note { margin-top: 12px; font-size: 12px; line-height: 1.9; }
.rank-warning { color: #e7b876; margin: 14px 0; line-height: 1.8; font-size: 13px; }
.rank-scroll { overflow-x: auto; max-width: 100%; margin-top: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
td,th { padding: 14px 12px; text-align: left; white-space: nowrap; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 500; }
.rank-coverage { margin-top: 18px; font-size: 13px; }
summary { cursor: pointer; }
.rank-row { border-top: 1px solid var(--border); margin-top: 12px; padding-top: 12px; }
.rank-row p,.rank-row small { color: var(--muted); margin-top: 5px; display: block; font-size: 12px; line-height: 1.9; }
@media(max-width:760px) { .panel-heading { flex-wrap: wrap; gap: 12px; } }
</style>
