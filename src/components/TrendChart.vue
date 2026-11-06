<script setup lang="ts">
import { computed, ref } from "vue";
import type { Point } from "../types";
const props = defineProps<{ points: Point[] }>();
const showTable = ref(false);
const priceValues = computed(() =>
  props.points.map((p) => p.close).filter((p): p is number => p !== null),
);
const priceNorm = (p: number | null) => {
  if (p === null) return 50;
  const min = Math.min(...priceValues.value),
    max = Math.max(...priceValues.value);
  return max === min ? 50 : 20 + ((p - min) / (max - min)) * 60;
};
const x = (i: number) => 42 + (i / Math.max(1, props.points.length - 1)) * 650;
const y = (v: number) => 214 - v * 1.78;
const line = (key: "score" | "price") =>
  props.points
    .map(
      (p, i) => `${x(i)},${y(key === "score" ? p.score : priceNorm(p.close))}`,
    )
    .join(" ");
</script>
<template>
  <section class="panel chart-panel" aria-labelledby="chart-heading">
    <div class="panel-heading">
      <h2 id="chart-heading">情绪与价格趋势</h2>
      <button class="text-button" @click="showTable = !showTable">
        {{ showTable ? "查看图表" : "查看明细" }}
      </button>
    </div>
    <div class="legend">
      <span><i class="teal-line"></i>情绪指数</span
      ><span><i class="price-line"></i>区间缩放价格</span>
    </div>
    <div v-if="showTable" class="table-scroll chart-data">
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>情绪</th>
            <th>模拟收盘价</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in points" :key="p.date">
            <td>{{ p.date }}</td>
            <td>{{ p.score }}</td>
            <td>{{ p.close?.toFixed(2) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <svg
      v-else
      viewBox="0 0 720 252"
      role="img"
      aria-label="每日情绪指数和区间缩放后的模拟价格，价格不使用情绪分值单位。可点击查看明细。"
    >
      <g v-for="tick in [0, 20, 40, 60, 80, 100]" :key="tick">
        <line x1="42" x2="692" :y1="y(tick)" :y2="y(tick)" class="grid-line" />
        <text x="30" :y="y(tick) + 4" text-anchor="end">{{ tick }}</text>
      </g>
      <g v-for="(p, i) in points" :key="p.date">
        <line
          :x1="x(i)"
          :x2="x(i)"
          y1="36"
          y2="214"
          class="grid-line vertical"
        />
        <text
          v-if="points.length <= 10 || i % 3 === 0 || i === points.length - 1"
          :x="x(i)"
          y="241"
          text-anchor="middle"
        >
          {{ p.date.slice(5) }}
        </text>
      </g>
      <polyline :points="line('price')" class="price-path" />
      <polyline :points="line('score')" class="sentiment-path" />
      <circle
        v-for="(p, i) in points"
        :key="p.date"
        :cx="x(i)"
        :cy="y(p.score)"
        r="3.5"
        fill="var(--accent)"
      >
        <title>
          {{ p.date }}：情绪 {{ p.score }} / 模拟价 {{ p.close?.toFixed(2) }}
        </title>
      </circle>
    </svg>
    <p class="chart-note">
      价格在本区间内缩放至20–80，仅比较变化方向；不是收益率。
    </p>
  </section>
</template>
