<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, computed } from 'vue';
import { request } from '../api';
type Point = {id: number; available_at: string; session_date: string; version: string;
  opinion_score: number | null; effective_posts: number; directional_posts: number; authors: number;
  window_days: number | null; range: string[] | null};
type Timeline = {points: Point[]; total_points: number; truncated: boolean; basis: string};
const props = defineProps<{instrumentId: string; content: 'titles' | 'body'}>();
const timeline = ref<Timeline | null>(null), busy = ref(false), error = ref(''), table = ref(false);
let version = 0;
const time = (s: string) => new Date(s).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false});
const points = computed(() => timeline.value?.points || []);
const times = computed(() => points.value.map(p => Date.parse(p.available_at)));
function x(i: number) {
  const start = times.value[0], end = times.value.at(-1);
  return !start || !end || start === end ? 370 : 45 + (times.value[i] - start) / (end - start) * 640;
}
const y = (value: number) => 212 - value * 1.75;
const segments = computed(() => points.value.slice(1).map((p, i) => ({a: points.value[i], b: p, i})).filter(s =>
  s.a.opinion_score !== null && s.b.opinion_score !== null && s.a.window_days === s.b.window_days &&
  !!s.a.window_days && s.a.effective_posts > 0 && s.b.effective_posts > 0));
async function load() {
  const id = ++version;
  timeline.value = null; error.value = ''; busy.value = true;
  try {
    const result = await request<Timeline>(`/live/timeline?${new URLSearchParams({instrument_id: props.instrumentId, content: props.content})}`);
    if (id === version) timeline.value = result;
  } catch (e) { if (id === version) error.value = e instanceof Error ? e.message : '读取失败'; }
  finally { if (id === version) busy.value = false; }
}
watch(() => [props.instrumentId, props.content], load);
onMounted(load);
onUnmounted(() => ++version);
defineExpose({load});
</script>
<template>
  <section class="panel live-timeline">
    <div class="panel-heading"><h2>采集快照趋势</h2><button class="text-button" @click="table = !table">{{ table ? '查看图表' : '查看快照明细' }}</button></div>
    <p class="subtle timeline-note">最近30天 · 横轴为实际采集完成时间，与上方自然日筛选独立；不以今日分类回写过去。</p>
    <p v-if="busy" role="status" class="timeline-note">正在读取历史快照…</p>
    <p v-else-if="error" role="alert" class="timeline-warning">{{ error }}</p>
    <template v-else-if="timeline">
      <p v-if="!points.length" class="subtle timeline-note">尚无此股票、此内容口径的已保存快照。</p>
      <template v-else>
        <p v-if="points.filter(p => p.opinion_score !== null).length < 2" class="timeline-warning">方向明确的历史快照不足2个，暂不能判断变化趋势。没有方向样本的快照保留为空值。</p>
        <div v-if="table" class="timeline-scroll" tabindex="0" aria-label="采集快照明细，可横向滚动"><table><thead><tr><th>采集完成（北京时间）</th><th>窗口</th><th>有效样本 / 作者</th><th>方向样本</th><th>舆论分</th></tr></thead><tbody><tr v-for="p in points" :key="p.id"><td>{{ time(p.available_at) }}</td><td>{{ p.window_days ?? '未知' }}自然日<br /><small>{{ p.range?.join(' 至 ') }}</small></td><td>{{ p.effective_posts }} / {{ p.authors }}</td><td>{{ p.directional_posts }}</td><td>{{ p.opinion_score ?? '—' }}</td></tr></tbody></table></div>
        <svg v-else-if="points.some(p => p.opinion_score !== null)" viewBox="0 0 730 252" role="img" aria-label="保存的采集时点舆论分，纵轴0到100，无方向样本处断开，不同窗口不连线；明细可切换查看">
          <g v-for="tick in [0,20,40,60,80,100]" :key="tick"><line x1="45" x2="685" :y1="y(tick)" :y2="y(tick)" class="grid" /><text x="34" :y="y(tick)+4" text-anchor="end">{{ tick }}</text></g>
          <line v-for="s in segments" :key="s.b.id" :x1="x(s.i)" :x2="x(s.i+1)" :y1="y(s.a.opinion_score!)" :y2="y(s.b.opinion_score!)" class="signal" />
          <template v-for="(p,i) in points" :key="p.id"><circle v-if="p.opinion_score !== null" :cx="x(i)" :cy="y(p.opinion_score)" r="4" fill="var(--accent)"><title>{{ time(p.available_at) }}：{{ p.opinion_score }}分 / {{ p.effective_posts }}样本 / {{ p.window_days }}自然日</title></circle></template>
          <text x="45" y="241">{{ time(points[0].available_at) }}</text><text v-if="points.length > 1" x="685" y="241" text-anchor="end">{{ time(points.at(-1)!.available_at) }}</text>
        </svg>
        <p v-else-if="!table" class="subtle timeline-note">已保存 {{ points.length }} 个快照，均无方向分。可查看明细；不会画成50分中性线。</p>
      </template>
      <p v-if="timeline.truncated" class="timeline-warning">共 {{ timeline.total_points }} 个快照，仅显示最近200个。</p>
      <p class="subtle timeline-note">快照保持采集当时的统计窗口与值；空值、未知窗口和不同长度窗口不连线。采集间隔和当天样本覆盖变化会影响分数，曲线不等于价格预测。</p>
    </template>
  </section>
</template>
<style scoped>
.live-timeline { min-width: 0; }
.timeline-note { margin-top: 12px; font-size: 12px; line-height: 1.9; }
.timeline-warning { color: #e7b876; margin: 12px 0; font-size: 13px; line-height: 1.8; }
svg { width: 100%; margin-top: 14px; }
svg text { fill: var(--muted); font-size: 11px; }
.grid { stroke: var(--border); stroke-width: 1; }
.signal { stroke: var(--accent); stroke-width: 2; }
.timeline-scroll { max-width: 100%; overflow-x: auto; margin-top: 18px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
td,th { text-align: left; padding: 13px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th,small { color: var(--muted); font-weight: 500; }
@media(max-width:760px) { .panel-heading { flex-wrap: wrap; gap: 12px; } }
</style>
