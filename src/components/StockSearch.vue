<script setup lang="ts">
import {ref,onUnmounted} from 'vue';
import {request} from '../api';
export type SearchStock={id:string;code:string;name:string;exchange:string;kind:string};
const props=defineProps<{disabled?:boolean}>();
const emit=defineEmits<{select:[stock:SearchStock]}>();
const query=ref(''),rows=ref<SearchStock[]>([]),busy=ref(false),error=ref(''),warning=ref(''),searched=ref(false);
let version=0;
const exchanges:Record<string,string>={XSHE:'深交所',XSHG:'上交所',XBSE:'北交所'};
async function search(){if(props.disabled)return;const id=++version;busy.value=true;rows.value=[];error.value='';warning.value='';searched.value=true;
  try{const result=await request<{items:SearchStock[];warning:string|null}>(`/live/search?q=${encodeURIComponent(query.value.trim())}`);if(id===version){rows.value=result.items;warning.value=result.warning||'';}}
  catch(e){if(id===version)error.value=e instanceof Error?e.message:'证券搜索失败';}
  finally{if(id===version)busy.value=false;}}
function select(stock:SearchStock){emit('select',stock);query.value=stock.name;rows.value=[];searched.value=false;}
function changed(){++version;busy.value=false;rows.value=[];searched.value=false;error.value='';warning.value='';}
onUnmounted(()=>++version);
</script>
<template>
  <div class="stock-search">
    <form @submit.prevent="search"><label><span class="sr-only">查询任意沪深北A股</span><input v-model="query" :disabled="disabled" @input="changed" aria-label="全A股代码或名称" placeholder="股票代码或名称，如600900或长江电力" maxlength="40" required /></label><button class="primary" :disabled="disabled||busy||!query.trim()">{{busy?'搜索中…':'搜索股票'}}</button></form>
    <p v-if="error" role="alert" class="error-banner">{{error}}</p><p v-if="warning" role="status" class="warning">{{warning}}</p>
    <div class="search-results" v-if="rows.length" aria-label="全A股搜索结果"><div v-for="s in rows" :key="s.id"><button :disabled="disabled||s.kind!=='stock'" class="secondary" @click="select(s)"><strong>{{s.name}}</strong><small>{{s.code}} · {{exchanges[s.exchange]||s.exchange}} · {{s.kind==='stock'?'A股':'指数，请使用大盘栏目'}}</small></button></div></div>
    <p v-else-if="searched&&!busy&&!error" class="subtle">未找到已核验的沪深北A股。请尝试完整六位代码；已退市、旧代码或源目录未收录的证券可能无法匹配。</p>
  </div>
</template>
<style scoped>.stock-search{margin:24px 0}.stock-search form{display:flex;gap:12px;align-items:end;flex-wrap:wrap}.stock-search label{display:grid;gap:8px;flex:1;min-width:180px;font-size:12px;color:var(--muted)}input{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:14px 16px;width:100%;font-size:16px;min-height:50px}form>.primary{min-height:50px;font-size:15px;min-width:130px}p{font-size:12px;line-height:1.9;margin-top:10px}.warning{color:#e7b876}.search-results{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.search-results button{text-align:left}.search-results small{display:block;color:var(--muted);margin-top:5px}.search-results button:disabled{opacity:.65}@media(max-width:550px){form>button{width:100%}.stock-search label{min-width:0;flex-basis:100%}}</style>
