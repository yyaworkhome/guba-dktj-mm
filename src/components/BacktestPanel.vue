<script setup lang="ts">
import type { Backtest } from "../types";
defineProps<{ data: Backtest | null; busy: boolean }>();
const threshold = defineModel<number>("threshold", { required: true });
defineEmits<{ run: [] }>();
</script>
<template>
  <section class="panel backtest-panel">
    <div class="panel-heading">
      <h2>历史信号回测</h2>
      <span class="subtle">模拟数据 · 非预测结果</span>
    </div>
    <form class="inline-form" @submit.prevent="$emit('run')">
      <label for="threshold">信号条件：综合分 ≥</label
      ><input
        id="threshold"
        v-model.number="threshold"
        type="number"
        min="0"
        max="100"
        required
      /><button class="primary" :disabled="busy">
        {{ busy ? "计算中…" : "运行回测" }}
      </button>
    </form>
    <template v-if="data"
      ><p class="subtle">
        当前结果条件：综合分 ≥ {{ data.threshold }}。{{ data.method }}
      </p>
      <p v-if="threshold !== data.threshold" class="bull subtle">
        阈值已修改，请重新运行回测。
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>持有周期</th>
              <th>完整样本</th>
              <th>未成熟剔除</th>
              <th>平均毛收益</th>
              <th>上涨比例</th>
              <th>95%区间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in data.results" :key="r.horizon">
              <td>{{ r.horizon }}个交易日</td>
              <td>{{ r.samples }}</td>
              <td>{{ r.excluded }}</td>
              <td :class="(r.mean_return ?? 0) >= 0 ? 'bull' : 'bear'">
                {{
                  r.mean_return === null
                    ? "—"
                    : `${r.mean_return > 0 ? "+" : ""}${r.mean_return.toFixed(2)}%`
                }}
              </td>
              <td>{{ r.up_pct === null ? "—" : `${r.up_pct}%` }}</td>
              <td>
                {{ r.ci_low === null ? "—" : `${r.ci_low}% – ${r.ci_high}%` }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-for="l in data.limitations" :key="l" class="subtle">
        {{ l }}
      </p></template
    >
    <p v-else class="empty-note">
      选择分析对象后运行。没有足够未来价格的事件不会计入胜率。
    </p>
  </section>
</template>
