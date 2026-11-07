<script setup lang="ts">
import { computed } from "vue";
import { Download, Info } from "lucide-vue-next";
import TrendChart from "./TrendChart.vue";
import { exportURL } from "../api";
import type { Analysis } from "../types";
const props = defineProps<{ analysis: Analysis }>();
defineEmits<{ backtest: [] }>();
const m = computed(() => props.analysis.metrics);
const fmt = (n: number) =>
  n >= 10000 ? `${(n / 10000).toFixed(1)}万` : n.toLocaleString("zh-CN");
const weights = [
  ["opinion", "股吧多空", 35],
  ["attention", "方向校准热度", 20],
  ["breadth", "赚钱效应", 20],
  ["activity", "资金活跃", 15],
  ["velocity", "变化速度", 10],
] as const;
</script>
<template>
  <div class="result-grid">
    <section class="panel score-panel">
      <div class="panel-heading">
        <div>
          <h2>{{ analysis.instrument.name }}</h2>
          <p class="subtle">
            {{ analysis.instrument.id }} · {{ analysis.period }}个交易日 ·
            模拟快照
          </p>
        </div>

      </div>
      <div class="score-line">
        <div>
          <strong class="score">{{ m.score ?? "—" }}</strong>
          <p>综合情绪指数</p>
        </div>
        <span class="state" :class="(m.score ?? 0) >= 60 ? 'bull' : 'bear'">{{
          m.state
        }}</span>
      </div>
      <div class="thermometer">
        <i
          :style="{ left: `${Math.max(1, Math.min(99, m.score ?? 50))}%` }"
        ></i>
      </div>
      <div class="sentiment-ratios">
        <div class="bull">
          <strong>{{ m.bullish_pct }}<small>%</small></strong
          ><span>看多</span>
        </div>
        <div class="bear">
          <strong>{{ m.bearish_pct }}<small>%</small></strong
          ><span>看空</span>
        </div>
        <div>
          <strong>{{ m.neutral_pct }}<small>%</small></strong
          ><span>中性</span>
        </div>
      </div>
      <div class="distribution" aria-label="多空分布">
        <i class="bull-bg" :style="{ width: `${m.bullish_pct}%` }"></i
        ><i class="bear-bg" :style="{ width: `${m.bearish_pct}%` }"></i
        ><i class="neutral-bg" :style="{ width: `${m.neutral_pct}%` }"></i>
      </div>
      <div class="count-strip">
        <span
          >发帖 <b>{{ fmt(m.posts) }}</b></span
        ><span
          >评论 <b>{{ fmt(m.comments) }}</b></span
        ><span
          >阅读 <b>{{ fmt(m.reads) }}</b></span
        >
      </div>
    </section>
    <TrendChart :points="analysis.trend" />
    <section class="panel keywords-panel">
      <h2>情绪关键词</h2>
      <p class="subtle">最新模拟快照的词频，不是交易信号</p>
      <div class="word-cloud">
        <span
          v-for="(w, i) in analysis.keywords"
          :key="w.text"
          :class="i === 0 ? 'bull' : i === 1 ? 'bear' : ''"
          :style="{ fontSize: `${Math.max(14, 28 - i * 2)}px` }"
          :title="`${w.count}次（模拟）`"
          >{{ w.text }}</span
        >
      </div>
    </section>
    <section class="panel signal-panel">
      <h2>信号观察</h2>
      <div v-for="s in analysis.signals" :key="s.name" class="signal-row">
        <Info :size="16" :class="s.tone === 'risk' ? 'bull' : 'bear'" />
        <div>
          <strong>{{ s.name }}</strong>
          <p>{{ s.reason }}</p>
        </div>
      </div>
      <p class="subtle">基于最新单日快照，阈值未经样本外验证。</p>
    </section>
  </div>
  <details class="panel methodology">
    <summary>
      指数口径与数据覆盖 <span>{{ m.coverage }}% 初始权重可用</span>
    </summary>
    <div class="method-grid">
      <div v-for="[key, label, weight] in weights" :key="key" class="dimension">
        <span
          >{{ label }} <small>{{ weight }}%</small></span
        ><b>{{ m.dimensions[key]?.toFixed(1) ?? "未接入" }}</b>
      </div>
    </div>
    <p>
      综合分为所选周期内多空总量得分与各可用维度日均分的加权值。缺失维度剔除后按可用权重重新归一，不默认为中性；分数不能直接与覆盖率不同的对象比较。
    </p>
    <p>
      多空比 {{ m.ratio ?? "无看空样本" }} · 方向一致性 {{ m.consistency }}% ·
      多空分歧 {{ m.disagreement }}%（仅在有方向帖子中计算）。中性占比另列。
    </p>
    <p v-if="analysis.instrument.scope.boards">
      模拟股吧覆盖口径：{{
        analysis.instrument.scope.boards.join("、")
      }}（真实来源待接入）。
    </p>
    <p v-if="analysis.instrument.scope.breadth">
      宽度口径：{{ analysis.instrument.scope.breadth }}。
    </p>
    <p v-if="analysis.instrument.scope.universe">
      {{ analysis.instrument.scope.universe }}；演示成分：{{
        analysis.instrument.scope.members?.join("、")
      }}。
    </p>
    <p v-for="l in analysis.limitations" :key="l">{{ l }}</p>
  </details>
  <div class="result-toolbar">
    <span class="subtle"
      >{{ analysis.range.join(" — ") }} · 模拟样本 · research-v1</span
    >
    <div>
      <a
        class="secondary button"
        :href="exportURL(analysis.instrument.id, analysis.period)"
        ><Download :size="15" />导出 Excel</a
      ><button class="secondary" @click="$emit('backtest')">
        查看历史回测
      </button>
    </div>
  </div>
</template>
