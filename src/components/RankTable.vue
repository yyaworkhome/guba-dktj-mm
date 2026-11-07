<script setup lang="ts">
import { ArrowUpRight } from "lucide-vue-next";
import type { Ranking, Instrument } from "../types";
defineProps<{ rows: Ranking[]; title?: string }>();
defineEmits<{ select: [value: Instrument] }>();
</script>
<template>
  <section class="panel ranking-panel">
    <div class="panel-heading">
      <h2>{{ title || "个股情绪排行" }}</h2>
      <span class="subtle">最新单日 · 模拟</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>股票名称 / 代码</th>
            <th>情绪指数</th>
            <th>看多占比</th>
            <th>看空占比</th>
            <th>讨论热度变化</th>
          <th aria-label="查看"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(r, i) in rows" :key="r.id">
            <td class="rank-number">{{ String(i + 1).padStart(2, "0") }}</td>
            <td>
              <button class="table-link" @click="$emit('select', r)">
                <strong>{{ r.name }}</strong
                ><span>{{ r.code }} · {{ r.exchange }}</span>
              </button>
            </td>
            <td class="numeric">{{ r.score }}</td>
            <td class="bull">{{ r.bullish_pct }}%</td>
            <td class="bear">{{ r.bearish_pct }}%</td>
            <td :class="r.heat_change >= 0 ? 'bull' : 'bear'">
              {{ r.heat_change > 0 ? "+" : "" }}{{ r.heat_change }}%
            </td>
            <td>
              <button
                class="icon-button"
                :aria-label="`分析${r.name}`"
                @click="$emit('select', r)"
              >
                <ArrowUpRight :size="17" />
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="7">暂无样本</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
