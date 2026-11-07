<script setup lang="ts">
import {ref} from 'vue';
import LiveHistory from './LiveHistory.vue';
import StockSearch,{type SearchStock} from './StockSearch.vue';
const props=defineProps<{view:'backtest';initialInstrument?:string}>();
defineEmits<{select:[id:string]}>();
const iid=ref(props.initialInstrument||'XSHE:300059'),name=ref(''),content=ref<'titles'|'body'>('titles');
function choose(s:SearchStock){iid.value=s.id;name.value=s.name;}
</script>
<template>
  <section class="panel history-search"><h1>历史数据</h1><p class="subtle">按实际采集时间保存的情绪快照</p><StockSearch @select="choose"/><div class="history-controls"><span>{{name||iid}}</span><label>内容口径<select v-model="content"><option value="titles">标题采样</option><option value="body">正文采样</option></select></label></div></section>
  <LiveHistory :instrument-id="iid" :content="content"/>
  <p class="subtle history-note">真实收益回测需要连续历史信号、交易日历和复权价格。目前仅展示已保存快照，不输出未经验证的胜率。</p>
</template>
<style scoped>.history-search{margin-bottom:24px}.history-search>p{margin-top:10px}.history-controls{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.history-controls label{display:flex;align-items:center;gap:12px}.history-note{margin-top:20px}</style>
