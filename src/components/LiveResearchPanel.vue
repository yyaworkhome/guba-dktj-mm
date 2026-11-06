<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue';
import {request} from '../api';
import LiveRankings from './LiveRankings.vue';
import LiveHistory from './LiveHistory.vue';
import LiveSignalScreen from './LiveSignalScreen.vue';
const props=defineProps<{view:'rankings'|'signals'|'backtest'}>();
const emit=defineEmits<{select:[id:string]}>();
const days=ref(3),content=ref<'titles'|'body'>('titles'),iid=ref('XSHE:300059');
const stocks=ref<{id:string;name:string}[]>([]),error=ref('');
let disposed=false;
onMounted(async()=>{try{const rows=await request<{id:string;name:string}[]>('/live/instruments');if(!disposed)stocks.value=rows;}catch(e){if(!disposed)error.value=e instanceof Error?e.message:'读取失败';}});
onUnmounted(()=>{disposed=true;});
</script>
<template>
  <section class="panel real-research">
    <h2>{{view==='rankings'?'真实样本排行':view==='signals'?'真实样本异动研究':'真实回测 · 数据准备'}}</h2>
    <p class="subtle section-note">{{view==='signals'?'先检查方向样本与作者覆盖，再筛选方向集中的股票。AI选股、跨日反转和价格背离尚未启用，不将高舆论分标记为买入机会。':view==='backtest'?'当前可以查询采集时点快照，核对真实信号是否已留痕。完整交易日历、复权价格与未来可成交价格尚未齐备，暂不输出真实收益或胜率。可切换模拟演示验证1/3/5/10日回测操作。':'标题与正文分别统计，5股样本榜与演示排行榜隔离。样本不足时保留覆盖原因。'}}</p>
    <div class="real-research-controls"><label>内容口径<select v-model="content" aria-label="研究内容口径"><option value="titles">标题采样</option><option value="body">正文采样</option></select></label><label v-if="view!=='backtest'">自然日窗口<select v-model="days" aria-label="研究自然日窗口"><option v-for="n in [1,3,5,10,20]" :key="n" :value="n">近{{n}}自然日</option></select></label><label v-else>股票<select v-model="iid" aria-label="历史研究股票"><option v-for="s in stocks" :key="s.id" :value="s.id">{{s.name}}</option></select></label></div>
    <p v-if="error" role="alert" class="error-banner">{{error}}</p>
  </section>
  <LiveSignalScreen v-if="view==='signals'" :days="days" :content="content" @select="emit('select',$event)" />
  <LiveHistory v-if="view==='backtest'" :instrument-id="iid" :content="content" />
  <LiveRankings v-else :days="days" :content="content" @select="emit('select',$event)" />
</template>
<style scoped>.real-research{margin-bottom:16px}.section-note{line-height:1.9}.real-research-controls{display:flex;gap:16px;flex-wrap:wrap;margin-top:20px}.real-research-controls label{display:grid;gap:8px;font-size:12px;color:var(--muted)}select{background:var(--bg);color:var(--text);padding:10px;border:1px solid var(--border);border-radius:6px}</style>
